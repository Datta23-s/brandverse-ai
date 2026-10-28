"""The recording phase never loads the encoder (owner, 2026-10-27), by default-deny.

Recording is search plus fetch plus store. The encoder is the single largest consumer on the worker
host (1.03 GB resident after embedding, 1.93 GB peak commit; `Architecture.md` §11, "Worker-host
memory"), and the recording is the long overnight job that a memory shortage already killed once. A
process that never loads `sentence_transformers` gets most of its headroom back, so that is made
impossible rather than remembered, in two layers because each covers what the other cannot:

1. **Static, and it runs everywhere including CI** (which does not install the encoder group, so a
   runtime check there would pass because the library is absent, not because nothing imports it):
   the import closure of the recording path, read from the source, must not reach a forbidden
   module at import time. An import inside a function is a lazy import and is allowed, as in
   `app/memory/embeddings.py`, which loads the library only when an encoder is built; a name
   under `if TYPE_CHECKING:` never executes and is ignored.
2. **Runtime, where it can fail for the right reason:** a clean interpreter imports every module on
   the recording path and the forbidden modules must not be in `sys.modules`. It is skipped, with
   its reason, where the encoder group is not installed, since there it cannot tell anything.

`RECORDING_MODULES` is the recording path. A module added to `app/research/` whose name says it
records (`record`, `snapshot`, `manifest`) must be added to it, or a test here fails: the guard
cannot be bypassed by writing the recorder somewhere it does not look. Both layers are shown to fail
on synthetic code, so they are known to work, not assumed.
"""

import ast
import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

# Everything search, fetch, cache, robots and store pull in at import time. The ramp is measurement
# tooling, not the recording, but it runs on the same machine overnight and is held to the same
# rule.
RECORDING_MODULES = (
    "app.research.search",
    "app.research.search_cache",
    "app.research.fetch",
    "app.research.page_cache",
    "app.research.robots",
    "app.research.rate_ramp",
    "app.research.snapshot",  # record_request: search + pages into the evaluation store
    "app.research.driver",  # what the snapshot runs in RECORD mode
    "app.research.planner",
    "app.memory.scoring",
)
# The encoder stack. `sentence_transformers` is the entry point; torch and transformers are what it
# costs. Naming all three means loading one of them some other way is caught too.
FORBIDDEN = ("sentence_transformers", "torch", "transformers")
RECORDER_WORDS = ("record", "snapshot", "manifest")


def module_file(name: str) -> Path | None:
    """The source file of an `app.*` module, or None if it is not a first-party module here."""
    base = BACKEND.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def parents_of(name: str) -> list[str]:
    """Importing `a.b.c` also runs `a/__init__.py` and `a/b/__init__.py`."""
    parts = name.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


class ImportVisitor(ast.NodeVisitor):
    """The names a module imports AT IMPORT TIME: function bodies (lazy imports) and
    `if TYPE_CHECKING:` blocks (never executed) are skipped; class bodies and `try`/`if` at module
    level execute and are read."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815
    visit_Lambda = visit_FunctionDef  # type: ignore[assignment]  # noqa: N815

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        test = node.test
        guarded = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        for child in node.orelse if guarded else [*node.body, *node.orelse]:
            self.visit(child)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.names += [alias.name for alias in node.names]

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.level or not node.module:
            return  # relative imports are not used in this codebase
        self.names.append(node.module)
        self.names += [f"{node.module}.{alias.name}" for alias in node.names]


def import_time_imports(source: str) -> list[str]:
    visitor = ImportVisitor()
    visitor.visit(ast.parse(source))
    return visitor.names


def forbidden_reached(modules: tuple[str, ...], forbidden: tuple[str, ...]) -> dict[str, list[str]]:
    """forbidden module -> the chain of first-party modules that import it at import time.
    Walks the import closure from `modules` through first-party (`app.*`) files only."""
    found: dict[str, list[str]] = {}
    seen: set[str] = set()
    stack: list[tuple[str, list[str]]] = [(m, [m]) for m in modules]
    while stack:
        name, chain = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for each in [*parents_of(name), name]:
            path = module_file(each)
            if path is None:
                continue
            for imported in import_time_imports(path.read_text(encoding="utf-8")):
                top = imported.split(".")[0]
                if top in forbidden:
                    found.setdefault(top, chain + [f"imports {imported}"])
                elif top == "app":
                    stack.append((imported, [*chain, imported]))
    return found


# ── The static layer, on the real recording path ─────────────────────────────────────────────


def test_the_recording_path_reaches_no_encoder_module_at_import_time() -> None:
    reached = forbidden_reached(RECORDING_MODULES, FORBIDDEN)
    assert not reached, "the recording path imports the encoder stack: " + "; ".join(
        f"{mod} via {' -> '.join(chain)}" for mod, chain in reached.items()
    )


def test_every_recording_module_exists() -> None:
    # A rename must not leave the guard checking nothing.
    missing = [m for m in RECORDING_MODULES if module_file(m) is None]
    assert not missing, f"RECORDING_MODULES names modules that do not exist: {missing}"


def test_a_module_that_looks_like_a_recorder_cannot_escape_the_guard() -> None:
    listed = {module_file(m) for m in RECORDING_MODULES}
    looks_like_one = [
        p
        for p in sorted((BACKEND / "app").rglob("*.py"))
        if any(word in p.stem for word in RECORDER_WORDS)
    ]
    unlisted = [p.relative_to(BACKEND).as_posix() for p in looks_like_one if p not in listed]
    assert not unlisted, (
        f"{unlisted} look like part of the recording but are not in RECORDING_MODULES: add them "
        "(the encoder must not be reachable from the recording, and it is checked only for what "
        "is listed)"
    )


# ── The static layer is shown to fail ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        "import sentence_transformers",
        "from sentence_transformers import SentenceTransformer",
        "import torch.nn as nn",
        "from transformers import AutoModel",
        "try:\n    import torch\nexcept ImportError:\n    torch = None",
        "if True:\n    import sentence_transformers",
        "class C:\n    import torch",
    ],
)
def test_the_scan_sees_an_import_that_runs_when_the_module_is_imported(source: str) -> None:
    names = import_time_imports(source)
    assert any(n.split(".")[0] in FORBIDDEN for n in names), names


@pytest.mark.parametrize(
    "source",
    [
        # lazy: inside a function, as embeddings.py loads the library only when an encoder is built
        "def load():\n    from sentence_transformers import SentenceTransformer",
        "async def load():\n    import torch",
        "f = lambda: __import__('torch')",
        # never executes
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import torch",
        "import typing\nif typing.TYPE_CHECKING:\n    from transformers import AutoModel",
    ],
)
def test_the_scan_allows_a_lazy_or_type_only_import(source: str) -> None:
    assert not [n for n in import_time_imports(source) if n.split(".")[0] in FORBIDDEN]


def test_the_closure_follows_first_party_imports_and_names_the_chain(tmp_path: Path) -> None:
    # The real embeddings module is the example: it names the library, but only inside a function,
    # so reaching it is fine; a module that imported it at the top level would not be.
    assert not forbidden_reached(("app.memory.embeddings",), FORBIDDEN)
    source = module_file("app.memory.embeddings")
    assert source is not None and "sentence_transformers" in source.read_text(encoding="utf-8")
    assert forbidden_reached(("app.memory.embeddings",), ("yaml",)), "the closure sees real imports"


# ── The runtime layer ────────────────────────────────────────────────────────────────────────

ENCODER_INSTALLED = importlib.util.find_spec("sentence_transformers") is not None


def loaded_after_importing(modules: tuple[str, ...], names: tuple[str, ...]) -> set[str]:
    """Import `modules` in a fresh interpreter; return which of `names` ended up in sys.modules."""
    code = textwrap.dedent(
        f"""
        import importlib, json, sys
        for m in {list(modules)!r}:
            importlib.import_module(m)
        print(json.dumps(sorted(n for n in {list(names)!r} if n in sys.modules)))
        """
    )
    done = subprocess.run(  # noqa: S603 - our own interpreter, our own code
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(BACKEND)!r})\n{code}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    import json

    return set(json.loads(done.stdout.strip().splitlines()[-1]))


def test_the_runtime_check_can_see_a_module_that_was_loaded() -> None:
    # A control, so "nothing was loaded" is known to mean nothing, not that the check is blind.
    # Standard-library modules only: a control that imported first-party code could be broken by
    # the very change it is there to detect.
    assert loaded_after_importing(("json", "csv"), ("json", "csv", "sqlite3")) == {"json", "csv"}
    assert loaded_after_importing(("json",), ("sentence_transformers",)) == set()


@pytest.mark.skipif(
    not ENCODER_INSTALLED,
    reason="the encoder group is not installed, so its absence from sys.modules proves nothing "
    "(CI is in this case; the static layer above is what guards it there)",
)
def test_importing_the_recording_path_does_not_load_the_encoder() -> None:
    loaded = loaded_after_importing(RECORDING_MODULES, FORBIDDEN)
    assert not loaded, f"importing the recording path loaded {sorted(loaded)}"
