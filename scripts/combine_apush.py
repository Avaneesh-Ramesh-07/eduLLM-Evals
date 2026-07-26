"""Concatenate the per-type APUSH JSONL (SAQ/DBQ/LEQ) into the combined
`scenarios.jsonl` + `rubrics.jsonl`, then cross-validate referential integrity.

Run after scrape_apush_saq.py and scrape_apush_essays.py:
    llm-from-scratch/Scripts/python.exe scripts/combine_apush.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "AP_IB" / "APUSH"
TYPES = ["saq", "dbq", "leq"]

# expected shape (per requirements): every rubric carries the three uncalibrated
# placeholders and the linking field; every scenario the full-credit reference.
RUBRIC_KEYS = {
    "criterion_id", "scenario_id", "criterion", "expected_evidence",
    "scoring_type", "score_anchors", "linked_criteria", "primary_skill",
    "q_mapping", "q_rationale", "difficulty_uncalibrated",
    "discrimination_uncalibrated", "irt_params_uncalibrated", "criticality",
    "objectivity", "explicitness", "source", "status", "version",
}
SCENARIO_KEYS = {
    "scenario_id", "use_case", "subject", "grade_band", "modality", "prompt",
    "conversation_context", "reference_solution", "criterion_ids", "source",
    "split", "version",
}


def _load(name: str) -> list[dict]:
    path = OUT_DIR / name
    return [json.loads(l) for l in path.open(encoding="utf-8")]


def _write(name: str, rows: list[dict]) -> None:
    with (OUT_DIR / name).open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def combine() -> tuple[list[dict], list[dict]]:
    scenarios, rubrics = [], []
    for t in TYPES:
        scenarios += _load(f"{t}_scenarios.jsonl")
        rubrics += _load(f"{t}_rubrics.jsonl")
    _write("scenarios.jsonl", scenarios)
    _write("rubrics.jsonl", rubrics)
    return scenarios, rubrics


def validate(scenarios: list[dict], rubrics: list[dict]) -> list[str]:
    errs: list[str] = []

    sids = [s["scenario_id"] for s in scenarios]
    cids = [r["criterion_id"] for r in rubrics]
    if len(set(sids)) != len(sids):
        errs.append("duplicate scenario_id")
    if len(set(cids)) != len(cids):
        errs.append("duplicate criterion_id")
    sid_set, cid_set = set(sids), set(cids)

    # schema completeness
    for s in scenarios:
        miss = SCENARIO_KEYS - s.keys()
        if miss:
            errs.append(f"{s['scenario_id']}: missing scenario keys {miss}")
        if not s.get("reference_solution"):
            errs.append(f"{s['scenario_id']}: empty reference_solution")
    for r in rubrics:
        miss = RUBRIC_KEYS - r.keys()
        if miss:
            errs.append(f"{r['criterion_id']}: missing rubric keys {miss}")
        for k in ("difficulty_uncalibrated", "discrimination_uncalibrated", "irt_params_uncalibrated"):
            if r.get(k) is not None:
                errs.append(f"{r['criterion_id']}: {k} should be null")
        if not r["expected_evidence"]:
            errs.append(f"{r['criterion_id']}: empty expected_evidence")

    # scenario <-> rubric referential integrity
    by_scenario: dict[str, list[str]] = {}
    for r in rubrics:
        if r["scenario_id"] not in sid_set:
            errs.append(f"{r['criterion_id']}: dangling scenario_id {r['scenario_id']}")
        by_scenario.setdefault(r["scenario_id"], []).append(r["criterion_id"])
        for link in r["linked_criteria"]:
            if link not in cid_set:
                errs.append(f"{r['criterion_id']}: dangling linked_criteria {link}")
            if r["scenario_id"] != next((x["scenario_id"] for x in rubrics if x["criterion_id"] == link), None):
                errs.append(f"{r['criterion_id']}: linked across scenarios -> {link}")
    for s in scenarios:
        declared = s["criterion_ids"]
        actual = by_scenario.get(s["scenario_id"], [])
        if set(declared) != set(actual):
            errs.append(f"{s['scenario_id']}: criterion_ids {declared} != rubric ids {actual}")

    return errs


def main() -> None:
    scenarios, rubrics = combine()
    errs = validate(scenarios, rubrics)

    from collections import Counter
    print(f"scenarios: {len(scenarios)}   rubrics: {len(rubrics)}")
    print("scenarios by use_case:", dict(Counter(s["use_case"] for s in scenarios)))
    print("rubrics by skill:", dict(Counter(r["primary_skill"] for r in rubrics)))
    linked = sum(1 for r in rubrics if r["linked_criteria"])
    print(f"criteria with linked_criteria: {linked}")
    ev = [len(r["expected_evidence"]) for r in rubrics]
    print(f"expected_evidence: min={min(ev)} max={max(ev)}")

    if errs:
        print(f"\nVALIDATION FAILED ({len(errs)} issues):")
        for e in errs[:40]:
            print("  -", e)
        sys.exit(1)
    print("\nvalidation: OK (all references consistent, schema complete)")


if __name__ == "__main__":
    main()
