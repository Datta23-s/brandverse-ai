"""The evaluation request set as the query planner reads it (evaluation/test_set.yaml).

The planner needs, per request, the sector in words, whether it is regulated, the product's generic
CATEGORY, and the audience. The category is the one product term the planner may use: brands and
products in the test set are pseudonyms no search engine can resolve (V7), and the real names are
there so claims can be checked, never so searches can use them. Categories were authored by the
developer from what each request describes, are untuned, and are frozen by
`evaluation/test_set.categories.lock`: a changed category changes every query built from it, so the
hash must be changed deliberately and the change recorded.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from app.research.planner import PlanRequest

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_SET = REPO_ROOT / "evaluation" / "test_set.yaml"
CATEGORY_LOCK = REPO_ROOT / "evaluation" / "test_set.categories.lock"


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a test set")
    return data


def categories(path: Path = TEST_SET) -> dict[str, str]:
    """{"B01.A": category, ...} for every product in the set."""
    out: dict[str, str] = {}
    for brand in _load(path)["brands"]:
        for key, product in brand["products"].items():
            out[f"{brand['id']}.{key}"] = product["category"]
    return out


def categories_hash(path: Path = TEST_SET) -> str:
    blob = json.dumps(categories(path), sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def plan_requests(path: Path = TEST_SET) -> dict[str, PlanRequest]:
    """{"R01": PlanRequest, ...} in the test set's order. The objective is carried, not used."""
    data = _load(path)
    brands = {b["id"]: b for b in data["brands"]}
    out: dict[str, PlanRequest] = {}
    for r in data["requests"]:
        brand = brands[r["brand"]]
        out[r["id"]] = PlanRequest(
            sector=str(brand["sector"]).replace("-", " "),
            regulated=bool(brand["regulated"]),
            category=brand["products"][r["product"]]["category"],
            audience=r.get("audience_hint"),
            objective=r.get("objective"),
        )
    return out


def evidence_tags(path: Path = TEST_SET) -> dict[str, dict[str, str]]:
    """Per request: is its research evidence NOVEL or a REPEAT of an earlier request's?

    Two requests share research evidence when they share a sector and category (the planner's only
    product term), which makes every query but the audience one identical. The first request of a
    group in the set's order is `novel`; later ones are `repeat`, and a repeat is `same_product`
    when an earlier request in the group used the same brand's same product, else
    `different_product`. `stage` adds the position the cold-start question needs: a request in
    position 1 has no memory by construction, so `novel` mixes cold starts with warm requests on a
    new category: `cold_start` (position 1), `warm_novel` (position 2 or 3, new category), `repeat`.
    """
    data = _load(path)
    brands = {b["id"]: b for b in data["brands"]}
    seen: dict[tuple[str, str], list[tuple[str, str]]] = {}
    out: dict[str, dict[str, str]] = {}
    for r in data["requests"]:
        brand = brands[r["brand"]]
        group = (str(brand["sector"]), brand["products"][r["product"]]["category"])
        earlier = seen.setdefault(group, [])
        product_id = (r["brand"], r["product"])
        if not earlier:
            kind, subtype = "novel", "first_in_group"
        else:
            kind = "repeat"
            subtype = "same_product" if product_id in earlier else "different_product"
        stage = (
            "repeat" if kind == "repeat" else ("cold_start" if r["position"] == 1 else "warm_novel")
        )
        earlier.append(product_id)
        out[r["id"]] = {"kind": kind, "subtype": subtype, "stage": stage}
    return out
