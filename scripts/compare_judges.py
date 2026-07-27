#!/usr/bin/env python3
"""Compare binary LLM-judge decisions with finalized human labels.

The input is a CSV with one row per exact tutor-response/criterion pair.  Extra
columns (the criterion text, candidate response, human notes, and so on) are
kept but ignored by the calculations.  The required shape is:

    case_id,candidate_model,scenario_id,criterion_id,human_label,judge_prometheus,judge_selene,judge_flow
    gpt55-q001-c01,gpt-5.5,q001,q001_c01,pass,pass,pass,fail

``case_id`` must uniquely identify the exact response/criterion pair.  Human
labels may be ``pass``/``fail`` (or 1/0).  Blank, ``ambiguous``, or
``unscorable`` human labels are reported but excluded from the gold set.
Judge columns are auto-detected by the ``judge_`` prefix; blank, error, timeout,
or unscorable judge values count as ``no_decision`` and therefore as incorrect
in end-to-end accuracy.

Example:

    python scripts/compare_judges.py judge_validation.csv \
        --json-out judge_validation_summary.json \
        --disagreements-out judge_validation_disagreements.csv

The headline metric is accuracy.  Coverage, balanced accuracy, and fail recall
are also shown because accuracy alone can hide an always-pass judge when human
failures are uncommon.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


PASS_LABELS = {"pass", "1", "1.0", "true", "yes", "correct", "met"}
FAIL_LABELS = {
    "fail",
    "0",
    "0.0",
    "false",
    "no",
    "incorrect",
    "not_met",
}
EXCLUDED_HUMAN_LABELS = {
    "",
    "ambiguous",
    "missing",
    "n/a",
    "na",
    "needs_review",
    "pending",
    "unclear",
    "unscorable",
}
NO_DECISION_LABELS = EXCLUDED_HUMAN_LABELS | {
    "abstain",
    "abstained",
    "api_error",
    "error",
    "no_decision",
    "parse_error",
    "timeout",
}

REQUIRED_COLUMNS = ("case_id", "human_label")


class InputValidationError(ValueError):
    """Raised when a label CSV cannot be compared safely."""


@dataclass
class Case:
    case_id: str
    human_label: str | None
    judge_labels: dict[str, str]
    raw: dict[str, str]


def _token(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_human_label(value: object, *, row_number: int) -> str | None:
    token = _token(value)
    if token in PASS_LABELS:
        return "pass"
    if token in FAIL_LABELS:
        return "fail"
    if token in EXCLUDED_HUMAN_LABELS:
        return None
    raise InputValidationError(
        f"row {row_number}: unsupported human_label {value!r}; use pass, fail, "
        "ambiguous, unscorable, or leave it blank"
    )


def normalize_judge_label(value: object, *, row_number: int, column: str) -> str:
    token = _token(value)
    if token in PASS_LABELS:
        return "pass"
    if token in FAIL_LABELS:
        return "fail"
    if token in NO_DECISION_LABELS:
        return "no_decision"
    raise InputValidationError(
        f"row {row_number}: unsupported value {value!r} in {column}; use pass, "
        "fail, no_decision, unscorable, error, or leave it blank"
    )


def load_cases(
    path: Path, judge_columns: Sequence[str] | None = None
) -> tuple[list[Case], list[str], list[str]]:
    """Load and validate a wide human/judge-label CSV.

    Returns ``(cases, judge_columns, original_fieldnames)``.
    """

    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise InputValidationError(f"could not open {path}: {exc}") from exc

    with handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames:
            raise InputValidationError(f"{path} has no CSV header")

        missing = [name for name in REQUIRED_COLUMNS if name not in fieldnames]
        if missing:
            raise InputValidationError(
                f"{path} is missing required column(s): {', '.join(missing)}"
            )

        if judge_columns is None:
            selected_judges = [name for name in fieldnames if name.startswith("judge_")]
        else:
            selected_judges = list(judge_columns)
            unknown = [name for name in selected_judges if name not in fieldnames]
            if unknown:
                raise InputValidationError(
                    f"judge column(s) not present in {path}: {', '.join(unknown)}"
                )

        if not selected_judges:
            raise InputValidationError(
                "no judge columns found; name them with the judge_ prefix or pass "
                "--judge-columns"
            )
        if len(set(selected_judges)) != len(selected_judges):
            raise InputValidationError("--judge-columns contains a duplicate column")

        cases: list[Case] = []
        seen_case_ids: set[str] = set()
        for row_number, raw_row in enumerate(reader, start=2):
            raw = {name: str(raw_row.get(name) or "") for name in fieldnames}
            case_id = raw["case_id"].strip()
            if not case_id:
                raise InputValidationError(f"row {row_number}: case_id is blank")
            if case_id in seen_case_ids:
                raise InputValidationError(
                    f"row {row_number}: duplicate case_id {case_id!r}; one case_id must "
                    "represent exactly one response/criterion pair"
                )
            seen_case_ids.add(case_id)

            human_label = normalize_human_label(
                raw["human_label"], row_number=row_number
            )
            normalized_judges = {
                column: normalize_judge_label(
                    raw[column], row_number=row_number, column=column
                )
                for column in selected_judges
            }
            cases.append(Case(case_id, human_label, normalized_judges, raw))

    if not cases:
        raise InputValidationError(f"{path} contains no data rows")
    return cases, selected_judges, fieldnames


def _safe_div(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def calculate_metrics(cases: Iterable[Case], judge_column: str) -> dict:
    """Calculate end-to-end metrics on rows with binary human gold labels."""

    gold_cases = [case for case in cases if case.human_label in {"pass", "fail"}]
    confusion = {
        "pass": {"pass": 0, "fail": 0, "no_decision": 0},
        "fail": {"pass": 0, "fail": 0, "no_decision": 0},
    }
    for case in gold_cases:
        confusion[case.human_label][case.judge_labels[judge_column]] += 1

    human_pass = sum(confusion["pass"].values())
    human_fail = sum(confusion["fail"].values())
    total = human_pass + human_fail
    correct = confusion["pass"]["pass"] + confusion["fail"]["fail"]
    decided = total - confusion["pass"]["no_decision"] - confusion["fail"]["no_decision"]
    pass_recall = _safe_div(confusion["pass"]["pass"], human_pass)
    fail_recall = _safe_div(confusion["fail"]["fail"], human_fail)
    balanced_accuracy = (
        (pass_recall + fail_recall) / 2
        if pass_recall is not None and fail_recall is not None
        else None
    )

    return {
        "n": total,
        "correct": correct,
        "accuracy": _safe_div(correct, total),
        "conditional_accuracy": _safe_div(correct, decided),
        "coverage": _safe_div(decided, total),
        "balanced_accuracy": balanced_accuracy,
        "pass_recall": pass_recall,
        "fail_recall": fail_recall,
        "false_pass_rate": _safe_div(confusion["fail"]["pass"], human_fail),
        "no_decision_n": total - decided,
        "human_pass_n": human_pass,
        "human_fail_n": human_fail,
        "confusion": confusion,
    }


def _percentile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("cannot take a percentile of an empty list")
    position = (len(sorted_values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def bootstrap_accuracy_interval(
    cases: Iterable[Case],
    judge_column: str,
    *,
    cluster_by: str,
    samples: int,
    seed: int,
) -> list[float] | None:
    """Return a scenario-clustered 95% bootstrap interval for accuracy."""

    if samples < 2:
        return None
    clusters: dict[str, list[Case]] = defaultdict(list)
    for case in cases:
        if case.human_label not in {"pass", "fail"}:
            continue
        cluster = case.raw.get(cluster_by, "").strip() or case.case_id
        clusters[cluster].append(case)
    cluster_ids = sorted(clusters)
    if len(cluster_ids) < 2:
        return None

    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(samples):
        sampled: list[Case] = []
        for _ in cluster_ids:
            sampled.extend(clusters[rng.choice(cluster_ids)])
        accuracy = calculate_metrics(sampled, judge_column)["accuracy"]
        if accuracy is not None:
            estimates.append(accuracy)
    estimates.sort()
    if not estimates:
        return None
    return [_percentile(estimates, 0.025), _percentile(estimates, 0.975)]


def build_report(
    cases: list[Case],
    judge_columns: Sequence[str],
    *,
    group_by: str | None,
    cluster_by: str,
    bootstrap_samples: int,
    seed: int,
) -> dict:
    binary_cases = [case for case in cases if case.human_label in {"pass", "fail"}]
    human_pass = sum(case.human_label == "pass" for case in binary_cases)
    human_fail = sum(case.human_label == "fail" for case in binary_cases)

    overall: dict[str, dict] = {}
    for column in judge_columns:
        metrics = calculate_metrics(binary_cases, column)
        metrics["accuracy_ci_95"] = bootstrap_accuracy_interval(
            binary_cases,
            column,
            cluster_by=cluster_by,
            samples=bootstrap_samples,
            seed=seed,
        )
        overall[column] = metrics

    grouped: dict[str, dict[str, dict]] = {}
    if group_by:
        group_values = sorted(
            {case.raw.get(group_by, "").strip() or "(blank)" for case in binary_cases}
        )
        for column in judge_columns:
            grouped[column] = {}
            for value in group_values:
                subset = [
                    case
                    for case in binary_cases
                    if (case.raw.get(group_by, "").strip() or "(blank)") == value
                ]
                grouped[column][value] = calculate_metrics(subset, column)

    return {
        "human_gold": {
            "input_rows": len(cases),
            "binary_gold_n": len(binary_cases),
            "excluded_ambiguous_or_missing_n": len(cases) - len(binary_cases),
            "pass_n": human_pass,
            "fail_n": human_fail,
            "pass_rate": _safe_div(human_pass, len(binary_cases)),
        },
        "settings": {
            "group_by": group_by,
            "cluster_by": cluster_by,
            "bootstrap_samples": bootstrap_samples,
            "seed": seed,
        },
        "judges": overall,
        "by_group": grouped,
    }


def _format_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def _format_interval(value: list[float] | None) -> str:
    if value is None:
        return "n/a"
    return f"{100 * value[0]:.1f}–{100 * value[1]:.1f}%"


def _print_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    rendered = [[str(value) for value in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in rendered:
        widths = [max(width, len(value)) for width, value in zip(widths, row)]
    print("  ".join(header.ljust(width) for header, width in zip(headers, widths)))
    print("  ".join("-" * width for width in widths))
    for row in rendered:
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)))


def print_report(report: dict) -> None:
    gold = report["human_gold"]
    print("Judge comparison against human gold")
    print(
        f"Gold rows: {gold['binary_gold_n']} "
        f"({gold['pass_n']} pass, {gold['fail_n']} fail); "
        f"excluded ambiguous/missing: {gold['excluded_ambiguous_or_missing_n']}"
    )
    print()

    rows = []
    for judge, metrics in report["judges"].items():
        rows.append(
            [
                judge,
                metrics["n"],
                metrics["correct"],
                _format_rate(metrics["accuracy"]),
                _format_interval(metrics["accuracy_ci_95"]),
                _format_rate(metrics["balanced_accuracy"]),
                _format_rate(metrics["fail_recall"]),
                _format_rate(metrics["false_pass_rate"]),
                _format_rate(metrics["coverage"]),
                metrics["no_decision_n"],
            ]
        )
    _print_table(
        [
            "judge",
            "N",
            "correct",
            "accuracy",
            "95% CI",
            "balanced",
            "fail recall",
            "false-pass",
            "coverage",
            "no decision",
        ],
        rows,
    )

    if report["by_group"]:
        print()
        print(f"Breakdown by {report['settings']['group_by']}")
        grouped_rows = []
        for judge, groups in report["by_group"].items():
            for group, metrics in groups.items():
                grouped_rows.append(
                    [
                        judge,
                        group,
                        metrics["n"],
                        _format_rate(metrics["accuracy"]),
                        _format_rate(metrics["fail_recall"]),
                        _format_rate(metrics["coverage"]),
                    ]
                )
        _print_table(
            ["judge", "group", "N", "accuracy", "fail recall", "coverage"],
            grouped_rows,
        )

    print()
    print("Confusion counts (rows are human labels)")
    confusion_rows = []
    for judge, metrics in report["judges"].items():
        confusion = metrics["confusion"]
        confusion_rows.extend(
            [
                [
                    judge,
                    "pass",
                    confusion["pass"]["pass"],
                    confusion["pass"]["fail"],
                    confusion["pass"]["no_decision"],
                ],
                [
                    judge,
                    "fail",
                    confusion["fail"]["pass"],
                    confusion["fail"]["fail"],
                    confusion["fail"]["no_decision"],
                ],
            ]
        )
    _print_table(
        ["judge", "human", "pred pass", "pred fail", "no decision"],
        confusion_rows,
    )


def write_disagreements(
    path: Path,
    cases: Iterable[Case],
    judge_columns: Sequence[str],
    fieldnames: Sequence[str],
) -> int:
    disagreements: list[tuple[Case, list[str]]] = []
    for case in cases:
        if case.human_label not in {"pass", "fail"}:
            continue
        differing = [
            column
            for column in judge_columns
            if case.judge_labels[column] != case.human_label
        ]
        if differing:
            disagreements.append((case, differing))

    path.parent.mkdir(parents=True, exist_ok=True)
    output_fields = list(fieldnames) + ["disagreeing_judges"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields)
        writer.writeheader()
        for case, differing in disagreements:
            row = dict(case.raw)
            row["disagreeing_judges"] = "|".join(differing)
            writer.writerow(row)
    return len(disagreements)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path, help="wide CSV containing human and judge labels")
    parser.add_argument(
        "--judge-columns",
        nargs="+",
        help="judge columns to compare (default: every column beginning with judge_)",
    )
    parser.add_argument(
        "--group-by",
        default="candidate_model",
        help="optional column used for a slice table (default: candidate_model)",
    )
    parser.add_argument(
        "--cluster-by",
        default="scenario_id",
        help="column defining bootstrap clusters (default: scenario_id)",
    )
    parser.add_argument(
        "--bootstrap-samples",
        type=int,
        default=2000,
        help="scenario-clustered bootstrap repetitions; 0 disables intervals (default: 2000)",
    )
    parser.add_argument("--seed", type=int, default=42, help="bootstrap seed (default: 42)")
    parser.add_argument("--json-out", type=Path, help="also write the full report as JSON")
    parser.add_argument(
        "--disagreements-out",
        type=Path,
        help="write rows where at least one judge differs from the human label",
    )
    args = parser.parse_args(argv)
    if args.bootstrap_samples < 0:
        parser.error("--bootstrap-samples must be nonnegative")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        cases, judge_columns, fieldnames = load_cases(
            args.input_csv, args.judge_columns
        )
        known_fields = set(fieldnames)
        group_by = args.group_by if args.group_by in known_fields else None
        if args.group_by and group_by is None:
            print(
                f"warning: group column {args.group_by!r} is absent; skipping group table",
                file=sys.stderr,
            )
        cluster_by = args.cluster_by
        if cluster_by not in known_fields:
            print(
                f"warning: cluster column {cluster_by!r} is absent; using case_id clusters",
                file=sys.stderr,
            )

        report = build_report(
            cases,
            judge_columns,
            group_by=group_by,
            cluster_by=cluster_by,
            bootstrap_samples=args.bootstrap_samples,
            seed=args.seed,
        )
        report["input_csv"] = str(args.input_csv)
        print_report(report)

        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
            print(f"\nWrote JSON report: {args.json_out}")
        if args.disagreements_out:
            count = write_disagreements(
                args.disagreements_out,
                cases,
                judge_columns,
                fieldnames,
            )
            print(
                f"Wrote {count} disagreement row(s): {args.disagreements_out}"
            )
    except InputValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
