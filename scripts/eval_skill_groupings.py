"""Score candidate WildBench skill groupings on MIRT-calibration criteria.

ANALYSIS ONLY -- writes nothing except an optional PNG. It does not modify
build_wildbench.py or any data file.

Why group at all
----------------
Calibrating a compensatory MIRT model p_i = sigmoid((q_i . a_i) . theta - b_i) on D
latent dimensions needs enough *respondents* (graded models) per dimension. ATLAS
(arXiv:2511.04689 §3.3) attributes TinyBenchmarks' severe M2/RMSEA misfit to
"estimating up to 15 latent traits from only 395 models". D=11 puts us in that
regime unless the respondent pool is large. Fewer, better-populated dimensions
calibrate more stably -- but only if the reduction does not destroy the property
that makes the model multidimensional in the first place.

The four things a grouping has to get right
-------------------------------------------
1. pct_multi   -- share of criteria loading >=2 dimensions. A design where every
                  criterion loads exactly one dimension is *between-item*
                  multidimensional: statistically identical to D separate
                  unidimensional tests, so theta components are estimated from
                  disjoint item sets and the compensatory term never engages.
                  This is the metric a naive partition silently destroys.
2. balance     -- min/max criteria per dimension. A dimension with few items has a
                  poorly determined metric no matter how many respondents there are.
3. max_jaccard -- largest pairwise overlap between two dimension columns. Two nearly
                  identical columns are collinear: their discriminations trade off
                  against each other and neither is stably estimated.
4. min_unique  -- fewest criteria loading ONE dimension only. These anchor a
                  dimension's own scale. Zero is acceptable only when identification
                  comes from an explicit constraint instead (the bifactor case).

Designs are expressed uniformly as tag -> list of dimensions, so partitions
(one dimension per tag), crossed facets (two), overlapping umbrellas (one or more),
and bifactor (specific + general) all go through the same scorer.

Run:
    llm-from-scratch/Scripts/python.exe scripts/eval_skill_groupings.py
    llm-from-scratch/Scripts/python.exe scripts/eval_skill_groupings.py --search
"""
from __future__ import annotations

import argparse
import itertools
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "cache" / "wildbench" / "v2_test-00000-of-00001.parquet"

TAGS = [
    "Advice seeking", "Brainstorming", "Coding & Debugging", "Creative Writing",
    "Data Analysis", "Editing", "Information seeking", "Math", "Planning",
    "Reasoning", "Role playing",
]
IDX = {t: i for i, t in enumerate(TAGS)}


# ---------------------------------------------------------------------------
# Data: collapse to distinct tag signatures (145 of them) with criteria weights
# ---------------------------------------------------------------------------


def load_signatures() -> tuple[np.ndarray, np.ndarray]:
    """Return (tag bitmask per distinct signature, criteria weight per signature).

    'Others' is dropped: 1 scenario / 10 criteria, and it is not a q_mapping column.
    Collapsing 1024 scenarios to their 145 distinct tag sets makes the exhaustive
    partition search ~7x cheaper with identical results, since the score depends
    only on which tags co-occur and how many criteria carry that combination.
    """
    if not PARQUET.exists():
        raise SystemExit(f"missing {PARQUET} -- see build_wildbench.py")
    sig_w: Counter[int] = Counter()
    for r in pq.read_table(PARQUET).to_pylist():
        tags = ({r["primary_tag"]} | set(r["secondary_tags"] or [])) - {"Others"}
        mask = 0
        for t in tags:
            mask |= 1 << IDX[t]
        sig_w[mask] += len(r["checklist"])
    masks = np.array(sorted(sig_w), dtype=np.int64)
    weights = np.array([sig_w[int(m)] for m in masks], dtype=np.int64)
    return masks, weights


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score(design: dict[str, list[str]], masks: np.ndarray, weights: np.ndarray,
          structural: tuple[str, ...] = ()) -> dict:
    """Score a tag -> dimensions design. All counts are criteria-weighted.

    `structural` names dimensions whose overlap with others is by construction rather
    than by accident -- a bifactor general factor is loaded by every criterion, so its
    Jaccard with each specific is just that specific's share of the item pool. Such a
    dimension is identified by an orthogonality constraint, not by non-overlap, so it
    is excluded from max_jaccard (which is meant to flag *accidental* collinearity
    between dimensions that are supposed to be distinct).
    """
    dims = sorted({d for ds in design.values() for d in ds})
    # dim_mask[j] = bitmask of tags that load dimension j
    dim_mask = np.array(
        [sum(1 << IDX[t] for t, ds in design.items() if dims[j] in ds) for j in range(len(dims))],
        dtype=np.int64,
    )
    # hits[j, s] = signature s loads dimension j
    hits = (masks[None, :] & dim_mask[:, None]) != 0
    loads = hits.sum(axis=0)

    total = int(weights.sum())
    per_dim = hits @ weights                                   # criteria per dimension
    inter = (hits[:, None, :] & hits[None, :, :]) @ weights     # pairwise intersections
    union = per_dim[:, None] + per_dim[None, :] - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        jac = np.where(union > 0, inter / np.maximum(union, 1), 0.0)
    np.fill_diagonal(jac, 0.0)
    jac_free = jac.copy()
    for j, d in enumerate(dims):        # zero out rows/cols of structural dimensions
        if d in structural:
            jac_free[j, :] = 0.0
            jac_free[:, j] = 0.0

    unique = np.array([int(weights[(loads == 1) & hits[j]].sum()) for j in range(len(dims))])
    dist = Counter()
    for L, w in zip(loads, weights):
        dist[int(L)] += int(w)

    return {
        "dims": dims,
        "D": len(dims),
        # Total 1s in the Q-matrix == number of free discrimination parameters, since
        # a_i is estimated only where q_i is loaded. This, not D, is the direct measure
        # of how much the calibration has to estimate from a fixed response matrix.
        "n_loads": int((loads * weights).sum()),
        "pct_multi": float(weights[loads >= 2].sum()) / total,
        "mean_loads": float((loads * weights).sum()) / total,
        "per_dim": {dims[j]: int(per_dim[j]) for j in range(len(dims))},
        "min_items": int(per_dim.min()),
        "balance": float(per_dim.min() / per_dim.max()),
        "max_jaccard": float(jac_free.max()),
        "argmax_jaccard": (dims[int(np.unravel_index(jac_free.argmax(), jac_free.shape)[0])],
                           dims[int(np.unravel_index(jac_free.argmax(), jac_free.shape)[1])]),
        "max_jaccard_all": float(jac.max()),
        "min_unique": int(unique.min()),
        "unique": {dims[j]: int(unique[j]) for j in range(len(dims))},
        "load_dist": dict(sorted(dist.items())),
    }


def show(name: str, note: str, s: dict) -> None:
    print("=" * 84)
    print(f"{name}   (D = {s['D']})")
    print(f"  {note}")
    print(f"  pct_multi   {s['pct_multi']:>7.1%}   (share of criteria loading >=2 dims)")
    print(f"  mean_loads  {s['mean_loads']:>7.2f}   dims per criterion")
    print(f"  balance     {s['balance']:>7.3f}   min/max criteria per dim "
          f"(min = {s['min_items']})")
    print(f"  max_jaccard {s['max_jaccard']:>7.3f}   worst pair: "
          f"{s['argmax_jaccard'][0]} / {s['argmax_jaccard'][1]}")
    print(f"  min_unique  {s['min_unique']:>7}   fewest single-dim anchor criteria")
    print(f"  load dist   {s['load_dist']}")
    print("  criteria per dimension (unique-only in parens):")
    for d, n in sorted(s["per_dim"].items(), key=lambda kv: -kv[1]):
        print(f"      {d:<26} {n:>6}  ({s['unique'][d]})")


# ---------------------------------------------------------------------------
# Candidate designs
# ---------------------------------------------------------------------------


def partition(groups: dict[str, list[str]]) -> dict[str, list[str]]:
    """{group: [tags]} -> {tag: [group]}, checking it really is a partition."""
    out: dict[str, list[str]] = {}
    for g, ts in groups.items():
        for t in ts:
            if t in out:
                raise ValueError(f"{t} in two groups: {out[t]} and {g}")
            out[t] = [g]
    missing = set(TAGS) - set(out)
    if missing:
        raise ValueError(f"tags not assigned: {sorted(missing)}")
    return out


def overlapping(groups: dict[str, list[str]]) -> dict[str, list[str]]:
    """{group: [tags]} -> {tag: [groups...]}; a tag may appear in several groups."""
    out: dict[str, list[str]] = {t: [] for t in TAGS}
    for g, ts in groups.items():
        for t in ts:
            out[t].append(g)
    empty = [t for t, ds in out.items() if not ds]
    if empty:
        raise ValueError(f"tags in no group: {empty}")
    return out


def facets(*facet_defs: dict[str, list[str]]) -> dict[str, list[str]]:
    """Cross several partitions. Each tag contributes one dimension per facet, so
    every criterion loads >= (number of facets) dimensions by construction."""
    out: dict[str, list[str]] = {t: [] for t in TAGS}
    for f in facet_defs:
        p = partition(f)
        for t in TAGS:
            out[t].extend(p[t])
    return out


def bifactor(specific: dict[str, list[str]], general: str = "general") -> dict[str, list[str]]:
    """Partition into specifics, plus one general dimension loaded by every criterion."""
    p = partition(specific)
    return {t: p[t] + [general] for t in TAGS}


P5 = {
    "analytical": ["Math", "Data Analysis", "Reasoning"],
    "technical": ["Coding & Debugging"],
    "knowledge": ["Information seeking", "Advice seeking"],
    "generative": ["Creative Writing", "Brainstorming", "Role playing"],
    "structuring": ["Planning", "Editing"],
}
P4 = {
    "technical": ["Coding & Debugging", "Math", "Data Analysis"],
    "inquiry": ["Information seeking", "Advice seeking", "Reasoning"],
    "generative": ["Creative Writing", "Brainstorming", "Role playing"],
    "structuring": ["Planning", "Editing"],
}
P3 = {
    "analytical": ["Coding & Debugging", "Math", "Data Analysis", "Reasoning"],
    "expressive": ["Creative Writing", "Brainstorming", "Role playing", "Editing"],
    "informational": ["Information seeking", "Advice seeking", "Planning"],
}
# P5 folds the hub into `analytical`; P6R instead gives Reasoning its own dimension,
# which is the single most consequential choice available (it is 48% of all criteria).
P6R = {
    "quantitative": ["Math", "Data Analysis"],
    "technical": ["Coding & Debugging"],
    "knowledge": ["Information seeking", "Advice seeking"],
    "generative": ["Creative Writing", "Brainstorming", "Role playing"],
    "structuring": ["Planning", "Editing"],
    "reasoning": ["Reasoning"],
}

# Hybrid: the tags with the LOWEST co-load ratio keep their own dimension (their
# signal is already close to unidimensional); the entangled ones get merged.
HYBRID6 = {
    "coding": ["Coding & Debugging"],          # co-load 1.13, 555 solo criteria
    "info_seeking": ["Information seeking"],   # co-load 1.23
    "prose": ["Creative Writing", "Editing", "Role playing"],
    "quantitative": ["Math", "Data Analysis"],
    "ideation": ["Planning", "Brainstorming", "Advice seeking"],
    "reasoning": ["Reasoning"],
}

FACET_MODE = {
    "generate": ["Brainstorming", "Creative Writing", "Planning", "Role playing"],
    "analyze": ["Advice seeking", "Coding & Debugging", "Data Analysis", "Math",
                "Information seeking", "Reasoning"],
    "transform": ["Editing"],
}
FACET_MEDIUM = {
    "formal": ["Coding & Debugging", "Data Analysis", "Math", "Reasoning"],
    "prose": ["Creative Writing", "Editing", "Information seeking"],
    "practical": ["Advice seeking", "Brainstorming", "Planning", "Role playing"],
}

# FACET_MODE/FACET_MEDIUM above put Math+Coding+Data+Reasoning in `analyze` AND in
# `formal`, making those two columns near-duplicates (Jaccard 0.82). A crossed design
# only pays off when the facets genuinely cut across each other, so this pair is built
# so that no two blocks share more than three tags.
# Every top-ranked partition at k>=4 in the exhaustive search isolates Reasoning, which
# is unsurprising: it is the co-occurrence hub (48% of criteria, degree 10) so folding it
# into any block drags that block's column toward a superset of the others. P4R keeps it
# separate while staying semantically readable, unlike the search's own optima.
P4R = {
    "reasoning": ["Reasoning"],
    "technical": ["Coding & Debugging", "Math", "Data Analysis"],
    "expressive": ["Creative Writing", "Brainstorming", "Role playing", "Editing"],
    "practical": ["Planning", "Advice seeking", "Information seeking"],
}
P2 = {
    "analytical": ["Coding & Debugging", "Math", "Data Analysis", "Reasoning",
                   "Information seeking"],
    "generative": ["Creative Writing", "Brainstorming", "Role playing", "Editing",
                   "Planning", "Advice seeking"],
}
# Winner of the exhaustive k=2 LEAN search (all 1023 partitions): fewest discrimination
# parameters subject to balanced blocks and separable columns. Semantically it lands on
# produce-an-artifact vs. reason-over-given-information, which is unusually clean for a
# search optimum -- the k=3..6 optima are all semantically incoherent.
P2_LEAN = {
    "produce": ["Brainstorming", "Coding & Debugging", "Creative Writing", "Editing",
                "Planning", "Role playing"],
    "analyze": ["Advice seeking", "Data Analysis", "Information seeking", "Math",
                "Reasoning"],
}
# The floor case: one dimension, no Q-matrix structure at all. This is what the
# LLM-IRT work that actually reports good fit uses (ATLAS: unidimensional 3PL).
P1 = {"general_ability": list(TAGS)}

OP_FACET = {          # what the model does with the input
    "produce": ["Brainstorming", "Creative Writing", "Planning", "Role playing"],
    "resolve": ["Math", "Coding & Debugging", "Reasoning", "Data Analysis"],
    "convey": ["Information seeking", "Editing", "Advice seeking"],
}
CONSTRAINT_FACET = {  # what makes an answer right or wrong
    "verifiable": ["Math", "Coding & Debugging", "Data Analysis", "Editing"],
    "audience_relative": ["Creative Writing", "Role playing", "Advice seeking"],
    "open_ended": ["Brainstorming", "Planning", "Information seeking", "Reasoning"],
}

OVERLAP5 = {
    "analytical": ["Math", "Data Analysis", "Reasoning", "Coding & Debugging"],
    "linguistic": ["Creative Writing", "Editing", "Role playing", "Brainstorming"],
    "informational": ["Information seeking", "Advice seeking", "Reasoning"],
    "structural": ["Planning", "Coding & Debugging", "Brainstorming", "Editing"],
    "interpersonal": ["Role playing", "Advice seeking", "Creative Writing"],
}

# (name, note, design, structural dims excluded from max_jaccard)
DESIGNS = [
    ("baseline: 11 leaf skills", "current build, one dimension per tag",
     {t: [t] for t in TAGS}, ()),
    ("A. partition k=3", "semantic 3-way split", partition(P3), ()),
    ("B. partition k=4", "semantic 4-way split", partition(P4), ()),
    ("C. partition k=5", "Reasoning folded into `analytical`", partition(P5), ()),
    ("D. partition k=6 (Reasoning isolated)", "hub gets its own dimension",
     partition(P6R), ()),
    ("E. hybrid k=6 (co-load driven)", "low-co-load tags stay standalone, rest merged",
     partition(HYBRID6), ()),
    ("F. crossed facets 3x3 (naive)", "mode x medium; the two facets nearly coincide",
     facets(FACET_MODE, FACET_MEDIUM), ()),
    ("J. crossed facets 3x3 (orthogonal)", "operation x constraint-type, built to cut across",
     facets(OP_FACET, CONSTRAINT_FACET), ()),
    ("G. overlapping umbrellas k=5", "tags may belong to several umbrellas",
     overlapping(OVERLAP5), ()),
    ("H. bifactor 1+4", "P4 specifics + a general dimension on every criterion",
     bifactor(P4), ("general",)),
    ("I. bifactor 1+3", "P3 specifics + general", bifactor(P3), ("general",)),
    ("K. partition k=4 (Reasoning isolated)", "semantic, hub on its own axis",
     partition(P4R), ()),
    ("L. bifactor 1+4 (Reasoning isolated)", "K's specifics + general",
     bifactor(P4R), ("general",)),
    ("M. partition k=2", "analytical / generative", partition(P2), ()),
    ("P. partition k=2 (lean-search optimum)", "produce / analyze -- fewest params, best balance",
     partition(P2_LEAN), ()),
    ("N. bifactor 1+2", "P2 specifics + general", bifactor(P2), ("general",)),
    ("O. unidimensional", "one dimension, no Q-matrix structure", partition(P1), ()),
]


# ---------------------------------------------------------------------------
# Exhaustive partition search
# ---------------------------------------------------------------------------


def restricted_growth(n: int, k: int):
    """Yield every partition of n items into exactly k blocks, as a label tuple."""
    a = [0] * n

    def rec(i: int, used: int):
        if i == n:
            if used == k:
                yield tuple(a)
            return
        # pruning: cannot reach k blocks if too few positions remain
        if used + (n - i) < k:
            return
        for v in range(min(used + 1, k)):
            a[i] = v
            yield from rec(i + 1, max(used, v + 1))

    yield from rec(0, 0)


def search(masks: np.ndarray, weights: np.ndarray, k: int, top: int = 6,
           lean: bool = False) -> None:
    """Exhaustively rank every k-block partition of the 11 tags.

    Two objectives, because they pull in opposite directions:

    `rich` (default) maximises pct_multi -- it wants co-occurring tags in DIFFERENT
    blocks so their loads survive. `lean` minimises mean_loads, i.e. the number of
    free discrimination parameters -- it wants co-occurring tags in the SAME block so
    their loads collapse into one. Both are gated on balance and column separation.

    These are not two views of one optimum. mean_loads = aParams / n_items, and
    pct_multi is a threshold on the same load distribution, so the two objectives are
    the same dial read from opposite ends. Which end you want is set by how many
    models you will grade, not by anything in the item bank.
    """
    total = int(weights.sum())
    best = []
    n_seen = 0
    for labels in restricted_growth(len(TAGS), k):
        n_seen += 1
        dim_mask = np.zeros(k, dtype=np.int64)
        for i, v in enumerate(labels):
            dim_mask[v] |= 1 << i
        hits = (masks[None, :] & dim_mask[:, None]) != 0
        loads = hits.sum(axis=0)
        pct_multi = float(weights[loads >= 2].sum()) / total
        per_dim = hits @ weights
        balance = float(per_dim.min() / per_dim.max())
        inter = (hits[:, None, :] & hits[None, :, :]) @ weights
        union = per_dim[:, None] + per_dim[None, :] - inter
        jac = np.where(union > 0, inter / np.maximum(union, 1), 0.0)
        np.fill_diagonal(jac, 0.0)
        mean_loads = float((loads * weights).sum()) / total
        sep = balance * (1.0 - float(jac.max()))   # shared gate: balanced, separable
        composite = (sep / mean_loads) if lean else (pct_multi * sep)
        best.append((composite, pct_multi, mean_loads, balance, float(jac.max()), labels))
    best.sort(reverse=True)
    obj = ("balance x (1 - maxJ) / mean_loads   [LEAN: fewest parameters]" if lean
           else "pct_multi x balance x (1 - maxJ)   [RICH: most multi-loading]")
    print(f"\nk={k}: {n_seen} partitions scored. objective = {obj}")
    print(f"{'comp':>6} {'aParams':>8} {'loads':>6} {'multi':>7} {'bal':>6} {'maxJ':>6}  blocks")
    for comp, pm, ml, bal, mj, labels in best[:top]:
        blocks = [[TAGS[i] for i, v in enumerate(labels) if v == b] for b in range(k)]
        blocks.sort(key=len, reverse=True)
        desc = " | ".join("+".join(x.split()[0] for x in b) for b in blocks)
        print(f"{comp:>6.3f} {ml * total:>8.0f} {ml:>6.2f} {pm:>7.1%} {bal:>6.3f} "
              f"{mj:>6.3f}  {desc}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--search", action="store_true",
                    help="Exhaustive partition search, RICH objective (max multi-loading).")
    ap.add_argument("--search-lean", action="store_true",
                    help="Exhaustive partition search, LEAN objective (fewest parameters).")
    args = ap.parse_args()

    masks, weights = load_signatures()
    print(f"{len(masks)} distinct tag signatures, {int(weights.sum())} criteria\n")

    rows = []
    for name, note, design, structural in DESIGNS:
        s = score(design, masks, weights, structural)
        show(name, note, s)
        rows.append((name, s))

    print("=" * 84)
    print("SUMMARY, ordered by parameter count (the calibration burden)")
    n_items = int(weights.sum())
    print(f"{'design':<40} {'D':>2} {'aParams':>8} {'items/dim':>10} {'multi':>7} "
          f"{'bal':>6} {'maxJ':>6} {'minUniq':>8}")
    for name, s in sorted(rows, key=lambda r: r[1]["n_loads"]):
        print(f"{name:<40} {s['D']:>2} {s['n_loads']:>8} "
              f"{n_items / s['D']:>10.0f} {s['pct_multi']:>7.1%} {s['balance']:>6.3f} "
              f"{s['max_jaccard']:>6.3f} {s['min_unique']:>8}")
    print(f"  aParams   = 1s in the Q-matrix = free discrimination params "
          f"(+ {n_items} difficulties, + N*D person params)")
    print("  items/dim = criteria per dimension if evenly spread (see bal for the reality)")
    print("  maxJ      = worst accidental column overlap (structural dims excluded)")

    if args.search:
        for k in (2, 3, 4, 5, 6):
            search(masks, weights, k)
    if args.search_lean:
        for k in (2, 3, 4, 5):
            search(masks, weights, k, lean=True)


if __name__ == "__main__":
    main()
