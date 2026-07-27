#!/usr/bin/env python3
"""Compare six judge reliability waves with human criterion labels.

Each ``--wave`` argument has the form ``JUDGE:WAVE:PATH``.  Every judge must
provide exactly these waves:

* canonical_r1, canonical_r2, canonical_r3
* whitespace_r1, header_synonyms_r1, instruction_politeness_r1

The JSONL files are the direct outputs of ``run_judge_validation.py``.  This
script validates their model/checkpoint provenance, wave metadata, complete
case coverage, and prepared-case input hashes before calculating anything.
It uses only the Python standard library.

Example::

    python scripts/compare_judge_reliability.py human_labels.csv \
      --wave selene:canonical_r1:selene/canonical_r1.jsonl \
      --wave selene:canonical_r2:selene/canonical_r2.jsonl \
      --wave selene:canonical_r3:selene/canonical_r3.jsonl \
      --wave selene:whitespace_r1:selene/whitespace_r1.jsonl \
      --wave selene:header_synonyms_r1:selene/header_synonyms_r1.jsonl \
      --wave selene:instruction_politeness_r1:selene/instruction_politeness_r1.jsonl \
      --json-out reliability.json --csv-out reliability.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence


EXPECTED_WAVES: dict[str, tuple[str, str]] = {
    "canonical_r1": ("canonical", "r1"),
    "canonical_r2": ("canonical", "r2"),
    "canonical_r3": ("canonical", "r3"),
    "whitespace_r1": ("whitespace", "r1"),
    "header_synonyms_r1": ("header_synonyms", "r1"),
    "instruction_politeness_r1": ("instruction_politeness", "r1"),
}
CANONICAL_WAVES = ("canonical_r1", "canonical_r2", "canonical_r3")
PROMPT_VARIANT_WAVES = (
    "whitespace_r1",
    "header_synonyms_r1",
    "instruction_politeness_r1",
)
PROVENANCE_FIELDS = (
    "judge_name",
    "judge_model",
    "judge_revision",
    "adapter",
    "prompt_version",
    "normalization_version",
    "checkpoint_provenance",
    "configuration_hash",
    "frozen_configuration_hash",
    "prompt_variant",
    "replicate_id",
)
CROSS_WAVE_IDENTITY_FIELDS = (
    "judge_name",
    "judge_model",
    "judge_revision",
    "adapter",
    "prompt_version",
    "normalization_version",
    "checkpoint_provenance",
    "frozen_configuration_hash",
)
PASS_LABELS = {"pass", "1", "1.0", "true", "yes", "correct", "met"}
FAIL_LABELS = {"fail", "0", "0.0", "false", "no", "incorrect", "not_met"}
DECISIONS = ("pass", "fail")
ALL_JUDGE_LABELS = ("pass", "fail", "no_decision")
CRITICALITIES = {"critical", "critical_negative"}
UNMAPPED_SKILL = "unmapped"
DEFAULT_THRESHOLDS = {
    "macro_f1_min": 0.80,
    "critical_failure_sensitivity_min": 0.90,
    "test_retest_worst_pairwise_strict_agreement_min": 0.90,
    "mapped_primary_skill_macro_f1_min": 0.70,
    "prompt_worst_flip_rate_max": 0.10,
}


class InputValidationError(ValueError):
    """Raised when inputs cannot be compared safely."""


@dataclass(frozen=True)
class HumanCase:
    case_id: str
    input_hash: str
    human_label: str
    scenario_id: str
    criterion_id: str
    primary_skill: str
    criticality: str
    raw: dict[str, str]


@dataclass(frozen=True)
class WaveData:
    judge: str
    wave: str
    path: Path
    file_sha256: str
    decisions: dict[str, str]
    prompt_hashes: dict[str, str]
    metadata: dict[str, object]
    status_counts: dict[str, int]


def _token(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _canonical_metadata_value(value: object) -> str:
    """Use a stable representation for metadata equality checks."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise InputValidationError(f"could not read {path}: {exc}") from exc
    return digest.hexdigest()


def _normalize_human_label(value: object, *, location: str) -> str:
    token = _token(value)
    if token in PASS_LABELS:
        return "pass"
    if token in FAIL_LABELS:
        return "fail"
    raise InputValidationError(
        f"{location}: human_label must be binary pass/fail, got {value!r}"
    )


def load_human_labels(path: Path) -> tuple[list[HumanCase], list[str]]:
    required = {
        "case_id",
        "case_input_hash",
        "human_label",
        "scenario_id",
        "criterion_id",
        "primary_skill",
        "criticality",
    }
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise InputValidationError(f"could not open {path}: {exc}") from exc

    with handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames:
            raise InputValidationError(f"{path}: missing CSV header")
        missing = sorted(required - set(fieldnames))
        if missing:
            raise InputValidationError(
                f"{path}: missing required column(s): {', '.join(missing)}"
            )

        cases: list[HumanCase] = []
        seen: set[str] = set()
        for row_number, source in enumerate(reader, 2):
            raw = {name: str(source.get(name) or "") for name in fieldnames}
            case_id = raw["case_id"].strip()
            if not case_id or case_id in seen:
                raise InputValidationError(
                    f"{path}:{row_number}: blank or duplicate case_id {case_id!r}"
                )
            seen.add(case_id)
            input_hash = raw["case_input_hash"].strip()
            scenario_id = raw["scenario_id"].strip()
            criterion_id = raw["criterion_id"].strip()
            if not input_hash:
                raise InputValidationError(
                    f"{path}:{row_number}: case_input_hash is blank for {case_id}"
                )
            if not scenario_id or not criterion_id:
                raise InputValidationError(
                    f"{path}:{row_number}: scenario_id and criterion_id must be nonblank"
                )
            primary_skill = _token(raw["primary_skill"]) or UNMAPPED_SKILL
            cases.append(
                HumanCase(
                    case_id=case_id,
                    input_hash=input_hash,
                    human_label=_normalize_human_label(
                        raw["human_label"], location=f"{path}:{row_number}"
                    ),
                    scenario_id=scenario_id,
                    criterion_id=criterion_id,
                    primary_skill=primary_skill,
                    criticality=_token(raw["criticality"]),
                    raw=raw,
                )
            )
    if not cases:
        raise InputValidationError(f"{path}: contains no human-label rows")
    return cases, fieldnames


def _load_jsonl(path: Path) -> list[tuple[int, dict]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise InputValidationError(f"could not read {path}: {exc}") from exc
    rows: list[tuple[int, dict]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InputValidationError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise InputValidationError(
                f"{path}:{line_number}: expected one JSON object per line"
            )
        rows.append((line_number, value))
    if not rows:
        raise InputValidationError(f"{path}: contains no judgment rows")
    return rows


def _uniform_metadata(path: Path, rows: Sequence[tuple[int, dict]]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for field in PROVENANCE_FIELDS:
        missing_lines = [line for line, row in rows if field not in row]
        if missing_lines:
            raise InputValidationError(
                f"{path}: provenance field {field!r} is missing (first at line "
                f"{missing_lines[0]})"
            )
        values: dict[str, object] = {}
        for _, row in rows:
            values[_canonical_metadata_value(row[field])] = row[field]
        if len(values) != 1:
            raise InputValidationError(
                f"{path}: rows do not share one provenance value for {field}"
            )
        value = next(iter(values.values()))
        if field != "judge_revision" and (
            value is None or (isinstance(value, str) and not value.strip())
        ):
            raise InputValidationError(f"{path}: provenance field {field} is blank")
        metadata[field] = value
    return metadata


def load_wave(
    path: Path,
    *,
    judge: str,
    wave: str,
    humans_by_id: Mapping[str, HumanCase],
) -> WaveData:
    if wave not in EXPECTED_WAVES:
        raise InputValidationError(f"unsupported wave {wave!r}")
    rows = _load_jsonl(path)
    metadata = _uniform_metadata(path, rows)
    if str(metadata["judge_name"]).strip() != judge:
        raise InputValidationError(
            f"{path}: judge_name {metadata['judge_name']!r} does not match --wave judge "
            f"{judge!r}"
        )
    expected_variant, expected_replicate = EXPECTED_WAVES[wave]
    if str(metadata["prompt_variant"]).strip() != expected_variant:
        raise InputValidationError(
            f"{path}: wave {wave} requires prompt_variant={expected_variant!r}, got "
            f"{metadata['prompt_variant']!r}"
        )
    if str(metadata["replicate_id"]).strip() != expected_replicate:
        raise InputValidationError(
            f"{path}: wave {wave} requires replicate_id={expected_replicate!r}, got "
            f"{metadata['replicate_id']!r}"
        )

    decisions: dict[str, str] = {}
    prompt_hashes: dict[str, str] = {}
    status_counts: Counter[str] = Counter()
    for line_number, row in rows:
        case_id = str(row.get("case_id") or "").strip()
        if not case_id or case_id in decisions:
            raise InputValidationError(
                f"{path}:{line_number}: blank or duplicate case_id {case_id!r}"
            )
        human = humans_by_id.get(case_id)
        if human is None:
            raise InputValidationError(f"{path}:{line_number}: unknown case_id {case_id!r}")
        input_hash = str(row.get("input_hash") or "").strip()
        if input_hash != human.input_hash:
            raise InputValidationError(
                f"{path}:{line_number}: input_hash does not match human case_input_hash "
                f"for {case_id}"
            )
        for field, expected in (
            ("scenario_id", human.scenario_id),
            ("criterion_id", human.criterion_id),
        ):
            if field in row and str(row[field] or "").strip() != expected:
                raise InputValidationError(
                    f"{path}:{line_number}: {field} does not match human labels for "
                    f"{case_id}"
                )
        prompt_hash = str(row.get("prompt_hash") or "").strip()
        if not prompt_hash:
            raise InputValidationError(
                f"{path}:{line_number}: prompt_hash is blank for {case_id}"
            )
        status = _token(row.get("status"))
        if not status:
            raise InputValidationError(f"{path}:{line_number}: status is blank for {case_id}")
        verdict = _token(row.get("verdict"))
        if status == "ok":
            if verdict not in ALL_JUDGE_LABELS:
                raise InputValidationError(
                    f"{path}:{line_number}: unsupported verdict {row.get('verdict')!r} "
                    f"for status=ok"
                )
            decision = verdict
        else:
            decision = "no_decision"
        decisions[case_id] = decision
        prompt_hashes[case_id] = prompt_hash
        status_counts[status] += 1

    expected_ids = set(humans_by_id)
    actual_ids = set(decisions)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        raise InputValidationError(
            f"{path}: incomplete case coverage; expected {len(expected_ids)}, got "
            f"{len(actual_ids)}; missing={missing[:5]}, extra={extra[:5]}"
        )
    return WaveData(
        judge=judge,
        wave=wave,
        path=path,
        file_sha256=file_sha256(path),
        decisions=decisions,
        prompt_hashes=prompt_hashes,
        metadata=metadata,
        status_counts=dict(sorted(status_counts.items())),
    )


def validate_judge_waves(judge: str, waves: Mapping[str, WaveData]) -> None:
    missing = sorted(set(EXPECTED_WAVES) - set(waves))
    extra = sorted(set(waves) - set(EXPECTED_WAVES))
    if missing or extra:
        raise InputValidationError(
            f"judge {judge!r} must provide exactly six waves; missing={missing}, "
            f"extra={extra}"
        )
    baseline = waves["canonical_r1"]
    for wave_name, wave in waves.items():
        for field in CROSS_WAVE_IDENTITY_FIELDS:
            if _canonical_metadata_value(wave.metadata[field]) != _canonical_metadata_value(
                baseline.metadata[field]
            ):
                raise InputValidationError(
                    f"judge {judge!r}: provenance field {field} differs between "
                    f"canonical_r1 and {wave_name}"
                )
    for case_id, expected_hash in baseline.prompt_hashes.items():
        for wave_name in ("canonical_r2", "canonical_r3"):
            if waves[wave_name].prompt_hashes[case_id] != expected_hash:
                raise InputValidationError(
                    f"judge {judge!r}: canonical prompt_hash changed for {case_id} "
                    f"between canonical_r1 and {wave_name}"
                )
        for wave_name in PROMPT_VARIANT_WAVES:
            if waves[wave_name].prompt_hashes[case_id] == expected_hash:
                raise InputValidationError(
                    f"judge {judge!r}: prompt variant {wave_name} did not change "
                    f"prompt_hash for {case_id}"
                )


def validate_cross_judge_versions(
    waves_by_judge: Mapping[str, Mapping[str, WaveData]],
) -> None:
    """Reject a comparison that mixes prompt or normalization experiments."""

    for field in ("prompt_version", "normalization_version"):
        values = {
            judge: _canonical_metadata_value(waves["canonical_r1"].metadata[field])
            for judge, waves in waves_by_judge.items()
        }
        if len(set(values.values())) > 1:
            raise InputValidationError(
                f"judges do not share one {field}; refusing mixed-study comparison: "
                f"{values}"
            )


def _safe_div(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def _class_metrics(
    gold: Sequence[str], predictions: Sequence[str], label: str
) -> dict[str, int | float | None]:
    support = sum(value == label for value in gold)
    predicted_n = sum(value == label for value in predictions)
    true_positive = sum(
        actual == label and predicted == label
        for actual, predicted in zip(gold, predictions)
    )
    false_positive = predicted_n - true_positive
    false_negative = support - true_positive
    precision = _safe_div(true_positive, true_positive + false_positive)
    recall = _safe_div(true_positive, true_positive + false_negative)
    denominator = 2 * true_positive + false_positive + false_negative
    # Match the conventional zero_division=0 behavior for an explicitly
    # requested binary label set.
    f1 = (2 * true_positive / denominator) if denominator else 0.0
    return {
        "support": support,
        "predicted_n": predicted_n,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def classification_metrics(
    cases: Sequence[HumanCase], predictions: Mapping[str, str]
) -> dict:
    gold = [case.human_label for case in cases]
    predicted = [predictions[case.case_id] for case in cases]
    n = len(cases)
    confusion = {
        actual: {decision: 0 for decision in ALL_JUDGE_LABELS} for actual in DECISIONS
    }
    for actual, decision in zip(gold, predicted):
        confusion[actual][decision] += 1
    per_class = {
        label: _class_metrics(gold, predicted, label) for label in DECISIONS
    }
    macro_f1 = (
        sum(float(per_class[label]["f1"]) for label in DECISIONS) / len(DECISIONS)
        if n
        else None
    )
    weighted_f1 = (
        sum(
            int(per_class[label]["support"]) * float(per_class[label]["f1"])
            for label in DECISIONS
        )
        / n
        if n
        else None
    )
    decided_pairs = [
        (actual, decision)
        for actual, decision in zip(gold, predicted)
        if decision in DECISIONS
    ]
    tp = sum(actual == "fail" and decision == "fail" for actual, decision in decided_pairs)
    tn = sum(actual == "pass" and decision == "pass" for actual, decision in decided_pairs)
    fp = sum(actual == "pass" and decision == "fail" for actual, decision in decided_pairs)
    fn = sum(actual == "fail" and decision == "pass" for actual, decision in decided_pairs)
    mcc_denominator = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    mcc = (tp * tn - fp * fn) / mcc_denominator if mcc_denominator else None
    decided_n = len(decided_pairs)
    correct = confusion["pass"]["pass"] + confusion["fail"]["fail"]
    return {
        "n": n,
        "decided_n": decided_n,
        "no_decision_n": n - decided_n,
        "coverage": _safe_div(decided_n, n),
        "correct_n": correct,
        "accuracy": _safe_div(correct, n),
        "macro_f1": macro_f1,
        "weighted_f1": weighted_f1,
        "mcc": mcc,
        "mcc_scope": "decided_cases_only",
        "per_class": per_class,
        "confusion": confusion,
    }


def critical_failure_metrics(
    cases: Sequence[HumanCase], predictions: Mapping[str, str]
) -> dict:
    eligible = [
        case
        for case in cases
        if case.criticality in CRITICALITIES and case.human_label == "fail"
    ]
    detected = sum(predictions[case.case_id] == "fail" for case in eligible)
    no_decision = sum(
        predictions[case.case_id] == "no_decision" for case in eligible
    )
    by_criticality = {}
    for criticality in sorted(CRITICALITIES):
        subset = [case for case in eligible if case.criticality == criticality]
        subset_detected = sum(predictions[case.case_id] == "fail" for case in subset)
        by_criticality[criticality] = {
            "critical_human_failure_n": len(subset),
            "detected_n": subset_detected,
            "sensitivity": _safe_div(subset_detected, len(subset)),
        }
    return {
        "included_criticalities": sorted(CRITICALITIES),
        "critical_human_failure_n": len(eligible),
        "detected_n": detected,
        "missed_n": len(eligible) - detected,
        "no_decision_n": no_decision,
        "sensitivity": _safe_div(detected, len(eligible)),
        "by_criticality": by_criticality,
    }


def performance_report(
    cases: Sequence[HumanCase], predictions: Mapping[str, str]
) -> dict:
    report = classification_metrics(cases, predictions)
    report["critical_failures"] = critical_failure_metrics(cases, predictions)
    skills = sorted(
        {case.primary_skill for case in cases if case.primary_skill != UNMAPPED_SKILL}
    )
    if any(case.primary_skill == UNMAPPED_SKILL for case in cases):
        skills.append(UNMAPPED_SKILL)
    by_skill = {}
    for skill in skills:
        subset = [case for case in cases if case.primary_skill == skill]
        by_skill[skill] = classification_metrics(subset, predictions)
        by_skill[skill]["mapped"] = skill != UNMAPPED_SKILL
    report["by_primary_skill"] = by_skill
    return report


def _cohen_kappa(first: Sequence[str], second: Sequence[str]) -> float | None:
    if not first or len(first) != len(second):
        return None
    n = len(first)
    observed = sum(left == right for left, right in zip(first, second)) / n
    first_counts = Counter(first)
    second_counts = Counter(second)
    expected = sum(
        first_counts[label] * second_counts[label] for label in ALL_JUDGE_LABELS
    ) / (n * n)
    return (observed - expected) / (1 - expected) if expected < 1 else None


def _pairwise_reliability(
    cases: Sequence[HumanCase], first: Mapping[str, str], second: Mapping[str, str]
) -> dict:
    left = [first[case.case_id] for case in cases]
    right = [second[case.case_id] for case in cases]
    n = len(cases)
    raw_agreement = sum(a == b for a, b in zip(left, right))
    strict_agreement = sum(a == b and a in DECISIONS for a, b in zip(left, right))
    both_decided = sum(a in DECISIONS and b in DECISIONS for a, b in zip(left, right))
    return {
        "n": n,
        "raw_agreement_n": raw_agreement,
        "raw_agreement_rate": _safe_div(raw_agreement, n),
        "strict_agreement_n": strict_agreement,
        "strict_agreement_rate": _safe_div(strict_agreement, n),
        "both_decided_n": both_decided,
        "no_decision_involved_n": n - both_decided,
        "cohen_kappa": _cohen_kappa(left, right),
        "cohen_kappa_labels": list(ALL_JUDGE_LABELS),
    }


def test_retest_report(
    cases: Sequence[HumanCase], waves: Mapping[str, WaveData]
) -> dict:
    pair_names = (
        ("canonical_r1", "canonical_r2"),
        ("canonical_r1", "canonical_r3"),
        ("canonical_r2", "canonical_r3"),
    )
    pairwise = {}
    for first, second in pair_names:
        key = f"{first}_vs_{second}"
        pairwise[key] = _pairwise_reliability(
            cases, waves[first].decisions, waves[second].decisions
        )
    labels = [
        tuple(waves[wave].decisions[case.case_id] for wave in CANONICAL_WAVES)
        for case in cases
    ]
    exact_raw = sum(len(set(values)) == 1 for values in labels)
    exact_strict = sum(
        len(set(values)) == 1 and values[0] in DECISIONS for values in labels
    )
    strict_rates = [pair["strict_agreement_rate"] for pair in pairwise.values()]
    return {
        "n": len(cases),
        "canonical_waves": list(CANONICAL_WAVES),
        "exact_three_repeat_agreement_n": exact_raw,
        "exact_three_repeat_agreement_rate": _safe_div(exact_raw, len(cases)),
        "exact_three_repeat_strict_agreement_n": exact_strict,
        "exact_three_repeat_strict_agreement_rate": _safe_div(
            exact_strict, len(cases)
        ),
        "strict_definition": (
            "labels must agree and be pass/fail; any no_decision is inconsistent"
        ),
        "pairwise": pairwise,
        "worst_pairwise_strict_agreement_rate": (
            min(strict_rates) if strict_rates else None
        ),
    }


def _variant_consistency(
    cases: Sequence[HumanCase], canonical: Mapping[str, str], variant: Mapping[str, str]
) -> dict:
    pairs = [(canonical[case.case_id], variant[case.case_id]) for case in cases]
    n = len(pairs)
    both_decided = sum(left in DECISIONS and right in DECISIONS for left, right in pairs)
    binary_flips = sum(
        left in DECISIONS and right in DECISIONS and left != right
        for left, right in pairs
    )
    raw_disagreements = sum(left != right for left, right in pairs)
    strict_flips = sum(
        left != right or left == "no_decision" or right == "no_decision"
        for left, right in pairs
    )
    return {
        "n": n,
        "both_decided_n": both_decided,
        "no_decision_involved_n": n - both_decided,
        "binary_flip_n": binary_flips,
        "binary_flip_rate_among_both_decided": _safe_div(binary_flips, both_decided),
        "raw_disagreement_n": raw_disagreements,
        "raw_disagreement_rate": _safe_div(raw_disagreements, n),
        "flip_n": strict_flips,
        "flip_rate": _safe_div(strict_flips, n),
        "flip_definition": (
            "different labels or any no_decision; two no_decisions remain inconsistent"
        ),
    }


def prompt_consistency_report(
    cases: Sequence[HumanCase], waves: Mapping[str, WaveData]
) -> dict:
    canonical = waves["canonical_r1"].decisions
    variants = {
        wave: _variant_consistency(cases, canonical, waves[wave].decisions)
        for wave in PROMPT_VARIANT_WAVES
    }
    rates = [value["flip_rate"] for value in variants.values()]
    any_flip = 0
    for case in cases:
        base = canonical[case.case_id]
        if any(
            base != waves[wave].decisions[case.case_id]
            or base == "no_decision"
            or waves[wave].decisions[case.case_id] == "no_decision"
            for wave in PROMPT_VARIANT_WAVES
        ):
            any_flip += 1
    pooled_n = len(cases) * len(PROMPT_VARIANT_WAVES)
    pooled_flips = sum(value["flip_n"] for value in variants.values())
    return {
        "canonical_wave": "canonical_r1",
        "variants": variants,
        "worst_variant_flip_rate": max(rates) if rates else None,
        "pooled_comparison_n": pooled_n,
        "pooled_flip_n": pooled_flips,
        "pooled_flip_rate": _safe_div(pooled_flips, pooled_n),
        "any_variant_flip_case_n": any_flip,
        "any_variant_flip_case_rate": _safe_div(any_flip, len(cases)),
    }


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def _interval(values: Iterable[float | None], requested_samples: int) -> dict | None:
    finite = sorted(
        float(value)
        for value in values
        if value is not None and math.isfinite(float(value))
    )
    if not finite:
        return None
    return {
        "lower": _percentile(finite, 0.025),
        "upper": _percentile(finite, 0.975),
        "confidence_level": 0.95,
        "valid_resamples": len(finite),
        "requested_resamples": requested_samples,
    }


def scenario_cluster_resamples(
    cases: Sequence[HumanCase], *, samples: int, seed: int
) -> list[list[HumanCase]]:
    if samples < 2:
        return []
    clusters: dict[str, list[HumanCase]] = defaultdict(list)
    for case in cases:
        clusters[case.scenario_id].append(case)
    cluster_ids = sorted(clusters)
    if len(cluster_ids) < 2:
        return []
    rng = random.Random(seed)
    result = []
    for _ in range(samples):
        sample: list[HumanCase] = []
        for _ in cluster_ids:
            sample.extend(clusters[rng.choice(cluster_ids)])
        result.append(sample)
    return result


def _bootstrap_intervals(
    resamples: Sequence[Sequence[HumanCase]],
    waves: Mapping[str, WaveData],
    skills: Sequence[str],
    *,
    requested_samples: int,
) -> dict:
    if not resamples:
        return {}
    values: dict[str, list[float | None]] = defaultdict(list)
    for cases in resamples:
        canonical = performance_report(cases, waves["canonical_r1"].decisions)
        for metric in ("macro_f1", "weighted_f1", "mcc", "coverage"):
            values[f"canonical.{metric}"].append(canonical[metric])
        values["canonical.critical_sensitivity"].append(
            canonical["critical_failures"]["sensitivity"]
        )
        for skill in skills:
            skill_cases = [case for case in cases if case.primary_skill == skill]
            skill_metric = (
                classification_metrics(skill_cases, waves["canonical_r1"].decisions)[
                    "macro_f1"
                ]
                if skill_cases
                else None
            )
            values[f"skill.{skill}.macro_f1"].append(skill_metric)

        retest = test_retest_report(cases, waves)
        values["retest.exact_strict"].append(
            retest["exact_three_repeat_strict_agreement_rate"]
        )
        values["retest.worst_pairwise_strict"].append(
            retest["worst_pairwise_strict_agreement_rate"]
        )
        for pair_name, pair in retest["pairwise"].items():
            values[f"retest.{pair_name}.cohen_kappa"].append(pair["cohen_kappa"])

        prompt = prompt_consistency_report(cases, waves)
        values["prompt.worst_flip"].append(prompt["worst_variant_flip_rate"])
        for wave_name, variant in prompt["variants"].items():
            values[f"prompt.{wave_name}.flip"].append(variant["flip_rate"])
    return {
        key: _interval(metric_values, requested_samples)
        for key, metric_values in values.items()
    }


def _attach_intervals(judge_report: dict, intervals: Mapping[str, dict | None]) -> None:
    canonical = judge_report["waves"]["canonical_r1"]
    canonical["confidence_intervals_95"] = {
        metric: intervals.get(f"canonical.{metric}")
        for metric in ("macro_f1", "weighted_f1", "mcc", "coverage")
    }
    canonical["critical_failures"]["sensitivity_ci_95"] = intervals.get(
        "canonical.critical_sensitivity"
    )
    for skill, metrics in canonical["by_primary_skill"].items():
        metrics["macro_f1_ci_95"] = intervals.get(f"skill.{skill}.macro_f1")
    retest = judge_report["test_retest"]
    retest["confidence_intervals_95"] = {
        "exact_three_repeat_strict_agreement_rate": intervals.get(
            "retest.exact_strict"
        ),
        "worst_pairwise_strict_agreement_rate": intervals.get(
            "retest.worst_pairwise_strict"
        ),
    }
    for pair_name, pair in retest["pairwise"].items():
        pair["cohen_kappa_ci_95"] = intervals.get(
            f"retest.{pair_name}.cohen_kappa"
        )
    prompt = judge_report["prompt_consistency"]
    prompt["worst_variant_flip_rate_ci_95"] = intervals.get("prompt.worst_flip")
    for wave_name, variant in prompt["variants"].items():
        variant["flip_rate_ci_95"] = intervals.get(f"prompt.{wave_name}.flip")


def _threshold_check(
    value: float | None, *, threshold: float, operator: str
) -> dict[str, object]:
    if operator == ">=":
        passed = value is not None and value >= threshold
    elif operator == "<=":
        passed = value is not None and value <= threshold
    else:  # pragma: no cover - internal programming guard
        raise ValueError(f"unsupported threshold operator {operator}")
    return {
        "value": value,
        "operator": operator,
        "threshold": threshold,
        "passed": passed,
    }


def acceptance_report(judge_report: dict) -> dict:
    canonical = judge_report["waves"]["canonical_r1"]
    retest = judge_report["test_retest"]
    prompt = judge_report["prompt_consistency"]
    mapped_skill_checks = {
        skill: _threshold_check(
            metrics["macro_f1"],
            threshold=DEFAULT_THRESHOLDS["mapped_primary_skill_macro_f1_min"],
            operator=">=",
        )
        for skill, metrics in canonical["by_primary_skill"].items()
        if metrics["mapped"]
    }
    checks = {
        "macro_f1": _threshold_check(
            canonical["macro_f1"],
            threshold=DEFAULT_THRESHOLDS["macro_f1_min"],
            operator=">=",
        ),
        "critical_failure_sensitivity": _threshold_check(
            canonical["critical_failures"]["sensitivity"],
            threshold=DEFAULT_THRESHOLDS["critical_failure_sensitivity_min"],
            operator=">=",
        ),
        "test_retest_worst_pairwise_strict_agreement": _threshold_check(
            retest["worst_pairwise_strict_agreement_rate"],
            threshold=DEFAULT_THRESHOLDS[
                "test_retest_worst_pairwise_strict_agreement_min"
            ],
            operator=">=",
        ),
        "prompt_worst_flip_rate": _threshold_check(
            prompt["worst_variant_flip_rate"],
            threshold=DEFAULT_THRESHOLDS["prompt_worst_flip_rate_max"],
            operator="<=",
        ),
    }
    skill_passed = bool(mapped_skill_checks) and all(
        check["passed"] for check in mapped_skill_checks.values()
    )
    checks["mapped_primary_skills"] = {
        "operator": ">=",
        "threshold": DEFAULT_THRESHOLDS["mapped_primary_skill_macro_f1_min"],
        "passed": skill_passed,
        "skills": mapped_skill_checks,
        "unmapped_excluded_from_acceptance": True,
    }
    passed = all(bool(check["passed"]) for check in checks.values())
    return {
        "passed": passed,
        "checks": checks,
        "marginal_reliability": {
            "status": "not_computed",
            "reason": (
                "formal IRT marginal reliability is not identifiable from these "
                "repeated judgments alone"
            ),
            "surrogate": {
                "metric": "MCC on decided canonical_r1 cases, reported with coverage",
                "mcc": canonical["mcc"],
                "coverage": canonical["coverage"],
                "acceptance_threshold": None,
            },
        },
    }


def build_report(
    human_path: Path,
    cases: Sequence[HumanCase],
    waves_by_judge: Mapping[str, Mapping[str, WaveData]],
    *,
    bootstrap_samples: int,
    seed: int,
) -> dict:
    validate_cross_judge_versions(waves_by_judge)
    resamples = scenario_cluster_resamples(
        cases, samples=bootstrap_samples, seed=seed
    )
    judges = {}
    input_waves = {}
    skills = sorted({case.primary_skill for case in cases})
    for judge in sorted(waves_by_judge):
        waves = waves_by_judge[judge]
        judge_report = {
            "provenance": {
                field: waves["canonical_r1"].metadata[field]
                for field in CROSS_WAVE_IDENTITY_FIELDS
            },
            "waves": {
                wave: performance_report(cases, waves[wave].decisions)
                for wave in EXPECTED_WAVES
            },
            "test_retest": test_retest_report(cases, waves),
            "prompt_consistency": prompt_consistency_report(cases, waves),
        }
        intervals = _bootstrap_intervals(
            resamples,
            waves,
            skills,
            requested_samples=bootstrap_samples,
        )
        _attach_intervals(judge_report, intervals)
        judges[judge] = judge_report
        input_waves[judge] = {
            wave: {
                "path": str(waves[wave].path),
                "sha256": waves[wave].file_sha256,
                "configuration_hash": waves[wave].metadata["configuration_hash"],
                "prompt_variant": waves[wave].metadata["prompt_variant"],
                "replicate_id": waves[wave].metadata["replicate_id"],
                "status_counts": waves[wave].status_counts,
            }
            for wave in EXPECTED_WAVES
        }

    acceptance_by_judge = {
        judge: acceptance_report(judge_report)
        for judge, judge_report in judges.items()
    }
    criterion_counts = Counter(case.criterion_id for case in cases)
    human_counts = Counter(case.human_label for case in cases)
    return {
        "schema_version": "judge-reliability-comparison-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": {
            "human_labels": {
                "path": str(human_path),
                "sha256": file_sha256(human_path),
            },
            "waves": input_waves,
        },
        "human_gold": {
            "criterion_case_n": len(cases),
            "unique_criterion_n": len(criterion_counts),
            "unique_scenario_n": len({case.scenario_id for case in cases}),
            "pass_n": human_counts["pass"],
            "fail_n": human_counts["fail"],
            "primary_skill_counts": dict(
                sorted(Counter(case.primary_skill for case in cases).items())
            ),
            "criticality_counts": dict(
                sorted(Counter(case.criticality for case in cases).items())
            ),
        },
        "settings": {
            "performance_wave": "canonical_r1",
            "bootstrap_method": "scenario_clustered_percentile",
            "bootstrap_samples": bootstrap_samples,
            "bootstrap_valid": bool(resamples),
            "seed": seed,
            "no_decision_policy": {
                "f1": "false negative for the true class",
                "test_retest_strict_agreement": "inconsistent",
                "prompt_flip_rate": "inconsistent, including two no_decisions",
                "mcc": "excluded; coverage is reported beside decided-only MCC",
            },
        },
        "judges": judges,
        "acceptance": {
            "thresholds": dict(DEFAULT_THRESHOLDS),
            "judges": acceptance_by_judge,
            "all_judges_pass": all(
                result["passed"] for result in acceptance_by_judge.values()
            ),
            "marginal_reliability": {
                "status": "not_computed",
                "replacement": "MCC plus coverage is reported as a non-IRT surrogate",
            },
        },
    }


def _csv_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "unmapped"


def write_csv_summary(path: Path, report: dict) -> None:
    skills = sorted(report["human_gold"]["primary_skill_counts"])
    fields = [
        "judge",
        "criterion_case_n",
        "canonical_macro_f1",
        "canonical_weighted_f1",
        "canonical_mcc_decided_only",
        "canonical_coverage",
        "critical_failure_sensitivity",
        "exact_three_repeat_strict_agreement_rate",
        "worst_pairwise_strict_agreement_rate",
        "prompt_worst_flip_rate",
        "acceptance_passed",
        "marginal_reliability_status",
    ]
    fields.extend(f"skill_{_csv_name(skill)}_macro_f1" for skill in skills)
    fields.extend(f"wave_{wave}_macro_f1" for wave in EXPECTED_WAVES)
    rows = []
    for judge, metrics in report["judges"].items():
        canonical = metrics["waves"]["canonical_r1"]
        row = {
            "judge": judge,
            "criterion_case_n": canonical["n"],
            "canonical_macro_f1": canonical["macro_f1"],
            "canonical_weighted_f1": canonical["weighted_f1"],
            "canonical_mcc_decided_only": canonical["mcc"],
            "canonical_coverage": canonical["coverage"],
            "critical_failure_sensitivity": canonical["critical_failures"][
                "sensitivity"
            ],
            "exact_three_repeat_strict_agreement_rate": metrics["test_retest"][
                "exact_three_repeat_strict_agreement_rate"
            ],
            "worst_pairwise_strict_agreement_rate": metrics["test_retest"][
                "worst_pairwise_strict_agreement_rate"
            ],
            "prompt_worst_flip_rate": metrics["prompt_consistency"][
                "worst_variant_flip_rate"
            ],
            "acceptance_passed": report["acceptance"]["judges"][judge]["passed"],
            "marginal_reliability_status": "not_computed",
        }
        for skill in skills:
            row[f"skill_{_csv_name(skill)}_macro_f1"] = canonical[
                "by_primary_skill"
            ][skill]["macro_f1"]
        for wave in EXPECTED_WAVES:
            row[f"wave_{wave}_macro_f1"] = metrics["waves"][wave]["macro_f1"]
        rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def print_report(report: dict) -> None:
    print(
        "Judge reliability comparison "
        f"({report['human_gold']['criterion_case_n']} criterion cases)"
    )
    header = (
        "judge",
        "macro-F1",
        "critical sens.",
        "retest worst",
        "prompt worst",
        "coverage",
        "accept",
    )
    rows = []
    for judge, metrics in report["judges"].items():
        canonical = metrics["waves"]["canonical_r1"]
        rows.append(
            (
                judge,
                _rate(canonical["macro_f1"]),
                _rate(canonical["critical_failures"]["sensitivity"]),
                _rate(
                    metrics["test_retest"][
                        "worst_pairwise_strict_agreement_rate"
                    ]
                ),
                _rate(metrics["prompt_consistency"]["worst_variant_flip_rate"]),
                _rate(canonical["coverage"]),
                "PASS" if report["acceptance"]["judges"][judge]["passed"] else "FAIL",
            )
        )
    widths = [len(value) for value in header]
    for row in rows:
        widths = [max(width, len(value)) for width, value in zip(widths, row)]
    print("  ".join(value.ljust(width) for value, width in zip(header, widths)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)))
    print("Marginal reliability: not computed; MCC plus coverage is the surrogate.")


def parse_wave_spec(value: str) -> tuple[str, str, Path]:
    parts = value.split(":", 2)
    if len(parts) != 3 or not all(part.strip() for part in parts):
        raise argparse.ArgumentTypeError(
            "--wave must have the form JUDGE:WAVE:PATH"
        )
    judge, wave, path = (part.strip() for part in parts)
    if wave not in EXPECTED_WAVES:
        raise argparse.ArgumentTypeError(
            f"unsupported wave {wave!r}; expected one of {', '.join(EXPECTED_WAVES)}"
        )
    return judge, wave, Path(path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("human_labels", type=Path, help="prepared human_labels.csv")
    parser.add_argument(
        "--wave",
        action="append",
        type=parse_wave_spec,
        required=True,
        metavar="JUDGE:WAVE:PATH",
        help="one judge wave; repeat exactly six times per judge",
    )
    parser.add_argument("--json-out", type=Path, help="write the complete JSON report")
    parser.add_argument("--csv-out", type=Path, help="write a one-row-per-judge summary")
    parser.add_argument(
        "--bootstrap-samples",
        type=int,
        default=2000,
        help="scenario-clustered bootstrap resamples; 0 disables CIs (default: 2000)",
    )
    parser.add_argument("--seed", type=int, default=42, help="bootstrap seed")
    parser.add_argument(
        "--enforce-thresholds",
        action="store_true",
        help="return exit code 3 when any judge misses an acceptance threshold",
    )
    args = parser.parse_args(argv)
    if args.bootstrap_samples < 0:
        parser.error("--bootstrap-samples must be nonnegative")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        cases, _ = load_human_labels(args.human_labels)
        humans_by_id = {case.case_id: case for case in cases}
        specs: dict[str, dict[str, Path]] = defaultdict(dict)
        for judge, wave, path in args.wave:
            if wave in specs[judge]:
                raise InputValidationError(
                    f"duplicate --wave for judge={judge!r}, wave={wave!r}"
                )
            specs[judge][wave] = path
        waves_by_judge: dict[str, dict[str, WaveData]] = {}
        for judge, judge_specs in specs.items():
            missing = sorted(set(EXPECTED_WAVES) - set(judge_specs))
            if missing:
                raise InputValidationError(
                    f"judge {judge!r} is missing wave(s): {', '.join(missing)}"
                )
            waves = {
                wave: load_wave(
                    judge_specs[wave],
                    judge=judge,
                    wave=wave,
                    humans_by_id=humans_by_id,
                )
                for wave in EXPECTED_WAVES
            }
            validate_judge_waves(judge, waves)
            waves_by_judge[judge] = waves
        report = build_report(
            args.human_labels,
            cases,
            waves_by_judge,
            bootstrap_samples=args.bootstrap_samples,
            seed=args.seed,
        )
        print_report(report)
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(
                json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"Wrote JSON report: {args.json_out}")
        if args.csv_out:
            write_csv_summary(args.csv_out, report)
            print(f"Wrote CSV summary: {args.csv_out}")
        if args.enforce_thresholds and not report["acceptance"]["all_judges_pass"]:
            return 3
        return 0
    except InputValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
