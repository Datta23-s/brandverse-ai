"""No accented character in the evaluation's text (Memory.md §5, "Normalisation by path").

The two halves of hybrid retrieval disagree on accents: the pinned tokenizer strips them, the
`english` text search configuration keeps them. The gap is left open, and it is inert for the
relevance-set results only while no text they are computed from contains an accented character.
This test makes that a checked fact rather than a remembered one: it scans everything under
`evaluation/` — the brief, the request and tuning sets, and the relevance corpus and query set once
they are committed — and fails on any character whose canonical decomposition carries a combining
mark (what the tokenizer's accent stripping removes). If it fails, the owner decides before the
corpus or pool goes further (ruling 2026-10-20).
"""

import unicodedata
from pathlib import Path

EVALUATION = Path(__file__).resolve().parents[2] / "evaluation"
TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".jsonl", ".txt", ".csv", ".lock"}


def accented(text: str) -> list[str]:
    return sorted(
        {
            ch
            for ch in text
            if ord(ch) > 127
            and any(unicodedata.category(c) == "Mn" for c in unicodedata.normalize("NFD", ch))
        }
    )


def test_the_detector_sees_accents_and_ignores_other_non_ascii() -> None:
    assert accented("Café crème, naïve, façade") == ["ç", "è", "é", "ï"]
    assert accented("e" + chr(0x301)) == [chr(0x301)]  # a combining mark on its own
    assert accented("kadai — tawa, ₹, §5, “percale”") == []


def test_no_evaluation_text_contains_an_accented_character() -> None:
    files = [p for p in EVALUATION.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES]
    assert any(p.name == "brief.md" for p in files)  # the scan reaches the relevance set
    found = {
        str(p.relative_to(EVALUATION)): chars
        for p in files
        if (chars := accented(p.read_text(encoding="utf-8")))
    }
    assert found == {}, f"accented characters in evaluation text (Memory.md §5): {found}"
