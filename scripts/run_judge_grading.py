"""Bridge + ingestion layer between our calibration staging and the teammate's
FROZEN judge runner, producing the MIRT response matrix.

The judge is graded by the teammate's canonical runner
``aws_judge_handoff/scripts/run_judge_validation.py`` (subcommand ``run``); we do
NOT reimplement his adapters / normalization / prompt policy here. This script:

  1. ``build-cases``  : convert our staged calibration inputs
     ``staging/judge_inputs.jsonl`` (from ``scripts/stage_judge_inputs.py``) into
     the teammate's EXACT blinded CASE schema (``staging/cases.jsonl``) so his
     ``run`` can grade the full fleet, plus a PRIVATE, non-blinded side-car
     ``staging/cases_index.jsonl`` that maps each ``case_id`` back to our
     ``(model, scenario, criterion)`` -- because ``candidate_model`` is a
     FORBIDDEN field in his case schema (the judge is blinded to tutor identity).

  2. ``grade --mode ingest-verdicts`` (PRIMARY): read the verdict JSONL(s) his
     ``run`` emits, auto-fail ``no_decision``/unscorable cells (y = 0, reason
     recorded), and assemble the models x criteria pass/fail response matrix in
     the FROZEN order from ``staging/judge_inputs_manifest.json`` (drop-in for the
     MIRT calibration; see ``tutor_cat/mirt.py`` + ``tutor_cat/schemas.py``).
     Provenance (judge model+revision, prompt/normalization/evidence-policy
     versions, frozen-config hash) is copied from the verdict rows and
     cross-checked against ``judge_frozen.yaml``.

  3. ``grade --mode call-judge`` (FALLBACK): a thin local smoke test that grades
     with ``tutor_cat.judge.OpenAICompatibleJudge`` against an OpenAI-compatible
     endpoint. Not the calibration path -- use it only to sanity-check plumbing.

End-to-end (see the schema-reconciliation note in the final report):

    python scripts/validate_responses.py
    python scripts/stage_judge_inputs.py
    python scripts/run_judge_grading.py build-cases            # -> staging/cases.jsonl (+ index)
    # ... ship staging/cases.jsonl to the GPU box as the teammate bundle's
    #     inputs/judge_cases.blinded.jsonl, then run the FROZEN judge:
    python aws_judge_handoff/scripts/run_judge_validation.py run \
        --cases inputs/judge_cases.blinded.jsonl --judge qwen \
        --output outputs/qwen/canonical_r1.jsonl --backend vllm \
        --prompt-variant canonical --replicate-id r1 --resume
    # ... bring the verdict JSONL back, then:
    python scripts/run_judge_grading.py grade --mode ingest-verdicts \
        --ingest-file outputs/qwen/canonical_r1.jsonl
    # -> staging/response_matrix.csv / .npy / _manifest.json

Pure existing deps (stdlib + numpy + pyyaml; tutor_cat). Deterministic. No network
(except the optional call-judge smoke path).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tutor_cat.dataio import load_bank  # noqa: E402
from tutor_cat.judge import (  # noqa: E402
    RESULT_PASS_THRESHOLD_DEFAULT,
    OpenAICompatibleJudge,
)
from tutor_cat.schemas import Rubric, Scenario  # noqa: E402

DEFAULT_STAGING = ROOT / "staging"
DEFAULT_JUDGE_CONFIG = ROOT / "judge_frozen.yaml"
DEFAULT_APP_CONFIG = ROOT / "config.yaml"
TEAMMATE_RUNNER = ROOT / "aws_judge_handoff" / "scripts" / "run_judge_validation.py"

JUDGE_INPUTS_NAME = "judge_inputs.jsonl"
JUDGE_INPUTS_MANIFEST_NAME = "judge_inputs_manifest.json"
CASES_NAME = "cases.jsonl"
CASES_INDEX_NAME = "cases_index.jsonl"
VERDICTS_NAME = "verdicts.jsonl"
MATRIX_CSV_NAME = "response_matrix.csv"
MATRIX_NPY_NAME = "response_matrix.npy"
MATRIX_MANIFEST_NAME = "response_matrix_manifest.json"

# Provenance fields we copy verbatim from the teammate's verdict rows.
PROVENANCE_FIELDS = (
    "judge_name",
    "judge_model",
    "judge_revision",
    "adapter",
    "prompt_version",
    "normalization_version",
    "evidence_policy_version",
    "prompt_variant",
    "replicate_id",
    "configuration_hash",
    "frozen_configuration_hash",
)


# =====================================================================
# case_id / response_id derivation (shared by build-cases and ingest)
# =====================================================================
# The teammate's case schema FORBIDS candidate_model, so the (model, scenario)
# identity must live only in a BLINDED, opaque response_id. We derive it
# deterministically so build-cases and ingest agree without extra state, and so
# resume is stable. The private mapping back to the model is kept in the side-car
# index (never shipped to the judge).
def response_id_for(model: str, scenario_id: str) -> str:
    digest = hashlib.sha1(f"{model}\x1f{scenario_id}".encode("utf-8")).hexdigest()
    return f"resp_{digest[:16]}"


def case_id_for(model: str, scenario_id: str, criterion_id: str) -> str:
    return f"{response_id_for(model, scenario_id)}__{criterion_id}"


# =====================================================================
# Frozen judge config (expected provenance + call-judge smoke settings)
# =====================================================================
@dataclass
class FrozenJudgeConfig:
    judge_name: str = "qwen"
    model: str = ""
    hf_revision: str = ""
    adapter: str = ""
    prompt_version: str = ""
    normalization_version: str = ""
    evidence_policy_version: str = ""
    prompt_variant: str = "canonical"
    replicate_id: str = "r1"
    base_url: str = "http://localhost:8000/v1"
    api_key_env: str = "JUDGE_API_KEY"
    temperature: float = 0.0
    max_tokens: int = 1024
    seed: int = 42
    result_pass_threshold: int = RESULT_PASS_THRESHOLD_DEFAULT

    @classmethod
    def from_dict(cls, block: dict[str, Any]) -> "FrozenJudgeConfig":
        return cls(
            judge_name=str(block.get("judge_name", "qwen")),
            model=str(block.get("model", "")),
            hf_revision=str(block.get("hf_revision", "")),
            adapter=str(block.get("adapter", "")),
            prompt_version=str(block.get("prompt_version", "")),
            normalization_version=str(block.get("normalization_version", "")),
            evidence_policy_version=str(block.get("evidence_policy_version", "")),
            prompt_variant=str(block.get("prompt_variant", "canonical")),
            replicate_id=str(block.get("replicate_id", "r1")),
            base_url=str(block.get("base_url", "http://localhost:8000/v1")),
            api_key_env=str(block.get("api_key_env", "JUDGE_API_KEY")),
            temperature=float(block.get("temperature", 0.0)),
            max_tokens=int(block.get("max_tokens", 1024)),
            seed=int(block.get("seed", 42)),
            result_pass_threshold=int(
                block.get("result_pass_threshold", RESULT_PASS_THRESHOLD_DEFAULT)
            ),
        )

    def expected_provenance(self) -> dict[str, Any]:
        return {
            "judge_name": self.judge_name,
            "judge_model": self.model,
            "judge_revision": self.hf_revision,
            "adapter": self.adapter,
            "prompt_version": self.prompt_version,
            "normalization_version": self.normalization_version,
            "evidence_policy_version": self.evidence_policy_version,
            "prompt_variant": self.prompt_variant,
            "replicate_id": self.replicate_id,
        }


def load_frozen_judge_config(path: Path | None) -> FrozenJudgeConfig:
    src = path or DEFAULT_JUDGE_CONFIG
    if not src.is_file():
        if DEFAULT_APP_CONFIG.is_file():
            with DEFAULT_APP_CONFIG.open(encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            block = cfg.get("judge")
            if block:
                return FrozenJudgeConfig.from_dict(block)
        raise FileNotFoundError(f"judge config not found: {src}")
    with src.open(encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
    block = loaded.get("judge", loaded)
    return FrozenJudgeConfig.from_dict(block)


# =====================================================================
# IO helpers
# =====================================================================
CellKey = tuple[str, str, str]  # (model, scenario, criterion_id)


def iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def _staged_key(row: dict) -> CellKey:
    return (str(row["model"]), str(row["scenario"]), str(row["criterion_id"]))


def _resolve_data_paths(scenarios: Path | None, rubrics: Path | None) -> tuple[Path, Path]:
    if scenarios is None or rubrics is None:
        cfg = {}
        if DEFAULT_APP_CONFIG.is_file():
            with DEFAULT_APP_CONFIG.open(encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        data = cfg.get("data", {})
        if scenarios is None:
            scenarios = ROOT / data.get("scenarios", "data/scenarios.jsonl")
        if rubrics is None:
            rubrics = ROOT / data.get("rubrics", "data/rubrics_qmatrix_final.jsonl")
    return Path(scenarios), Path(rubrics)


def _import_teammate_runner():
    """Import the teammate's runner module (stdlib-only at import time) so we can
    reuse his REQUIRED/FORBIDDEN field sets, JUDGES specs and validate_judge_cases
    instead of duplicating them. Returns None if it cannot be located."""
    if not TEAMMATE_RUNNER.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            "teammate_run_judge_validation", TEAMMATE_RUNNER
        )
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module
    except Exception as e:  # pragma: no cover - defensive
        print(f"WARNING: could not import teammate runner for validation: {e!r}",
              file=sys.stderr)
        return None


# =====================================================================
# build-cases : staged inputs -> teammate CASE schema (+ private index)
# =====================================================================
def build_case_dict(row: dict, bank) -> "dict | None":
    """Assemble one teammate CASE dict from a staged gradeable row + the ItemBank.

    Mirrors the field set emitted by the teammate's ``prepare_cases`` so his
    ``run`` treats our calibration cases identically to the selection-study cases.
    conversation_context is passed through as a SEPARATE list-of-turns field (NOT
    folded into scenario_prompt) exactly as his prepare/case example does.
    """
    model = str(row["model"])
    scenario_id = str(row["scenario"])
    criterion_id = str(row["criterion_id"])
    scenario = bank.scenarios.get(scenario_id)
    rubric = bank.rubrics.get(criterion_id)
    if scenario is None or rubric is None:
        return None
    return {
        "case_id": case_id_for(model, scenario_id, criterion_id),
        "response_id": response_id_for(model, scenario_id),
        "scenario_id": scenario_id,
        "criterion_id": criterion_id,
        "use_case": scenario.use_case,
        "subject": scenario.subject,
        "scenario_prompt": scenario.prompt,
        "conversation_context": scenario.conversation_context,
        "reference_solution": scenario.reference_solution,
        "candidate_response": str(row.get("response", "")),
        "criterion": rubric.criterion,
        "expected_evidence": [],
        "primary_skill": rubric.primary_skill,
        "criticality": rubric.criticality,
    }


def cmd_build_cases(args: argparse.Namespace) -> int:
    staging = args.staging_dir
    inputs_path = staging / JUDGE_INPUTS_NAME
    if not inputs_path.is_file():
        print(f"ERROR: {inputs_path} not found.", file=sys.stderr)
        print("       run scripts/stage_judge_inputs.py first.", file=sys.stderr)
        return 2

    scenarios_path, rubrics_path = _resolve_data_paths(args.scenarios, args.rubrics)
    for p, label in ((scenarios_path, "scenarios"), (rubrics_path, "rubrics")):
        if not p.is_file():
            print(f"ERROR: {label} file not found: {p}", file=sys.stderr)
            return 2
    bank, report = load_bank(scenarios_path, rubrics_path)
    if report.errors:
        print(f"WARNING: ItemBank load reported {len(report.errors)} error(s); "
              f"first few: {report.errors[:3]}", file=sys.stderr)

    cases_out = staging / CASES_NAME
    index_out = staging / CASES_INDEX_NAME

    cases: list[dict] = []
    n_total = 0
    n_gradeable = 0
    n_auto_fail = 0
    n_bank_missing = 0
    index_rows: list[dict] = []

    for row in iter_jsonl(inputs_path):
        n_total += 1
        model = str(row["model"])
        scenario_id = str(row["scenario"])
        criterion_id = str(row["criterion_id"])
        auto_fail = int(row.get("auto_fail", 0))
        cid = case_id_for(model, scenario_id, criterion_id)
        index_rows.append({
            "case_id": cid,
            "response_id": response_id_for(model, scenario_id),
            "model": model,
            "scenario_id": scenario_id,
            "criterion_id": criterion_id,
            "auto_fail": auto_fail,
            "auto_fail_reason": row.get("auto_fail_reason", ""),
        })
        if auto_fail == 1:
            # Auto-fail cells (error/empty/missing responses) are NEVER sent to the
            # judge: their candidate_response is blank and would fail his case
            # validation. They are scored y=0 downstream at ingest time.
            n_auto_fail += 1
            continue
        case = build_case_dict(row, bank)
        if case is None:
            n_bank_missing += 1
            print(f"  ! bank missing scenario/criterion for {model}/{scenario_id}/"
                  f"{criterion_id} -- skipped", file=sys.stderr)
            continue
        cases.append(case)
        n_gradeable += 1

    # Reuse the teammate's own validator so we fail fast on any schema drift.
    runner = _import_teammate_runner()
    validation_note = "validated with teammate.validate_judge_cases"
    if runner is not None and hasattr(runner, "validate_judge_cases"):
        try:
            runner.validate_judge_cases(cases)
        except Exception as e:
            print(f"ERROR: generated cases failed teammate validation: {e}", file=sys.stderr)
            return 1
    else:
        validation_note = "teammate validator unavailable; cases NOT cross-validated"
        print(f"WARNING: {validation_note}", file=sys.stderr)

    print("=" * 72)
    print("build-cases : staged inputs -> teammate CASE schema")
    print("=" * 72)
    print(f"staged cells      : {n_total}")
    print(f"  gradeable cases : {n_gradeable}")
    print(f"  auto-fail (skip): {n_auto_fail}  (scored y=0 at ingest, never judged)")
    if n_bank_missing:
        print(f"  bank missing    : {n_bank_missing}")
    print(f"validation        : {validation_note}")

    if args.dry_run:
        print("\n[dry-run] no files written.")
        return 0

    staging.mkdir(parents=True, exist_ok=True)
    with cases_out.open("w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    with index_out.open("w", encoding="utf-8") as f:
        for r in index_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nwrote judge cases (SHIP THIS to the GPU box) -> {cases_out}")
    print(f"wrote PRIVATE case index (keep local)        -> {index_out}")
    print("next: run the frozen judge with "
          "aws_judge_handoff/scripts/run_judge_validation.py run --judge qwen ...")
    return 0


# =====================================================================
# grade : ingest teammate verdicts (primary) or call-judge smoke (fallback)
# =====================================================================
class VerdictWriter:
    def __init__(self, path: Path):
        self._fh = path.open("a", encoding="utf-8")

    def write(self, row: dict) -> None:
        self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def flush(self) -> None:
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.flush()
        finally:
            self._fh.close()


def load_done_keys(path: Path) -> dict[CellKey, dict]:
    index: dict[CellKey, dict] = {}
    if not path.is_file():
        return index
    for row in iter_jsonl(path):
        try:
            key = (str(row["model"]), str(row["scenario"]), str(row["criterion_id"]))
        except KeyError:
            continue
        index[key] = row
    return index


def make_verdict_row(key: CellKey, *, y: int, source: str, verdict: str,
                     extra: "dict | None" = None) -> dict:
    model, scenario, criterion_id = key
    row: dict[str, Any] = {
        "model": model,
        "scenario": scenario,
        "criterion_id": criterion_id,
        "y": int(y),
        "verdict": verdict,
        "source": source,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        row.update(extra)
    return row


def _expand_ingest_paths(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out.extend(sorted(p.glob("*.jsonl")))
        else:
            out.append(p)
    return out


def load_ingest_index(paths: list[Path]) -> tuple[dict[str, dict], dict[str, set], int]:
    """Index the teammate's verdict rows by case_id. On duplicate case_id (e.g.
    multiple waves) the LAST file wins; a conflicting verdict is warned. Also
    collects the distinct provenance values seen, for cross-checking."""
    by_case: dict[str, dict] = {}
    provenance: dict[str, set] = {f: set() for f in PROVENANCE_FIELDS}
    conflicts = 0
    for path in paths:
        for row in iter_jsonl(path):
            cid = str(row.get("case_id", "")).strip()
            if not cid:
                continue
            prev = by_case.get(cid)
            if prev is not None and str(prev.get("verdict")) != str(row.get("verdict")):
                conflicts += 1
            by_case[cid] = row
            for fld in PROVENANCE_FIELDS:
                if fld in row and row[fld] is not None:
                    provenance[fld].add(str(row[fld]))
    return by_case, provenance, conflicts


def normalize_verdict(row: dict) -> tuple[int, str, str, dict]:
    """Map a teammate verdict row to (y, source, verdict_label, extra).

    Per judge-normalization-v3, a cell is pass/fail ONLY when the verdict is
    exactly 'pass'/'fail'; everything else (no_decision, generation_error, or any
    non pass/fail) is auto-failed to y=0 with the reason recorded. The parser
    never infers from prose -- we honor that by trusting only the 'verdict' field.
    """
    verdict = str(row.get("verdict", "")).strip().lower()
    status = str(row.get("status", "") or "").strip().lower()
    prov = {f: row.get(f) for f in PROVENANCE_FIELDS if row.get(f) is not None}
    prov["case_id"] = row.get("case_id")
    if verdict == "pass" and status in ("", "ok"):
        return 1, "ingest", "pass", prov
    if verdict == "fail" and status in ("", "ok"):
        return 0, "ingest", "fail", prov
    # no_decision / unscorable / generation_error -> auto-fail y=0, record why.
    reason = verdict or "unscorable"
    if verdict not in ("no_decision", "pass", "fail"):
        reason = f"unscorable_verdict:{verdict or 'blank'}"
    prov["no_decision_reason"] = reason
    if status and status != "ok":
        prov["status"] = status
    if row.get("error"):
        prov["error"] = str(row["error"])
    return 0, "ingest_no_decision", "fail", prov


def cmd_grade(args: argparse.Namespace) -> int:
    staging = args.staging_dir
    inputs_path = staging / JUDGE_INPUTS_NAME
    input_manifest_path = staging / JUDGE_INPUTS_MANIFEST_NAME
    verdicts_path = staging / VERDICTS_NAME

    if not inputs_path.is_file() or not input_manifest_path.is_file():
        print(f"ERROR: {inputs_path} / {input_manifest_path} not found.", file=sys.stderr)
        print("       run scripts/stage_judge_inputs.py first.", file=sys.stderr)
        return 2

    with input_manifest_path.open(encoding="utf-8") as f:
        in_manifest = json.load(f)
    models: list[str] = list(in_manifest["models"])
    criterion_ids: list[str] = list(in_manifest["criterion_ids"])

    fj = load_frozen_judge_config(args.judge_config)
    existing = load_done_keys(verdicts_path) if args.resume else {}

    # --- ingest sources / call-judge setup ---
    ingest_by_case: dict[str, dict] = {}
    ingest_prov: dict[str, set] = {}
    smoke = None
    bank = None
    if args.mode == "ingest-verdicts":
        if not args.ingest_file:
            print("ERROR: --mode ingest-verdicts requires --ingest-file", file=sys.stderr)
            return 2
        ingest_paths = _expand_ingest_paths(args.ingest_file)
        missing = [p for p in ingest_paths if not p.is_file()]
        if missing or not ingest_paths:
            print(f"ERROR: ingest file(s) not found: {missing or args.ingest_file}",
                  file=sys.stderr)
            return 2
        ingest_by_case, ingest_prov, conflicts = load_ingest_index(ingest_paths)
        print(f"ingest: {len(ingest_by_case)} verdict rows from "
              f"{len(ingest_paths)} file(s)"
              + (f"; {conflicts} cross-file verdict conflict(s)" if conflicts else ""))
        _cross_check_provenance(fj, ingest_prov)
    else:  # call-judge smoke
        bank = _load_bank_for_grading(args)
        if bank is None:
            return 2
        smoke = OpenAICompatibleJudge(
            base_url=fj.base_url, model=fj.model,
            api_key_env=fj.api_key_env, temperature=fj.temperature,
            max_tokens=fj.max_tokens, seed=fj.seed,
            result_pass_threshold=fj.result_pass_threshold,
        )

    # --- plan ---
    plan = _scan_plan(inputs_path, existing, args.resume, ingest_by_case, args.mode)
    _print_plan(plan, args, fj, len(models), len(criterion_ids), len(existing))
    if args.dry_run:
        print("\n[dry-run] no cells graded, no files written.")
        return 0

    staging.mkdir(parents=True, exist_ok=True)
    writer = VerdictWriter(verdicts_path)
    stats = {"auto_fail": 0, "ingested_pass_fail": 0, "ingested_no_decision": 0,
             "smoke_judged": 0, "skipped_existing": 0, "ingest_missing": 0,
             "smoke_failed": 0, "bank_missing": 0}

    smoke_batch: list[tuple[CellKey, Scenario, Rubric, str]] = []

    def flush_smoke() -> None:
        if not smoke_batch or smoke is None:
            smoke_batch.clear()
            return
        def _one(item):
            key, sc, ru, resp = item
            try:
                return key, smoke.evaluate(sc, ru, resp)
            except Exception as e:
                print(f"  ! smoke judge failed {key}: {e!r}", file=sys.stderr)
                return key, None
        workers = max(1, args.concurrency)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for key, verdict in ex.map(_one, list(smoke_batch)):
                if verdict is None:
                    stats["smoke_failed"] += 1
                    continue
                writer.write(make_verdict_row(
                    key, y=verdict.y, source="call-judge", verdict=verdict.verdict,
                    extra={"judge_model": fj.model, "unscorable_reason": verdict.unscorable_reason},
                ))
                stats["smoke_judged"] += 1
        writer.flush()
        smoke_batch.clear()

    try:
        for row in iter_jsonl(inputs_path):
            key = _staged_key(row)
            if args.resume and key in existing:
                stats["skipped_existing"] += 1
                continue

            if int(row.get("auto_fail", 0)) == 1:
                writer.write(make_verdict_row(
                    key, y=0, source="auto_fail", verdict="fail",
                    extra={"auto_fail_reason": row.get("auto_fail_reason", "")},
                ))
                stats["auto_fail"] += 1
                continue

            if args.mode == "ingest-verdicts":
                cid = case_id_for(*key)
                vrow = ingest_by_case.get(cid)
                if vrow is None:
                    stats["ingest_missing"] += 1
                    continue  # hole; retriable when a later wave/file arrives
                y, source, verdict_label, extra = normalize_verdict(vrow)
                writer.write(make_verdict_row(key, y=y, source=source,
                                              verdict=verdict_label, extra=extra))
                if source == "ingest":
                    stats["ingested_pass_fail"] += 1
                else:
                    stats["ingested_no_decision"] += 1
            else:  # call-judge smoke
                scenario = bank.scenarios.get(key[1]) if bank else None
                rubric = bank.rubrics.get(key[2]) if bank else None
                if scenario is None or rubric is None:
                    stats["bank_missing"] += 1
                    continue
                smoke_batch.append((key, scenario, rubric, str(row.get("response", ""))))
                if len(smoke_batch) >= args.batch_size:
                    flush_smoke()
        flush_smoke()
    finally:
        writer.close()

    # --- assemble the response matrix from ALL verdicts on disk ---
    final_verdicts = load_done_keys(verdicts_path)
    arr, csv_cells, n_holes = assemble_matrix(models, criterion_ids, final_verdicts)
    matrix_csv = staging / MATRIX_CSV_NAME
    matrix_npy = staging / MATRIX_NPY_NAME
    matrix_manifest = staging / MATRIX_MANIFEST_NAME
    write_matrix_csv(matrix_csv, models, criterion_ids, csv_cells)
    np.save(matrix_npy, arr)
    _write_matrix_manifest(matrix_manifest, args, fj, models, criterion_ids,
                           final_verdicts, n_holes, stats, ingest_prov)
    _print_run_summary(stats, n_holes, models, criterion_ids,
                       matrix_csv, matrix_npy, matrix_manifest)
    return 0


def _load_bank_for_grading(args: argparse.Namespace):
    scenarios_path, rubrics_path = _resolve_data_paths(args.scenarios, args.rubrics)
    for p, label in ((scenarios_path, "scenarios"), (rubrics_path, "rubrics")):
        if not p.is_file():
            print(f"ERROR: {label} file not found: {p} (needed for call-judge)",
                  file=sys.stderr)
            return None
    bank, report = load_bank(scenarios_path, rubrics_path)
    if report.errors:
        print(f"WARNING: ItemBank load reported {len(report.errors)} error(s).",
              file=sys.stderr)
    return bank


def _cross_check_provenance(fj: FrozenJudgeConfig, prov: dict[str, set]) -> None:
    expected = fj.expected_provenance()
    for fld, exp in expected.items():
        seen = prov.get(fld, set())
        if not exp:
            continue
        if len(seen) > 1:
            print(f"WARNING: ingested rows mix {fld}={sorted(seen)} "
                  "(cross-wave/judge drift?)", file=sys.stderr)
        elif seen and exp not in seen:
            print(f"WARNING: ingested {fld}={sorted(seen)} != expected {exp!r} "
                  "(judge_frozen.yaml)", file=sys.stderr)


# =====================================================================
# matrix assembly
# =====================================================================
def assemble_matrix(models: list[str], criterion_ids: list[str],
                    verdicts: dict[CellKey, dict]) -> tuple[np.ndarray, list[list[str]], int]:
    by_mc: dict[tuple[str, str], int] = {}
    for (model, _scenario, criterion_id), row in verdicts.items():
        by_mc[(model, criterion_id)] = int(row["y"])
    n_rows, n_cols = len(models), len(criterion_ids)
    arr = np.full((n_rows, n_cols), np.nan, dtype=float)
    csv_cells: list[list[str]] = []
    n_holes = 0
    for i, model in enumerate(models):
        row_cells: list[str] = []
        for j, cid in enumerate(criterion_ids):
            y = by_mc.get((model, cid))
            if y is None:
                n_holes += 1
                row_cells.append("")
            else:
                arr[i, j] = float(y)
                row_cells.append(str(int(y)))
        csv_cells.append(row_cells)
    return arr, csv_cells, n_holes


def write_matrix_csv(path: Path, models: list[str], criterion_ids: list[str],
                     csv_cells: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", *criterion_ids])
        for model, row_cells in zip(models, csv_cells):
            w.writerow([model, *row_cells])


# =====================================================================
# planning / reporting
# =====================================================================
@dataclass
class Plan:
    total_cells: int = 0
    auto_fail_cells: int = 0
    gradeable_cells: int = 0
    already_done: int = 0
    to_process: int = 0
    ingest_covered: int = 0
    ingest_missing: int = 0
    auto_fail_by_reason: dict[str, int] = field(default_factory=dict)


def _scan_plan(inputs_path: Path, existing: dict[CellKey, dict], resume: bool,
               ingest_by_case: dict[str, dict], mode: str) -> Plan:
    plan = Plan()
    for row in iter_jsonl(inputs_path):
        plan.total_cells += 1
        key = _staged_key(row)
        done = resume and key in existing
        if int(row.get("auto_fail", 0)) == 1:
            plan.auto_fail_cells += 1
            reason = row.get("auto_fail_reason") or "auto_fail"
            plan.auto_fail_by_reason[reason] = plan.auto_fail_by_reason.get(reason, 0) + 1
            if not done:
                plan.to_process += 1
            continue
        plan.gradeable_cells += 1
        if done:
            plan.already_done += 1
            continue
        plan.to_process += 1
        if mode == "ingest-verdicts":
            if case_id_for(*key) in ingest_by_case:
                plan.ingest_covered += 1
            else:
                plan.ingest_missing += 1
    return plan


def _print_plan(plan: Plan, args: argparse.Namespace, fj: FrozenJudgeConfig,
                n_models: int, n_criteria: int, n_existing: int) -> None:
    print("=" * 72)
    print("grade -- plan")
    print("=" * 72)
    print(f"mode              : {args.mode}")
    print(f"frozen judge      : {fj.judge_name} / {fj.model} "
          f"(rev {fj.hf_revision or 'UNPINNED'})")
    print(f"policy versions   : prompt={fj.prompt_version} "
          f"norm={fj.normalization_version} evidence={fj.evidence_policy_version}")
    print(f"matrix dims       : {n_models} models x {n_criteria} criteria "
          f"= {n_models * n_criteria} cells")
    print("-" * 72)
    print(f"staged cells      : {plan.total_cells}")
    print(f"  auto-fail       : {plan.auto_fail_cells}")
    for reason, count in sorted(plan.auto_fail_by_reason.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"      {reason:24s}: {count}")
    print(f"  gradeable       : {plan.gradeable_cells}")
    print(f"resume            : {args.resume} (existing verdicts on disk: {n_existing})")
    print(f"  already done    : {plan.already_done}")
    print(f"  to process      : {plan.to_process}")
    if args.mode == "ingest-verdicts":
        print(f"  ingest covered  : {plan.ingest_covered}")
        print(f"  ingest MISSING  : {plan.ingest_missing}"
              + ("  <-- gradeable cells with no teammate verdict (holes)"
                 if plan.ingest_missing else ""))


def _write_matrix_manifest(path: Path, args: argparse.Namespace, fj: FrozenJudgeConfig,
                           models: list[str], criterion_ids: list[str],
                           verdicts: dict[CellKey, dict], n_holes: int,
                           stats: dict, ingest_prov: dict[str, set]) -> None:
    source_counts: dict[str, int] = {}
    for row in verdicts.values():
        s = row.get("source", "unknown")
        source_counts[s] = source_counts.get(s, 0) + 1
    n_rows, n_cols = len(models), len(criterion_ids)
    total = n_rows * n_cols
    observed_prov = {f: sorted(v) for f, v in (ingest_prov or {}).items() if v}
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "frozen_judge_expected": fj.expected_provenance(),
        "frozen_judge_observed": observed_prov,  # from the teammate verdict rows
        "inputs": {
            "judge_inputs": JUDGE_INPUTS_NAME,
            "judge_inputs_manifest": JUDGE_INPUTS_MANIFEST_NAME,
            "verdicts": VERDICTS_NAME,
            "ingest_files": args.ingest_file if args.mode == "ingest-verdicts" else None,
        },
        "matrix": {
            "csv": MATRIX_CSV_NAME,
            "npy": MATRIX_NPY_NAME,
            "rows_models": n_rows,
            "cols_criteria": n_cols,
            "orientation": "rows=models, cols=criteria; 0/1 (NaN=hole in .npy, ''=hole in .csv)",
        },
        "counts": {
            "total_cells": total,
            "verdicts_on_disk": len(verdicts),
            "by_source": source_counts,
            "this_run": stats,
        },
        "coverage": {"complete": n_holes == 0, "n_holes": n_holes, "n_filled": total - n_holes},
        "provenance": {
            "script": "scripts/run_judge_grading.py",
            "argv": sys.argv[1:],
            "teammate_runner": str(TEAMMATE_RUNNER.relative_to(ROOT))
            if TEAMMATE_RUNNER.is_relative_to(ROOT) else str(TEAMMATE_RUNNER),
        },
        "models": models,
        "criterion_ids": criterion_ids,
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def _print_run_summary(stats: dict, n_holes: int, models: list[str],
                       criterion_ids: list[str], matrix_csv: Path,
                       matrix_npy: Path, matrix_manifest: Path) -> None:
    total = len(models) * len(criterion_ids)
    print("\n" + "=" * 72)
    print("grade -- done")
    print("=" * 72)
    print(f"auto-fail written    : {stats['auto_fail']}")
    print(f"ingested pass/fail   : {stats['ingested_pass_fail']}")
    print(f"ingested no_decision : {stats['ingested_no_decision']} (auto-failed y=0)")
    if stats["smoke_judged"]:
        print(f"call-judge judged    : {stats['smoke_judged']}")
    if stats["smoke_failed"]:
        print(f"call-judge failed    : {stats['smoke_failed']} (left ungraded; retry on resume)")
    print(f"skipped (existing)   : {stats['skipped_existing']}")
    if stats["ingest_missing"]:
        print(f"ingest missing       : {stats['ingest_missing']} (no teammate verdict; hole)")
    if stats["bank_missing"]:
        print(f"bank missing         : {stats['bank_missing']}")
    print("-" * 72)
    print(f"matrix               : {len(models)} x {len(criterion_ids)} = {total} cells")
    print(f"  filled             : {total - n_holes}")
    print(f"  holes              : {n_holes}"
          + ("  <-- INCOMPLETE; re-run after more verdicts arrive" if n_holes else "  (complete)"))
    print()
    print(f"wrote matrix CSV      -> {matrix_csv}")
    print(f"wrote matrix NPY      -> {matrix_npy}")
    print(f"wrote matrix manifest -> {matrix_manifest}")


# =====================================================================
# CLI
# =====================================================================
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="command", required=True)

    pb = sub.add_parser("build-cases",
                        help="convert staged inputs -> teammate CASE schema (+ private index)")
    pb.add_argument("--staging-dir", type=Path, default=DEFAULT_STAGING)
    pb.add_argument("--scenarios", type=Path, default=None,
                    help="scenarios.jsonl (default: config.yaml data.scenarios)")
    pb.add_argument("--rubrics", type=Path, default=None,
                    help="rubrics jsonl (default: config.yaml data.rubrics)")
    pb.add_argument("--dry-run", action="store_true",
                    help="validate + count only; write nothing")
    pb.set_defaults(fn=cmd_build_cases)

    pg = sub.add_parser("grade",
                        help="ingest teammate verdicts (primary) or call-judge smoke; build matrix")
    pg.add_argument("--staging-dir", type=Path, default=DEFAULT_STAGING)
    pg.add_argument("--mode", choices=["ingest-verdicts", "call-judge"],
                    default="ingest-verdicts")
    pg.add_argument("--ingest-file", nargs="+", default=None,
                    help="teammate verdict JSONL(s) or dir(s) of *.jsonl (ingest-verdicts)")
    pg.add_argument("--judge-config", type=Path, default=None,
                    help=f"frozen judge block (default: {DEFAULT_JUDGE_CONFIG.name}, "
                         "then config.yaml judge:)")
    pg.add_argument("--scenarios", type=Path, default=None)
    pg.add_argument("--rubrics", type=Path, default=None)
    pg.add_argument("--batch-size", type=int, default=32,
                    help="cells per checkpoint flush (call-judge)")
    pg.add_argument("--concurrency", type=int, default=8,
                    help="parallel calls for call-judge smoke")
    pg.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True,
                    help="skip cells already in staging/verdicts.jsonl (default: on)")
    pg.add_argument("--dry-run", action="store_true",
                    help="validate inputs + print planned counts; no writes")
    pg.set_defaults(fn=cmd_grade)
    return ap


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
