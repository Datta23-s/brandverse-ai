"""The committed frontend contract file is exactly what the backend models produce."""

from app.api.contracts import CONTRACTS_PATH, build_contracts, render_contracts


def test_committed_contracts_are_current() -> None:
    assert CONTRACTS_PATH.exists(), "run `uv run python -m scripts.export_contracts`"
    committed = CONTRACTS_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == render_contracts(), (
        "frontend/src/contracts/api-contracts.json is stale: "
        "run `uv run python -m scripts.export_contracts` from backend/"
    )


def test_contracts_carry_all_fifteen_steps_and_every_event_type() -> None:
    contracts = build_contracts()
    assert [s["number"] for s in contracts["pipeline_steps"]] == list(range(1, 16))
    event_schema = contracts["schemas"]["RunEvent"]
    types = set(event_schema["discriminator"]["mapping"])
    assert types == {"run.queued", "run.started", "step", "run.completed", "run.failed"}
