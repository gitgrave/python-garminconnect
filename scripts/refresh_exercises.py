#!/usr/bin/env python3
"""Regenerate the bundled exercise catalog from Garmin Connect's web-data.

Garmin serves its strength-exercise taxonomy as static-ish web assets behind a
browser (JWT_WEB) session — the OAuth/DI token used by the library cannot reach
them. So the catalog is generated here and committed as package data
(``garminconnect/data/exercises.json``); the ``Garmin.get_exercise_types``
method reads that bundle at runtime with no network/auth.

Sources (all under https://connect.garmin.com):
  * /web-api/web-data/exercises/Exercises.json          (categories -> exercises -> muscles)
  * /web-api/web-data/exercises/exerciseToEquipments.json (equipment per exercise)
  * /web-translations/exercise_types/exercise_types.properties (display names)

Usage:
  # From already-downloaded files (no auth needed):
  python scripts/refresh_exercises.py --from-dir ~/Downloads

  # Live, using a browser JWT_WEB cookie (Connect > devtools > copy cookie):
  python scripts/refresh_exercises.py --cookie "$GARMIN_WEB_COOKIE"
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

BASE = "https://connect.garmin.com"
EXERCISES_URL = f"{BASE}/web-api/web-data/exercises/Exercises.json"
EQUIPMENT_URL = f"{BASE}/web-api/web-data/exercises/exerciseToEquipments.json"
PROPS_URL = f"{BASE}/web-translations/exercise_types/exercise_types.properties"

OUT_PATH = Path(__file__).resolve().parent.parent / "garminconnect" / "data" / "exercises.json"


def _parse_properties(text: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        props[key.strip()] = value.strip()
    return props


def _titleize(key: str) -> str:
    return key.replace("_", " ").title()


def _load_from_dir(d: Path) -> tuple[dict, list, dict]:
    exercises = json.loads((d / "Exercises.json").read_text())
    equipment = json.loads((d / "exerciseToEquipments.json").read_text())
    props = _parse_properties((d / "exercise_types.properties").read_text())
    return exercises, equipment, props


def _load_from_web(cookie: str) -> tuple[dict, list, dict]:
    try:
        from curl_cffi import requests as creq  # browser TLS to clear Cloudflare
    except ImportError:
        import requests as creq  # type: ignore

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "Referer": f"{BASE}/modern/",
        "Cookie": cookie,
    }
    kw = {"impersonate": "chrome"} if creq.__name__ == "curl_cffi.requests" else {}

    def _get(url: str) -> str:
        r = creq.get(url, headers=headers, timeout=30, **kw)
        if r.status_code != 200:
            raise SystemExit(f"GET {url} -> {r.status_code} (cookie expired/invalid?)")
        return r.text

    exercises = json.loads(_get(EXERCISES_URL))
    equipment = json.loads(_get(EQUIPMENT_URL))
    props = _parse_properties(_get(f"{PROPS_URL}?bust=refresh"))
    return exercises, equipment, props


def build_catalog(exercises: dict, equipment: list, props: dict) -> dict:
    """Merge the three source docs into the bundled catalog structure."""
    # (category, exercise) -> equipmentKeys
    equip_map: dict[tuple[str, str], list[str]] = {}
    for cat in equipment:
        ckey = cat.get("exerciseCategoryKey")
        for ex in cat.get("exercisesInCategory", []):
            equip_map[(ckey, ex.get("exerciseKey"))] = ex.get("equipmentKeys", [])

    categories: dict[str, dict] = {}
    for cat_key, cat_val in exercises.get("categories", {}).items():
        ex_out: dict[str, dict] = {}
        for ex_key, ex_val in cat_val.get("exercises", {}).items():
            ex_out[ex_key] = {
                "displayName": props.get(f"{cat_key}_{ex_key}", _titleize(ex_key)),
                "primaryMuscles": ex_val.get("primaryMuscles", []),
                "secondaryMuscles": ex_val.get("secondaryMuscles", []),
                "equipment": equip_map.get((cat_key, ex_key), []),
            }
        categories[cat_key] = {
            "displayName": props.get(f"{cat_key}_{cat_key}", _titleize(cat_key)),
            "exercises": dict(sorted(ex_out.items())),
        }

    return {
        "_source": "connect.garmin.com/web-api/web-data/exercises + web-translations",
        "_generated": datetime.now(UTC).strftime("%Y-%m-%d"),
        "categories": dict(sorted(categories.items())),
    }


def _report(msg: str) -> None:
    print(msg)  # noqa: T201 — CLI progress output


def main() -> int:
    """Parse args, load the sources, and write the bundled catalog."""
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--from-dir", type=Path, help="Directory with the 3 downloaded source files")
    g.add_argument("--cookie", help="Browser Cookie header containing a valid JWT_WEB")
    args = ap.parse_args()

    if args.from_dir:
        exercises, equipment, props = _load_from_dir(args.from_dir)
    else:
        exercises, equipment, props = _load_from_web(args.cookie)

    catalog = build_catalog(exercises, equipment, props)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")

    n_cat = len(catalog["categories"])
    n_ex = sum(len(c["exercises"]) for c in catalog["categories"].values())
    _report(f"Wrote {OUT_PATH} — {n_cat} categories, {n_ex} exercises")
    return 0


if __name__ == "__main__":
    sys.exit(main())
