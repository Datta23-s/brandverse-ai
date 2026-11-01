"""The Edit-only rule is a refusal, not a reminder (Rules.md section 1.5; owner, 2026-11-02).

The heredoc-and-script pattern rebuilds a file from what the model believes it contains and has
mangled three files; a written rule did not stop it, so `.claude/hooks/block_source_rewrites.py`
refuses the command. These tests pin what is refused, what is not, and that the hook is wired.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".claude" / "hooks" / "block_source_rewrites.py"
SCRATCH = "C:/Users/DATTAS~1/AppData/Local/Temp/claude/C--Brandverse/abc/scratchpad"

spec = importlib.util.spec_from_file_location("block_source_rewrites", HOOK)
assert spec is not None and spec.loader is not None
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

REFUSED = [
    "uv run python - <<'EOF'\nopen('app/x.py','w').write('x')\nEOF",
    "node - <<'EOF'\nrequire('fs').writeFileSync('src/components/RunView.tsx', s)\nEOF",
    "sed -i 's/a/b/' Architecture.md",
    "perl -pi -e 's/a/b/' backend/app/core/config.py",
    "cat > backend/app/new_module.py <<'EOF'\nx = 1\nEOF",
    "Set-Content -Path frontend/src/lib/api.ts -Value $x",
    "python -c \"from pathlib import Path; Path('Phases.md').write_text('x')\"",
    "echo '# x' >> docs/decision-log.md",
]
ALLOWED = [
    "uv run pytest -q -m 'not db' 2>&1 | tail -5",
    'git commit -qm "docs: a note about Architecture.md and app/x.py"',
    "grep -n foo backend/app/core/config.py",
    "sed -n '1,40p' Phases.md",
    "uv run python scripts/chunk_f_first_campaign.py --max-calls 10 > /dev/null",
    f'uv run python - <<EOF\nopen("{SCRATCH}/note.json", "w").write("{{}}")\nEOF',
    f"uv run pytest -q > {SCRATCH}/out.txt 2>&1",
    "echo '- note' >> 'C:/Users/Datta Sable/.claude/projects/C--Brandverse/memory/MEMORY.md'",
    "uv run ruff format . && uv run mypy",
    "cd backend && uv run python -m scripts.export_contracts",
]


@pytest.mark.parametrize("command", REFUSED)
def test_a_script_that_rewrites_a_source_file_is_refused(command: str) -> None:
    reason = hook.decide(command)
    assert reason is not None and "Edit tool" in reason


@pytest.mark.parametrize("command", ALLOWED)
def test_reading_running_repository_scripts_and_scratch_writes_are_allowed(command: str) -> None:
    assert hook.decide(command) is None


def test_a_scratch_script_that_rewrites_a_source_file_is_refused_by_what_is_inside_it() -> None:
    """`uv run python <scratchpad>/patch.py` hides the write in a file: the hook reads the file."""
    body = "p = 'app/workers/jobs.py'\ns = open(p).read()\nopen(p, 'w').write(s)\n"
    command = f'uv run python "{SCRATCH}/patch.py"'
    assert hook.decide(command, lambda path: body) is not None
    harmless = "print('hello')\n"
    assert hook.decide(command, lambda path: harmless) is None


def test_the_hook_blocks_with_status_2_and_a_reason_on_stderr() -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": "sed -i 's/a/b/' Phases.md"}}
    done = subprocess.run(  # noqa: S603 - a fixed command
        [sys.executable, "-I", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 2 and "Rules.md section 1.5" in done.stderr


def test_the_hook_never_wedges_the_tool_on_input_it_cannot_read() -> None:
    done = subprocess.run(  # noqa: S603 - a fixed command
        [sys.executable, "-I", str(HOOK)],
        input="not json",
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0


def test_the_hook_is_wired_into_the_projects_settings() -> None:
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    entries = settings["hooks"]["PreToolUse"]
    assert any(
        e["matcher"] == "Bash"
        and any("block_source_rewrites.py" in h["command"] for h in e["hooks"])
        for e in entries
    )
