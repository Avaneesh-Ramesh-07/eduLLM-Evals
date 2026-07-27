"""Confirmatory MULTIDIMENSIONAL 2PL (M2PL) calibration from the (partial) response
matrix, with the frozen 3-skill Q-matrix.

Context
-------
This is the *deferred* multidimensional fit promised by
``scripts/calibrate_partial.py`` (which does the UNIDIMENSIONAL, preliminary 2PL).
It closes the audit gaps GAP-5/6/7: it estimates a genuinely multidimensional item
bank (per-skill discrimination loadings + a scalar difficulty) under the
CONFIRMATORY constraint that each item may only load on the skills its Q-matrix row
marks (``q_k == 1``); every ``q_k == 0`` loading is held at EXACTLY zero.

The fitter is PURE numpy/scipy (no torch / JAX / rpy2 / girth). It is portable to
Python 3.14 where those wheels are risky. The only optional dependency is
``factor_analyzer`` for the ``--efa`` scree diagnostic; it is guarded and skipped
cleanly when absent.

Model
-----
For person ``i`` with a 3-dim latent ability ``theta_i ~ MVN(0, R)`` and item ``j``
with loading vector ``a_j`` (3-vector, masked by the Q row) and scalar difficulty
``b_j``::

    P(y_ij = 1 | theta_i) = sigmoid( a_j . theta_i - b_j )

This reduces to the ordinary 2PL ``sigmoid(a (theta - b_loc))`` when the item loads
on a single dimension (with the offset ``b = a * b_loc``). ``b`` here is the natural
M2PL scalar *offset* difficulty (a.k.a. ``-d`` intercept), reported consistently for
both the uni and multi fits so their log-likelihoods are directly comparable.

Estimation
----------
Marginal maximum likelihood by the Bock--Aitkin EM algorithm over a FIXED
Gauss--Hermite quadrature grid (``--grid`` nodes per dimension; total ``grid**3``
nodes for the 3-d model -- cost scales as grid^3):

* E-step: posterior over the latent grid per person, using ONLY the observed
  (non-NaN) cells for that person -> holes are handled natively (marginalised).
* M-step: per item, a masked weighted-logistic Newton solve for the free loadings
  (only ``q_k == 1`` dims) plus the scalar difficulty, using the E-step expected
  counts at each grid node.

The latent correlation ``R`` starts at the identity. ``--estimate-latent-corr``
re-estimates the 3x3 latent *correlation* matrix from the posterior second moments
each EM iteration (variances fixed to 1 for identifiability; the loadings carry the
scale). The off-diagonals of ``R`` are the "are the 3 skills distinguishable?"
evidence: near +/-1 correlations mean the model is collapsing toward
unidimensionality.

Uni-vs-multi comparison
-----------------------
We ALSO fit the UNIDIMENSIONAL 2PL baseline. To keep the log-likelihood / AIC / BIC
comparison apples-to-apples (same marginal-ML likelihood, same native missing-data
handling) the baseline is the SAME EM machinery run with ``n_dims = 1`` and a single
free loading per item. When ``girth`` happens to be importable we ALSO run
``tutor_cat.mcq_irt.calibrate.fit_2pl`` as an auxiliary cross-check and report the
rank agreement of its ``a`` / ``b`` with our internal unidim fit (girth cannot
consume holes, so it is a dense-block cross-check only, never the primary baseline).

Identifiability WARNING
-----------------------
A 3-dim confirmatory M2PL needs a healthy person sample to be identified. On the
current ~1/4-filled fleet (~20 strict-judge persons) the 3-dim model is NOT
identifiable -- run this on the FULL graded matrix. The script prints and records a
WARNING whenever the fitted person count is below ``--min-persons-identifiable``
(default 150). It still RUNS on tiny data (and on the synthetic tests) so the
machinery can be exercised now; just do not trust tiny-N numbers.

Outputs
-------
* ``staging/calibration_mirt.csv``     -- per fitted criterion: a_content,
  a_diagnosis, a_scaffolding (0 where q=0), b, n_persons, flags.
* ``staging/calibration_mirt_manifest.json`` -- method, grid, fit dimensions,
  dropped zero-variance items, loglik/AIC/BIC for uni & multi, latent corr, and the
  identifiability warning.
* ``--write-params`` -> a NEW file ``data/rubrics_qmatrix_mirt.jsonl`` with the
  masked 3-vector ``discrimination``, scalar ``difficulty``, and
  ``irt_params.source = "calibrated-m2pl"`` + provenance. The frozen
  ``data/rubrics_qmatrix_final.jsonl`` is NEVER overwritten.

Usage
-----
    # coverage report only (no fit):
    python scripts/calibrate_mirt.py --report-only

    # fit on the full matrix later (7 nodes/dim = 343 nodes), estimate latent corr:
    python scripts/calibrate_mirt.py --estimate-latent-corr

    # smaller/faster grid + optional EFA scree:
    python scripts/calibrate_mirt.py --grid 5 --efa

    # also write a calibrated rubric COPY (new file, never overwrites final):
    python scripts/calibrate_mirt.py --estimate-latent-corr --write-params

    # show the plan without fitting:
    python scripts/calibrate_mirt.py --dry-run
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import expit, log_expit, logsumexp

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tutor_cat import SKILLS  # noqa: E402  (order: content, diagnosis, scaffolding)

# Reuse the sibling script's IO / coverage / zero-variance helpers verbatim so the
# two calibrators stay behaviourally identical (same loader, same coverage report,
# same zero-variance definition, same manifest/provenance conventions).
_CP_PATH = ROOT / "scripts" / "calibrate_partial.py"
_spec = importlib.util.spec_from_file_location("calibrate_partial", _CP_PATH)
assert _spec and _spec.loader
cp = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("calibrate_partial", cp)
_spec.loader.exec_module(cp)

N_SKILLS = len(SKILLS)

DEFAULT_MATRIX = ROOT / "staging" / "response_matrix.csv"
DEFAULT_RUBRICS = ROOT / "data" / "rubrics_qmatrix_final.jsonl"
DEFAULT_OUT_DIR = ROOT / "staging"
DEFAULT_MIRT_RUBRICS = ROOT / "data" / "rubrics_qmatrix_mirt.jsonl"

CALIBRATION_CSV_NAME = "calibration_mirt.csv"
CALIBRATION_MANIFEST_NAME = "calibration_mirt_manifest.json"

CALIBRATION_SOURCE = "calibrated-m2pl"

# Loadings above this are treated as unstable (thin-sample / near-separation).
EXTREME_A = 6.0


class CalibrationError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Q-matrix
# ---------------------------------------------------------------------------


def load_q_matrix(rubrics_path: Path) -> dict[str, np.ndarray]:
    """Map criterion_id -> (3,) 0/1 Q row (order = SKILLS) from the rubric bank."""
    if not rubrics_path.is_file():
        raise FileNotFoundError(f"rubric bank not found: {rubrics_path}")
    q_by: dict[str, np.ndarray] = {}
    for rec in cp.read_jsonl(rubrics_path):
        cid = rec.get("criterion_id")
        qmap = rec.get("q_mapping")
        if cid is None or not isinstance(qmap, dict):
            continue
        q_by[cid] = np.array([int(qmap.get(s, 0)) for s in SKILLS], dtype=int)
    return q_by


def align_q_rows(
    columns: list[str], q_by: dict[str, np.ndarray]
) -> tuple[np.ndarray, list[str], list[str]]:
    """Build the Q matrix aligned to ``columns``.

    Returns (Q, aligned_columns, missing). Criteria absent from the bank, or whose
    Q row is all-zero (no skill assigned -> unfittable under confirmatory masking),
    are reported in ``missing`` and excluded from the fit.
    """
    rows: list[np.ndarray] = []
    aligned: list[str] = []
    missing: list[str] = []
    for c in columns:
        q = q_by.get(c)
        if q is None or int(q.sum()) == 0:
            missing.append(c)
            continue
        rows.append(q)
        aligned.append(c)
    Q = np.array(rows, dtype=int) if rows else np.zeros((0, N_SKILLS), dtype=int)
    return Q, aligned, missing


# ---------------------------------------------------------------------------
# Gauss-Hermite quadrature grid
# ---------------------------------------------------------------------------


def build_grid(n_dims: int, nodes_per_dim: int) -> np.ndarray:
    """Fixed Gauss-Hermite grid nodes for a standard-normal latent (per dim).

    Returns an (n_nodes, n_dims) array of node coordinates. The companion
    standard-normal quadrature weights are obtained from :func:`base_log_weights`.
    Physicists' GH integrates ``int e^{-x^2} g(x) dx``; the change of variables
    ``theta = sqrt(2) x`` maps it to ``int phi(theta) g(theta) dtheta`` (standard
    normal), so node positions are ``sqrt(2) * x`` and weights ``w / sqrt(pi)``.
    """
    x, _ = np.polynomial.hermite_e.hermegauss(nodes_per_dim)  # probabilists' GH
    # hermegauss integrates int e^{-x^2/2} g(x) dx ~ sum w g(x); nodes are already
    # on the standard-normal scale. Grid = cartesian product over dims.
    mesh = np.meshgrid(*([x] * n_dims), indexing="ij")
    grid = np.stack([m.reshape(-1) for m in mesh], axis=1)
    return grid.astype(float)


def _base_1d_weights(nodes_per_dim: int) -> tuple[np.ndarray, np.ndarray]:
    x, w = np.polynomial.hermite_e.hermegauss(nodes_per_dim)
    w = w / np.sqrt(2.0 * np.pi)  # normalise to a standard-normal density weight
    return x, w


def base_log_weights(n_dims: int, nodes_per_dim: int) -> np.ndarray:
    """log of the product standard-normal quadrature weights over the grid."""
    _, w = _base_1d_weights(nodes_per_dim)
    logw = np.log(w)
    mesh = np.meshgrid(*([logw] * n_dims), indexing="ij")
    return np.sum(np.stack([m.reshape(-1) for m in mesh], axis=1), axis=1)


def prior_log_weights(grid: np.ndarray, base_logw: np.ndarray, R: np.ndarray) -> np.ndarray:
    """Log prior weight of each grid node under MVN(0, R), normalised to sum 1.

    The fixed grid + ``base_logw`` approximate ``int g(theta) prod phi(theta_k)``.
    To integrate against MVN(0, R) we reweight each node by the density ratio
    ``N(node; 0, R) / prod phi(node_k)`` (in log space, this drops the per-dim
    normalisers and leaves ``-0.5 theta' (R^-1 - I) theta - 0.5 log|R|``).
    """
    n_dims = grid.shape[1]
    if np.allclose(R, np.eye(n_dims)):
        logw = base_logw
    else:
        Rinv = np.linalg.inv(R)
        sign, logdet = np.linalg.slogdet(R)
        quad = np.einsum("gi,ij,gj->g", grid, (Rinv - np.eye(n_dims)), grid)
        logw = base_logw - 0.5 * quad - 0.5 * logdet
    logw = logw - logsumexp(logw)
    return logw


# ---------------------------------------------------------------------------
# M2PL EM
# ---------------------------------------------------------------------------


def _item_neg_loglik(beta, X, r, N, ridge):
    """Weighted-logistic negative loglik + gradient + Hessian for one item.

    ``X`` is (n_nodes, p) with a trailing constant column (the -b intercept).
    ``r`` = expected successes per node, ``N`` = expected count per node. ``ridge``
    penalises the loadings (not the intercept) for numerical stability.
    """
    eta = X @ beta
    logp = log_expit(eta)
    log1mp = log_expit(-eta)
    nll = -(np.dot(r, logp) + np.dot(N - r, log1mp))
    p = expit(eta)
    grad = X.T @ (N * p - r)
    W = N * p * (1.0 - p)
    H = (X * W[:, None]).T @ X
    if ridge > 0:
        pen = np.ones(beta.shape[0])
        pen[-1] = 0.0  # do not penalise the intercept
        nll = nll + 0.5 * ridge * float(np.dot(pen * beta, beta))
        grad = grad + ridge * (pen * beta)
        H = H + ridge * np.diag(pen)
    return nll, grad, H


def _fit_item(X, r, N, ridge, max_newton=50, tol=1e-8):
    """Newton-Raphson MLE for one item's [free loadings..., intercept]."""
    p = X.shape[1]
    beta = np.zeros(p)
    # Warm start the intercept from the pooled pass rate at this item.
    tot = float(N.sum())
    if tot > 0:
        pbar = float(r.sum()) / tot
        pbar = min(max(pbar, 1e-3), 1 - 1e-3)
        beta[-1] = np.log(pbar / (1.0 - pbar))
    nll, grad, H = _item_neg_loglik(beta, X, r, N, ridge)
    for _ in range(max_newton):
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(H, grad, rcond=None)[0]
        # Step-halving line search to guarantee descent.
        alpha = 1.0
        for _ in range(30):
            cand = beta - alpha * step
            new_nll, new_grad, new_H = _item_neg_loglik(cand, X, r, N, ridge)
            if np.isfinite(new_nll) and new_nll <= nll + 1e-12:
                break
            alpha *= 0.5
        else:
            break
        if abs(nll - new_nll) < tol and np.max(np.abs(beta - cand)) < tol:
            beta, nll, grad, H = cand, new_nll, new_grad, new_H
            break
        beta, nll, grad, H = cand, new_nll, new_grad, new_H
    return beta


def fit_m2pl_em(
    Y: np.ndarray,
    M: np.ndarray,
    Q: np.ndarray,
    nodes_per_dim: int,
    estimate_corr: bool = False,
    ridge: float = 1e-3,
    max_iter: int = 200,
    tol: float = 1e-4,
) -> dict:
    """Confirmatory M2PL by Bock-Aitkin EM.

    ``Y`` (n_persons, n_items) 0/1 (values under holes ignored), ``M`` the observed
    mask (bool), ``Q`` (n_items, n_dims) 0/1 confirmatory mask. Returns a dict with
    fitted ``A`` (n_items, n_dims; 0 exactly off-mask), ``b`` (n_items,), ``R``, the
    marginal ``loglik``, ``n_params``, ``n_iter`` and ``converged``.
    """
    n_persons, n_items = Y.shape
    n_dims = Q.shape[1]
    grid = build_grid(n_dims, nodes_per_dim)
    base_logw = base_log_weights(n_dims, nodes_per_dim)
    n_nodes = grid.shape[0]

    YM = np.where(M, Y, 0.0)          # observed successes
    NM = np.where(M, 1.0 - Y, 0.0)    # observed failures
    Mf = M.astype(float)

    A = np.zeros((n_items, n_dims))
    A[Q == 1] = 1.0                   # free loadings start at 1
    # Difficulty warm start from item pass rates (offset scale).
    obs_per_item = Mf.sum(axis=0)
    pass_rate = np.where(obs_per_item > 0, YM.sum(axis=0) / np.maximum(obs_per_item, 1), 0.5)
    pass_rate = np.clip(pass_rate, 1e-3, 1 - 1e-3)
    b = -np.log(pass_rate / (1.0 - pass_rate))
    R = np.eye(n_dims)

    # Precompute per-item free-dim column indices and design matrices over the grid.
    free_dims = [np.where(Q[j] == 1)[0] for j in range(n_items)]
    ones_col = np.ones((n_nodes, 1))
    designs = [np.hstack([grid[:, fd], ones_col]) for fd in free_dims]

    prev_ll = -np.inf
    converged = False
    n_iter = 0
    posterior = None
    for it in range(max_iter):
        n_iter = it + 1
        log_prior = prior_log_weights(grid, base_logw, R)
        # E-step: per-person loglik over nodes, then posterior.
        eta = A @ grid.T - b[:, None]              # (n_items, n_nodes)
        logP = log_expit(eta)
        log1mP = log_expit(-eta)
        LL = YM @ logP + NM @ log1mP               # (n_persons, n_nodes)
        joint = LL + log_prior[None, :]
        person_ll = logsumexp(joint, axis=1)       # (n_persons,)
        marg_ll = float(person_ll.sum())
        posterior = np.exp(joint - person_ll[:, None])  # (n_persons, n_nodes)

        # M-step: expected counts per item x node.
        r_jg = YM.T @ posterior                    # (n_items, n_nodes)
        N_jg = Mf.T @ posterior                    # (n_items, n_nodes)
        for j in range(n_items):
            beta = _fit_item(designs[j], r_jg[j], N_jg[j], ridge)
            A[j] = 0.0
            A[j, free_dims[j]] = beta[:-1]
            b[j] = -beta[-1]

        if estimate_corr and n_dims > 1:
            # Posterior second moment -> covariance -> correlation (var fixed to 1).
            w = posterior.sum(axis=0)              # (n_nodes,)
            Sigma = (grid.T * w) @ grid / n_persons
            d = np.sqrt(np.clip(np.diag(Sigma), 1e-8, None))
            R = Sigma / np.outer(d, d)
            R = np.clip(R, -0.999, 0.999)
            np.fill_diagonal(R, 1.0)

        if abs(marg_ll - prev_ll) < tol:
            converged = True
            prev_ll = marg_ll
            break
        prev_ll = marg_ll

    A[Q == 0] = 0.0  # enforce the confirmatory mask exactly

    # Recompute the marginal loglik with the FINAL params (the in-loop value lags
    # one M-step behind), so it matches the returned A/b for exact AIC/BIC.
    log_prior = prior_log_weights(grid, base_logw, R)
    eta = A @ grid.T - b[:, None]
    LL = YM @ log_expit(eta) + NM @ log_expit(-eta)
    final_ll = float(logsumexp(LL + log_prior[None, :], axis=1).sum())

    n_free_load = int(Q.sum())
    n_params = n_free_load + n_items
    if estimate_corr and n_dims > 1:
        n_params += n_dims * (n_dims - 1) // 2

    return {
        "A": A,
        "b": b,
        "R": R,
        "loglik": final_ll,
        "n_params": int(n_params),
        "n_free_loadings": n_free_load,
        "n_iter": n_iter,
        "converged": converged,
        "n_dims": n_dims,
        "grid_nodes": int(n_nodes),
    }


def aic_bic(loglik: float, n_params: int, n_obs: int) -> tuple[float, float]:
    aic = 2 * n_params - 2 * loglik
    bic = n_params * np.log(max(n_obs, 1)) - 2 * loglik
    return float(aic), float(bic)


# ---------------------------------------------------------------------------
# optional girth cross-check + EFA
# ---------------------------------------------------------------------------


def girth_crosscheck(block: pd.DataFrame, internal_items, internal_a, internal_b) -> dict:
    """Optional dense-block girth 2PL cross-check (rank agreement only). Guarded."""
    try:
        from tutor_cat.mcq_irt.calibrate import fit_2pl
    except Exception as e:  # pragma: no cover
        return {"available": False, "reason": f"import failed: {e}"}
    try:
        calib = fit_2pl(block, method="girth")
    except Exception as e:  # pragma: no cover - girth absent or degenerate block
        return {"available": False, "reason": str(e)}
    ga = calib.a_by_item()
    gb = calib.b_by_item()
    shared = [it for it in internal_items if it in ga]
    if len(shared) < 3:
        return {"available": True, "n_shared": len(shared)}
    idx = {it: k for k, it in enumerate(internal_items)}
    ia = pd.Series([internal_a[idx[it]] for it in shared])
    ib = pd.Series([internal_b[idx[it]] for it in shared])
    ca = pd.Series([ga[it] for it in shared])
    cb = pd.Series([gb[it] for it in shared])
    return {
        "available": True,
        "n_shared": len(shared),
        "a_rank_corr": float(ia.rank().corr(ca.rank())),
        "b_rank_corr": float(ib.rank().corr(cb.rank())),
    }


def run_efa(block: np.ndarray, n_factors: int = 3) -> dict:
    """Optional EFA / scree on the variant-item block. Skips cleanly if absent."""
    try:
        from factor_analyzer import FactorAnalyzer
    except Exception:
        return {"available": False, "reason": "factor_analyzer not installed"}
    try:
        fa = FactorAnalyzer(n_factors=n_factors, rotation="varimax")
        fa.fit(block)
        ev, _ = fa.get_eigenvalues()
        return {
            "available": True,
            "eigenvalues": [float(v) for v in ev[: max(n_factors + 3, 6)]],
            "n_factors_ge_1": int(np.sum(np.asarray(ev) >= 1.0)),
        }
    except Exception as e:  # pragma: no cover
        return {"available": False, "reason": str(e)}


# ---------------------------------------------------------------------------
# selection (holes handled natively) + fit driver
# ---------------------------------------------------------------------------


def prepare_block(mat: pd.DataFrame, q_by: dict[str, np.ndarray]) -> tuple:
    """Drop empty rows/cols, zero-variance items, and Q-less items; align Q rows.

    Returns (Y, M, Q, items, diag). Holes stay in-place (marginalised by the E-step);
    only fully-empty rows and unfittable columns are removed.
    """
    sub, sel_diag = cp.select_sparse(mat)
    kept, all_fail, all_pass = cp.split_zero_variance(sub)
    sub = sub[kept]
    sub = sub[sub.notna().any(axis=1)]

    Q, items, missing_q = align_q_rows(list(sub.columns), q_by)
    sub = sub[items]
    sub = sub[sub.notna().any(axis=1)]

    Y = np.nan_to_num(sub.to_numpy(dtype=float), nan=0.0)
    M = sub.notna().to_numpy()

    diag = {
        **sel_diag,
        "dropped_all_fail": len(all_fail),
        "dropped_all_pass": len(all_pass),
        "dropped_zero_variance_total": len(all_fail) + len(all_pass),
        "dropped_all_fail_criteria": all_fail,
        "dropped_all_pass_criteria": all_pass,
        "dropped_missing_qrow": len(missing_q),
        "dropped_missing_qrow_criteria": missing_q,
        "n_items_fit": int(sub.shape[1]),
        "n_persons_fit": int(sub.shape[0]),
        "q_pattern_counts": _q_pattern_counts(Q),
    }
    return Y, M, Q, items, sub, diag


def _q_pattern_counts(Q: np.ndarray) -> dict:
    counts: dict[str, int] = {}
    for row in Q:
        key = "".join(str(int(v)) for v in row)
        counts[key] = counts.get(key, 0) + 1
    return counts


def per_item_n_persons(M: np.ndarray) -> np.ndarray:
    return M.sum(axis=0).astype(int)


def item_flags(A: np.ndarray, Q: np.ndarray, n_persons_item: np.ndarray, min_ident: int) -> list[str]:
    flags = []
    for j in range(A.shape[0]):
        f = []
        free_vals = A[j][Q[j] == 1]
        if np.any(~np.isfinite(free_vals)) or np.any(np.abs(free_vals) > EXTREME_A):
            f.append("extreme_a")
        if n_persons_item[j] < min_ident:
            f.append("low_n")
        flags.append("|".join(f))
    return flags


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def build_frame(items, A, b, n_persons_item, flags) -> pd.DataFrame:
    data = {"criterion_id": items}
    for k, skill in enumerate(SKILLS):
        data[f"a_{skill}"] = np.round(A[:, k], 6)
    data["b"] = np.round(b, 6)
    data["n_persons"] = n_persons_item
    data["flags"] = flags
    cols = ["criterion_id"] + [f"a_{s}" for s in SKILLS] + ["b", "n_persons", "flags"]
    return pd.DataFrame(data)[cols]


def write_csv(frame: pd.DataFrame, out_dir: Path) -> Path:
    path = out_dir / CALIBRATION_CSV_NAME
    frame.to_csv(path, index=False)
    return path


def write_manifest(
    out_dir: Path,
    args: argparse.Namespace,
    diag: dict,
    multi: dict,
    uni: dict,
    n_obs: int,
    latent_corr,
    crosscheck: dict,
    efa: dict,
    matrix_prov: dict,
    identifiable: bool,
) -> Path:
    path = out_dir / CALIBRATION_MANIFEST_NAME
    multi_aic, multi_bic = aic_bic(multi["loglik"], multi["n_params"], n_obs)
    uni_aic, uni_bic = aic_bic(uni["loglik"], uni["n_params"], n_obs)
    warning = None
    if not identifiable:
        warning = (
            f"n_persons_fit={diag['n_persons_fit']} < "
            f"--min-persons-identifiable={args.min_persons_identifiable}: the 3-dim "
            "confirmatory M2PL is NOT identifiable at this sample size. Numbers are "
            "for machinery-exercise only -- run on the FULL graded matrix."
        )
    manifest = {
        "generated_at": cp._utcnow(),
        "note": (
            "CONFIRMATORY MULTIDIMENSIONAL 2PL (M2PL) fit, pure numpy/scipy, "
            "Bock-Aitkin EM over a Gauss-Hermite grid. Loadings masked by the "
            "3-skill Q-matrix (a_k free iff q_k==1, else exactly 0)."
        ),
        "method": "confirmatory-m2pl-mml-em",
        "skills_order": list(SKILLS),
        "grid_nodes_per_dim": args.grid,
        "grid_total_nodes": multi["grid_nodes"],
        "ridge": args.ridge,
        "estimate_latent_corr": args.estimate_latent_corr,
        "em": {
            "max_iter": args.max_iter,
            "tol": args.tol,
            "multi_n_iter": multi["n_iter"],
            "multi_converged": multi["converged"],
            "uni_n_iter": uni["n_iter"],
            "uni_converged": uni["converged"],
        },
        "block": diag,
        "n_items_fit": diag["n_items_fit"],
        "n_persons_fit": diag["n_persons_fit"],
        "n_observed_cells": n_obs,
        "dropped_zero_variance": diag["dropped_zero_variance_total"],
        "dropped_all_fail": diag["dropped_all_fail"],
        "dropped_all_pass": diag["dropped_all_pass"],
        "dropped_missing_qrow": diag["dropped_missing_qrow"],
        "comparison": {
            "multi": {
                "n_dims": multi["n_dims"],
                "loglik": multi["loglik"],
                "n_params": multi["n_params"],
                "aic": multi_aic,
                "bic": multi_bic,
            },
            "uni": {
                "n_dims": 1,
                "loglik": uni["loglik"],
                "n_params": uni["n_params"],
                "aic": uni_aic,
                "bic": uni_bic,
            },
            "multi_beats_uni_aic": bool(multi_aic < uni_aic),
            "multi_beats_uni_bic": bool(multi_bic < uni_bic),
            "delta_aic_uni_minus_multi": float(uni_aic - multi_aic),
            "delta_bic_uni_minus_multi": float(uni_bic - multi_bic),
            "bic_sample_size_convention": "n_observed_cells",
        },
        "latent_correlation": (
            np.round(np.asarray(latent_corr), 6).tolist() if latent_corr is not None else None
        ),
        "girth_crosscheck": crosscheck,
        "efa": efa,
        "identifiability": {
            "min_persons_identifiable": args.min_persons_identifiable,
            "identifiable": identifiable,
            "warning": warning,
        },
        "matrix": matrix_prov,
        "provenance": {"script": "scripts/calibrate_mirt.py", "argv": sys.argv[1:]},
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return path


def write_mirt_rubrics(
    rubrics_path: Path,
    out_path: Path,
    items,
    A,
    b,
    args: argparse.Namespace,
    matrix_prov: dict,
    latent_corr,
) -> tuple[Path, int]:
    """Write a COPY of the rubric bank with the fitted masked 3-vector + scalar b.

    Fitted criteria get ``discrimination`` = the masked 3-vector (0 where q=0),
    ``difficulty`` = b, and ``irt_params.source = 'calibrated-m2pl'`` + provenance.
    Non-fitted criteria keep all synthetic values. Never mutates the input; refuses
    to write over ``rubrics_qmatrix_final.jsonl`` or the input path.
    """
    if out_path.resolve() == rubrics_path.resolve():
        raise CalibrationError("refusing to overwrite the input rubric bank in place.")
    if out_path.name == DEFAULT_RUBRICS.name:
        raise CalibrationError(
            "refusing to write to the frozen rubrics_qmatrix_final.jsonl; "
            "choose a different --out-rubrics."
        )
    records = cp.read_jsonl(rubrics_path)
    a_by = {it: A[i] for i, it in enumerate(items)}
    b_by = {it: float(b[i]) for i, it in enumerate(items)}

    provenance = {
        "source": CALIBRATION_SOURCE,
        "method": "confirmatory-m2pl-mml-em",
        "grid_nodes_per_dim": args.grid,
        "estimate_latent_corr": args.estimate_latent_corr,
        "ridge": args.ridge,
        "latent_correlation": (
            np.round(np.asarray(latent_corr), 6).tolist() if latent_corr is not None else None
        ),
        "matrix": matrix_prov,
        "calibrated_at": cp._utcnow(),
        "version": "1.0",
    }

    n_updated = 0
    for rec in records:
        cid = rec.get("criterion_id")
        if cid in a_by:
            avec = a_by[cid]
            if np.all(np.isfinite(avec)) and np.isfinite(b_by[cid]):
                rec["discrimination"] = {
                    s: round(float(avec[k]), 4) for k, s in enumerate(SKILLS)
                }
                rec["difficulty"] = round(b_by[cid], 4)
                rec["irt_params"] = dict(provenance)
                n_updated += 1
    cp.write_jsonl(out_path, records)
    return out_path, n_updated


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX,
                   help=f"response matrix CSV (default: {DEFAULT_MATRIX}).")
    p.add_argument("--rubrics", type=Path, default=DEFAULT_RUBRICS,
                   help=f"rubric bank JSONL for the Q-matrix (default: {DEFAULT_RUBRICS}).")
    p.add_argument("--grid", type=int, default=7,
                   help="Gauss-Hermite nodes per latent dim (default 7 -> 343 nodes; "
                        "cost scales as grid^3).")
    p.add_argument("--estimate-latent-corr", action="store_true",
                   help="estimate the 3x3 latent correlation from the posterior "
                        "(default: fixed identity).")
    p.add_argument("--ridge", type=float, default=1e-3,
                   help="L2 ridge on loadings in the M-step for stability (default 1e-3).")
    p.add_argument("--max-iter", type=int, default=200, help="max EM iterations.")
    p.add_argument("--tol", type=float, default=1e-4,
                   help="EM convergence tol on marginal loglik (default 1e-4).")
    p.add_argument("--min-persons-identifiable", type=int, default=150,
                   help="warn if persons < this (3-dim M2PL identifiability; default 150).")
    p.add_argument("--efa", action="store_true",
                   help="optional EFA/scree diagnostic (needs factor_analyzer; guarded).")
    p.add_argument("--report-only", action="store_true",
                   help="emit the coverage report and exit (no fit).")
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan (block shape, grid) and exit without fitting.")
    p.add_argument("--write-params", action="store_true",
                   help="also write a calibrated COPY of the rubric bank (OFF by default).")
    p.add_argument("--out-rubrics", type=Path, default=DEFAULT_MIRT_RUBRICS,
                   help=f"output rubric JSONL for --write-params (default: {DEFAULT_MIRT_RUBRICS}).")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                   help=f"directory for calibration outputs (default: {DEFAULT_OUT_DIR}).")
    args = p.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    try:
        mat = cp.load_matrix(args.matrix)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    cov = cp.compute_coverage(mat)
    cp.print_coverage(cov)
    cov_csv, cov_json = cp.write_coverage_reports(cov, args.out_dir)
    print(f"\nwrote coverage report -> {cov_csv}")
    print(f"wrote coverage summary -> {cov_json}")

    if args.report_only:
        return 0

    try:
        q_by = load_q_matrix(args.rubrics)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    Y, M, Q, items, block_df, diag = prepare_block(mat, q_by)

    n_items = diag["n_items_fit"]
    n_persons = diag["n_persons_fit"]
    print("\n" + "=" * 72)
    print("confirmatory M2PL calibration plan")
    print("=" * 72)
    print(f"fitting {n_items} items x {n_persons} persons, {args.grid} nodes/dim "
          f"({args.grid ** N_SKILLS} grid nodes)")
    print(f"dropped zero-variance : {diag['dropped_zero_variance_total']} "
          f"({diag['dropped_all_fail']} all-fail, {diag['dropped_all_pass']} all-pass)")
    print(f"dropped missing Q-row : {diag['dropped_missing_qrow']}")
    print(f"Q pattern counts      : {diag['q_pattern_counts']}")

    identifiable = n_persons >= args.min_persons_identifiable
    if not identifiable:
        print(f"WARNING: n_persons={n_persons} < {args.min_persons_identifiable}; the "
              "3-dim M2PL is NOT identifiable at this N. Run on the FULL matrix; "
              "treating this as a machinery-exercise run.")

    if args.dry_run:
        print("\n--dry-run: not fitting. Re-run without --dry-run to calibrate.")
        return 0

    if n_items < 1 or n_persons < 2:
        print("\nERROR: nothing fittable after selection (need >=1 item, >=2 persons).",
              file=sys.stderr)
        return 3

    n_obs = int(M.sum())

    print("\nfitting UNIDIMENSIONAL 2PL baseline (EM, 1 dim) ...")
    Q_uni = np.ones((n_items, 1), dtype=int)
    uni = fit_m2pl_em(Y, M, Q_uni, args.grid, estimate_corr=False,
                      ridge=args.ridge, max_iter=args.max_iter, tol=args.tol)

    print(f"fitting CONFIRMATORY M2PL ({N_SKILLS} dims) ...")
    multi = fit_m2pl_em(Y, M, Q, args.grid, estimate_corr=args.estimate_latent_corr,
                        ridge=args.ridge, max_iter=args.max_iter, tol=args.tol)

    A, b = multi["A"], multi["b"]
    latent_corr = multi["R"] if args.estimate_latent_corr else None

    n_persons_item = per_item_n_persons(M)
    flags = item_flags(A, Q, n_persons_item, args.min_persons_identifiable)

    multi_aic, multi_bic = aic_bic(multi["loglik"], multi["n_params"], n_obs)
    uni_aic, uni_bic = aic_bic(uni["loglik"], uni["n_params"], n_obs)

    print("\n" + "=" * 72)
    print("uni vs multi")
    print("=" * 72)
    print(f"unidim  : loglik={uni['loglik']:.2f}  k={uni['n_params']}  "
          f"AIC={uni_aic:.2f}  BIC={uni_bic:.2f}")
    print(f"multi   : loglik={multi['loglik']:.2f}  k={multi['n_params']}  "
          f"AIC={multi_aic:.2f}  BIC={multi_bic:.2f}")
    print(f"multi beats uni : AIC={multi_aic < uni_aic}  BIC={multi_bic < uni_bic}")
    if latent_corr is not None:
        print("latent correlation (content, diagnosis, scaffolding):")
        for row in np.round(latent_corr, 3):
            print("   ", row.tolist())

    crosscheck = girth_crosscheck(block_df.dropna(axis=0, how="any"), items,
                                  uni["A"][:, 0], uni["b"])
    efa = run_efa(np.nan_to_num(block_df.to_numpy(dtype=float)), N_SKILLS) if args.efa else \
        {"available": False, "reason": "not requested (pass --efa)"}
    if args.efa:
        if efa.get("available"):
            print(f"EFA eigenvalues : {efa['eigenvalues']} "
                  f"({efa['n_factors_ge_1']} >= 1)")
        else:
            print(f"EFA skipped     : {efa['reason']}")

    frame = build_frame(items, A, b, n_persons_item, flags)
    csv_path = write_csv(frame, args.out_dir)
    matrix_prov = cp._matrix_manifest_prov(args.matrix)
    manifest_path = write_manifest(
        args.out_dir, args, diag, multi, uni, n_obs, latent_corr, crosscheck, efa,
        matrix_prov, identifiable,
    )
    print(f"\nwrote calibration CSV -> {csv_path}")
    print(f"wrote calibration manifest -> {manifest_path}")

    if args.write_params:
        try:
            out_path, n_updated = write_mirt_rubrics(
                args.rubrics, args.out_rubrics, items, A, b, args, matrix_prov, latent_corr
            )
        except (CalibrationError, FileNotFoundError) as e:
            print(f"\nERROR: --write-params failed: {e}", file=sys.stderr)
            return 4
        print(f"\nwrote calibrated rubric copy -> {out_path} "
              f"({n_updated} criteria updated; rest kept synthetic)")
        print(f"(input {args.rubrics} left untouched)")

    if not identifiable:
        print("\nreminder: tiny-N run -- 3-dim M2PL NOT identifiable. FULL matrix only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
