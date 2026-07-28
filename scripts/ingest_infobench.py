"""Convert the InFoBench dataset into Scenario + Rubric Schema JSONL.

Source: https://huggingface.co/datasets/kqsong/InFoBench  (one `train` split,
500 instructions, 2250 decomposed questions total).

Mirrors the WildBench pipeline shape (scripts/build_wildbench.py): this emits the
16 base schema fields, including a **q-matrix** built from the dataset's own
`question_label` annotation and **placeholder** metadata. The synthetic MIRT
`difficulty`/`discrimination`/`irt_params` are appended afterwards by
scripts/assign_irt_params.py, so re-running this script strips them -- always
re-run the assign step after a rebuild:

    python scripts/ingest_infobench.py
    python scripts/assign_irt_params.py \
        --input data/InFoBench/rubrics.jsonl \
        --skills content,format,number,style,linguistic \
        --log-dir data/InFoBench/irt_logs --no-backup

Mapping (InFoBench field -> schema field):
    id                     -> source_id            (join key back to HuggingFace)
    instruction (+ input)  -> prompt               (input appended only when present)
    category               -> subject              (raw domain/source string)
    subset                 -> subset               (native Easy_set/Hard_set band, kept)
    decomposed_questions[i]-> criterion            (one binary rubric row each)
    question_label[i]      -> question_label + q_mapping + primary_skill

The q-matrix is built from `question_label`, which is **multi-label** in InFoBench:
of the 2250 criteria, 576 carry 2 labels and 70 carry 3. Unlike WildBench (exactly
one skill per criterion), an InFoBench criterion may mark >1 of the five constraint
types, so `q_mapping` can sum to >1. The raw label list is preserved verbatim in the
`question_label` field for provenance. `primary_skill` is the first label's slug.

Placeholders (NOT native to InFoBench, matching WildBench's fixed values): every
criterion is stamped `criticality="critical"`, `objectivity="objective"`,
`explicitness="explicit"`. These feed only the synthetic IRT heuristic downstream;
because they are uniform, they add no per-item signal there (same as WildBench).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from datasets import load_dataset

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "InFoBench"

HF_DATASET = "kqsong/InFoBench"
SOURCE_URL = "https://huggingface.co/datasets/kqsong/InFoBench"
SPLIT = "calibration"   # pipeline-role label (matches TutorBench/WildBench), not the HF split
VERSION = "1.0"

# Placeholder metadata (InFoBench has no native equivalent; matches WildBench).
CRITICALITY = "critical"
OBJECTIVITY = "objective"
EXPLICITNESS = "explicit"

# InFoBench's five constraint types -> code-safe slugs (lowercase, matching the
# TutorBench content/diagnosis/scaffolding and WildBench slug conventions).
# The list order is the fixed q-matrix column order and must not be reordered
# in place -- downstream MIRT code indexes skills positionally.
LABEL_SLUG = {
    "Content": "content",
    "Format": "format",
    "Number": "number",
    "Style": "style",
    "Linguistic": "linguistic",
}
SKILLS = ["content", "format", "number", "style", "linguistic"]

SCENARIO_KEYS = [
    "scenario_id", "source_id", "use_case", "subject", "subset", "grade_band",
    "modality", "prompt", "conversation_context", "reference_solution",
    "criterion_ids", "source", "split", "version",
]
RUBRIC_KEYS = [
    "criterion_id", "scenario_id", "criterion", "expected_evidence",
    "scoring_type", "score_anchors", "question_label", "primary_skill",
    "q_mapping", "q_rationale", "criticality", "objectivity", "explicitness",
    "source", "status", "version",
]


def scenario_id(index: int) -> str:
    """Zero-based, 4-digit so the 500 ids sort lexicographically."""
    return f"ifb_{index:04d}"


def build_prompt(instruction: str, input_text: str) -> str:
    """Instruction, with the optional context block appended only when non-empty."""
    instruction = (instruction or "").strip()
    input_text = (input_text or "").strip()
    return f"{instruction}\n\n{input_text}" if input_text else instruction


def q_mapping(labels: list[str]) -> dict[str, int]:
    """Multi-hot over the five constraint slugs: 1 for every label this criterion carries."""
    marked = {LABEL_SLUG[lab] for lab in labels}
    return {skill: int(skill in marked) for skill in SKILLS}


def build() -> tuple[list[dict], list[dict], int]:
    rows = load_dataset(HF_DATASET)["train"]

    scenarios: list[dict] = []
    rubrics: list[dict] = []
    n_with_input = 0

    for index, row in enumerate(rows):
        sid = scenario_id(index)
        questions = row["decomposed_questions"]
        labels = row["question_label"]
        if (row["input"] or "").strip():
            n_with_input += 1
        if len(labels) != len(questions):
            raise SystemExit(
                f"{sid}: question_label ({len(labels)}) != decomposed_questions ({len(questions)})"
            )

        criterion_ids = [f"{sid}_c{i:02d}" for i in range(1, len(questions) + 1)]

        scenarios.append({
            "scenario_id": sid,
            "source_id": row["id"],
            "use_case": "instruction_following",
            "subject": row["category"],
            "subset": row["subset"],
            "grade_band": None,
            "modality": "text",
            "prompt": build_prompt(row["instruction"], row["input"]),
            "conversation_context": [],
            "reference_solution": None,
            "criterion_ids": criterion_ids,
            "source": SOURCE_URL,
            "split": SPLIT,
            "version": VERSION,
        })

        for cid, criterion, label in zip(criterion_ids, questions, labels):
            primary = LABEL_SLUG[label[0]]
            rubrics.append({
                "criterion_id": cid,
                "scenario_id": sid,
                "criterion": criterion,
                "expected_evidence": [],
                "scoring_type": "binary",
                "score_anchors": None,
                # Raw native annotation, preserved verbatim (multi-label kept intact).
                "question_label": list(label),
                "primary_skill": primary,
                "q_mapping": q_mapping(label),
                "q_rationale": f"Derived from the InFoBench question_label(s) {label}",
                # Placeholders (uniform, like WildBench); feed only the synthetic IRT step.
                "criticality": CRITICALITY,
                "objectivity": OBJECTIVITY,
                "explicitness": EXPLICITNESS,
                "source": SOURCE_URL,
                "status": "approved",
                "version": VERSION,
            })

    return scenarios, rubrics, n_with_input


def validate(scenarios: list[dict], rubrics: list[dict]) -> list[str]:
    errs: list[str] = []

    for name, records, keys in (
        ("scenario", scenarios, SCENARIO_KEYS),
        ("rubric", rubrics, RUBRIC_KEYS),
    ):
        for r in records:
            if list(r.keys()) != keys:
                errs.append(f"{name} {list(r.values())[0]}: key set/order mismatch")
                break

    sids = [s["scenario_id"] for s in scenarios]
    cids = [r["criterion_id"] for r in rubrics]
    if len(set(sids)) != len(sids):
        errs.append("duplicate scenario_id")
    if len(set(cids)) != len(cids):
        errs.append("duplicate criterion_id")

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
        if not (s["prompt"] or "").strip():
            errs.append(f"{s['scenario_id']}: empty prompt")
        if not s["criterion_ids"]:
            errs.append(f"{s['scenario_id']}: no criteria")
    for r in rubrics:
        if not (r["criterion"] or "").strip():
            errs.append(f"{r['criterion_id']}: empty criterion")
        labels = r["question_label"]
        if not labels:
            errs.append(f"{r['criterion_id']}: empty question_label")
        unknown = set(labels) - set(LABEL_SLUG)
        if unknown:
            errs.append(f"{r['criterion_id']}: unknown question_label(s) {sorted(unknown)}")
        # q-matrix consistency: keys == the fixed axis, values 0/1, one 1 per distinct label.
        q = r["q_mapping"]
        if list(q.keys()) != SKILLS:
            errs.append(f"{r['criterion_id']}: q_mapping keys/order != {SKILLS}")
        if set(q.values()) - {0, 1}:
            errs.append(f"{r['criterion_id']}: q_mapping values must be 0/1")
        if sum(q.values()) != len({LABEL_SLUG[lab] for lab in labels}):
            errs.append(f"{r['criterion_id']}: q_mapping sum != number of distinct labels")
        if r["primary_skill"] not in SKILLS:
            errs.append(f"{r['criterion_id']}: primary_skill {r['primary_skill']!r} not a skill")
        elif q.get(r["primary_skill"]) != 1:
            errs.append(f"{r['criterion_id']}: primary_skill not marked in q_mapping")

    return errs


def write(name: str, records: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUT_DIR / f"{name}.jsonl"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    json_path = OUT_DIR / f"{name}.json"
    with json_path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    size = jsonl_path.stat().st_size / 1e6
    print(f"wrote {jsonl_path.relative_to(ROOT)} + .json  ({len(records)} rows, {size:.1f} MB)")


def main() -> None:
    scenarios, rubrics, n_with_input = build()

    errs = validate(scenarios, rubrics)
    if errs:
        print(f"VALIDATION FAILED ({len(errs)} issues):", file=sys.stderr)
        for e in errs[:20]:
            print(f"  - {e}", file=sys.stderr)
        raise SystemExit(1)
    print(f"validation passed: {len(scenarios)} scenarios, {len(rubrics)} criteria")

    write("scenarios", scenarios)
    write("rubrics", rubrics)

    # q-matrix column loads (how many criteria mark each skill) + primary distribution.
    loads = Counter(s for r in rubrics for s in SKILLS if r["q_mapping"][s])
    primaries = Counter(r["primary_skill"] for r in rubrics)
    multi = sum(1 for r in rubrics if sum(r["q_mapping"].values()) > 1)
    print("\nq_mapping loads (criteria marking each skill) / primary_skill:")
    for skill in SKILLS:
        print(f"  {skill:<12} load={loads.get(skill, 0):>5}   primary={primaries.get(skill, 0):>5}")
    print(f"\nmulti-skill criteria (q sums >1): {multi}")
    print(f"scenarios with an input block:    {n_with_input}")
    print("difficulty/discrimination/irt_params: appended by assign_irt_params.py (dummy synthetic).")


if __name__ == "__main__":
    main()
