"""Regression tests for Equations 1-3, pinned to the PRD's worked example."""

import numpy as np
import pytest

from tutor_cat.mirt import initial_state, pass_probability, standard_errors, update

# PRD worked example inputs
THETA = np.array([0.50, 0.00, -0.20])
U = np.diag([0.25, 0.25, 0.25])
A = np.array([1.2, 0.8, 0.5])
Q = np.array([1, 1, 0])
B = 0.3
Y = 1


def test_equation1_pass_probability():
    p = pass_probability(THETA, A, Q, B)
    assert p == pytest.approx(0.574, abs=1e-3)  # sigmoid(0.30)


def test_equations_2_and_3_worked_example():
    theta_new, U_new, p = update(THETA, U, A, Q, B, Y)

    # Equation 2: U_new from the information update
    assert U_new[0, 0] == pytest.approx(0.2305, abs=2e-4)
    assert U_new[1, 1] == pytest.approx(0.2413, abs=2e-4)
    assert U_new[2, 2] == pytest.approx(0.25, abs=1e-12)      # scaffolding untouched
    assert U_new[0, 1] == pytest.approx(-0.0130, abs=2e-4)    # negative off-diagonal

    # Equation 3: theta update uses U_new
    assert theta_new == pytest.approx([0.613, 0.076, -0.200], abs=2e-3)

    # SE trajectory from the PRD text
    se = standard_errors(U_new)
    assert se == pytest.approx([0.480, 0.491, 0.500], abs=2e-3)


def test_q_mask_blocks_off_skill_updates():
    theta_new, U_new, _ = update(THETA, U, A, Q, B, Y)
    assert theta_new[2] == THETA[2]                    # scaffolding theta unchanged
    assert U_new[2, 2] == pytest.approx(U[2, 2])       # scaffolding SE unchanged


def test_fail_moves_theta_down():
    theta_new, _, p = update(THETA, U, A, Q, B, 0)
    assert theta_new[0] < THETA[0] and theta_new[1] < THETA[1]


def test_uncertainty_never_grows():
    theta, U_cur = initial_state()
    rng = np.random.default_rng(0)
    for _ in range(50):
        q = np.zeros(3, dtype=int)
        q[rng.integers(3)] = 1
        a = q * rng.uniform(0.6, 2.0, 3)
        prev_diag = np.diag(U_cur).copy()
        theta, U_cur, _ = update(theta, U_cur, a, q, float(rng.normal()), int(rng.random() < 0.5))
        assert (np.diag(U_cur) <= prev_diag + 1e-12).all()


def test_initial_state_defaults():
    theta, U0 = initial_state()
    assert (theta == 0).all()
    assert np.allclose(U0, np.eye(3))
