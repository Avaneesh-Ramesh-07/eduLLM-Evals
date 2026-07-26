"""Aggregate the three blind Q-matrix human reviews and prepare adjudication.

Inputs are the coordinator manifest plus qmatrix_review_A/B/C.csv files produced
by ``prepare_qmatrix_human_review.py``. Outputs are written to
``qmatrix_human_review/analysis`` by default:

* ``report.md`` -- human agreement and AI-vs-human audit summary;
* ``summary.json`` -- machine-readable metrics;
* ``ratings_long.csv`` -- normalized raw human ratings;
* ``label_comparison.csv`` -- one row per criterion-skill label;
* ``adjudication_queue.csv`` -- every non-unanimous human decision, every
  resolved human/AI mismatch, and every primary-skill disagreement.

The sampled set deliberately over-represents generator/verifier disagreements.
Consequently, overall AI-vs-human agreement is an audit statistic, not an
unbiased estimate of accuracy over all 6,462 criteria. The report separates
``changed`` and ``stable`` sampling strata for that reason.

Usage:
    python scripts/analyze_qmatrix_human_review.py
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("content", "diagnosis", "scaffolding")
REVIEWERS = ("A", "B", "C")
PRIMARY_VALUES = {*SKILLS, "none"}


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
    return rows


def find_review_file(review_dir: Path, reviewer: str) -> Path:
    wanted = f"qmatrix_review_{reviewer}.csv".lower()
    matches = [path for path in review_dir.glob("qmatrix_review_*.csv") if path.name.lower() == wanted]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one {wanted} in {review_dir}; found {[p.name for p in matches]}"
        )
    return matches[0]


def read_review_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def validate_reviews(manifest: dict, review_dir: Path) -> dict[str, dict[str, dict[str, str]]]:
    criteria = manifest.get("criteria") or []
    if len(criteria) != 25:
        raise ValueError(f"manifest must contain 25 criteria; found {len(criteria)}")
    expected: dict[str, set[str]] = {
        reviewer: {
            row["criterion_id"] for row in criteria if reviewer in row.get("reviewers", [])
        }
        for reviewer in REVIEWERS
    }
    reviews: dict[str, dict[str, dict[str, str]]] = {}
    for reviewer in REVIEWERS:
        path = find_review_file(review_dir, reviewer)
        rows = read_review_csv(path)
        if len(rows) != 20:
            raise ValueError(f"{path}: expected 20 rows; found {len(rows)}")
        by_id: dict[str, dict[str, str]] = {}
        for line, row in enumerate(rows, 2):
            cid = (row.get("criterion_id") or "").strip()
            if not cid:
                raise ValueError(f"{path}:{line}: missing criterion_id")
            if cid in by_id:
                raise ValueError(f"{path}:{line}: duplicate criterion_id {cid}")
            recorded_reviewer = (row.get("reviewer") or "").strip().upper()
            if recorded_reviewer != reviewer:
                raise ValueError(
                    f"{path}:{line}: reviewer is {recorded_reviewer!r}, expected {reviewer!r}"
                )
            for skill in SKILLS:
                if row.get(skill) not in {"0", "1"}:
                    raise ValueError(f"{path}:{line}: {skill} must be 0 or 1")
            primary = (row.get("primary_skill") or "").strip().lower()
            if primary not in PRIMARY_VALUES:
                raise ValueError(f"{path}:{line}: invalid primary_skill {primary!r}")
            confidence = (row.get("confidence") or "").strip().lower()
            if confidence not in {"low", "medium", "high"}:
                raise ValueError(f"{path}:{line}: invalid confidence {confidence!r}")
            normalized = dict(row)
            normalized["reviewer"] = reviewer
            normalized["primary_skill"] = primary
            normalized["confidence"] = confidence
            normalized["notes"] = row.get("notes") or ""
            by_id[cid] = normalized
        actual = set(by_id)
        if actual != expected[reviewer]:
            missing = sorted(expected[reviewer] - actual)
            unexpected = sorted(actual - expected[reviewer])
            raise ValueError(f"{path}: assignment mismatch; missing={missing}, unexpected={unexpected}")
        reviews[reviewer] = by_id
    return reviews


def write_csv(path: Path, rows: Iterable[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def provisional_consensus(values: list[int]) -> int | None:
    """Return unanimous/two-of-three consensus; a one-one split is unresolved."""
    if len(values) not in {2, 3}:
        raise ValueError(f"expected two or three ratings; found {len(values)}")
    if len(values) == 2 and values[0] != values[1]:
        return None
    return int(sum(values) >= 2) if len(values) == 3 else values[0]


def categorical_consensus(values: list[str]) -> str | None:
    counts = Counter(values)
    value, count = counts.most_common(1)[0]
    return value if count > len(values) / 2 else None


def safe_div(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def rounded(value: float | None, digits: int = 4) -> float | None:
    return None if value is None or math.isnan(value) else round(value, digits)


def cohen_kappa(left: list[int], right: list[int]) -> dict:
    if len(left) != len(right) or not left:
        return {"n": len(left), "agreement": None, "kappa": None}
    n = len(left)
    observed = sum(a == b for a, b in zip(left, right)) / n
    left_one = sum(left) / n
    right_one = sum(right) / n
    expected = left_one * right_one + (1 - left_one) * (1 - right_one)
    kappa = safe_div(observed - expected, 1 - expected)
    return {"n": n, "agreement": rounded(observed), "kappa": rounded(kappa)}


def fleiss_kappa(rows: list[list[int]]) -> dict:
    if not rows:
        return {"n_items": 0, "agreement": None, "kappa": None}
    if any(len(row) != 3 for row in rows):
        raise ValueError("Fleiss kappa input must contain exactly three ratings per item")
    item_agreements = []
    ones = 0
    for row in rows:
        n1 = sum(row)
        n0 = 3 - n1
        item_agreements.append((n0 * (n0 - 1) + n1 * (n1 - 1)) / (3 * 2))
        ones += n1
    observed = sum(item_agreements) / len(item_agreements)
    p1 = ones / (len(rows) * 3)
    expected = p1**2 + (1 - p1) ** 2
    kappa = safe_div(observed - expected, 1 - expected)
    return {"n_items": len(rows), "agreement": rounded(observed), "kappa": rounded(kappa)}


def binary_metrics(rows: list[dict]) -> dict:
    resolved = [row for row in rows if row["human_consensus"] is not None]
    tp = sum(row["ai_label"] == 1 and row["human_consensus"] == 1 for row in resolved)
    fp = sum(row["ai_label"] == 1 and row["human_consensus"] == 0 for row in resolved)
    fn = sum(row["ai_label"] == 0 and row["human_consensus"] == 1 for row in resolved)
    tn = sum(row["ai_label"] == 0 and row["human_consensus"] == 0 for row in resolved)
    precision = safe_div(tp, tp + fp)
    recall = safe_div(tp, tp + fn)
    f1 = safe_div(2 * precision * recall, precision + recall) if precision is not None and recall is not None else None
    return {
        "n": len(rows),
        "resolved": len(resolved),
        "unresolved": len(rows) - len(resolved),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "accuracy": rounded(safe_div(tp + tn, len(resolved))),
        "precision": rounded(precision),
        "recall": rounded(recall),
        "f1": rounded(f1),
    }


def fmt(value: object) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def analyze(
    manifest: dict,
    reviews: dict[str, dict[str, dict[str, str]]],
    rubrics: dict[str, dict],
    scenarios: dict[str, dict],
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    manifest_rows = {row["criterion_id"]: row for row in manifest["criteria"]}
    ratings_long: list[dict] = []
    comparisons: list[dict] = []
    adjudication: list[dict] = []

    for cid, meta in manifest_rows.items():
        rubric = rubrics[cid]
        scenario = scenarios[rubric["scenario_id"]]
        assigned = list(meta["reviewers"])
        for reviewer in assigned:
            rating = reviews[reviewer][cid]
            ratings_long.append(
                {
                    "reviewer": reviewer,
                    "criterion_id": cid,
                    "scenario_id": rubric["scenario_id"],
                    "review_group": meta["review_group"],
                    "sampling_target": meta["sampling_target"],
                    "content": rating["content"],
                    "diagnosis": rating["diagnosis"],
                    "scaffolding": rating["scaffolding"],
                    "primary_skill": rating["primary_skill"],
                    "confidence": rating["confidence"],
                    "notes": rating["notes"],
                }
            )

        for skill in SKILLS:
            values_by_reviewer = {r: int(reviews[r][cid][skill]) for r in assigned}
            values = list(values_by_reviewer.values())
            consensus = provisional_consensus(values)
            unanimous = len(set(values)) == 1
            ai_label = int((rubric.get("q_mapping") or {}).get(skill, 0))
            stratum = "changed" if meta["sampling_target"].startswith("changed_") else "stable"
            comparison = {
                "criterion_id": cid,
                "scenario_id": rubric["scenario_id"],
                "skill": skill,
                "review_group": meta["review_group"],
                "sampling_target": meta["sampling_target"],
                "sampling_stratum": stratum,
                "n_raters": len(values),
                "rating_A": values_by_reviewer.get("A"),
                "rating_B": values_by_reviewer.get("B"),
                "rating_C": values_by_reviewer.get("C"),
                "human_unanimous": unanimous,
                "human_consensus": consensus,
                "ai_label": ai_label,
                "human_ai_match": None if consensus is None else consensus == ai_label,
            }
            comparisons.append(comparison)
            issues = []
            if not unanimous:
                issues.append("human_disagreement")
            if consensus is not None and consensus != ai_label:
                issues.append("human_vs_ai")
            if issues:
                adjudication.append(
                    {
                        **comparison,
                        "issue_type": ";".join(issues),
                        "ai_value": ai_label,
                        "provisional_human_value": consensus,
                        "confidence_A": reviews["A"].get(cid, {}).get("confidence", ""),
                        "confidence_B": reviews["B"].get(cid, {}).get("confidence", ""),
                        "confidence_C": reviews["C"].get(cid, {}).get("confidence", ""),
                        "notes_A": reviews["A"].get(cid, {}).get("notes", ""),
                        "notes_B": reviews["B"].get(cid, {}).get("notes", ""),
                        "notes_C": reviews["C"].get(cid, {}).get("notes", ""),
                        "criterion": rubric.get("criterion", ""),
                        "scenario_prompt": scenario.get("prompt", ""),
                        "adjudicated_value": "",
                        "adjudication_rationale": "",
                    }
                )

        primary_values = {r: reviews[r][cid]["primary_skill"] for r in assigned}
        primary_consensus = categorical_consensus(list(primary_values.values()))
        primary_unanimous = len(set(primary_values.values())) == 1
        ai_primary = rubric.get("primary_skill") or "none"
        if not primary_unanimous or (primary_consensus is not None and primary_consensus != ai_primary):
            adjudication.append(
                {
                    "criterion_id": cid,
                    "scenario_id": rubric["scenario_id"],
                    "skill": "primary_skill",
                    "review_group": meta["review_group"],
                    "sampling_target": meta["sampling_target"],
                    "sampling_stratum": "changed" if meta["sampling_target"].startswith("changed_") else "stable",
                    "n_raters": len(primary_values),
                    "rating_A": primary_values.get("A"),
                    "rating_B": primary_values.get("B"),
                    "rating_C": primary_values.get("C"),
                    "human_unanimous": primary_unanimous,
                    "human_consensus": primary_consensus,
                    "ai_label": ai_primary,
                    "human_ai_match": None if primary_consensus is None else primary_consensus == ai_primary,
                    "issue_type": ";".join(
                        part for part, include in (
                            ("human_disagreement", not primary_unanimous),
                            ("human_vs_ai", primary_consensus is not None and primary_consensus != ai_primary),
                        ) if include
                    ),
                    "ai_value": ai_primary,
                    "provisional_human_value": primary_consensus,
                    "confidence_A": reviews["A"].get(cid, {}).get("confidence", ""),
                    "confidence_B": reviews["B"].get(cid, {}).get("confidence", ""),
                    "confidence_C": reviews["C"].get(cid, {}).get("confidence", ""),
                    "notes_A": reviews["A"].get(cid, {}).get("notes", ""),
                    "notes_B": reviews["B"].get(cid, {}).get("notes", ""),
                    "notes_C": reviews["C"].get(cid, {}).get("notes", ""),
                    "criterion": rubric.get("criterion", ""),
                    "scenario_prompt": scenario.get("prompt", ""),
                    "adjudicated_value": "",
                    "adjudication_rationale": "",
                }
            )

    human_agreement = {}
    pairwise = {}
    fleiss = {}
    for skill in SKILLS:
        skill_rows = [row for row in comparisons if row["skill"] == skill]
        human_agreement[skill] = {
            "n_labels": len(skill_rows),
            "unanimous": sum(row["human_unanimous"] for row in skill_rows),
            "unanimous_rate": rounded(sum(row["human_unanimous"] for row in skill_rows) / len(skill_rows)),
            "unresolved_two_rater_splits": sum(row["human_consensus"] is None for row in skill_rows),
        }
        pairwise[skill] = {}
        for left, right in (("A", "B"), ("A", "C"), ("B", "C")):
            overlap = [cid for cid, meta in manifest_rows.items() if left in meta["reviewers"] and right in meta["reviewers"]]
            pairwise[skill][f"{left}-{right}"] = cohen_kappa(
                [int(reviews[left][cid][skill]) for cid in overlap],
                [int(reviews[right][cid][skill]) for cid in overlap],
            )
        core = [cid for cid, meta in manifest_rows.items() if meta["review_group"] == "CORE"]
        fleiss[skill] = fleiss_kappa(
            [[int(reviews[r][cid][skill]) for r in REVIEWERS] for cid in core]
        )

    ai_comparison = {skill: binary_metrics([r for r in comparisons if r["skill"] == skill]) for skill in SKILLS}
    ai_comparison["overall"] = binary_metrics(comparisons)
    by_stratum = {
        stratum: binary_metrics([r for r in comparisons if r["sampling_stratum"] == stratum])
        for stratum in ("changed", "stable")
    }
    summary = {
        "sampling_version": manifest.get("sampling_version"),
        "n_criteria": len(manifest_rows),
        "n_criterion_reviews": len(ratings_long),
        "n_binary_skill_labels": len(comparisons),
        "input_validation": "passed",
        "human_agreement": human_agreement,
        "pairwise_cohen_kappa": pairwise,
        "core_fleiss_kappa": fleiss,
        "ai_vs_human": ai_comparison,
        "ai_vs_human_by_sampling_stratum": by_stratum,
        "adjudication_rows": len(adjudication),
        "adjudication_binary_labels": sum(row["skill"] in SKILLS for row in adjudication),
        "adjudication_primary_skill_rows": sum(row["skill"] == "primary_skill" for row in adjudication),
    }
    return summary, ratings_long, comparisons, adjudication


def render_report(summary: dict) -> str:
    lines = [
        "# Q-matrix human-review audit",
        "",
        "All three review files passed assignment and completeness validation.",
        "",
        "## Scope",
        "",
        f"- Criteria: {summary['n_criteria']}",
        f"- Criterion reviews: {summary['n_criterion_reviews']}",
        f"- Binary criterion-skill labels audited: {summary['n_binary_skill_labels']}",
        f"- Adjudication rows: {summary['adjudication_rows']}",
        "",
        "> This was a dispute-enriched audit, not a simple random sample. Overall AI agreement must not be reported as population accuracy for the full Q-matrix.",
        "",
        "## Human agreement",
        "",
        "| Skill | Unanimous | Rate | Unresolved 1–1 splits | Core Fleiss κ |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for skill in SKILLS:
        agreement = summary["human_agreement"][skill]
        fleiss = summary["core_fleiss_kappa"][skill]
        lines.append(
            f"| {skill} | {agreement['unanimous']}/{agreement['n_labels']} | "
            f"{fmt(agreement['unanimous_rate'])} | {agreement['unresolved_two_rater_splits']} | "
            f"{fmt(fleiss['kappa'])} |"
        )
    lines.extend([
        "",
        "### Pairwise Cohen κ",
        "",
        "| Skill | A–B | A–C | B–C |",
        "| --- | ---: | ---: | ---: |",
    ])
    for skill in SKILLS:
        stats = summary["pairwise_cohen_kappa"][skill]
        lines.append(
            f"| {skill} | {fmt(stats['A-B']['kappa'])} | {fmt(stats['A-C']['kappa'])} | {fmt(stats['B-C']['kappa'])} |"
        )
    lines.extend([
        "",
        "## Final AI Q-matrix versus provisional human consensus",
        "",
        "| Slice | Resolved | Accuracy | Precision | Recall | F1 | FP | FN |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for skill in (*SKILLS, "overall"):
        metric = summary["ai_vs_human"][skill]
        lines.append(
            f"| {skill} | {metric['resolved']}/{metric['n']} | {fmt(metric['accuracy'])} | "
            f"{fmt(metric['precision'])} | {fmt(metric['recall'])} | {fmt(metric['f1'])} | "
            f"{metric['fp']} | {metric['fn']} |"
        )
    lines.extend([
        "",
        "### By sampling stratum",
        "",
        "| Stratum | Resolved | Accuracy | FP | FN |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for stratum in ("changed", "stable"):
        metric = summary["ai_vs_human_by_sampling_stratum"][stratum]
        lines.append(
            f"| {stratum} | {metric['resolved']}/{metric['n']} | {fmt(metric['accuracy'])} | {metric['fp']} | {metric['fn']} |"
        )
    lines.extend([
        "",
        "## Next step",
        "",
        "Open `adjudication_queue.csv`. Review every row independently of the provisional majority, enter `adjudicated_value` and `adjudication_rationale`, and only then decide whether sampled mappings should be patched. A 2–1 vote is intentionally queued rather than silently accepted.",
        "",
    ])
    return "\n".join(lines)


RATINGS_FIELDS = [
    "reviewer", "criterion_id", "scenario_id", "review_group", "sampling_target",
    "content", "diagnosis", "scaffolding", "primary_skill", "confidence", "notes",
]
COMPARISON_FIELDS = [
    "criterion_id", "scenario_id", "skill", "review_group", "sampling_target",
    "sampling_stratum", "n_raters", "rating_A", "rating_B", "rating_C",
    "human_unanimous", "human_consensus", "ai_label", "human_ai_match",
]
ADJUDICATION_FIELDS = [
    *COMPARISON_FIELDS, "issue_type", "ai_value", "provisional_human_value",
    "confidence_A", "confidence_B", "confidence_C", "notes_A", "notes_B", "notes_C",
    "criterion", "scenario_prompt", "adjudicated_value", "adjudication_rationale",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", type=Path, default=ROOT / "qmatrix_human_review")
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--rubrics", type=Path, default=ROOT / "data/rubrics_qmatrix_final.jsonl")
    parser.add_argument("--scenarios", type=Path, default=ROOT / "data/scenarios.jsonl")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    manifest_path = args.manifest or args.review_dir / "coordinator_manifest.json"
    out_dir = args.out or args.review_dir / "analysis"
    manifest = read_json(manifest_path)
    reviews = validate_reviews(manifest, args.review_dir)
    rubrics = {row["criterion_id"]: row for row in read_jsonl(args.rubrics)}
    scenarios = {row["scenario_id"]: row for row in read_jsonl(args.scenarios)}
    missing_rubrics = sorted({row["criterion_id"] for row in manifest["criteria"]} - set(rubrics))
    if missing_rubrics:
        raise ValueError(f"sample criteria missing from rubric file: {missing_rubrics}")

    summary, ratings, comparisons, adjudication = analyze(manifest, reviews, rubrics, scenarios)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "ratings_long.csv", ratings, RATINGS_FIELDS)
    write_csv(out_dir / "label_comparison.csv", comparisons, COMPARISON_FIELDS)
    write_csv(out_dir / "adjudication_queue.csv", adjudication, ADJUDICATION_FIELDS)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render_report(summary), encoding="utf-8")
    print(f"validated 3 review files; wrote analysis to {out_dir}")
    print(f"adjudication rows: {summary['adjudication_rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
