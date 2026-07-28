"""Preliminary UNIDIMENSIONAL 2PL calibration from a partial (sparse) response
matrix.

Context
-------
``scripts/run_judge_grading.py`` emits ``staging/response_matrix.csv`` with header
``["model", *criterion_ids]`` -- rows = models (persons), cols = criteria (items),
values 0/1, and HOLES = empty cells (NaN in the ``.npy``). On a ~1/4-filled fleet
the matrix is far too sparse to hand straight to a dense fitter: the only fitter,
``tutor_cat.mcq_irt.calibrate.fit_2pl`` (girth ``twopl_mml`` / rasch / optional
py-irt), does ``mat.dropna(axis=0, how="any")`` internally, which would delete
almost every model.

This script gives us a *preliminary* item bank from that partial matrix, two ways:

* ``girth`` / ``rasch`` -- **dense-block** path. Pick the criteria observed by
  enough persons, then pick the models complete over that block, producing a
  hole-free sub-block that girth/rasch can fit. We report exactly what was kept
  and dropped and why. Only the kept criteria get calibrated values.

* ``pyirt`` -- **sparse** path. Uses only the observed cells (no dense-block
  reduction) via the guarded ``allow_missing=True`` option on ``fit_2pl``.
  Optional; needs ``pip install py-irt`` (pulls torch/pyro). Fails gracefully with
  a clear message if the package is absent.

What this is / is NOT
---------------------
This is a **unidimensional, preliminary, partial-data** 2PL calibration. It gives a
single scalar discrimination ``a`` and difficulty ``b`` per fitted criterion, and a
rank-ordering of item difficulty/discrimination good enough to sanity-check the
synthetic bank and to seed a CAT smoke test. It is NOT the multidimensional M2PL /
R-mirt fit (that is deferred): it collapses the three tutoring skills onto one
latent axis and, on the dense-block path, only speaks to the sub-population of
models/criteria dense enough to survive block selection. Treat every number as
provisional.

Unidimensional-a -> 3-vector schema mapping (for ``--write-params``)
-------------------------------------------------------------------
``schemas.Rubric`` stores discrimination as a 3-vector ``a = (a_content,
a_diagnosis, a_scaffolding)`` (the M2PL layout). Our fit produces a single scalar.
To avoid silently corrupting a multidimensional vector with a unidimensional
number, ``--write-params`` takes the least-surprising, reversible choice:

* write the fitted scalar to a NEW top-level field ``a_unidim`` (the authoritative
  unidimensional discrimination), and update the scalar ``difficulty`` (``b``);
* LEAVE the existing 3-vector ``discrimination`` untouched (it stays synthetic and
  will be replaced wholesale when the deferred M2PL fitter runs);
* stamp ``irt_params.source = "calibrated-2pl-partial"`` with full provenance.

Criteria without a fit keep all their synthetic values untouched. The output is
ALWAYS written to a NEW file (default ``data/rubrics_qmatrix_calibrated_partial.jsonl``);
``data/rubrics_qmatrix_final.jsonl`` is never overwritten in place.

Usage (see the module report for copy-paste commands)
-----------------------------------------------------
    # coverage report only (no fit):
    python scripts/calibrate_partial.py --report-only

    # dense-block girth fit:
    python scripts/calibrate_partial.py --method girth --min-persons-per-item 15

    # also write a calibrated rubric copy:
    python scripts/calibrate_partial.py --method girth --write-params

    # sparse Bayesian fit (optional dep):
    python scripts/calibrate_partial.py --method pyirt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tutor_cat.mcq_irt.calibrate import EXTREME_A, fit_2pl  # noqa: E402

DEFAULT_MATRIX = ROOT / "staging" / "response_matrix.csv"
DEFAULT_MATRIX_MANIFEST = ROOT / "staging" / "response_matrix_manifest.json"
DEFAULT_RUBRICS = ROOT / "data" / "rubrics_qmatrix_final.jsonl"
DEFAULT_OUT_DIR = ROOT / "staging"
DEFAULT_CALIBRATED_RUBRICS = ROOT / "data" / "rubrics_qmatrix_calibrated_partial.jsonl"

COVERAGE_CSV_NAME = "coverage_report.csv"
COVERAGE_JSON_NAME = "coverage_report.json"
CALIBRATION_CSV_NAME = "calibration_2pl.csv"
CALIBRATION_MANIFEST_NAME = "calibration_2pl_manifest.json"

CALIBRATION_SOURCE = "calibrated-2pl-partial"


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def load_matrix(path: Path) -> pd.DataFrame:
    """Load the response matrix; empty cells -> NaN, values coerced to float.

    Rows = models (indexed by the ``model`` column), cols = criteria.
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"response matrix not found: {path}\n"
            "Run scripts/run_judge_grading.py grade ... first to emit it."
        )
    mat = pd.read_csv(path, index_col="model")
    # Coerce every cell to float so '' -> NaN and '0'/'1' -> 0.0/1.0.
    mat = mat.apply(pd.to_numeric, errors="coerce")
    return mat


# ---------------------------------------------------------------------------
# coverage
# ---------------------------------------------------------------------------


def compute_coverage(mat: pd.DataFrame) -> dict:
    """Per-criterion observed-person counts and per-model observed-item counts."""
    observed = mat.notna()
    per_item = observed.sum(axis=0).astype(int)      # observed persons per criterion
    per_model = observed.sum(axis=1).astype(int)     # observed items per model
    n_models, n_criteria = mat.shape
    total = int(n_models * n_criteria)
    n_filled = int(observed.to_numpy().sum())
    fill_rate = (n_filled / total) if total else 0.0

    # Matrix-wide zero-variance census (over each criterion's observed cells). These
    # constant columns are mathematically unfittable and are dropped before fitting;
    # at ~20 strict-judge persons they dominate, so surface them up front.
    zv_all_fail = zv_all_pass = zv_variant = 0
    if n_criteria:
        col_sum = mat.sum(axis=0, skipna=True)        # sum of observed 1s
        obs_cnt = per_item
        has_obs = obs_cnt > 0
        zv_all_fail = int(((col_sum == 0) & has_obs).sum())
        zv_all_pass = int(((col_sum == obs_cnt) & has_obs).sum())
        zv_variant = int(has_obs.sum()) - zv_all_fail - zv_all_pass

    # Histogram of per-item coverage (observed-person count buckets).
    hist_counts, hist_edges = np.histogram(
        per_item.to_numpy(), bins=min(10, max(1, n_models)) if n_models else 1
    )
    histogram = [
        {
            "lo": float(hist_edges[i]),
            "hi": float(hist_edges[i + 1]),
            "n_criteria": int(hist_counts[i]),
        }
        for i in range(len(hist_counts))
    ]

    return {
        "n_models": int(n_models),
        "n_criteria": int(n_criteria),
        "total_cells": total,
        "n_filled": n_filled,
        "fill_rate": round(fill_rate, 6),
        "per_item": per_item,
        "per_model": per_model,
        "per_item_coverage_histogram": histogram,
        "zero_variance": {
            "all_fail": zv_all_fail,
            "all_pass": zv_all_pass,
            "variant": zv_variant,
        },
    }


def write_coverage_reports(cov: dict, out_dir: Path) -> tuple[Path, Path]:
    per_item: pd.Series = cov["per_item"]
    per_model: pd.Series = cov["per_model"]

    # Long-format CSV: one row per criterion and per model.
    item_rows = pd.DataFrame(
        {
            "kind": "criterion",
            "id": per_item.index,
            "observed_count": per_item.to_numpy(),
            "coverage_fraction": (per_item.to_numpy() / cov["n_models"])
            if cov["n_models"]
            else 0.0,
        }
    )
    model_rows = pd.DataFrame(
        {
            "kind": "model",
            "id": per_model.index,
            "observed_count": per_model.to_numpy(),
            "coverage_fraction": (per_model.to_numpy() / cov["n_criteria"])
            if cov["n_criteria"]
            else 0.0,
        }
    )
    csv_path = out_dir / COVERAGE_CSV_NAME
    pd.concat([item_rows, model_rows], ignore_index=True).to_csv(csv_path, index=False)

    json_path = out_dir / COVERAGE_JSON_NAME
    summary = {
        "generated_at": _utcnow(),
        "n_models": cov["n_models"],
        "n_criteria": cov["n_criteria"],
        "total_cells": cov["total_cells"],
        "n_filled": cov["n_filled"],
        "fill_rate": cov["fill_rate"],
        "per_item_coverage": {
            "min": int(per_item.min()) if len(per_item) else 0,
            "median": float(per_item.median()) if len(per_item) else 0.0,
            "max": int(per_item.max()) if len(per_item) else 0,
        },
        "per_model_coverage": {
            "min": int(per_model.min()) if len(per_model) else 0,
            "median": float(per_model.median()) if len(per_model) else 0.0,
            "max": int(per_model.max()) if len(per_model) else 0,
        },
        "per_item_coverage_histogram": cov["per_item_coverage_histogram"],
        "zero_variance": cov["zero_variance"],
    }
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return csv_path, json_path


def print_coverage(cov: dict) -> None:
    per_item: pd.Series = cov["per_item"]
    per_model: pd.Series = cov["per_model"]
    print("=" * 72)
    print("coverage report")
    print("=" * 72)
    print(f"matrix            : {cov['n_models']} models x {cov['n_criteria']} criteria "
          f"= {cov['total_cells']} cells")
    print(f"filled cells      : {cov['n_filled']} ({cov['fill_rate'] * 100:.1f}% fill rate)")
    if len(per_item):
        print(f"per-criterion obs : min={int(per_item.min())} "
              f"median={per_item.median():.1f} max={int(per_item.max())}")
    if len(per_model):
        print(f"per-model obs     : min={int(per_model.min())} "
              f"median={per_model.median():.1f} max={int(per_model.max())}")
    zv = cov["zero_variance"]
    print(f"zero-variance     : {zv['all_fail']} all-fail + {zv['all_pass']} all-pass "
          f"= {zv['all_fail'] + zv['all_pass']} unfittable "
          f"({zv['variant']} variant/fittable) -- dropped before fitting")
    print("per-item coverage histogram (observed-person count buckets):")
    for bucket in cov["per_item_coverage_histogram"]:
        print(f"    [{bucket['lo']:6.1f}, {bucket['hi']:6.1f}) : {bucket['n_criteria']}")


# ---------------------------------------------------------------------------
# dense-block selection (girth / rasch)
# ---------------------------------------------------------------------------


def select_dense_block(
    mat: pd.DataFrame, min_persons_per_item: int, min_items_per_person: int
) -> tuple[pd.DataFrame, dict]:
    """Reduce a sparse matrix to a hole-free sub-block.

    1. Keep criteria (cols) observed by >= ``min_persons_per_item`` persons.
    2. Over that column block, keep models (rows) that are COMPLETE (no holes)
       and cover >= ``min_items_per_person`` items.

    Returns the sub-block plus a diagnostics dict describing what was dropped.
    """
    observed = mat.notna()
    per_item = observed.sum(axis=0)
    kept_cols = per_item.index[per_item >= min_persons_per_item].tolist()
    dropped_cols = [c for c in mat.columns if c not in set(kept_cols)]

    diag = {
        "min_persons_per_item": min_persons_per_item,
        "min_items_per_person": min_items_per_person,
        "n_criteria_in": int(mat.shape[1]),
        "n_models_in": int(mat.shape[0]),
        "n_criteria_kept_by_coverage": len(kept_cols),
        "n_criteria_dropped_low_coverage": len(dropped_cols),
    }

    if not kept_cols:
        diag.update({"block_rows": 0, "block_cols": 0, "n_models_dropped_incomplete": 0})
        return mat.iloc[0:0, 0:0], diag

    sub = mat[kept_cols]
    complete_mask = sub.notna().all(axis=1) & (sub.notna().sum(axis=1) >= min_items_per_person)
    block = sub[complete_mask]
    diag.update(
        {
            "n_models_dropped_incomplete": int((~complete_mask).sum()),
            "block_rows": int(block.shape[0]),
            "block_cols": int(block.shape[1]),
            "kept_criteria": kept_cols,
            "dropped_criteria_low_coverage": dropped_cols,
        }
    )
    return block, diag


# ---------------------------------------------------------------------------
# sparse selection (pyirt)
# ---------------------------------------------------------------------------


def select_sparse(mat: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Drop only fully-empty rows/cols; keep every observed cell otherwise."""
    observed = mat.notna()
    keep_cols = observed.sum(axis=0) > 0
    keep_rows = observed.sum(axis=1) > 0
    sub = mat.loc[keep_rows, keep_cols]
    diag = {
        "n_criteria_in": int(mat.shape[1]),
        "n_models_in": int(mat.shape[0]),
        "n_criteria_dropped_empty": int((~keep_cols).sum()),
        "n_models_dropped_empty": int((~keep_rows).sum()),
        "block_rows": int(sub.shape[0]),
        "block_cols": int(sub.shape[1]),
        "n_observed_cells": int(sub.notna().to_numpy().sum()),
    }
    return sub, diag


# ---------------------------------------------------------------------------
# fitting
# ---------------------------------------------------------------------------


class CalibrationError(RuntimeError):
    pass


def split_zero_variance(block: pd.DataFrame) -> tuple[list[str], list[str], list[str]]:
    """Partition columns by observed-response variance.

    A criterion whose observed (non-NaN) responses are all 0 (all-fail) or all 1
    (all-pass) has zero variance: it carries no information for a 2PL/1PL fit and
    makes MML blow up (log(0) / invalid scalar subtract). We split those out here,
    on the exact matrix that will be fed to the fitter.

    Returns (kept, all_fail, all_pass). Columns with no observations at all (which
    should not survive selection) are treated as droppable and reported as all-fail.
    """
    kept: list[str] = []
    all_fail: list[str] = []
    all_pass: list[str] = []
    for col in block.columns:
        obs = block[col].dropna()
        vals = set(np.unique(obs.to_numpy())) if len(obs) else set()
        if vals == {1.0}:
            all_pass.append(col)
        elif len(vals) <= 1:  # {0.0} (all-fail) or empty (no data)
            all_fail.append(col)
        else:
            kept.append(col)
    return kept, all_fail, all_pass


def _drop_zero_variance(block: pd.DataFrame, diag: dict) -> pd.DataFrame:
    """Drop zero-variance columns from ``block`` and record what/why in ``diag``.

    For a dense (hole-free) block this is variance over the selected complete rows;
    for the pyirt sparse block it is variance over each column's observed cells,
    after which any row left with no observations is dropped too.
    """
    kept, all_fail, all_pass = split_zero_variance(block)
    filtered = block[kept]
    # A sparse block may leave rows with no observed cell once columns are dropped.
    filtered = filtered[filtered.notna().any(axis=1)]
    diag.update(
        {
            "dropped_all_fail": len(all_fail),
            "dropped_all_pass": len(all_pass),
            "dropped_zero_variance_total": len(all_fail) + len(all_pass),
            "dropped_all_fail_criteria": all_fail,
            "dropped_all_pass_criteria": all_pass,
            "n_items_fit": int(filtered.shape[1]),
            "n_persons_fit": int(filtered.shape[0]),
        }
    )
    return filtered


def run_fit(
    mat: pd.DataFrame, method: str, min_persons_per_item: int, min_items_per_person: int
) -> tuple[object, dict]:
    """Select the appropriate block, drop zero-variance items, and fit.

    Returns (Calibration, block_diag). Zero-variance (all-fail / all-pass) columns
    are removed BEFORE the fitter is invoked for every method, so girth/rasch MML
    never sees an unfittable constant column.
    """
    if method in ("girth", "rasch"):
        block, diag = select_dense_block(mat, min_persons_per_item, min_items_per_person)
        if block.shape[0] < 2 or block.shape[1] < 1:
            raise CalibrationError(
                "dense block is empty or trivially small "
                f"(rows={block.shape[0]}, cols={block.shape[1]}). "
                f"Lower --min-persons-per-item (now {min_persons_per_item}) or "
                f"--min-items-per-person (now {min_items_per_person}), or collect "
                "more responses. Not calling girth on a degenerate block."
            )
        block = _drop_zero_variance(block, diag)
        _print_fit_line(block, diag)
        if block.shape[1] < 1 or block.shape[0] < 2:
            raise CalibrationError(
                "after dropping zero-variance (all-fail/all-pass) items the block "
                f"is empty (items={block.shape[1]}, persons={block.shape[0]}; "
                f"dropped {diag['dropped_all_fail']} all-fail + "
                f"{diag['dropped_all_pass']} all-pass). With ~this many strict-judge "
                "respondents almost every criterion is constant -- collect more "
                "responses or lower thresholds. Not calling the fitter on an empty array."
            )
        calib = fit_2pl(block, method=method)
        return calib, diag

    if method == "pyirt":
        block, diag = select_sparse(mat)
        if block.shape[0] < 2 or block.shape[1] < 1:
            raise CalibrationError(
                "sparse block has no usable observed cells "
                f"(rows={block.shape[0]}, cols={block.shape[1]})."
            )
        block = _drop_zero_variance(block, diag)
        _print_fit_line(block, diag)
        if block.shape[1] < 1 or block.shape[0] < 2:
            raise CalibrationError(
                "after dropping zero-variance (all-fail/all-pass) items the sparse "
                f"block is empty (items={block.shape[1]}, persons={block.shape[0]}; "
                f"dropped {diag['dropped_all_fail']} all-fail + "
                f"{diag['dropped_all_pass']} all-pass)."
            )
        try:
            calib = fit_2pl(block, method="pyirt", allow_missing=True)
        except ImportError as e:
            raise CalibrationError(str(e)) from e
        return calib, diag

    raise CalibrationError(f"unknown method: {method!r}")


def _print_fit_line(block: pd.DataFrame, diag: dict) -> None:
    """One-line pre-fit status so the run isn't silent before the (slow) fitter."""
    print(
        f"fitting {diag['n_items_fit']} items x {diag['n_persons_fit']} persons "
        f"(dropped {diag['dropped_zero_variance_total']} zero-variance: "
        f"{diag['dropped_all_fail']} all-fail, {diag['dropped_all_pass']} all-pass)"
    )


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def build_calibration_frame(calib, method: str) -> pd.DataFrame:
    frame = calib.as_frame().reset_index().rename(columns={"item": "criterion_id"})
    a = frame["a"].to_numpy(dtype=float)
    flags = [
        "extreme_a" if (not np.isfinite(v) or abs(v) > EXTREME_A) else ""
        for v in a
    ]
    frame["method"] = calib.method
    frame["n_models_used"] = calib.n_models
    frame["flags"] = flags
    return frame[["criterion_id", "a", "b", "method", "n_models_used", "flags"]]


def write_calibration_csv(frame: pd.DataFrame, out_dir: Path) -> Path:
    path = out_dir / CALIBRATION_CSV_NAME
    frame.to_csv(path, index=False)
    return path


def write_calibration_manifest(
    out_dir: Path,
    method: str,
    args: argparse.Namespace,
    block_diag: dict,
    calib,
    matrix_manifest_prov: dict,
) -> Path:
    path = out_dir / CALIBRATION_MANIFEST_NAME
    manifest = {
        "generated_at": _utcnow(),
        "note": (
            "UNIDIMENSIONAL + PRELIMINARY + partial-data 2PL calibration. NOT the "
            "deferred M2PL/R-mirt fit. Treat all values as provisional."
        ),
        "method": calib.method,
        "requested_method": method,
        "thresholds": {
            "min_persons_per_item": args.min_persons_per_item,
            "min_items_per_person": args.min_items_per_person,
        },
        "block": block_diag,
        "dropped_all_fail": block_diag.get("dropped_all_fail", 0),
        "dropped_all_pass": block_diag.get("dropped_all_pass", 0),
        "n_items_fit": block_diag.get("n_items_fit"),
        "n_persons_fit": block_diag.get("n_persons_fit"),
        "n_models_used": int(calib.n_models),
        "n_criteria_fitted": len(calib.items),
        "n_extreme_a": int(calib.n_extreme_a),
        "extreme_a_threshold": EXTREME_A,
        "a_unidim_mapping": (
            "scalar a written to new field 'a_unidim'; 3-vector 'discrimination' "
            "left untouched (see --write-params)."
        ),
        "matrix": matrix_manifest_prov,
        "provenance": {"script": "scripts/calibrate_partial.py", "argv": sys.argv[1:]},
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return path


# ---------------------------------------------------------------------------
# --write-params
# ---------------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_calibrated_rubrics(
    rubrics_path: Path,
    out_path: Path,
    calib,
    method: str,
    args: argparse.Namespace,
    matrix_manifest_prov: dict,
) -> tuple[Path, int]:
    """Write a COPY of the rubric bank with calibrated b + a_unidim for fitted
    criteria. Never mutates the input file. Returns (out_path, n_updated)."""
    if out_path.resolve() == rubrics_path.resolve():
        raise CalibrationError(
            "refusing to overwrite the input rubric bank in place; "
            "choose a different --out-rubrics."
        )
    records = read_jsonl(rubrics_path)
    a_by = calib.a_by_item()
    b_by = calib.b_by_item()

    provenance = {
        "source": CALIBRATION_SOURCE,
        "method": calib.method,
        "requested_method": method,
        "thresholds": {
            "min_persons_per_item": args.min_persons_per_item,
            "min_items_per_person": args.min_items_per_person,
        },
        "n_models_used": int(calib.n_models),
        "matrix": matrix_manifest_prov,
        "calibrated_at": _utcnow(),
        "version": "1.0",
    }

    n_updated = 0
    for rec in records:
        cid = rec.get("criterion_id")
        if cid in a_by:
            a_val = float(a_by[cid])
            b_val = float(b_by[cid])
            if np.isfinite(a_val):
                rec["difficulty"] = round(b_val, 4)
                # Least-surprising mapping: new scalar field, 3-vector left as-is.
                rec["a_unidim"] = round(a_val, 4)
                rec["irt_params"] = dict(provenance)
                n_updated += 1
            # non-finite a -> leave synthetic values untouched.
    write_jsonl(out_path, records)
    return out_path, n_updated


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _matrix_manifest_prov(matrix_path: Path) -> dict:
    """Best-effort provenance of the source matrix (path, sha256, manifest time)."""
    prov: dict = {"csv": str(matrix_path)}
    try:
        prov["sha256"] = hashlib.sha256(matrix_path.read_bytes()).hexdigest()
    except OSError:
        prov["sha256"] = None
    mpath = matrix_path.parent / DEFAULT_MATRIX_MANIFEST.name
    if mpath.is_file():
        try:
            with mpath.open(encoding="utf-8") as f:
                mm = json.load(f)
            prov["manifest"] = mpath.name
            prov["manifest_generated_at"] = mm.get("generated_at")
        except (OSError, json.JSONDecodeError):
            pass
    return prov


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX,
                   help=f"response matrix CSV (default: {DEFAULT_MATRIX}).")
    p.add_argument("--method", choices=["girth", "rasch", "pyirt"], default="girth",
                   help="girth (default) / rasch = dense-block path; "
                        "pyirt = sparse path (optional dep).")
    p.add_argument("--min-persons-per-item", type=int, default=15,
                   help="keep criteria observed by >= this many persons (default 15).")
    p.add_argument("--min-items-per-person", type=int, default=1,
                   help="keep models covering >= this many items in the block (default 1).")
    p.add_argument("--report-only", action="store_true",
                   help="emit the coverage report and exit (no fit).")
    p.add_argument("--write-params", action="store_true",
                   help="also write a calibrated COPY of the rubric bank (OFF by default).")
    p.add_argument("--rubrics", type=Path, default=DEFAULT_RUBRICS,
                   help=f"input rubric JSONL for --write-params (default: {DEFAULT_RUBRICS}).")
    p.add_argument("--out-rubrics", type=Path, default=DEFAULT_CALIBRATED_RUBRICS,
                   help=f"output rubric JSONL for --write-params "
                        f"(default: {DEFAULT_CALIBRATED_RUBRICS}).")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                   help=f"directory for reports + calibration outputs (default: {DEFAULT_OUT_DIR}).")
    args = p.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    try:
        mat = load_matrix(args.matrix)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    cov = compute_coverage(mat)
    print_coverage(cov)
    cov_csv, cov_json = write_coverage_reports(cov, args.out_dir)
    print(f"\nwrote coverage report -> {cov_csv}")
    print(f"wrote coverage summary -> {cov_json}")

    if args.report_only:
        return 0

    matrix_prov = _matrix_manifest_prov(args.matrix)

    try:
        calib, block_diag = run_fit(
            mat, args.method, args.min_persons_per_item, args.min_items_per_person
        )
    except CalibrationError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 3

    print("\n" + "=" * 72)
    print(f"calibration ({args.method})")
    print("=" * 72)
    print(f"block shape       : {block_diag['block_rows']} models x "
          f"{block_diag['block_cols']} criteria")
    if args.method in ("girth", "rasch"):
        print(f"dropped criteria  : {block_diag['n_criteria_dropped_low_coverage']} "
              f"(observed-person count < {args.min_persons_per_item})")
        print(f"dropped models    : {block_diag['n_models_dropped_incomplete']} "
              f"(incomplete over the kept-criteria block)")
    print(f"dropped zero-var  : {block_diag.get('dropped_zero_variance_total', 0)} "
          f"({block_diag.get('dropped_all_fail', 0)} all-fail, "
          f"{block_diag.get('dropped_all_pass', 0)} all-pass)")
    print(f"fit block         : {block_diag.get('n_items_fit')} items x "
          f"{block_diag.get('n_persons_fit')} persons")
    print(f"fitted method     : {calib.method}")
    print(f"criteria fitted   : {len(calib.items)}")
    print(f"models used       : {calib.n_models}")
    if calib.n_extreme_a:
        print(f"WARNING           : {calib.n_extreme_a} criteria with extreme/unstable "
              f"|a| > {EXTREME_A} (thin-sample artifact) -- flagged in output.")

    frame = build_calibration_frame(calib, args.method)
    calib_csv = write_calibration_csv(frame, args.out_dir)
    calib_manifest = write_calibration_manifest(
        args.out_dir, args.method, args, block_diag, calib, matrix_prov
    )
    print(f"\nwrote calibration CSV -> {calib_csv}")
    print(f"wrote calibration manifest -> {calib_manifest}")

    if args.write_params:
        try:
            out_path, n_updated = write_calibrated_rubrics(
                args.rubrics, args.out_rubrics, calib, args.method, args, matrix_prov
            )
        except (CalibrationError, FileNotFoundError) as e:
            print(f"\nERROR: --write-params failed: {e}", file=sys.stderr)
            return 4
        print(f"\nwrote calibrated rubric copy -> {out_path} "
              f"({n_updated} criteria updated; rest kept synthetic)")
        print(f"(input {args.rubrics} left untouched)")

    print("\nreminder: UNIDIMENSIONAL + PRELIMINARY + partial-data. Not M2PL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
