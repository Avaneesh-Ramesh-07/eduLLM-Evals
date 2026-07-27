#!/usr/bin/env python3
"""Re-normalize saved judge raw outputs without changing the source results.

The command reads every ``JUDGE/WAVE/WAVE.jsonl`` file below a result root,
applies the current parser from ``run_judge_validation.py``, and writes a new
derived result tree with an audit trail and fresh manifests. Source JSONL and
manifest files are verified and never modified.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import tempfile
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import run_judge_validation as runner


SCHEMA_VERSION = "judge-renormalization-v1"
EXPECTED_WAVES = {
    "canonical_r1",
    "canonical_r2",
    "canonical_r3",
    "whitespace_r1",
    "header_synonyms_r1",
    "instruction_politeness_r1",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected one JSON object")
    return value


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    try:
        handle = path.open(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"could not open {path}: {exc}") from exc
    with handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            case_id = str(row.get("case_id") or "").strip()
            if not case_id or case_id in seen:
                raise ValueError(
                    f"{path}:{line_number}: blank or duplicate case_id {case_id!r}"
                )
            seen.add(case_id)
            rows.append(row)
    if not rows:
        raise ValueError(f"{path}: contains no rows")
    return rows


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _source_artifact_hash(manifest: dict, filename: str) -> str | None:
    artifacts = manifest.get("s3", {}).get("artifacts", {})
    if not isinstance(artifacts, dict):
        return None
    for name, metadata in artifacts.items():
        if not isinstance(metadata, dict):
            continue
        local_name = Path(str(metadata.get("local_path") or name)).name
        if local_name == filename:
            value = str(metadata.get("sha256") or "").strip()
            return value or None
    return None


def discover_waves(source_root: Path) -> list[tuple[str, str, Path, Path]]:
    if (source_root / "renormalization_summary.json").exists():
        raise ValueError("source root is already a derived renormalized result tree")
    discovered: list[tuple[str, str, Path, Path]] = []
    for source_path in sorted(source_root.glob("*/*/*.jsonl")):
        judge = source_path.parent.parent.name
        wave = source_path.parent.name
        if source_path.name != f"{wave}.jsonl":
            continue
        if judge not in runner.JUDGES:
            raise ValueError(f"{source_path}: unknown judge directory {judge!r}")
        if wave not in EXPECTED_WAVES:
            raise ValueError(f"{source_path}: unknown reliability wave {wave!r}")
        manifest_path = source_path.with_suffix(".manifest.json")
        if not manifest_path.is_file():
            raise ValueError(f"missing source manifest: {manifest_path}")
        discovered.append((judge, wave, source_path, manifest_path))
    if not discovered:
        raise ValueError(f"no judge wave files found below {source_root}")
    waves_by_judge: dict[str, set[str]] = {}
    for judge, wave, _, _ in discovered:
        waves_by_judge.setdefault(judge, set()).add(wave)
    for judge, waves in sorted(waves_by_judge.items()):
        if waves != EXPECTED_WAVES:
            missing = sorted(EXPECTED_WAVES - waves)
            extra = sorted(waves - EXPECTED_WAVES)
            raise ValueError(
                f"judge {judge!r} must have exactly six reliability waves; "
                f"missing={missing}, extra={extra}"
            )
    return discovered


def _validate_source_wave(
    *,
    judge: str,
    wave: str,
    source_path: Path,
    manifest_path: Path,
    rows: Sequence[dict],
    manifest: dict,
) -> tuple[dict, str, str]:
    configuration = manifest.get("configuration")
    if not isinstance(configuration, dict):
        raise ValueError(f"{manifest_path}: missing configuration object")
    if manifest.get("status") not in {"complete", "complete_with_errors"}:
        raise ValueError(f"{manifest_path}: source run is not in a final state")
    source_configuration_hash = str(manifest.get("configuration_hash") or "")
    if runner.stable_hash(configuration) != source_configuration_hash:
        raise ValueError(f"{manifest_path}: configuration hash does not verify")
    expected_variant, expected_replicate = wave.rsplit("_", 1)
    if expected_variant.startswith("canonical"):
        expected_variant = "canonical"
    expected_configuration = {
        "judge_name": judge,
        "model_id": runner.JUDGES[judge].model_id,
        "revision": runner.JUDGES[judge].revision,
        "adapter": runner.JUDGES[judge].adapter,
        "prompt_variant": expected_variant,
        "replicate_id": expected_replicate,
    }
    for field, expected in expected_configuration.items():
        if configuration.get(field) != expected:
            raise ValueError(
                f"{manifest_path}: configuration field {field!r} does not match "
                "the pinned judge/wave"
            )
    source_normalization_version = str(
        configuration.get("normalization_version") or ""
    )
    source_prompt_version = str(configuration.get("prompt_version") or "")
    if not source_normalization_version or not source_prompt_version:
        raise ValueError(
            f"{manifest_path}: prompt/normalization version must be present"
        )
    expected_frozen_hash = runner.frozen_configuration_hash(configuration)
    if str(manifest.get("frozen_configuration_hash") or "") != expected_frozen_hash:
        raise ValueError(f"{manifest_path}: frozen configuration hash does not verify")
    if int(manifest.get("case_count") or 0) != len(rows):
        raise ValueError(f"{manifest_path}: case_count does not match {source_path}")
    artifact_hash = _source_artifact_hash(manifest, source_path.name)
    if artifact_hash is None:
        raise ValueError(f"{manifest_path}: source output SHA-256 is missing")
    actual_hash = runner.file_sha256(source_path)
    if artifact_hash != actual_hash:
        raise ValueError(f"{source_path}: SHA-256 does not match source manifest")
    status_counts = Counter(str(row.get("status") or "unknown") for row in rows)
    usable_decisions = sum(
        row.get("status") == "ok" and row.get("verdict") in {"pass", "fail"}
        for row in rows
    )
    expected_manifest_counts = {
        "usable_decisions": usable_decisions,
        "no_decision_rows": len(rows) - usable_decisions,
        "status_counts": dict(status_counts),
    }
    for field, expected in expected_manifest_counts.items():
        if manifest.get(field) != expected:
            raise ValueError(f"{manifest_path}: {field} does not match source rows")
    for row in rows:
        if "renormalization" in row:
            raise ValueError(f"{source_path}: refuses an already-renormalized row")
        status = str(row.get("status") or "unknown")
        verdict = str(row.get("verdict") or "")
        if (status == "ok") != (verdict in {"pass", "fail"}):
            raise ValueError(
                f"{source_path}: invalid status/verdict pairing for {row['case_id']}"
            )
        checks = {
            "judge_name": judge,
            "judge_model": configuration["model_id"],
            "judge_revision": configuration["revision"],
            "adapter": runner.JUDGES[judge].adapter,
            "prompt_version": source_prompt_version,
            "normalization_version": source_normalization_version,
            "configuration_hash": source_configuration_hash,
            "frozen_configuration_hash": expected_frozen_hash,
            "prompt_variant": expected_variant,
            "replicate_id": expected_replicate,
        }
        for field, expected in checks.items():
            if str(row.get(field) or "") != expected:
                raise ValueError(
                    f"{source_path}: {field} does not match directory/manifest metadata"
                )
        if "raw_output" not in row:
            raise ValueError(f"{source_path}: row is missing raw_output")
    return configuration, actual_hash, runner.file_sha256(manifest_path)


def renormalize_wave(
    *,
    judge: str,
    wave: str,
    source_path: Path,
    source_manifest_path: Path,
    output_path: Path,
    logical_output_path: Path | None = None,
) -> dict:
    rows = load_jsonl(source_path)
    source_manifest = load_json(source_manifest_path)
    source_configuration, source_output_hash, source_manifest_hash = (
        _validate_source_wave(
        judge=judge,
        wave=wave,
        source_path=source_path,
        manifest_path=source_manifest_path,
        rows=rows,
        manifest=source_manifest,
        )
    )

    source_configuration_hash = str(source_manifest["configuration_hash"])
    source_normalization_version = str(
        source_configuration.get("normalization_version") or "unknown"
    )
    configuration = deepcopy(source_configuration)
    configuration["source_normalization_version"] = source_normalization_version
    configuration["normalization_version"] = runner.NORMALIZATION_VERSION
    configuration["normalization_runner_sha256"] = runner.file_sha256(runner.__file__)
    configuration["renormalizer_sha256"] = runner.file_sha256(__file__)
    configuration_hash = runner.stable_hash(configuration)
    frozen_configuration_hash = runner.frozen_configuration_hash(configuration)
    threshold = int(configuration.get("prometheus_pass_threshold", 4))

    actions: Counter[str] = Counter()
    output_statuses: Counter[str] = Counter()
    output_rows: list[dict] = []
    for source_row in rows:
        raw_output = str(source_row.get("raw_output") or "")
        source_status = str(source_row.get("status") or "unknown")
        source_verdict = str(source_row.get("verdict") or "no_decision")

        if source_status in {"ok", "parse_error"}:
            parsed = runner.parse_judgment(
                raw_output,
                runner.JUDGES[judge],
                prometheus_pass_threshold=threshold,
            )
        else:
            parsed = runner.ParsedJudgment(
                verdict="no_decision",
                status=source_status,
                error=str(source_row.get("error") or "source generation was not usable"),
            )

        if source_status == "ok" and source_verdict in {"pass", "fail"}:
            if parsed.status == "ok" and parsed.verdict != source_verdict:
                raise ValueError(
                    f"{source_path}: parser would flip accepted verdict for "
                    f"{source_row['case_id']} ({source_verdict} -> {parsed.verdict})"
                )
            action = "unchanged" if parsed.status == "ok" else "invalidated_ambiguous"
        elif source_status == "parse_error" and parsed.status == "ok":
            action = "recovered"
        else:
            action = "unresolved"

        output_row = dict(source_row)
        output_row.update(
            {
                "normalization_version": runner.NORMALIZATION_VERSION,
                "configuration_hash": configuration_hash,
                "frozen_configuration_hash": frozen_configuration_hash,
                "verdict": parsed.verdict,
                "native_score": parsed.native_score,
                "rationale": parsed.rationale,
                "evidence": parsed.evidence,
                "status": parsed.status,
                "error": parsed.error,
                "renormalization": {
                    "schema_version": SCHEMA_VERSION,
                    "action": action,
                    "source_normalization_version": source_normalization_version,
                    "source_configuration_hash": source_configuration_hash,
                    "source_status": source_status,
                    "source_verdict": source_verdict,
                    "source_error": source_row.get("error"),
                },
            }
        )
        actions[action] += 1
        output_statuses[parsed.status] += 1
        output_rows.append(output_row)

    write_jsonl(output_path, output_rows)
    usable = sum(
        row["status"] == "ok" and row["verdict"] in {"pass", "fail"}
        for row in output_rows
    )
    no_decision = len(output_rows) - usable
    completed_at = utc_now()
    output_manifest_path = output_path.with_suffix(".manifest.json")
    output_manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "complete" if no_decision == 0 else "complete_with_errors",
        "completed_at": completed_at,
        "judge_name": judge,
        "wave": wave,
        "case_count": len(output_rows),
        "prompt_variant": configuration["prompt_variant"],
        "replicate_id": configuration["replicate_id"],
        "normalization_version": runner.NORMALIZATION_VERSION,
        "configuration": configuration,
        "configuration_hash": configuration_hash,
        "frozen_configuration_hash": frozen_configuration_hash,
        "source": {
            "output_file": str(source_path),
            "output_sha256": source_output_hash,
            "manifest_file": str(source_manifest_path),
            "manifest_sha256": source_manifest_hash,
            "normalization_version": source_normalization_version,
            "configuration_hash": source_configuration_hash,
        },
        "output_file": str(logical_output_path or output_path),
        "output_sha256": runner.file_sha256(output_path),
        "usable_decisions": usable,
        "no_decision_rows": no_decision,
        "status_counts": dict(sorted(output_statuses.items())),
        "normalization_actions": dict(sorted(actions.items())),
    }
    output_manifest_path.write_text(
        json.dumps(output_manifest, indent=2) + "\n", encoding="utf-8"
    )
    return output_manifest


def _write_summary(root: Path, summary: dict) -> None:
    (root / "renormalization_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    fieldnames = [
        "judge",
        "wave",
        "case_count",
        "unchanged",
        "recovered",
        "invalidated_ambiguous",
        "unresolved",
        "usable_decisions",
        "no_decision_rows",
    ]
    with (root / "renormalization_summary.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for wave in summary["waves"]:
            actions = wave["normalization_actions"]
            writer.writerow(
                {
                    "judge": wave["judge_name"],
                    "wave": wave["wave"],
                    "case_count": wave["case_count"],
                    "unchanged": actions.get("unchanged", 0),
                    "recovered": actions.get("recovered", 0),
                    "invalidated_ambiguous": actions.get(
                        "invalidated_ambiguous", 0
                    ),
                    "unresolved": actions.get("unresolved", 0),
                    "usable_decisions": wave["usable_decisions"],
                    "no_decision_rows": wave["no_decision_rows"],
                }
            )


def renormalize_tree(source_root: Path, output_root: Path) -> dict:
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    if not source_root.is_dir():
        raise ValueError(f"source result root does not exist: {source_root}")
    if output_root.exists():
        raise FileExistsError(f"output root already exists: {output_root}")
    if source_root == output_root or source_root in output_root.parents:
        raise ValueError("output root must not be inside the source result root")

    waves = discover_waves(source_root)
    output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix=f".{output_root.name}.", dir=output_root.parent)
    )
    try:
        manifests: list[dict] = []
        for judge, wave, source_path, source_manifest_path in waves:
            output_path = temporary_root / judge / wave / f"{wave}.jsonl"
            logical_output_path = output_root / judge / wave / f"{wave}.jsonl"
            manifests.append(
                renormalize_wave(
                    judge=judge,
                    wave=wave,
                    source_path=source_path,
                    source_manifest_path=source_manifest_path,
                    output_path=output_path,
                    logical_output_path=logical_output_path,
                )
            )

        action_totals: Counter[str] = Counter()
        status_totals: Counter[str] = Counter()
        for manifest in manifests:
            action_totals.update(manifest["normalization_actions"])
            status_totals.update(manifest["status_counts"])
        summary = {
            "schema_version": SCHEMA_VERSION,
            "completed_at": utc_now(),
            "source_root": str(source_root),
            "output_root": str(output_root),
            "normalization_version": runner.NORMALIZATION_VERSION,
            "wave_count": len(manifests),
            "case_rows": sum(item["case_count"] for item in manifests),
            "normalization_actions": dict(sorted(action_totals.items())),
            "status_counts": dict(sorted(status_totals.items())),
            "waves": manifests,
        }
        _write_summary(temporary_root, summary)
        for manifest in manifests:
            source = manifest["source"]
            if runner.file_sha256(source["output_file"]) != source["output_sha256"]:
                raise ValueError("source output changed during renormalization")
            if (
                runner.file_sha256(source["manifest_file"])
                != source["manifest_sha256"]
            ):
                raise ValueError("source manifest changed during renormalization")
        temporary_root.replace(output_root)
        return summary
    except BaseException:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("output_root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = renormalize_tree(args.source_root, args.output_root)
    except (FileExistsError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    actions = summary["normalization_actions"]
    print(
        f"Wrote {summary['case_rows']} rows to {args.output_root}; "
        f"recovered={actions.get('recovered', 0)}, "
        f"invalidated_ambiguous={actions.get('invalidated_ambiguous', 0)}, "
        f"unresolved={actions.get('unresolved', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
