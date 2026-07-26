"""Fisher-information CAT over a calibrated 2PL bank.

Each step: pick the not-yet-administered item with the highest Fisher
information at the current EAP theta, reveal the test-taker's known response,
re-estimate theta by EAP, and stop when the posterior SD drops below `se_stop`
or `max_items` is reached. We replay a model's already-observed answers, so the
CAT only decides *which* item to look at next; no model is queried.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .ability import eap, fisher_information, normal_grid


@dataclass
class CATResult:
    theta: float
    se: float
    n_items: int
    administered: list[str]
    responses: list[int]
    theta_trace: list[float] = field(default_factory=list)
    se_trace: list[float] = field(default_factory=list)


def run_cat(
    responses_by_item: dict[str, int],
    a_by_item: dict[str, float],
    b_by_item: dict[str, float],
    *,
    max_items: int = 50,
    se_stop: float = 0.3,
    grid: tuple[np.ndarray, np.ndarray] | None = None,
) -> CATResult:
    """Adaptive test for one model. Only items present in all three dicts are
    eligible. Deterministic: ties in information break by item id."""
    grid = grid if grid is not None else normal_grid()
    eligible = [it for it in responses_by_item if it in a_by_item and it in b_by_item]
    a_arr = np.array([a_by_item[it] for it in eligible], dtype=float)
    b_arr = np.array([b_by_item[it] for it in eligible], dtype=float)
    r_arr = np.array([responses_by_item[it] for it in eligible], dtype=float)

    remaining = set(range(len(eligible)))
    picked: list[int] = []
    theta, se = 0.0, 1.0
    res = CATResult(theta=theta, se=se, n_items=0, administered=[], responses=[])

    while remaining and len(picked) < max_items:
        idx = np.fromiter(remaining, dtype=int)
        info = fisher_information(theta, a_arr[idx], b_arr[idx])
        # break ties deterministically by eligible-item order (idx ascending)
        best = idx[int(np.lexsort((idx, -info))[0])]
        remaining.discard(int(best))
        picked.append(int(best))

        sel = np.array(picked, dtype=int)
        theta, se = eap(r_arr[sel], a_arr[sel], b_arr[sel], grid)
        res.theta_trace.append(theta)
        res.se_trace.append(se)
        if se < se_stop:
            break

    res.theta, res.se, res.n_items = theta, se, len(picked)
    res.administered = [eligible[i] for i in picked]
    res.responses = [int(r_arr[i]) for i in picked]
    return res
