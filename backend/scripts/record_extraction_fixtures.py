"""Record the extraction library's raw output for each saved HTML page (tests/fixtures/extraction).

CI does not install trafilatura (optional `extraction` group), so it runs `Extractor` over these
recordings. Each `<name>.expected.json` holds the SHA-256 of its HTML and the library version, so a
changed page or a bumped pin fails CI until this is run again and the diff is reviewed.

Usage (from backend/, with `uv sync --inexact --group extraction`):
  uv run python -m scripts.record_extraction_fixtures
"""

import hashlib
import json
import sys
from importlib.metadata import version
from pathlib import Path

from app.research.extraction import trafilatura_extract

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "extraction"
URL = "https://fixtures.example.test/"


def main() -> int:
    library = version("trafilatura")
    for page in sorted(FIXTURES.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        recorded = {
            "html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "trafilatura": library,
            "url": URL + page.name,
            "raw": trafilatura_extract(html, URL + page.name),
        }
        page.with_suffix(".expected.json").write_text(
            json.dumps(recorded, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"recorded {page.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
