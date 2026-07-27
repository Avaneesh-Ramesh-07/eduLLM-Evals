"""Hermetic tests for the partial/sparse 2PL calibration path.

Builds a SYNTHETIC model x criterion matrix with a known dense core, injected
holes, sparse low-coverage criteria, AND zero-variance (all-fail / all-pass)
criteria. Exercises: the loader (empty cells -> NaN), the coverage report incl.
the zero-variance census, dense-block selection (threshold respect), zero-variance
column dropping before the fit (the real-data bug), the fit + output-CSV schema,
and that --write-params emits a new rubric file WITHOUT mutating the input. No
network, no real data. The pyirt branch is skipped unless py-irt is installed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).parents[1]
SCRIPT_PATH = ROOT / "scripts" / "calibrate_partial.py"
SPEC = importlib.util.spec_from_file_location("calibrate_partial", SCRIPT_PATH)
assert SPEC and SPEC.loader
cp = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cp
SPEC.loader.exec_module(cp)


# --- synthetic data -------------------------------------------------------


def _make_matrix() -> tuple[pd.DataFrame, list[str], list[str], list[str]]:
    """Return (mat, dense_items, sparse_items, const_items).

    - dense_items: 6 variant criteria observed by all 40 models (2 holes punched).
    - const_items: ['zf00' all-fail (all 0), 'zp00' all-pass (all 1)], observed by
      all 40 -> survive coverage selection but are zero-variance & unfittable.
    - sparse_items: 4 criteria observed by only the first 5 models (forced variant).
    """
    rng = np.random.default_rng(11)
    n_models = 40
    dense_items = [f"c{j:02d}" for j in range(6)]
    const_items = ["zf00", "zp00"]
    sparse_items = [f"s{j:02d}" for j in range(4)]

    a = rng.uniform(0.7, 1.8, len(dense_items))
    b = rng.normal(0.0, 0.8, len(dense_items))
    theta = rng.normal(0.0, 1.0, n_models)
    z = a[None, :] * (theta[:, None] - b[None, :])
    p = 1.0 / (1.0 + np.exp(-z))
    core = (rng.random((n_models, len(dense_items))) < p).astype(float)

    models = [f"m{k:02d}" for k in range(n_models)]
    mat = pd.DataFrame(core, index=models, columns=dense_items)

    # Zero-variance columns observed by everyone.
    mat["zf00"] = np.zeros(n_models, dtype=float)   # all-fail
    mat["zp00"] = np.ones(n_models, dtype=float)    # all-pass

    # Sparse criteria: only first 5 models observed; forced to contain both 0 and 1.
    for s in sparse_items:
        col = np.full(n_models, np.nan)
        col[:5] = (rng.random(5) < 0.5).astype(float)
        col[0] = 0.0
        col[1] = 1.0
        mat[s] = col

    # Punch a couple of holes into the dense core (rows m00, m01).
    mat.loc["m00", "c00"] = np.nan
    mat.loc["m01", "c01"] = np.nan

    mat.index.name = "model"
    return mat, dense_items, sparse_items, const_items


def _write_matrix_csv(mat: pd.DataFrame, path: Path) -> None:
    # Mirror the emitter: holes -> empty string, values -> 0/1 ints.
    out = mat.copy()
    for c in out.columns:
        out[c] = out[c].map(lambda v: "" if pd.isna(v) else str(int(v)))
    out.to_csv(path)


def _make_rubrics(criterion_ids: list[str], path: Path) -> None:
    records = []
    for cid in criterion_ids:
        records.append(
            {
                "criterion_id": cid,
                "scenario_id": "syn_000",
                "criterion": f"synthetic {cid}",
                "primary_skill": "content",
                "q_mapping": {"content": 1, "diagnosis": 0, "scaffolding": 0},
                "difficulty": 0.1234,
                "discrimination": {"content": 1.5, "diagnosis": 0.0, "scaffolding": 0.0},
                "irt_params": {"source": "synthetic", "method": "metadata_heuristic_v1"},
            }
        )
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


N_CRITERIA = 12  # 6 dense + 2 const + 4 sparse


# --- loader ---------------------------------------------------------------


def test_loader_empty_cells_become_nan(tmp_path: Path):
    mat, _, sparse, _ = _make_matrix()
    csv = tmp_path / "response_matrix.csv"
    _write_matrix_csv(mat, csv)

    loaded = cp.load_matrix(csv)
    assert loaded.index.name == "model"
    assert loaded.shape == mat.shape
    # Sparse criteria have holes; core injected holes present too.
    assert loaded[sparse[0]].isna().sum() == 35
    assert pd.isna(loaded.loc["m00", "c00"])
    # Present cells are 0/1 floats.
    assert set(np.unique(loaded["c02"].dropna())).issubset({0.0, 1.0})


# --- coverage -------------------------------------------------------------


def test_coverage_report_correctness(tmp_path: Path):
    mat, dense, sparse, const = _make_matrix()
    csv = tmp_path / "response_matrix.csv"
    _write_matrix_csv(mat, csv)
    loaded = cp.load_matrix(csv)

    cov = cp.compute_coverage(loaded)
    assert cov["n_models"] == 40
    assert cov["n_criteria"] == N_CRITERIA
    # Sparse criteria observed by exactly 5 persons.
    assert int(cov["per_item"][sparse[0]]) == 5
    # Dense criterion c02 has no injected hole -> observed by all 40.
    assert int(cov["per_item"]["c02"]) == 40
    # c00 lost one observation (m00 hole).
    assert int(cov["per_item"]["c00"]) == 39

    # Zero-variance census: the two constant columns are counted.
    assert cov["zero_variance"]["all_fail"] >= 1
    assert cov["zero_variance"]["all_pass"] >= 1
    assert cov["zero_variance"]["variant"] >= 6  # at least the dense core

    total = 40 * N_CRITERIA
    n_filled = int(loaded.notna().to_numpy().sum())
    assert cov["n_filled"] == n_filled
    assert cov["fill_rate"] == pytest.approx(n_filled / total, rel=1e-6)

    csv_out, json_out = cp.write_coverage_reports(cov, tmp_path)
    assert csv_out.is_file() and json_out.is_file()
    rep = pd.read_csv(csv_out)
    assert set(rep["kind"]) == {"criterion", "model"}
    assert (rep["kind"] == "criterion").sum() == N_CRITERIA
    assert (rep["kind"] == "model").sum() == 40
    summary = json.loads(json_out.read_text())
    assert summary["n_models"] == 40 and summary["n_criteria"] == N_CRITERIA
    assert "per_item_coverage_histogram" in summary
    assert summary["zero_variance"]["all_fail"] >= 1
    assert summary["zero_variance"]["all_pass"] >= 1


# --- dense-block selection ------------------------------------------------


def test_dense_block_respects_thresholds(tmp_path: Path):
    mat, dense, sparse, const = _make_matrix()
    loaded = cp.load_matrix(tmp_path_or_write(mat, tmp_path))

    # min-persons-per-item=15 drops all 4 sparse criteria (observed by 5); the two
    # constant columns are observed by 40 so they survive *selection* (they are
    # removed later by the zero-variance filter, not here).
    block, diag = cp.select_dense_block(loaded, min_persons_per_item=15, min_items_per_person=1)
    assert diag["n_criteria_dropped_low_coverage"] == 4
    assert diag["n_criteria_kept_by_coverage"] == 8  # 6 dense + 2 const
    assert set(block.columns) == set(dense) | set(const)
    # Block must be hole-free.
    assert block.notna().to_numpy().all()
    # Rows m00/m01 were incomplete over the kept block -> dropped.
    assert "m00" not in block.index and "m01" not in block.index
    assert diag["n_models_dropped_incomplete"] == 2
    assert block.shape[0] == 38


def test_dense_block_low_threshold_keeps_sparse(tmp_path: Path):
    mat, dense, sparse, const = _make_matrix()
    loaded = cp.load_matrix(tmp_path_or_write(mat, tmp_path))
    block, diag = cp.select_dense_block(loaded, min_persons_per_item=5, min_items_per_person=1)
    assert diag["n_criteria_kept_by_coverage"] == N_CRITERIA
    assert block.notna().to_numpy().all()
    assert block.shape[0] <= 5


# --- zero-variance dropping (the real-data bug) ---------------------------


def test_zero_variance_dropped_counted_and_excluded(tmp_path: Path):
    pytest.importorskip("girth")
    mat, dense, sparse, const = _make_matrix()
    loaded = cp.load_matrix(tmp_path_or_write(mat, tmp_path))

    calib, diag = cp.run_fit(loaded, "rasch", min_persons_per_item=15, min_items_per_person=1)

    # Constant columns are dropped and correctly bucketed.
    assert diag["dropped_all_fail"] >= 1
    assert diag["dropped_all_pass"] >= 1
    assert "zf00" in diag["dropped_all_fail_criteria"]
    assert "zp00" in diag["dropped_all_pass_criteria"]
    # They never reach the fitter / output.
    assert "zf00" not in calib.items and "zp00" not in calib.items
    # A normal variant column still fits.
    assert "c02" in calib.items
    assert set(calib.items).issubset(set(dense))
    assert diag["n_items_fit"] == len(calib.items)

    frame = cp.build_calibration_frame(calib, "rasch")
    assert "zf00" not in set(frame["criterion_id"])
    assert "zp00" not in set(frame["criterion_id"])
    out_csv = cp.write_calibration_csv(frame, tmp_path)
    written = pd.read_csv(out_csv)
    assert "zf00" not in set(written["criterion_id"])
    assert "zp00" not in set(written["criterion_id"])

    # Manifest records the drops + the actual fit dimensions.
    manifest = cp.write_calibration_manifest(tmp_path, "rasch", _ns(), diag, calib, {"csv": "x"})
    m = json.loads(manifest.read_text())
    assert m["dropped_all_fail"] >= 1
    assert m["dropped_all_pass"] >= 1
    assert m["n_items_fit"] == len(calib.items)
    assert m["n_persons_fit"] == diag["n_persons_fit"]


def test_all_constant_block_raises(tmp_path: Path):
    # A matrix where every observed criterion is constant -> nothing fittable.
    n = 25
    models = [f"m{k:02d}" for k in range(n)]
    mat = pd.DataFrame(
        {"a0": np.zeros(n), "a1": np.ones(n), "a2": np.zeros(n)}, index=models
    )
    mat.index.name = "model"
    loaded = cp.load_matrix(tmp_path_or_write(mat, tmp_path))
    with pytest.raises(cp.CalibrationError):
        cp.run_fit(loaded, "girth", min_persons_per_item=5, min_items_per_person=1)


# --- fit + output schema --------------------------------------------------


def test_run_fit_and_output_schema(tmp_path: Path):
    pytest.importorskip("girth")
    mat, dense, sparse, const = _make_matrix()
    loaded = cp.load_matrix(tmp_path_or_write(mat, tmp_path))

    calib, diag = cp.run_fit(loaded, "rasch", min_persons_per_item=15, min_items_per_person=1)
    assert set(calib.items).issubset(set(dense))
    frame = cp.build_calibration_frame(calib, "rasch")
    assert list(frame.columns) == [
        "criterion_id", "a", "b", "method", "n_models_used", "flags",
    ]
    assert (frame["n_models_used"] == calib.n_models).all()

    out_csv = cp.write_calibration_csv(frame, tmp_path)
    assert out_csv.is_file()
    manifest = cp.write_calibration_manifest(tmp_path, "rasch", _ns(), diag, calib, {"csv": "x"})
    m = json.loads(manifest.read_text())
    assert "UNIDIMENSIONAL" in m["note"]
    assert m["thresholds"]["min_persons_per_item"] == 15


def test_empty_block_raises_clear_error(tmp_path: Path):
    mat, *_ = _make_matrix()
    loaded = cp.load_matrix(tmp_path_or_write(mat, tmp_path))
    with pytest.raises(cp.CalibrationError):
        cp.run_fit(loaded, "girth", min_persons_per_item=1000, min_items_per_person=1)


# --- write-params ---------------------------------------------------------


def test_write_params_new_file_no_mutation(tmp_path: Path):
    pytest.importorskip("girth")
    mat, dense, sparse, const = _make_matrix()
    loaded = cp.load_matrix(tmp_path_or_write(mat, tmp_path))

    rubrics_in = tmp_path / "rubrics_in.jsonl"
    _make_rubrics(dense + sparse + const, rubrics_in)
    original = rubrics_in.read_text()

    calib, diag = cp.run_fit(loaded, "rasch", min_persons_per_item=15, min_items_per_person=1)
    out_path = tmp_path / "rubrics_calibrated.jsonl"
    written, n_updated = cp.write_calibrated_rubrics(
        rubrics_in, out_path, calib, "rasch", _ns(), {"csv": "x"}
    )

    # Input untouched.
    assert rubrics_in.read_text() == original
    assert written.is_file()
    assert n_updated == len(calib.items)  # only the fitted variant criteria

    recs = {json.loads(l)["criterion_id"]: json.loads(l)
            for l in out_path.read_text().splitlines() if l.strip()}
    # Fitted criteria: new a_unidim field + calibrated source; 3-vector untouched.
    fitted = recs["c02"]
    assert "a_unidim" in fitted
    assert fitted["irt_params"]["source"] == "calibrated-2pl-partial"
    assert fitted["discrimination"] == {"content": 1.5, "diagnosis": 0.0, "scaffolding": 0.0}
    # Unfitted (sparse) criteria: fully synthetic, no a_unidim.
    unfitted = recs["s00"]
    assert "a_unidim" not in unfitted
    assert unfitted["irt_params"]["source"] == "synthetic"
    assert unfitted["difficulty"] == 0.1234
    # Zero-variance criteria: never fitted, stay synthetic.
    assert "a_unidim" not in recs["zf00"]
    assert recs["zp00"]["irt_params"]["source"] == "synthetic"


def test_write_params_refuses_inplace(tmp_path: Path):
    # The in-place guard fires before any fit is needed, so a fake calib is fine.
    mat, dense, *_ = _make_matrix()
    rubrics_in = tmp_path / "rubrics_in.jsonl"
    _make_rubrics(dense, rubrics_in)
    with pytest.raises(cp.CalibrationError):
        cp.write_calibrated_rubrics(
            rubrics_in, rubrics_in, _FakeCalib(dense), "rasch", _ns(), {}
        )


# --- pyirt (optional) -----------------------------------------------------


def test_pyirt_sparse_branch_if_available(tmp_path: Path):
    pytest.importorskip("py_irt")
    mat, dense, sparse, const = _make_matrix()
    loaded = cp.load_matrix(tmp_path_or_write(mat, tmp_path))
    calib, diag = cp.run_fit(loaded, "pyirt", min_persons_per_item=15, min_items_per_person=1)
    # Sparse path keeps all criteria at selection, then drops the 2 constants.
    assert diag["block_cols"] == N_CRITERIA
    assert diag["dropped_all_fail"] >= 1 and diag["dropped_all_pass"] >= 1
    assert "zf00" not in calib.items and "zp00" not in calib.items
    assert diag["n_items_fit"] == len(calib.items)


# --- helpers --------------------------------------------------------------


def tmp_path_or_write(mat: pd.DataFrame, tmp_path: Path) -> Path:
    csv = tmp_path / "response_matrix.csv"
    _write_matrix_csv(mat, csv)
    return csv


class _FakeCalib:
    def __init__(self, items):
        self.items = list(items)

    def a_by_item(self):
        return {i: 1.0 for i in self.items}

    def b_by_item(self):
        return {i: 0.0 for i in self.items}

    method = "fake"
    n_models = 1


def _ns():
    import argparse

    return argparse.Namespace(min_persons_per_item=15, min_items_per_person=1)
