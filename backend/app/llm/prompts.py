"""Versioned prompt templates (Phases.md Phase 2): files, not inline f-strings.

A template is `config/prompts/<prompt_id>/<version>.yaml`, naming its own id and version (both
must match the path), the variables it takes, and its system and user text. Placeholders are
`string.Template` ones (`$name`; a literal dollar is `$$`) — no expressions, no code, and the
placeholders must be exactly the declared variables.

Every model call records the id and version of the template that produced it (llm_calls
`prompt_id`, `prompt_version`, migration 0014), so the evaluation can show that every run in an
arm used the same prompts (Rules.md A5, A6; app/evaluation/prompt_versions.py).

A released version is immutable. `config/prompts/versions.lock` holds the SHA-256 of every
version file (line endings normalised, so a Windows checkout hashes as CI does); the library
refuses to load if a file is missing from the lock or no longer matches it. Changing a prompt
means adding a new version file and its lock line, and changing the caller to name it — never
editing a version in place, which would make two different prompts share one recorded version.
Callers name the version explicitly; there is no "latest".
"""

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

LOCK_FILE = "versions.lock"
_ID = r"[a-z][a-z0-9_]{0,62}"
_VERSION = r"v[1-9][0-9]{0,5}"
_LOCK_LINE = re.compile(rf"^({_ID}) ({_VERSION}) sha256:([0-9a-f]{{64}})$")


@dataclass(frozen=True, slots=True)
class PromptRef:
    """What llm_calls records about the prompt behind a call."""

    prompt_id: str
    prompt_version: str

    def __str__(self) -> str:
        return f"{self.prompt_id}@{self.prompt_version}"


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """A template with its variables filled in. The gateway accepts nothing else."""

    ref: PromptRef
    system: str | None
    user: str
    # Part of the locked unit (it is in the hash-locked file). None: the provider's default.
    temperature: float | None = None


class PromptTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=rf"^{_ID}$")
    version: str = Field(pattern=rf"^{_VERSION}$")
    description: str = Field(min_length=1)
    variables: tuple[str, ...]
    system: str | None
    user: str = Field(min_length=1)
    # The sampling temperature this prompt is called at, or None for the provider's default. It is
    # part of the file, so part of the hash: a prompt called at another temperature is another
    # version.
    temperature: float | None = Field(default=None, ge=0, le=2)

    @model_validator(mode="after")
    def _placeholders_are_the_declared_variables(self) -> "PromptTemplate":
        found: set[str] = set()
        for text in (self.system, self.user):
            if text is None:
                continue
            template = Template(text)
            if not template.is_valid():
                raise ValueError(f"{self.ref}: malformed placeholder (a literal $ is $$)")
            found.update(template.get_identifiers())
        if found != set(self.variables) or len(set(self.variables)) != len(self.variables):
            raise ValueError(
                f"{self.ref}: placeholders {sorted(found)} != declared variables "
                f"{list(self.variables)}"
            )
        return self

    @property
    def ref(self) -> PromptRef:
        return PromptRef(self.id, self.version)

    def render(self, **values: str) -> RenderedPrompt:
        if set(values) != set(self.variables):
            raise ValueError(f"{self.ref}: takes {sorted(self.variables)}, given {sorted(values)}")
        system = Template(self.system).substitute(values) if self.system is not None else None
        return RenderedPrompt(
            self.ref, system, Template(self.user).substitute(values), self.temperature
        )


def file_sha256(path: Path) -> str:
    """The hash recorded in the lock: of the file's text with line endings normalised to LF."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PromptLibrary:
    def __init__(self, templates: dict[PromptRef, PromptTemplate]) -> None:
        self._templates = templates

    def get(self, prompt_id: str, version: str) -> PromptTemplate:
        try:
            return self._templates[PromptRef(prompt_id, version)]
        except KeyError:
            raise KeyError(f"no prompt {prompt_id}@{version} in config/prompts") from None

    def refs(self) -> list[PromptRef]:
        return sorted(self._templates, key=str)


def load_library(directory: Path) -> PromptLibrary:
    lock = _read_lock(directory / LOCK_FILE)
    templates: dict[PromptRef, PromptTemplate] = {}
    for path in sorted(directory.glob("*/*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError(f"{path} is not a mapping")
        template = PromptTemplate.model_validate(document)
        if (template.id, template.version) != (path.parent.name, path.stem):
            raise ValueError(f"{path}: names {template.ref}, but its path says otherwise")
        expected = lock.pop(template.ref, None)
        if expected is None:
            raise ValueError(f"{path}: {template.ref} is not in {LOCK_FILE}")
        if file_sha256(path) != expected:
            raise ValueError(
                f"{path}: {template.ref} differs from the version recorded in {LOCK_FILE}. "
                "A released version is never edited: add a new version instead."
            )
        templates[template.ref] = template
    if lock:
        raise ValueError(f"{LOCK_FILE} lists versions with no file: {sorted(map(str, lock))}")
    return PromptLibrary(templates)


def _read_lock(path: Path) -> dict[PromptRef, str]:
    entries: dict[PromptRef, str] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        match = _LOCK_LINE.match(line.strip())
        if match is None:
            raise ValueError(f"{path}:{n}: expected '<id> <version> sha256:<hex>'")
        ref = PromptRef(match.group(1), match.group(2))
        if ref in entries:
            raise ValueError(f"{path}:{n}: {ref} listed twice")
        entries[ref] = match.group(3)
    return entries


@lru_cache(maxsize=4)
def library_from(directory: Path) -> PromptLibrary:
    """Loaded once per directory per process (config is read once, Rules.md §3.1)."""
    return load_library(directory)
