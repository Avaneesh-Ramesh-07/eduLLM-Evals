"""Core MIRT math: PRD Equations 1-3.

Notation (PRD "Updating Running MIRT Vector"):
    theta : (3,) ability vector (content, diagnosis, scaffolding)
    a     : (3,) calibrated discrimination vector of a criterion (frozen)
    q     : (3,) Q-matrix row of a criterion, entries in {0,1} (frozen)
    b     : scalar calibrated difficulty (frozen)
    y     : judge outcome, 1 = pass, 0 = fail
    U     : (3,3) uncertainty (posterior covariance) matrix; SE_k = sqrt(U_kk)
"""

from __future__ import annotations

import numpy as np

from . import N_SKILLS

# Clip the logit to keep exp() finite; sigmoid(60) is 1.0 to double precision.
_Z_CLIP = 60.0


def sigmoid(z: float | np.ndarray) -> float | np.ndarray:
    z = np.clip(z, -_Z_CLIP, _Z_CLIP)
    return 1.0 / (1.0 + np.exp(-z))


def masked_discrimination(a: np.ndarray, q: np.ndarray) -> np.ndarray:
    """q ⊙ a — the Q-masked discrimination vector."""
    return q * a


def pass_probability(theta: np.ndarray, a: np.ndarray, q: np.ndarray, b: float) -> float:
    """Equation 1:  p = sigmoid( (q ⊙ a) · theta − b )"""
    m = masked_discrimination(a, q)
    return float(sigmoid(float(m @ theta) - b))


def update(
    theta: np.ndarray,
    U: np.ndarray,
    a: np.ndarray,
    q: np.ndarray,
    b: float,
    y: int,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Apply Equations 1-3 for one criterion verdict.

    Equation 2 (uncertainty update, applied FIRST):
        U_new^-1 = U^-1 + p(1−p) · outer(q ⊙ a)
    Equation 3 (ability update, uses U_new):
        theta_new = theta + U_new · (q ⊙ a) · (y − p)

    Returns (theta_new, U_new, p) where p is the pre-update pass probability.
    """
    m = masked_discrimination(a, q)
    p = pass_probability(theta, a, q, b)

    info = p * (1.0 - p) * np.outer(m, m)
    U_new = np.linalg.inv(np.linalg.inv(U) + info)
    # Symmetrize to wash out floating-point asymmetry from the inversions.
    U_new = (U_new + U_new.T) / 2.0

    theta_new = theta + U_new @ m * (float(y) - p)
    return theta_new, U_new, p


def standard_errors(U: np.ndarray) -> np.ndarray:
    """SE_k = sqrt(U_kk) for each of the 3 skills."""
    return np.sqrt(np.diag(U))


def initial_state(
    theta_init: list[float] | None = None,
    u_init_diag: list[float] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """PRD initial conditions: theta_0 = (0,0,0), U_0 = diag(1,1,1). Both configurable."""
    theta = np.zeros(N_SKILLS) if theta_init is None else np.asarray(theta_init, dtype=float)
    diag = np.ones(N_SKILLS) if u_init_diag is None else np.asarray(u_init_diag, dtype=float)
    if theta.shape != (N_SKILLS,) or diag.shape != (N_SKILLS,):
        raise ValueError("theta_init and u_init_diag must each have length 3")
    return theta, np.diag(diag)
