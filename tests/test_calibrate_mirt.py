"""Hermetic tests for the confirmatory M2PL calibration path.

Simulates a RECOVERABLE 3-dim M2PL dataset: ~300 persons, 3-dim theta ~ MVN(known
corr), a set of items with a KNOWN confirmatory Q pattern (some unidimensional, some
multi-loaded), 0/1 responses drawn from ``P = sigmoid(a . theta - b)``, with injected
holes. Then fits and asserts:

  (a) loadings are EXACTLY 0 wherever q == 0 (confirmatory mask holds),
  (b) free loadings + difficulties recover the truth within tolerance,
  (c) missing cells are handled (fit completes, no NaN),
  (d) multi beats uni on AIC/BIC for the multi-generated data,
  (e) --estimate-latent-corr recovers a positive latent correlation,
  (f) --write-params emits a NEW rubric file WITHOUT mutating the input and refuses
      to overwrite the frozen final bank.

Hermetic: tmp_path, seeded RNG, no network. Small GH grid for speed.
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
SCRIPT_PATH = ROOT / "scripts" / "calibrate_mirt.py"
SPEC = importlib.util.spec_from_file_location("calibrate_mirt", SCRIPT_PATH)
assert SPEC and SPEC.loader
cm = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cm
SPEC.loader.exec_module(cm)

SKILLS = cm.SKILLS  # (content, diagnosis, scaffolding)


# --- simulation -----------------------------------------------------------


def _q_patterns() -> list[np.ndarray]:
    """A confirmatory Q design: unidim on each skill, pairwise, and all-three,
    repeated so every free loading is well estimated."""
    base = [
        (1, 0, 0), (0, 1, 0), (0, 0, 1),   # unidimensional
        (1, 1, 0), (1, 0, 1), (0, 1, 1),   # pairwise multi-loaded
        (1, 1, 1),                          # fully multi-loaded
    ]
    patterns = []
    for _ in range(3):  # 3 copies -> 21 items
        for b in base:
            patterns.append(np.array(b, dtype=int))
    return patterns


def _simulate(n_persons: int, corr: float, seed: int, hole_frac: float = 0.15):
    rng = np.random.default_rng(seed)
    patterns = _q_patterns()
    n_items = len(patterns)
    Q = np.array(patterns, dtype=int)

    # True loadings: positive on free dims (so sign is identified), 0 elsewhere.
    # Use a WIDE dynamic range (SD ~0.58) so a correlation-based recovery check
    # measures estimator accuracy rather than being swamped by sampling noise: with
    # a narrow range (e.g. 0.9-1.8, SD ~0.26) the per-item discrimination SE (~0.15
    # at these sample sizes) dominates and depresses the correlation regardless of
    # how accurate the fit is (this is the classic reliability = signal/(signal+
    # noise) effect). The absolute-error assertions below guard accuracy directly.
    A_true = np.zeros((n_items, 3))
    for j in range(n_items):
        free = Q[j] == 1
        A_true[j, free] = rng.uniform(0.5, 2.5, size=int(free.sum()))
    b_true = rng.normal(0.0, 0.8, size=n_items)

    # 3-dim latent with a known equicorrelation matrix.
    R = np.full((3, 3), corr)
    np.fill_diagonal(R, 1.0)
    L = np.linalg.cholesky(R)
    theta = rng.standard_normal((n_persons, 3)) @ L.T

    eta = theta @ A_true.T - b_true[None, :]           # (n_persons, n_items)
    p = 1.0 / (1.0 + np.exp(-eta))
    Y = (rng.random((n_persons, p.shape[1])) < p).astype(float)

    # Inject holes.
    holes = rng.random(Y.shape) < hole_frac
    items = [f"it{j:02d}" for j in range(n_items)]
    models = [f"m{k:03d}" for k in range(n_persons)]
    df = pd.DataFrame(Y, index=models, columns=items)
    df[holes] = np.nan
    df.index.name = "model"
    return df, Q, A_true, b_true, items, R


def _write_matrix_csv(df: pd.DataFrame, path: Path) -> None:
    out = df.copy()
    for c in out.columns:
        out[c] = out[c].map(lambda v: "" if pd.isna(v) else str(int(v)))
    out.to_csv(path)


def _write_rubrics(items, Q, path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for j, cid in enumerate(items):
            qmap = {s: int(Q[j, k]) for k, s in enumerate(SKILLS)}
            rec = {
                "criterion_id": cid,
                "scenario_id": "syn_000",
                "criterion": f"synthetic {cid}",
                "q_mapping": qmap,
                "difficulty": 0.4321,
                "discrimination": {s: 9.99 for s in SKILLS},  # obviously-synthetic sentinel
                "irt_params": {"source": "synthetic", "method": "metadata_heuristic_v1"},
            }
            f.write(json.dumps(rec) + "\n")


# --- grid / prior sanity --------------------------------------------------


def test_grid_weights_sum_to_one():
    for dims in (1, 3):
        base_logw = cm.base_log_weights(dims, 5)
        assert np.isclose(np.exp(base_logw).sum(), 1.0, atol=1e-8)
    # First two moments of the 1-d standard-normal quadrature.
    x = cm.build_grid(1, 7)[:, 0]
    w = np.exp(cm.base_log_weights(1, 7))
    assert abs(float(np.sum(w * x))) < 1e-8
    assert abs(float(np.sum(w * x ** 2)) - 1.0) < 1e-8


def test_prior_reweight_identity_matches_base():
    grid = cm.build_grid(3, 5)
    base = cm.base_log_weights(3, 5)
    lp = cm.prior_log_weights(grid, base, np.eye(3))
    assert np.allclose(lp, base, atol=1e-10)


# --- confirmatory mask + recovery -----------------------------------------


@pytest.fixture(scope="module")
def fitted_independent():
    df, Q, A_true, b_true, items, _ = _simulate(n_persons=500, corr=0.0, seed=7)
    q_by = {items[j]: Q[j] for j in range(len(items))}
    Y, M, Qa, aligned, block_df, diag = cm.prepare_block(df, q_by)
    # No zero-variance / missing at this N: every item survives, order preserved.
    assert aligned == items
    # Production-default grid (7 nodes/dim) is sufficient; a few hundred persons +
    # a wide loading range make recovery tight.
    res = cm.fit_m2pl_em(Y, M, Qa, nodes_per_dim=7, estimate_corr=False,
                         ridge=1e-3, max_iter=250, tol=1e-3)
    return res, Qa, A_true, b_true, M, diag


def test_confirmatory_mask_exact_zeros(fitted_independent):
    res, Q, *_ = fitted_independent
    A = res["A"]
    # Every q==0 cell must be EXACTLY zero.
    assert np.all(A[Q == 0] == 0.0)
    # Free cells are non-trivial (not left at zero).
    assert np.all(np.abs(A[Q == 1]) > 1e-3)


def test_no_nan_and_holes_handled(fitted_independent):
    res, Q, A_true, b_true, M, diag = fitted_independent
    assert np.all(np.isfinite(res["A"]))
    assert np.all(np.isfinite(res["b"]))
    # Holes were present: observed cells strictly fewer than the full block.
    assert int(M.sum()) < M.size


def test_recovers_free_loadings_and_difficulty(fitted_independent):
    res, Q, A_true, b_true, M, diag = fitted_independent
    A = res["A"]
    free_est = A[Q == 1]
    free_true = A_true[Q == 1]
    # Strong correlation + bounded error on the free loadings. With a wide true
    # range and ~500 persons the estimator recovers loadings tightly, so we hold a
    # high bar (well above the 0.75 minimum the task set).
    assert np.corrcoef(free_est, free_true)[0, 1] > 0.85
    assert np.median(np.abs(free_est - free_true)) < 0.35
    assert np.max(np.abs(free_est - free_true)) < 1.0
    # Difficulty recovery.
    assert np.corrcoef(res["b"], b_true)[0, 1] > 0.85
    assert np.median(np.abs(res["b"] - b_true)) < 0.35


def test_multi_beats_uni_on_information_criteria():
    # Fit both uni and multi on the SAME simulated data for a clean comparison.
    df, Qsim, _, _, items, _ = _simulate(n_persons=320, corr=0.0, seed=7)
    q_by = {items[j]: Qsim[j] for j in range(len(items))}
    Yf, Mf, Qa, aligned, _, _ = cm.prepare_block(df, q_by)
    n_obs = int(Mf.sum())

    uni = cm.fit_m2pl_em(Yf, Mf, np.ones((Qa.shape[0], 1), dtype=int), 5,
                         estimate_corr=False, ridge=1e-3, max_iter=200, tol=1e-4)
    multi = cm.fit_m2pl_em(Yf, Mf, Qa, 5, estimate_corr=True,
                           ridge=1e-3, max_iter=200, tol=1e-4)
    uni_aic, uni_bic = cm.aic_bic(uni["loglik"], uni["n_params"], n_obs)
    multi_aic, multi_bic = cm.aic_bic(multi["loglik"], multi["n_params"], n_obs)
    assert multi["loglik"] > uni["loglik"]
    assert multi_aic < uni_aic
    assert multi_bic < uni_bic


def test_estimate_latent_corr_recovers_positive():
    df, Q, A_true, b_true, items, R = _simulate(n_persons=400, corr=0.4, seed=13)
    q_by = {items[j]: Q[j] for j in range(len(items))}
    Y, M, Qa, aligned, _, _ = cm.prepare_block(df, q_by)
    res = cm.fit_m2pl_em(Y, M, Qa, nodes_per_dim=5, estimate_corr=True,
                         ridge=1e-3, max_iter=200, tol=1e-4)
    Rhat = res["R"]
    assert np.allclose(np.diag(Rhat), 1.0, atol=1e-6)
    off = Rhat[np.triu_indices(3, k=1)]
    # True off-diagonal is +0.4; expect clearly positive, roughly in the ballpark.
    assert np.all(off > 0.1)
    assert np.all(off < 0.9)
    assert abs(float(np.mean(off)) - 0.4) < 0.25


# --- selection: zero-variance + missing-Q dropping ------------------------


def test_prepare_block_drops_zero_variance_and_missing_q(tmp_path: Path):
    df, Q, A_true, b_true, items, _ = _simulate(n_persons=120, corr=0.0, seed=3)
    # Force a zero-variance (all-pass) and (all-fail) column.
    df["it00"] = 1.0
    df["it01"] = 0.0
    # Add a column with NO Q-row in the bank (should be dropped as unfittable).
    df["orphan"] = (np.arange(len(df)) % 2).astype(float)
    df.index.name = "model"

    q_by = {items[j]: Q[j] for j in range(len(items))}  # 'orphan' absent on purpose
    Y, M, Qa, aligned, _, diag = cm.prepare_block(df, q_by)

    assert "orphan" not in aligned
    assert diag["dropped_missing_qrow"] >= 1
    assert diag["dropped_all_pass"] >= 1
    assert diag["dropped_all_fail"] >= 1
    assert "it00" not in aligned and "it01" not in aligned
    # Q rows align 1:1 with the surviving items.
    assert Qa.shape[0] == len(aligned)
    assert np.all(Qa.sum(axis=1) >= 1)


# --- output frame + write-params ------------------------------------------


def test_build_frame_schema_and_masked_zeros():
    items = ["a", "b"]
    A = np.array([[1.2, 0.0, 0.0], [0.5, 0.0, 0.8]])
    b = np.array([0.1, -0.3])
    frame = cm.build_frame(items, A, b, np.array([50, 60]), ["", "low_n"])
    assert list(frame.columns) == [
        "criterion_id", "a_content", "a_diagnosis", "a_scaffolding",
        "b", "n_persons", "flags",
    ]
    assert frame.loc[0, "a_diagnosis"] == 0.0
    assert frame.loc[1, "a_diagnosis"] == 0.0


def test_write_params_new_file_no_mutation(tmp_path: Path):
    df, Q, A_true, b_true, items, _ = _simulate(n_persons=80, corr=0.0, seed=5)
    rubrics_in = tmp_path / "rubrics_in.jsonl"
    _write_rubrics(items, Q, rubrics_in)
    original = rubrics_in.read_text()

    A = np.zeros((len(items), 3))
    A[Q == 1] = 1.4
    b = np.full(len(items), 0.2)
    out_path = tmp_path / "rubrics_mirt.jsonl"

    class _Args:
        grid = 5
        estimate_latent_corr = True
        ridge = 1e-3

    written, n_updated = cm.write_mirt_rubrics(
        rubrics_in, out_path, items, A, b, _Args(), {"csv": "x"}, np.eye(3)
    )
    assert rubrics_in.read_text() == original  # input untouched
    assert n_updated == len(items)

    recs = {json.loads(l)["criterion_id"]: json.loads(l)
            for l in out_path.read_text().splitlines() if l.strip()}
    fitted = recs[items[0]]
    assert fitted["irt_params"]["source"] == "calibrated-m2pl"
    # Masked 3-vector: 0 exactly where q == 0.
    for k, s in enumerate(SKILLS):
        if Q[0, k] == 0:
            assert fitted["discrimination"][s] == 0.0
        else:
            assert fitted["discrimination"][s] == pytest.approx(1.4, abs=1e-6)
    assert fitted["difficulty"] == pytest.approx(0.2, abs=1e-6)


def test_write_params_refuses_inplace_and_final(tmp_path: Path):
    df, Q, A_true, b_true, items, _ = _simulate(n_persons=40, corr=0.0, seed=9)
    rubrics_in = tmp_path / "rubrics_in.jsonl"
    _write_rubrics(items, Q, rubrics_in)
    A = np.zeros((len(items), 3)); A[Q == 1] = 1.0
    b = np.zeros(len(items))

    class _Args:
        grid = 5
        estimate_latent_corr = False
        ridge = 1e-3

    with pytest.raises(cm.CalibrationError):
        cm.write_mirt_rubrics(rubrics_in, rubrics_in, items, A, b, _Args(), {}, None)

    final_like = tmp_path / "rubrics_qmatrix_final.jsonl"
    with pytest.raises(cm.CalibrationError):
        cm.write_mirt_rubrics(rubrics_in, final_like, items, A, b, _Args(), {}, None)
