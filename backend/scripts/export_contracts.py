"""Write the run contracts the frontend is built against (app/api/contracts.py).

Usage (from backend/):  uv run python -m scripts.export_contracts
"""

from app.api.contracts import CONTRACTS_PATH, render_contracts

if __name__ == "__main__":
    CONTRACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTRACTS_PATH.write_text(render_contracts(), encoding="utf-8", newline="\n")
    print(f"wrote {CONTRACTS_PATH}")
