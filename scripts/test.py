import json
from pathlib import Path

from datasets import load_dataset

ds = load_dataset("ScaleAI/TutorBench")

# Keep only text-only entries (drop multimodal rows that include an image).
text_only = ds.filter(lambda row: not (row["IMAGE_URL"] or "").strip())

# ---------------------------------------------------------------------------
# Reformat each text-only entry into a Scenario record + its Rubric records,
# following the schemas in schemas.txt.
# ---------------------------------------------------------------------------

OUT_DIR = Path("data")

# TutorBench BATCH use-case number -> human-readable use_case label.
USE_CASE_LABELS = {
    "1": "adaptive_explanation",  # problem -> tutor solution -> student's confused follow-up
    "2": "feedback",              # actionable feedback on a student's work
    "3": "hint_generation",       # hints promoting active learning
}


def use_case_num(batch: str) -> str:
    """Extract the use-case number from a BATCH label like ``USE_CASE_2_TEXT``."""
    for part in (batch or "").split("_"):
        if part.isdigit():
            return part
    return ""


def clean(text):
    """Return a stripped string, or ``None`` for empty/missing values."""
    text = (text or "").strip()
    return text or None


def build_conversation(row) -> list:
    """
    Build the prior-turn conversation context.

    The evaluated turn (``FOLLOW_UP_PROMPT``) is stored separately as the scenario
    ``prompt``; everything before it goes here. For UC1 the tutor's initial worked
    explanation is a real prior turn; UC2/UC3 only have the original problem.
    """
    context = [{"role": "student", "content": row["PROMPT"]}]
    initial = clean(row["UC1_INITIAL_EXPLANATION"])
    if initial is not None:
        context.append({"role": "tutor", "content": initial})
    return context


def build(row, idx):
    """Reformat one TutorBench row into ``(scenario, [rubrics])``."""
    scenario_id = f"tb_{idx:04d}"
    uc = use_case_num(row["BATCH"])
    initial = clean(row["UC1_INITIAL_EXPLANATION"])

    rubrics = []
    for crit in json.loads(row["RUBRICS"]):
        attrs = crit.get("attributes", {})
        severity = (attrs.get("severity") or "").strip()
        if severity == "deleted":  # criterion was removed upstream; skip it
            continue

        skill = (attrs.get("tutoring_skill") or "").strip()
        rubrics.append(
            {
                "criterion_id": f"{scenario_id}_c{len(rubrics) + 1:02d}",
                "scenario_id": scenario_id,
                "criterion": crit.get("criteria"),
                "expected_evidence": [],
                "scoring_type": "binary",
                "score_anchors": None,
                "primary_skill": None if skill in ("", "Not applicable") else skill,
                "q_mapping": None,
                "q_rationale": None,
                "criticality": severity or None,
                "objectivity": clean(attrs.get("objectivity")),
                "explicitness": clean(attrs.get("explicitness")),
                "source": "TutorBench",
                "status": "approved",
                "version": "1.0",
            }
        )

    scenario = {
        "scenario_id": scenario_id,
        "use_case": USE_CASE_LABELS.get(uc, row["BATCH"]),
        "subject": (row["SUBJECT"] or "").strip().lower().replace(" ", "_"),
        "grade_band": None,
        "modality": "text",
        "prompt": row["FOLLOW_UP_PROMPT"],
        "conversation_context": build_conversation(row),
        "reference_solution": initial if uc == "1" else None,
        "criterion_ids": [r["criterion_id"] for r in rubrics],
        "source": "TutorBench",
        "split": "calibration",
        "version": "1.0",
    }
    return scenario, rubrics


def write_jsonl(path, records):
    """Write one JSON object per line."""
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_json(path, records):
    """Write a single pretty-printed (multi-line) JSON array."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    OUT_DIR.mkdir(exist_ok=True)

    all_scenarios = []
    all_rubrics = []
    n_deleted = 0
    no_image_multimodal = []

    for idx, row in enumerate(text_only["train"], start=1):
        scenario, rubrics = build(row, idx)

        # Count criteria dropped for being marked "deleted".
        n_deleted += sum(
            1
            for c in json.loads(row["RUBRICS"])
            if (c.get("attributes", {}).get("severity") or "").strip() == "deleted"
        )
        # Flag rows tagged multimodal that nonetheless carry no image.
        if "MULTIMODAL" in (row["BATCH"] or ""):
            no_image_multimodal.append(row["TASK_ID"])

        all_scenarios.append(scenario)
        all_rubrics.extend(rubrics)

    # One-object-per-line (.jsonl) and pretty-printed multi-line (.json) versions.
    write_jsonl(OUT_DIR / "scenarios.jsonl", all_scenarios)
    write_jsonl(OUT_DIR / "rubrics.jsonl", all_rubrics)
    write_json(OUT_DIR / "scenarios.json", all_scenarios)
    write_json(OUT_DIR / "rubrics.json", all_rubrics)

    print(f"Wrote {len(all_scenarios)} scenarios -> {OUT_DIR}/scenarios.{{jsonl,json}}")
    print(f"Wrote {len(all_rubrics)} rubric criteria -> {OUT_DIR}/rubrics.{{jsonl,json}}")
    print(f"Dropped {n_deleted} criteria marked 'deleted'.")
    if no_image_multimodal:
        print(
            f"WARNING: {len(no_image_multimodal)} rows tagged *_MULTIMODAL have no image "
            f"but passed the text-only filter (kept anyway): {no_image_multimodal}"
        )


if __name__ == "__main__":
    main()
