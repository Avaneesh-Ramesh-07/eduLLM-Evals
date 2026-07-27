"""Convert the WildBench v2 test split into Scenario + Rubric Schema JSONL.

Source: https://huggingface.co/datasets/allenai/WildBench (config `v2`, 1024 rows).
`v2` is used rather than `v2-hard` because only `v2` carries the `references`
column, which both `reference_solution` and `expected_evidence` depend on.

Mapping (WildBench column -> schema field):
    primary_tag                    -> use_case, subject, primary_skill (slug), q_mapping
    conversation_input[-1].content -> prompt            (every row ends on a user turn)
    conversation_input[:-1]        -> conversation_context, as {role, content} dicts
    references["gpt-4"]            -> reference_solution, expected_evidence
    checklist[i]                   -> one rubric row each, criterion_id <sid>_c{i+1:02d}
    id                             -> source_id (join key back to HuggingFace)

Q-matrix column order is the fixed TAGS list below - alphabetical, 11 columns.
WildBench's canonical taxonomy also has "Others", but no v2 row carries it, so
including it would add a permanently zero column; an unseen tag raises instead.

Input parquet is read from cache/wildbench/. Python's SSL stack cannot verify
huggingface.co in this environment (OpenSSL rejects a CA in the Windows store),
so fetch with PowerShell if the cache is cold - see _ensure_parquet().

This emits the 15 schema fields only. The MIRT `difficulty`/`discrimination`/
`irt_params` are appended afterwards by assign_irt_params.py, so re-running this
script strips them - always re-run the assign step after a rebuild:

Run:
    llm-from-scratch/Scripts/python.exe scripts/build_wildbench.py
    llm-from-scratch/Scripts/python.exe scripts/assign_irt_params.py \
        --input data/WildBench/rubrics.jsonl --skills <the 11 slugs above> \
        --log-dir data/WildBench/irt_logs --no-json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "cache" / "wildbench" / "v2_test-00000-of-00001.parquet"
OUT_DIR = ROOT / "data" / "WildBench"

SOURCE_URL = "https://huggingface.co/datasets/allenai/WildBench"
SPLIT = "calibration"
VERSION = "1.0"
REFERENCE_MODEL = "gpt-4"

# Fixed Q-matrix columns. Order is part of the artifact: downstream MIRT code
# indexes skills positionally, so this list must not be reordered in place.
TAGS = [
    "Advice seeking",
    "Brainstorming",
    "Coding & Debugging",
    "Creative Writing",
    "Data Analysis",
    "Editing",
    "Information seeking",
    "Math",
    "Planning",
    "Reasoning",
    "Role playing",
]

# q_mapping keys and primary_skill use code-safe slugs, matching the lowercase
# identifiers already used by the TutorBench (content/diagnosis/scaffolding) and
# APUSH (describe/explain) q-maps. use_case/subject keep the raw display tag.
SLUGS = {
    "Advice seeking": "advice_seeking",
    "Brainstorming": "brainstorming",
    "Coding & Debugging": "coding_debugging",
    "Creative Writing": "creative_writing",
    "Data Analysis": "data_analysis",
    "Editing": "editing",
    "Information seeking": "information_seeking",
    "Math": "math",
    "Planning": "planning",
    "Reasoning": "reasoning",
    "Role playing": "role_playing",
}

SCENARIO_KEYS = [
    "scenario_id", "source_id", "use_case", "subject", "grade_band", "modality",
    "prompt", "conversation_context", "reference_solution", "criterion_ids",
    "source", "split", "version",
]
RUBRIC_KEYS = [
    "criterion_id", "scenario_id", "criterion", "expected_evidence",
    "scoring_type", "score_anchors", "primary_skill", "q_mapping",
    "q_rationale", "criticality", "objectivity", "explicitness", "source",
    "status", "version",
]


def _ensure_parquet() -> None:
    if PARQUET.exists():
        return
    raise SystemExit(
        f"missing {PARQUET}\n\n"
        "Fetch it first (PowerShell - Python's SSL cannot verify huggingface.co here):\n"
        '  Invoke-WebRequest -Uri "https://huggingface.co/datasets/allenai/'
        'WildBench/resolve/main/v2/test-00000-of-00001.parquet" `\n'
        f'    -OutFile "{PARQUET}" -UseBasicParsing'
    )


def scenario_id(index: int) -> str:
    """Zero-based, 4-digit so the 1024 ids sort lexicographically."""
    return f"WB_{index:04d}"


def q_mapping(tag: str) -> dict[str, int]:
    return {SLUGS[t]: int(t == tag) for t in TAGS}


def build() -> tuple[list[dict], list[dict]]:
    rows = pq.read_table(PARQUET).to_pylist()

    unknown = sorted({r["primary_tag"] for r in rows} - set(TAGS))
    if unknown:
        raise SystemExit(f"primary_tag values missing from TAGS: {unknown}")

    scenarios: list[dict] = []
    rubrics: list[dict] = []

    for index, row in enumerate(rows):
        sid = scenario_id(index)
        tag = row["primary_tag"]
        turns = row["conversation_input"]
        reference = (row["references"] or {}).get(REFERENCE_MODEL) or ""

        # Every v2 row ends on a user turn; that final turn is the actual request
        # and anything before it is prior dialogue context.
        if turns[-1]["role"] != "user":
            raise SystemExit(f"{sid}: conversation does not end on a user turn")

        criterion_ids = [f"{sid}_c{i:02d}" for i in range(1, len(row["checklist"]) + 1)]

        scenarios.append({
            "scenario_id": sid,
            "source_id": row["id"],
            "use_case": tag,
            "subject": tag,
            "grade_band": "N/A",
            "modality": "text",
            "prompt": turns[-1]["content"],
            "conversation_context": [
                {"role": t["role"], "content": t["content"]} for t in turns[:-1]
            ],
            "reference_solution": reference,
            "criterion_ids": criterion_ids,
            "source": SOURCE_URL,
            "split": SPLIT,
            "version": VERSION,
        })

        for cid, criterion in zip(criterion_ids, row["checklist"]):
            rubrics.append({
                "criterion_id": cid,
                "scenario_id": sid,
                "criterion": criterion,
                "expected_evidence": [reference] if reference else [],
                "scoring_type": "binary",
                "score_anchors": None,
                "primary_skill": SLUGS[tag],
                "q_mapping": q_mapping(tag),
                "q_rationale": f"The primary tag marked in the dataset was {tag}",
                "criticality": "critical",
                "objectivity": "objective",
                "explicitness": "explicit",
                "source": SOURCE_URL,
                "status": "approved",
                "version": VERSION,
            })

    return scenarios, rubrics


def validate(scenarios: list[dict], rubrics: list[dict]) -> list[str]:
    errs: list[str] = []

    for name, rows, keys in (
        ("scenario", scenarios, SCENARIO_KEYS),
        ("rubric", rubrics, RUBRIC_KEYS),
    ):
        for r in rows:
            if list(r.keys()) != keys:
                errs.append(f"{name} {list(r.values())[0]}: key set/order mismatch")
                break

    sids = [s["scenario_id"] for s in scenarios]
    cids = [r["criterion_id"] for r in rubrics]
    if len(set(sids)) != len(sids):
        errs.append("duplicate scenario_id")
    if len(set(cids)) != len(cids):
        errs.append("duplicate criterion_id")

    # every declared criterion exists, and every rubric points at a real scenario
    declared = {c for s in scenarios for c in s["criterion_ids"]}
    actual = set(cids)
    if declared != actual:
        errs.append(
            f"criterion_ids mismatch: {len(declared - actual)} declared-but-missing, "
            f"{len(actual - declared)} present-but-undeclared"
        )
    orphans = {r["scenario_id"] for r in rubrics} - set(sids)
    if orphans:
        errs.append(f"{len(orphans)} rubrics reference unknown scenarios")

    for s in scenarios:
        if not s["prompt"].strip():
            errs.append(f"{s['scenario_id']}: empty prompt")
        if not s["criterion_ids"]:
            errs.append(f"{s['scenario_id']}: no criteria")
    for r in rubrics:
        if not r["criterion"].strip():
            errs.append(f"{r['criterion_id']}: empty criterion")
        if sum(r["q_mapping"].values()) != 1:
            errs.append(f"{r['criterion_id']}: q_mapping must mark exactly one skill")
        if r["primary_skill"] not in r["q_mapping"]:
            errs.append(f"{r['criterion_id']}: primary_skill not a q_mapping key")
        if r["q_mapping"].get(r["primary_skill"]) != 1:
            errs.append(f"{r['criterion_id']}: primary_skill is not the marked skill")

    return errs


def write(name: str, rows: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    size = path.stat().st_size / 1e6
    print(f"wrote {path.relative_to(ROOT)}  ({len(rows)} rows, {size:.1f} MB)")


def main() -> None:
    _ensure_parquet()
    scenarios, rubrics = build()

    errs = validate(scenarios, rubrics)
    if errs:
        print(f"VALIDATION FAILED ({len(errs)} issues):", file=sys.stderr)
        for e in errs[:20]:
            print(f"  - {e}", file=sys.stderr)
        raise SystemExit(1)
    print(f"validation passed: {len(scenarios)} scenarios, {len(rubrics)} criteria")

    write("scenarios.jsonl", scenarios)
    write("rubrics.jsonl", rubrics)

    from collections import Counter
    dist = Counter(s["use_case"] for s in scenarios)
    crit = Counter(r["primary_skill"] for r in rubrics)
    print("\nper primary_tag (scenarios / criteria):")
    for tag in TAGS:
        print(f"  {tag:<22} {dist.get(tag, 0):>5}  {crit.get(SLUGS[tag], 0):>6}")
    multi = sum(1 for s in scenarios if s["conversation_context"])
    print(f"\nmulti-turn scenarios (non-empty conversation_context): {multi}")


if __name__ == "__main__":
    main()
