"""Offline tests for the MCQ IRT/CAT pipeline.

The pure-numpy pieces (ability, CAT, item filtering) run with no extra deps; the
2PL-fit recovery test needs girth and is skipped if it is not installed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tutor_cat.mcq_irt.ability import eap, fisher_information, normal_grid, prob_2pl
from tutor_cat.mcq_irt.cat import run_cat
from tutor_cat.mcq_irt.matrix import choose_diagnostic, filter_items


# --- ability -------------------------------------------------------------

def test_prob_monotonic_in_theta():
    a = np.array([1.0]); b = np.array([0.0])
    assert prob_2pl(-2.0, a, b)[0] < prob_2pl(0.0, a, b)[0] < prob_2pl(2.0, a, b)[0]


def test_fisher_info_peaks_at_difficulty():
    a = np.array([1.5]); b = np.array([0.3])
    thetas = np.linspace(-3, 3, 121)
    info = np.array([fisher_information(t, a, b)[0] for t in thetas])
    assert abs(thetas[info.argmax()] - 0.3) < 0.1  # info peaks at theta == b


def test_eap_recovers_theta():
    rng = np.random.default_rng(0)
    n = 120
    a = rng.uniform(0.7, 2.0, n)
    b = rng.normal(0.0, 1.0, n)
    grid = normal_grid()
    for true_theta in (-1.5, 0.0, 1.2):
        p = prob_2pl(true_theta, a, b)
        r = (rng.random(n) < p).astype(int)
        theta, se = eap(r, a, b, grid)
        assert abs(theta - true_theta) < 0.4
        assert 0 < se < 1.0


# --- CAT -----------------------------------------------------------------

def test_cat_shrinks_se_and_recovers_theta():
    rng = np.random.default_rng(1)
    n = 200
    a = rng.uniform(0.8, 2.2, n)
    b = rng.normal(0.0, 1.2, n)
    items = [f"i{j}" for j in range(n)]
    true_theta = 0.8
    p = prob_2pl(true_theta, a, b)
    r = (rng.random(n) < p).astype(int)
    resp = {items[j]: int(r[j]) for j in range(n)}
    a_by = {items[j]: float(a[j]) for j in range(n)}
    b_by = {items[j]: float(b[j]) for j in range(n)}

    res = run_cat(resp, a_by, b_by, max_items=40, se_stop=0.3)
    assert res.se < 0.4
    assert res.n_items <= 40
    assert abs(res.theta - true_theta) < 0.6
    # SE is non-increasing overall (first vs last)
    assert res.se_trace[-1] <= res.se_trace[0]


# --- item filtering ------------------------------------------------------

def test_filter_drops_all_pass_and_all_fail():
    # 5 models, 4 items: item0 all pass, item1 all fail, item2/3 mixed
    data = pd.DataFrame(
        {"i0": [1, 1, 1, 1, 1], "i1": [0, 0, 0, 0, 0],
         "i2": [1, 0, 1, 0, 1], "i3": [0, 1, 1, 0, 1]},
        index=[f"m{k}" for k in range(5)],
    )
    kept, rep = filter_items(data, benchmark="t", min_point_biserial=-1.0)
    assert "i0" in rep.dropped_all_pass
    assert "i1" in rep.dropped_all_fail
    assert set(kept.columns).issubset({"i2", "i3"})


def test_choose_diagnostic_uses_common_models():
    m1 = pd.DataFrame(np.eye(10, dtype=int), index=[f"m{k}" for k in range(10)])
    m2 = pd.DataFrame(np.eye(8, dtype=int), index=[f"m{k}" for k in range(8)])
    diag = choose_diagnostic({"a": m1, "b": m2}, frac=0.25, seed=0)
    assert len(diag) == 2  # round(0.25 * 8 common)
    assert all(d in set(m2.index) for d in diag)


# --- 2PL fit recovery (needs girth) --------------------------------------

def test_twopl_fit_recovers_parameters():
    girth = pytest.importorskip("girth")  # noqa: F841
    from tutor_cat.mcq_irt.calibrate import fit_2pl

    rng = np.random.default_rng(3)
    n_models, n_items = 400, 60
    a = rng.uniform(0.6, 2.0, n_items)
    b = rng.normal(0.0, 1.0, n_items)
    theta = rng.normal(0.0, 1.0, n_models)
    z = a[None, :] * (theta[:, None] - b[None, :])
    p = 1.0 / (1.0 + np.exp(-z))
    X = (rng.random((n_models, n_items)) < p).astype(int)
    mat = pd.DataFrame(X, index=[f"m{k}" for k in range(n_models)],
                       columns=[f"i{j}" for j in range(n_items)])

    calib = fit_2pl(mat, method="girth")
    assert calib.a.shape == (n_items,)
    assert np.corrcoef(calib.b, b)[0, 1] > 0.9
    assert np.corrcoef(calib.a, a)[0, 1] > 0.6
