"""Unidimensional 2PL item-response math: probability, Fisher information, and
EAP ability estimation. Pure numpy, no I/O, so it is unit-testable anywhere.

2PL model: P(correct | theta) = sigmoid(a * (theta - b)), where `a` is the item
discrimination and `b` the difficulty. Fisher information at theta is
a^2 * P * (1 - P); the CAT selects the item that maximizes it. Ability is
estimated by Expected A Posteriori (posterior mean over a standard-normal grid),
which is stable with very few items, unlike raw MLE which diverges on all-correct
or all-wrong response patterns.
"""

from __future__ import annotations

import numpy as np

_Z_CLIP = 60.0  # sigmoid(60) is 1.0 to double precision; keeps exp() finite
_EPS = 1e-9


def prob_2pl(theta: float | np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """P(correct) under 2PL. Broadcasts theta (scalar or (G,)) over items (n,)."""
    theta = np.asarray(theta, dtype=float)
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    z = a * (theta[..., None] - b) if theta.ndim else a * (theta - b)
    return 1.0 / (1.0 + np.exp(-np.clip(z, -_Z_CLIP, _Z_CLIP)))


def fisher_information(theta: float, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Item Fisher information at a scalar theta: a^2 * P * (1 - P)."""
    p = prob_2pl(float(theta), a, b)
    return np.asarray(a, dtype=float) ** 2 * p * (1.0 - p)


def normal_grid(n: int = 61, lo: float = -5.0, hi: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """Quadrature nodes + standard-normal prior weights for EAP."""
    nodes = np.linspace(lo, hi, n)
    prior = np.exp(-0.5 * nodes**2)
    prior /= prior.sum()
    return nodes, prior


def eap(
    responses: np.ndarray,
    a: np.ndarray,
    b: np.ndarray,
    grid: tuple[np.ndarray, np.ndarray] | None = None,
) -> tuple[float, float]:
    """Expected A Posteriori theta and posterior SD given a 0/1 response vector
    and the administered items' (a, b). Returns (theta, se)."""
    responses = np.asarray(responses, dtype=float)
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if responses.size == 0:
        return 0.0, 1.0  # prior mean/SD before any item
    nodes, prior = grid if grid is not None else normal_grid()
    # p[g, i] = P(correct on item i at node g)
    z = a[None, :] * (nodes[:, None] - b[None, :])
    p = 1.0 / (1.0 + np.exp(-np.clip(z, -_Z_CLIP, _Z_CLIP)))
    p = np.clip(p, _EPS, 1.0 - _EPS)
    loglik = responses[None, :] * np.log(p) + (1.0 - responses)[None, :] * np.log(1.0 - p)
    ll = loglik.sum(axis=1)
    w = np.exp(ll - ll.max()) * prior
    total = w.sum()
    if not np.isfinite(total) or total <= 0:
        return 0.0, 1.0
    w /= total
    theta = float((nodes * w).sum())
    var = float(((nodes - theta) ** 2 * w).sum())
    return theta, float(np.sqrt(max(var, 0.0)))


def test_information(theta: float, a: np.ndarray, b: np.ndarray) -> float:
    """Total test information at theta (sum of item informations)."""
    return float(fisher_information(theta, a, b).sum())
