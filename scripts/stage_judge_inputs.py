"""Stage the judge-input table from usable tutor responses + the rubric ItemBank.

This is the judge-INDEPENDENT join that makes grading a drop-in once the judge is
frozen. It reads the usable-model manifest written by
``scripts/validate_responses.py`` and the rubric/scenario ``ItemBank`` from
``tutor_cat.dataio.load_bank``, then emits one JSONL row per gradable *cell*
(model x scenario x criterion) with everything the judge will need.

Each output row::

    {
      "model":           "<HF repo id, e.g. Qwen/Qwen2.5-7B-Instruct>",
      "scenario":        "tb_0001",
      "criterion_id":    "tb_0001_c01",
      "rubric":          "<criterion text sent to the judge>",
      "response":        "<the tutor Output for this (model, scenario)>",
      "auto_fail":       0 | 1,
      "auto_fail_reason":"" | "finish_reason_error" | "empty_output" | ...
    }

===================================================================
AUTO-FAIL POLICY  (what is / is NOT sent to the judge)
===================================================================
The judge only sees cells with ``auto_fail == 0``. A cell is auto-failed
(``auto_fail == 1``, scored y=0 downstream WITHOUT a judge call) when the tutor
output cannot be meaningfully graded:

  * finish_reason_error  -> Finish Reason == "error" (generation/load failure;
                            Output is empty, Generation Params == {}).
  * empty_output         -> Output is blank/whitespace-only even though the row
                            did not error (e.g. Finish Reason == "stop" but the
                            model emitted nothing).
  * missing_response     -> the usable model's shard has no row for this scenario
                            at all (keeps the response matrix rectangular).

DEFAULT (recommended) policy:
  * error / empty / missing            -> auto_fail = 1   (never judged)
  * NON-empty, length-capped outputs   -> auto_fail = 0   (GRADEABLE)
    A ``Finish Reason == "length"`` output is truncated but still contains real
    tutoring content, so it is graded normally by default. Truncation is a
    quality signal for the judge/rubric, not a reason to skip grading.

OPT-IN policy  (``--auto-fail-degenerate``):
  Also auto-fail NON-empty outputs that look degenerate (pathological decoding
  loops that are not worth a judge call). Heuristic (all thresholds are module
  constants; a cell trips if ANY sub-rule fires), applied only to outputs with
  >= DEGEN_MIN_TOKENS tokens or >= DEGEN_MIN_CHARS chars:
    - low_lexical_diversity : unique_tokens / total_tokens < DEGEN_UNIQUE_RATIO
    - dominant_token        : the single most frequent whitespace token accounts
                              for > DEGEN_DOMINANT_TOKEN_RATIO of all tokens
    - long_char_run         : some character repeats >= DEGEN_CHAR_RUN times in a
                              row (e.g. "aaaaaa..." or "======...")
  Reason is recorded as ``degenerate:<sub-rule>``. This heuristic is deliberately
  conservative (it targets clear decoding pathologies, not merely repetitive but
  legitimate answers); leave it OFF unless you have inspected its hits.

===================================================================
Downstream shape (for tutor_cat.mirt)
-------------------------------------------------------------------
The response matrix MIRT consumes is rows = usable models, cols = criteria
(pass/fail). This stager fixes the deterministic ordering used to build it:
  * columns: criteria in (scenario_id, criterion_id) order -- exactly
    ``ItemBank.rubrics_for(sid)`` (sorted by criterion_id) over sorted scenarios.
  * rows:    usable models, sorted.
Both orderings, plus counts, are written to the staging manifest so matrix
assembly after grading is unambiguous.

Usage
-----
.. code-block:: powershell

    python scripts/stage_judge_inputs.py
    # opt into degenerate auto-fail:
    python scripts/stage_judge_inputs.py --auto-fail-degenerate

Pure standard library + tutor_cat. Deterministic. No network.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tutor_cat.dataio import load_bank  # noqa: E402

DEFAULT_MANIFEST = ROOT / "tutorbench-responses" / "_response_manifest.json"
DEFAULT_RESPONSES_DIR = ROOT / "tutorbench-responses"
DEFAULT_SCENARIOS = ROOT / "data" / "scenarios.jsonl"
DEFAULT_RUBRICS = ROOT / "data" / "rubrics_qmatrix_final.jsonl"
DEFAULT_OUT = ROOT / "staging" / "judge_inputs.jsonl"
DEFAULT_OUT_MANIFEST = ROOT / "staging" / "judge_inputs_manifest.json"

ERROR_FINISH = "error"

# --- degeneracy heuristic thresholds (opt-in via --auto-fail-degenerate) ---
DEGEN_MIN_TOKENS = 30          # only inspect outputs at least this many tokens...
DEGEN_MIN_CHARS = 200          # ...or this many characters
DEGEN_UNIQUE_RATIO = 0.10      # unique/total token ratio below this = degenerate
DEGEN_DOMINANT_TOKEN_RATIO = 0.50  # one token > this fraction of all tokens
DEGEN_CHAR_RUN = 50            # a single char repeated >= this many times
_CHAR_RUN_RE = re.compile(r"(.)\1{%d,}" % (DEGEN_CHAR_RUN - 1), flags=re.DOTALL)


def is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def degeneracy_reason(output: str) -> "str | None":
    """Return a sub-rule name if ``output`` looks like a decoding pathology, else None.

    Conservative by design: only long outputs are inspected, and the rules target
    clear loops (near-zero lexical diversity, one dominating token, long char runs)
    rather than merely repetitive-but-valid answers. See module docstring.
    """
    if output is None:
        return None
    n_chars = len(output)
    tokens = output.split()
    n_tokens = len(tokens)
    if n_tokens < DEGEN_MIN_TOKENS and n_chars < DEGEN_MIN_CHARS:
        return None

    if _CHAR_RUN_RE.search(output):
        return "long_char_run"

    if n_tokens >= DEGEN_MIN_TOKENS:
        unique_ratio = len(set(tokens)) / n_tokens
        if unique_ratio < DEGEN_UNIQUE_RATIO:
            return "low_lexical_diversity"
        top_count = Counter(tokens).most_common(1)[0][1]
        if top_count / n_tokens > DEGEN_DOMINANT_TOKEN_RATIO:
            return "dominant_token"

    return None


def classify_cell(rec: "dict | None", check_degenerate: bool) -> "tuple[int, str, str]":
    """Return (auto_fail, reason, output) for one (model, scenario) response row.

    ``rec is None`` means the scenario is absent from the model's shard.
    """
    if rec is None:
        return 1, "missing_response", ""

    output = rec.get("Output")
    output_str = output if isinstance(output, str) else ("" if output is None else str(output))
    finish = rec.get("Finish Reason")

    if finish == ERROR_FINISH:
        return 1, "finish_reason_error", output_str
    if is_blank(output_str):
        return 1, "empty_output", output_str
    if check_degenerate:
        sub = degeneracy_reason(output_str)
        if sub:
            return 1, f"degenerate:{sub}", output_str
    return 0, "", output_str


def load_response_index(path: Path) -> "tuple[dict[str, dict], dict[str, int]]":
    """Map Scenario -> record for one shard (first occurrence wins).

    Returns (index, duplicate_counts) where duplicate_counts[sid] is the number of
    extra rows seen for a scenario beyond the first.
    """
    index: dict[str, dict] = {}
    dupes: dict[str, int] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            sid = rec.get("Scenario")
            if not sid:
                continue
            if sid in index:
                dupes[sid] = dupes.get(sid, 0) + 1
                continue
            index[sid] = rec
    return index, dupes


def criteria_for(bank, scenario_id: str) -> list:
    """Rubric objects for a scenario in fixed criterion_id order.

    Reuses ``ItemBank.rubrics_for``; falls back to filtering present criterion_ids
    only if a criterion has no rubric (a load-validation error), so one bad
    scenario cannot abort the whole staging run.
    """
    try:
        return bank.rubrics_for(scenario_id)
    except KeyError:
        scenario = bank.scenarios[scenario_id]
        present = [bank.rubrics[cid] for cid in scenario.criterion_ids if cid in bank.rubrics]
        return sorted(present, key=lambda r: r.criterion_id)


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                    help="usable-model manifest from validate_responses.py")
    ap.add_argument("--responses-dir", type=Path, default=DEFAULT_RESPONSES_DIR)
    ap.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    ap.add_argument("--rubrics", type=Path, default=DEFAULT_RUBRICS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--out-manifest", type=Path, default=DEFAULT_OUT_MANIFEST)
    ap.add_argument("--auto-fail-degenerate", action="store_true",
                    help="also auto-fail non-empty outputs flagged by the degeneracy heuristic")
    args = ap.parse_args(argv)

    for p, label in ((args.manifest, "manifest"), (args.scenarios, "scenarios"),
                     (args.rubrics, "rubrics")):
        if not p.is_file():
            print(f"ERROR: {label} file not found: {p}", file=sys.stderr)
            if label == "manifest":
                print("       run scripts/validate_responses.py first.", file=sys.stderr)
            return 2
    if not args.responses_dir.is_dir():
        print(f"ERROR: responses dir not found: {args.responses_dir}", file=sys.stderr)
        return 2

    with args.manifest.open(encoding="utf-8") as f:
        manifest = json.load(f)
    usable_models = sorted(manifest.get("usable_models", []))
    model_files = manifest.get("model_files", {})
    if not usable_models:
        print("ERROR: manifest lists no usable_models; nothing to stage.", file=sys.stderr)
        return 2

    bank, report = load_bank(args.scenarios, args.rubrics)
    if report.errors:
        print(f"WARNING: ItemBank load reported {len(report.errors)} error(s); "
              f"first few: {report.errors[:3]}", file=sys.stderr)
    if report.warnings:
        print(f"note: ItemBank load reported {len(report.warnings)} warning(s) (non-fatal).",
              file=sys.stderr)

    scenario_ids = sorted(bank.scenarios)

    # Fixed column order (matches tutor_cat.mirt matrix columns after grading).
    ordered_criterion_ids: list[str] = []
    criteria_by_scenario: dict[str, list] = {}
    for sid in scenario_ids:
        crits = criteria_for(bank, sid)
        criteria_by_scenario[sid] = crits
        ordered_criterion_ids.extend(c.criterion_id for c in crits)
    n_criteria = len(ordered_criterion_ids)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out_manifest.parent.mkdir(parents=True, exist_ok=True)

    total_cells = 0
    gradeable_cells = 0
    auto_fail_cells = 0
    reason_counts: Counter[str] = Counter()
    per_model_dupes: dict[str, dict] = {}
    missing_models: list[str] = []

    with args.out.open("w", encoding="utf-8") as out:
        for model in usable_models:
            fname = model_files.get(model)
            fpath = (args.responses_dir / fname) if fname else None
            if not fpath or not fpath.is_file():
                # Should not happen for a usable model, but stay rectangular:
                # emit every cell as missing_response so the matrix keeps its row.
                missing_models.append(model)
                index, dupes = {}, {}
            else:
                index, dupes = load_response_index(fpath)
            if dupes:
                per_model_dupes[model] = dupes

            for sid in scenario_ids:
                rec = index.get(sid)
                auto_fail, reason, response = classify_cell(rec, args.auto_fail_degenerate)
                for crit in criteria_by_scenario[sid]:
                    row = {
                        "model": model,
                        "scenario": sid,
                        "criterion_id": crit.criterion_id,
                        "rubric": crit.criterion,
                        "response": response,
                        "auto_fail": auto_fail,
                        "auto_fail_reason": reason,
                    }
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    total_cells += 1
                    if auto_fail:
                        auto_fail_cells += 1
                        reason_counts[reason] += 1
                    else:
                        gradeable_cells += 1

    out_manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest": str(args.manifest.relative_to(ROOT)) if args.manifest.is_relative_to(ROOT) else str(args.manifest),
        "rubrics_path": str(args.rubrics.relative_to(ROOT)) if args.rubrics.is_relative_to(ROOT) else str(args.rubrics),
        "scenarios_path": str(args.scenarios.relative_to(ROOT)) if args.scenarios.is_relative_to(ROOT) else str(args.scenarios),
        "out": str(args.out.relative_to(ROOT)) if args.out.is_relative_to(ROOT) else str(args.out),
        "auto_fail_degenerate": bool(args.auto_fail_degenerate),
        "n_usable_models": len(usable_models),
        "n_scenarios": len(scenario_ids),
        "n_criteria": n_criteria,
        "matrix_dims": {"rows_models": len(usable_models), "cols_criteria": n_criteria},
        "total_cells": total_cells,
        "gradeable_cells": gradeable_cells,
        "auto_fail_cells": auto_fail_cells,
        "auto_fail_reason_counts": dict(reason_counts),
        "models": usable_models,
        "criterion_ids": ordered_criterion_ids,
        "models_missing_shard": missing_models,
        "per_model_duplicate_scenarios": per_model_dupes,
    }
    with args.out_manifest.open("w", encoding="utf-8") as f:
        json.dump(out_manifest, f, indent=2, ensure_ascii=False)

    print("=" * 70)
    print("Judge-input staging")
    print("=" * 70)
    print(f"usable models      : {len(usable_models)}")
    print(f"scenarios          : {len(scenario_ids)}")
    print(f"criteria (columns) : {n_criteria}")
    print(f"response matrix    : {len(usable_models)} models x {n_criteria} criteria "
          f"= {len(usable_models) * n_criteria} cells")
    print("-" * 70)
    print(f"total cells written: {total_cells}")
    print(f"  gradeable        : {gradeable_cells}")
    print(f"  auto-fail        : {auto_fail_cells}")
    for reason, count in sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"      {reason:24s}: {count}")
    if args.auto_fail_degenerate:
        print("  (degenerate auto-fail: ENABLED)")
    else:
        print("  (degenerate auto-fail: disabled; use --auto-fail-degenerate to enable)")
    if missing_models:
        print(f"WARNING: {len(missing_models)} usable model(s) had no readable shard "
              f"(all cells -> missing_response): {missing_models[:5]}")
    if per_model_dupes:
        print(f"note: {len(per_model_dupes)} model(s) had intra-shard duplicate Scenario rows "
              f"(first row kept).")
    print()
    print(f"wrote judge inputs -> {args.out}")
    print(f"wrote staging manifest -> {args.out_manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
