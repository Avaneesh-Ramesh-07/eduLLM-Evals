"""Validate the AWS-produced tutor responses under ``tutorbench-responses/*.jsonl``.

This is judge-INDEPENDENT plumbing. It scans every per-model response shard,
computes exact counts the earlier shell-less triage could not sum, and writes two
JSON artifacts plus a printed summary:

* ``tutorbench-responses/_validation_report.json`` -- the full, per-file detail
  (record counts, Finish Reason breakdown, Issue/Truncated/empty-Output counts,
  intra-file duplicate ``Scenario`` detection, and per-model scenario coverage
  vs ``data/scenarios.jsonl``).
* ``tutorbench-responses/_response_manifest.json`` -- the machine-readable
  ``usable_models`` / ``dead_models`` lists (plus a model -> filename map)
  consumed by ``scripts/stage_judge_inputs.py``.

Schema (Model Output records, Title-Case keys, emitted by
``tutor_cat/respgen/records.py``)::

    Benchmark, Scenario, Model, Model Revision, Chat Template Applied,
    Rendered Prompt, Generation Params{...}, Max Model Len, Prompt Tokens,
    Output Tokens, Finish Reason (stop|length|error), Truncated, Latency (s),
    Output, Issue, Issue Description

Error-row nuance (handled here): on an error cell ``Finish Reason == "error"``,
``Generation Params == {}``, ``Output``/``Rendered Prompt``/``Model Revision``
are ``""`` and token counts are 0.

USABLE vs DEAD
--------------
A row is *gradeable-capable* when ``Finish Reason != "error"`` AND ``Output`` is
non-blank. A model is **DEAD** when it has zero gradeable-capable rows (i.e. every
row errored or produced empty Output); otherwise it is **USABLE**. Only usable
models are staged for the judge.

Usage
-----
.. code-block:: powershell

    python scripts/validate_responses.py
    # optional overrides:
    python scripts/validate_responses.py --responses-dir tutorbench-responses `
        --scenarios data/scenarios.jsonl

Pure standard library. Deterministic. No network.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_RESPONSES_DIR = ROOT / "tutorbench-responses"
DEFAULT_SCENARIOS = ROOT / "data" / "scenarios.jsonl"
DEFAULT_MANIFEST = DEFAULT_RESPONSES_DIR / "_response_manifest.json"
DEFAULT_REPORT = DEFAULT_RESPONSES_DIR / "_validation_report.json"

EXPECTED_MODELS = 97
EXPECTED_SCENARIOS = 662
FINISH_REASONS = ("stop", "length", "error")

ERROR_FINISH = "error"


def is_blank(value: object) -> bool:
    """True for None or whitespace-only strings (Output is always a str per schema)."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def read_jsonl(path: Path) -> "list[tuple[int, dict]]":
    """Return (lineno, obj) for each non-empty line; malformed lines are skipped
    and surfaced separately by the caller via ``parse_errors``."""
    rows: list[tuple[int, dict]] = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            rows.append((lineno, json.loads(line)))
    return rows


def load_reference_scenarios(path: Path) -> "set[str]":
    """Scenario IDs from ``data/scenarios.jsonl`` (id set only, not a rubric parser)."""
    ids: set[str] = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            sid = obj.get("scenario_id")
            if sid:
                ids.add(sid)
    return ids


def scan_file(path: Path, reference: "set[str]") -> dict:
    """Compute every per-file statistic for one model shard."""
    finish = Counter()
    model_counter: Counter[str] = Counter()
    scenario_counter: Counter[str] = Counter()
    records = 0
    issue_1 = 0
    truncated_1 = 0
    empty_output = 0
    gen_params_empty = 0
    gradeable_capable = 0
    parse_errors: list[int] = []

    try:
        rows = read_jsonl(path)
    except json.JSONDecodeError:
        # Fall back to line-by-line so one bad line does not sink the whole file.
        rows = []
        with path.open(encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append((lineno, json.loads(line)))
                except json.JSONDecodeError:
                    parse_errors.append(lineno)

    for _lineno, rec in rows:
        records += 1

        model = rec.get("Model") or ""
        if model:
            model_counter[model] += 1

        sid = rec.get("Scenario") or ""
        if sid:
            scenario_counter[sid] += 1

        fr = rec.get("Finish Reason")
        finish[fr if fr in FINISH_REASONS else f"other:{fr}"] += 1

        if int(rec.get("Issue") or 0) == 1:
            issue_1 += 1
        if int(rec.get("Truncated") or 0) == 1:
            truncated_1 += 1

        output = rec.get("Output")
        output_blank = is_blank(output)
        if output_blank:
            empty_output += 1

        # Error-row nuance: Generation Params is {} on error cells.
        if not rec.get("Generation Params"):
            gen_params_empty += 1

        if fr != ERROR_FINISH and not output_blank:
            gradeable_capable += 1

    seen_scenarios = set(scenario_counter)
    duplicates = {s: c for s, c in scenario_counter.items() if c > 1}
    missing = sorted(reference - seen_scenarios)
    unknown = sorted(seen_scenarios - reference)

    # File's canonical model id: the most common Model value, else the file stem.
    if model_counter:
        model_id = model_counter.most_common(1)[0][0]
    else:
        model_id = path.stem
    multiple_models = sorted(m for m in model_counter if m != model_id)

    status = "usable" if gradeable_capable > 0 else "dead"

    return {
        "file": path.name,
        "model": model_id,
        "records": records,
        "finish_reason": {r: finish.get(r, 0) for r in FINISH_REASONS},
        "finish_reason_other": {k: v for k, v in finish.items() if k not in FINISH_REASONS},
        "issue_1": issue_1,
        "truncated_1": truncated_1,
        "empty_output": empty_output,
        "gen_params_empty": gen_params_empty,
        "gradeable_capable_rows": gradeable_capable,
        "distinct_scenarios": len(seen_scenarios),
        "duplicate_scenarios": duplicates,
        "missing_scenarios": missing,
        "unknown_scenarios": unknown,
        "multiple_models": multiple_models,
        "parse_errors": parse_errors,
        "status": status,
    }


def build_report(
    responses_dir: Path,
    scenarios_path: Path,
    expected_models: int = EXPECTED_MODELS,
    expected_scenarios: int = EXPECTED_SCENARIOS,
) -> dict:
    reference = load_reference_scenarios(scenarios_path)
    files = sorted(p for p in responses_dir.glob("*.jsonl") if not p.name.startswith("_"))

    per_file = [scan_file(p, reference) for p in files]

    total_records = 0
    total_finish = Counter()
    total_issue = 0
    total_truncated = 0
    total_empty = 0
    distinct_models: set[str] = set()
    usable_models: list[str] = []
    dead_models: list[str] = []
    model_files: dict[str, str] = {}
    # Exact union of scenarios seen anywhere, reconstructed from each file's
    # coverage: seen = (reference - missing) | unknown. No re-reading needed.
    distinct_scenarios: set[str] = set()

    for f in per_file:
        total_records += f["records"]
        for r in FINISH_REASONS:
            total_finish[r] += f["finish_reason"][r]
        for k, v in f["finish_reason_other"].items():
            total_finish[k] += v
        total_issue += f["issue_1"]
        total_truncated += f["truncated_1"]
        total_empty += f["empty_output"]
        distinct_models.add(f["model"])
        model_files[f["model"]] = f["file"]
        distinct_scenarios |= (reference - set(f["missing_scenarios"])) | set(f["unknown_scenarios"])
        if f["status"] == "usable":
            usable_models.append(f["model"])
        else:
            dead_models.append(f["model"])

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "responses_dir": str(responses_dir.relative_to(ROOT)) if responses_dir.is_relative_to(ROOT) else str(responses_dir),
        "scenarios_path": str(scenarios_path.relative_to(ROOT)) if scenarios_path.is_relative_to(ROOT) else str(scenarios_path),
        "expected_models": expected_models,
        "expected_scenarios": expected_scenarios,
        "reference_scenarios": len(reference),
        "n_files": len(files),
        "distinct_models": len(distinct_models),
        "distinct_scenarios": len(distinct_scenarios),
        "totals": {
            "records": total_records,
            "finish_reason": {r: total_finish.get(r, 0) for r in FINISH_REASONS},
            "finish_reason_other": {k: v for k, v in total_finish.items() if k not in FINISH_REASONS},
            "issue_1": total_issue,
            "truncated_1": total_truncated,
            "empty_output": total_empty,
            "usable_models": len(usable_models),
            "dead_models": len(dead_models),
        },
        "files": per_file,
    }

    manifest = {
        "generated_at": report["generated_at"],
        "responses_dir": report["responses_dir"],
        "scenarios_path": report["scenarios_path"],
        "expected_scenarios": expected_scenarios,
        "n_files": len(files),
        "n_usable": len(usable_models),
        "n_dead": len(dead_models),
        "usable_models": sorted(usable_models),
        "dead_models": sorted(dead_models),
        "model_files": model_files,
    }

    return {"report": report, "manifest": manifest}


def _fmt_missing(missing: "list[str]") -> str:
    if not missing:
        return "-"
    if len(missing) <= 3:
        return ",".join(missing)
    return f"{len(missing)} missing (e.g. {missing[0]}..{missing[-1]})"


def print_summary(report: dict, manifest: dict) -> None:
    r = report
    print("=" * 78)
    print("TutorBench response validation")
    print("=" * 78)
    print(f"responses dir     : {r['responses_dir']}")
    print(f"files scanned     : {r['n_files']}  (expected {r['expected_models']})")
    print(f"distinct models   : {r['distinct_models']}  (expected {r['expected_models']})")
    print(f"distinct scenarios: {r['distinct_scenarios']}  (expected {r['expected_scenarios']}, "
          f"reference has {r['reference_scenarios']})")
    t = r["totals"]
    print(f"total records     : {t['records']}")
    print(f"finish reason     : stop={t['finish_reason']['stop']}  "
          f"length={t['finish_reason']['length']}  error={t['finish_reason']['error']}"
          + (f"  other={t['finish_reason_other']}" if t["finish_reason_other"] else ""))
    print(f"Issue==1          : {t['issue_1']}")
    print(f"Truncated==1      : {t['truncated_1']}")
    print(f"empty/blank Output: {t['empty_output']}")
    print(f"USABLE models     : {t['usable_models']}")
    print(f"DEAD models       : {t['dead_models']}")
    print("-" * 78)
    print(f"{'model':44s} {'rec':>4s} {'stop':>5s} {'len':>5s} {'err':>5s} {'empty':>6s} {'status':>7s}")
    print("-" * 78)
    for f in r["files"]:
        name = f["model"]
        name = name if len(name) <= 44 else name[:41] + "..."
        flags = []
        if f["records"] != r["expected_scenarios"]:
            flags.append(f"records={f['records']}")
        if f["duplicate_scenarios"]:
            flags.append(f"dupes={len(f['duplicate_scenarios'])}")
        if f["missing_scenarios"]:
            flags.append(_fmt_missing(f["missing_scenarios"]))
        if f["unknown_scenarios"]:
            flags.append(f"unknown={len(f['unknown_scenarios'])}")
        if f["multiple_models"]:
            flags.append(f"multi-model={len(f['multiple_models'])}")
        if f["parse_errors"]:
            flags.append(f"parse_err={len(f['parse_errors'])}")
        line = (
            f"{name:44s} {f['records']:>4d} {f['finish_reason']['stop']:>5d} "
            f"{f['finish_reason']['length']:>5d} {f['finish_reason']['error']:>5d} "
            f"{f['empty_output']:>6d} {f['status']:>7s}"
        )
        if flags:
            line += "  !! " + "; ".join(flags)
        print(line)
    print("-" * 78)
    if r["distinct_models"] != r["expected_models"]:
        print(f"NOTE: distinct model count {r['distinct_models']} != expected {r['expected_models']}.")
    if r["distinct_scenarios"] != r["expected_scenarios"]:
        print(f"NOTE: distinct scenario count {r['distinct_scenarios']} != expected {r['expected_scenarios']}.")
    print(f"usable models -> {manifest['n_usable']}; dead models -> {manifest['n_dead']}")


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--responses-dir", type=Path, default=DEFAULT_RESPONSES_DIR)
    ap.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--expected-models", type=int, default=EXPECTED_MODELS)
    ap.add_argument("--expected-scenarios", type=int, default=EXPECTED_SCENARIOS)
    args = ap.parse_args(argv)

    if not args.responses_dir.is_dir():
        print(f"ERROR: responses dir not found: {args.responses_dir}", file=sys.stderr)
        return 2
    if not args.scenarios.is_file():
        print(f"ERROR: scenarios file not found: {args.scenarios}", file=sys.stderr)
        return 2

    built = build_report(
        args.responses_dir, args.scenarios, args.expected_models, args.expected_scenarios
    )
    report, manifest = built["report"], built["manifest"]

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    with args.manifest.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print_summary(report, manifest)
    print()
    print(f"wrote report   -> {args.report}")
    print(f"wrote manifest -> {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
