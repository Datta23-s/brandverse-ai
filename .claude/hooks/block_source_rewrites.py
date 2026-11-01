"""PreToolUse hook for Bash: refuse a command that REWRITES a source or document file from a script.

Rules.md section 1.5 (owner, 2026-11-01): files are edited with the Edit tool only, never by a
script or a heredoc that rewrites them. A script rebuilds a file from what the model BELIEVES the
file contains, which may be truncated or stale, and lint passing does not prove the surviving
content is what was there before. This pattern mangled three files; a reminder did not stop a
fourth and fifth occurrence, so the command is refused instead.

What is refused: a command (or a script file it runs from outside the repository) that both
  - writes (`open(..., "w")`, `write_text`, `writeFileSync`, `sed -i`, `perl -i`, `Set-Content`,
    a shell redirect `>` / `>>` / `tee`), and
  - names a source or document path (.py .ts .tsx .js .sql .yaml .toml .json .md ...) that is not
    scratch space (a temp directory, the scratchpad) or the assistant's own memory.
What is allowed: running a script that is part of the repository (`uv run python scripts/x.py`),
reading anything, and writing scratch files.

Exit status 2 with the reason on stderr blocks the call (Claude Code hook protocol); anything else
lets it through. A hook that cannot read its input allows the call: it must never wedge the tool.
"""

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

# Calls that write a file, wherever the path is named in the same command (they appear only in an
# inline script or a deliberate write command, so any source path in the command is the suspect).
TOOL_WRITES = re.compile(
    r"""open\([^)]*['"][wa]b?\+?['"]"""
    r"|\.write_text\(|\.write_bytes\("
    r"|writeFileSync|appendFileSync|writeFile\(|fs\.write"
    r"|\bsed\s+(-[A-Za-z]*\s+)*-[A-Za-z]*i|\bperl\s+(-[A-Za-z]*\s+)*-[A-Za-z]*i"
    r"|\bSet-Content\b|\bOut-File\b|\bAdd-Content\b"
    r"|\btee\b"
)
# A shell redirect counts only for ITS TARGET: `> /dev/null; pytest tests/x.py` writes no source.
REDIRECT = re.compile(r"""(?<![<>=\-0-9&|])>{1,2}(?![>&=])\s*["']?([^\s"'|;&]+)""")
SOURCE_PATH = re.compile(
    r"""[\w./\\:~$-]*[\w-]\.(?:py|ts|tsx|js|jsx|mjs|css|sql|ya?ml|toml|json|md)\b""",
    re.IGNORECASE,
)
# Scratch space and the assistant's own state are not repository sources.
EXEMPT_PARTS = ("scratchpad", "/tmp/", "\\temp\\", "/temp/", "appdata", "/.claude/", "\\.claude\\")


def _is_exempt(path: str) -> bool:
    p = path.lower().replace("\\\\", "\\")
    if p.startswith("/dev/null"):
        return True
    return any(part in p for part in EXEMPT_PARTS) and ".claude/hooks" not in p


def _paths(text: str) -> list[str]:
    return [m.group(0) for m in SOURCE_PATH.finditer(text)]


def rewrites_source(text: str) -> list[str]:
    """The non-scratch source paths named in `text` if it also writes; [] when it does not."""
    paths: list[str] = []
    if TOOL_WRITES.search(text):
        paths += _paths(text)
    for match in REDIRECT.finditer(text):
        paths += _paths(match.group(1))
    return [p for p in paths if not _is_exempt(p)]


def decide(command: str, read_file: Callable[[str], str | None] = lambda p: None) -> str | None:
    """None to allow the command, or the reason it is refused. `read_file` lets the hook look
    inside a script the command runs (a path outside the repository is read; a repository script
    is trusted: it is reviewed code, not a one-off rewrite)."""
    named = rewrites_source(command)
    if named:
        return _reason(named, "the command")
    for path in re.findall(r"""[\w./\\:~$-]+\.(?:py|js|mjs|sh|ps1)\b""", command):
        if not _is_exempt(path):
            continue  # a repository script: reviewed code, allowed
        body = read_file(path)
        if body:
            inside = rewrites_source(body)
            if inside:
                return _reason(inside, f"the script {path}")
    return None


def _reason(paths: list[str], where: str) -> str:
    shown = ", ".join(sorted(set(paths))[:4])
    return (
        f"Refused: {where} writes to {shown} from a script. Rules.md section 1.5: edit source and "
        "document files with the Edit tool only (or Write to create one), never with a script or "
        "heredoc that rewrites them; a script rebuilds a file from what you believe it contains."
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        command = str(payload.get("tool_input", {}).get("command", ""))
    except Exception:
        return 0  # never wedge the tool on input this hook cannot read

    def read(path: str) -> str | None:
        try:
            return Path(path.replace("\\\\", "\\")).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    reason = decide(command, read)
    if reason is not None:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
