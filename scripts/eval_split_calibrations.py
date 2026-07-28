"""Optimal split of the 11 WildBench tags into SEPARATE unidimensional calibrations.

ANALYSIS ONLY -- writes nothing. Does not modify build_wildbench.py or any data file.

eval_skill_groupings.py scores groupings for a single *joint* MIRT fit, where the
figure of merit is the free-parameter count of one Q-matrix. This script scores the
same groupings for a different architecture: G independent unidimensional 2PL
calibrations, one per group, reconciled at a second stage.

The two architectures have different optima because they fail differently:

  joint MIRT       one fit; D thetas per model plus a DxD theta covariance matrix.
                   Fails by *underidentification* -- too many item discriminations
                   for the number of respondents, weak per-dimension anchoring,
                   rotational indeterminacy, non-convergence.

  G separate fits  G unidimensional 2PLs. Each uses ALL N models and only its own
                   items, so respondents-per-dimension is N rather than N/D and
                   every fit is a textbook 2PL that will converge.
                   Fails by *heterogeneity* -- if a block is not internally
                   unidimensional, its single theta averages over distinct
                   abilities and the fit misfits no matter how much data there is.

So the joint objective (few parameters, balanced columns, separable columns) is
replaced here by: each block internally cohesive, blocks comparably sized, and as
little tag co-occurrence as possible crossing block boundaries.

Assignment rule
---------------
Criteria are assigned by PRIMARY tag only, which makes the criterion set an exact
partition -- no criterion enters two calibrations. That matters: a shared criterion
would appear in two likelihoods, so the two theta estimates would carry correlated
*error* that stage 2 cannot distinguish from real ability correlation. The price is
that secondary tags are discarded, and `leak` measures exactly how much.

`leak` reads two ways at once, which is the useful part:
  - as information sacrificed by the split (co-tagging the model can no longer use);
  - as the expected theta correlation between blocks, since cross-block weight is
    precisely the criteria mass whose scenarios exercise both blocks. High leak is
    bad for the separate fits but is exactly what the stage-2 shrinkage feeds on.

Run:
    llm-from-scratch/Scripts/python.exe scripts/eval_split_calibrations.py
    llm-from-scratch/Scripts/python.exe scripts/eval_split_calibrations.py --search
"""
from __future__ import annotations

import argparse
import itertools
from collections import Counter
from pathlib import Path

import networkx as nx
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "cache" / "wildbench" / "v2_test-00000-of-00001.parquet"

TAGS = [
    "Advice seeking", "Brainstorming", "Coding & Debugging", "Creative Writing",
    "Data Analysis", "Editing", "Information seeking", "Math", "Planning",
    "Reasoning", "Role playing",
]
IDX = {t: i for i, t in enumerate(TAGS)}
N = len(TAGS)

# Number of graded models in the WildBench v2 leaderboard release -- the respondent
# count every calibration below is bounded by. Reported so the items/respondent
# arithmetic is visible rather than implied.
N_MODELS = 60


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def load() -> tuple[list[int], list[list[float]], int]:
    """Return (criteria per primary tag, co-tag weight matrix, total criteria).

    `prim[i]`   = criteria whose *primary* tag is TAGS[i]. Sums to the full 11,667:
                  no v2 row has primary_tag "Others", so the 11 tags partition it.
    `w[i][j]`   = criteria in scenarios carrying BOTH tags (primary or secondary).
                  This is the same edge weight skill_graph.py draws.
    """
    if not PARQUET.exists():
        raise SystemExit(f"missing {PARQUET} -- see build_wildbench.py for the fetch command")

    prim = [0] * N
    w = [[0.0] * N for _ in range(N)]
    total = 0
    for r in pq.read_table(PARQUET).to_pylist():
        n = len(r["checklist"])
        total += n
        prim[IDX[r["primary_tag"]]] += n
        tags = sorted(({r["primary_tag"]} | set(r["secondary_tags"] or [])) - {"Others"})
        for y, z in itertools.combinations(tags, 2):
            w[IDX[y]][IDX[z]] += n
            w[IDX[z]][IDX[y]] += n
    return prim, w, total


def pairs_of(w) -> list[tuple[int, int, float]]:
    """Upper-triangle nonzero co-tag pairs -- 53 of 55, so the search loop is tiny."""
    return [(i, j, w[i][j]) for i in range(N) for j in range(i + 1, N) if w[i][j] > 0]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score(labels: list[int], prim, pairs, w_tot: float, total: int) -> dict:
    """Score one partition (labels[i] = block index of TAGS[i]) for separate fits."""
    k = max(labels) + 1
    items = [0] * k
    for i, lab in enumerate(labels):
        items[lab] += prim[i]

    w_in = sum(x for i, j, x in pairs if labels[i] == labels[j])
    leak = 1.0 - w_in / w_tot

    lo, hi = min(items), max(items)
    balance = lo / hi if hi else 0.0
    return {
        "k": k, "labels": labels[:], "items": items,
        "min_items": lo, "max_items": hi, "balance": balance,
        # Item parameters per block for a unidimensional 2PL: one a and one b each.
        # Total across all blocks is 2 * 11667 REGARDLESS of k -- the criterion set is
        # partitioned, so splitting more finely never adds an item parameter. That is
        # the structural advantage of this architecture over joint MIRT.
        "params": 2 * total,
        "leak": leak, "cohesion": 1.0 - leak,
        # Respondents per item parameter, worst block. Below ~1 the block's item
        # parameters are estimated from less data than they have dimensions.
        "resp_per_param": N_MODELS * lo / (2.0 * lo) if lo else 0.0,
        "composite": balance * (1.0 - leak),
    }


def blocks(labels: list[int]) -> list[list[str]]:
    k = max(labels) + 1
    out: list[list[str]] = [[] for _ in range(k)]
    for i, lab in enumerate(labels):
        out[lab].append(TAGS[i])
    return out


def labels_of(design: dict[str, list[str]]) -> list[int]:
    """Convert a named block dict to a label vector; rejects overlap and gaps."""
    lab = [-1] * N
    for bi, (name, tags) in enumerate(sorted(design.items())):
        for t in tags:
            if lab[IDX[t]] != -1:
                raise ValueError(f"{t} appears in two blocks (last: {name})")
            lab[IDX[t]] = bi
    missing = [TAGS[i] for i, v in enumerate(lab) if v == -1]
    if missing:
        raise ValueError(f"tags not assigned: {missing}")
    return lab


# ---------------------------------------------------------------------------
# Named designs (same definitions as eval_skill_groupings.py, partitions only --
# bifactor / crossed-facet designs have no separate-calibration analogue, since a
# criterion cannot be partitioned into two blocks at once)
# ---------------------------------------------------------------------------

P2_LEAN = {
    "produce": ["Brainstorming", "Coding & Debugging", "Creative Writing", "Editing",
                "Planning", "Role playing"],
    "analyze": ["Advice seeking", "Data Analysis", "Information seeking", "Math",
                "Reasoning"],
}
P2 = {
    "analytical": ["Coding & Debugging", "Math", "Data Analysis", "Reasoning",
                   "Information seeking"],
    "generative": ["Creative Writing", "Brainstorming", "Role playing", "Editing",
                   "Planning", "Advice seeking"],
}
P3 = {
    "analytical": ["Coding & Debugging", "Math", "Data Analysis", "Reasoning"],
    "expressive": ["Creative Writing", "Brainstorming", "Role playing", "Editing"],
    "informational": ["Information seeking", "Advice seeking", "Planning"],
}
P4 = {
    "technical": ["Coding & Debugging", "Math", "Data Analysis"],
    "inquiry": ["Information seeking", "Advice seeking", "Reasoning"],
    "generative": ["Creative Writing", "Brainstorming", "Role playing"],
    "structuring": ["Planning", "Editing"],
}
P4R = {
    "reasoning": ["Reasoning"],
    "technical": ["Coding & Debugging", "Math", "Data Analysis"],
    "expressive": ["Creative Writing", "Brainstorming", "Role playing", "Editing"],
    "practical": ["Planning", "Advice seeking", "Information seeking"],
}
P5 = {
    "analytical": ["Math", "Data Analysis", "Reasoning"],
    "technical": ["Coding & Debugging"],
    "knowledge": ["Information seeking", "Advice seeking"],
    "generative": ["Creative Writing", "Brainstorming", "Role playing"],
    "structuring": ["Planning", "Editing"],
}
P6R = {
    "quantitative": ["Math", "Data Analysis"],
    "technical": ["Coding & Debugging"],
    "knowledge": ["Information seeking", "Advice seeking"],
    "generative": ["Creative Writing", "Brainstorming", "Role playing"],
    "structuring": ["Planning", "Editing"],
    "reasoning": ["Reasoning"],
}
HYBRID6 = {
    "coding": ["Coding & Debugging"],
    "info_seeking": ["Information seeking"],
    "prose": ["Creative Writing", "Editing", "Role playing"],
    "quantitative": ["Math", "Data Analysis"],
    "ideation": ["Planning", "Brainstorming", "Advice seeking"],
    "reasoning": ["Reasoning"],
}
P1 = {"general_ability": list(TAGS)}

NAMED = [
    ("O. single calibration (k=1)", P1),
    ("P. k=2 lean-search optimum", P2_LEAN),
    ("M. k=2 semantic", P2),
    ("A. k=3 semantic", P3),
    ("B. k=4 semantic", P4),
    ("K. k=4 Reasoning isolated", P4R),
    ("C. k=5 semantic", P5),
    ("D. k=6 Reasoning isolated", P6R),
    ("E. k=6 hybrid (co-load driven)", HYBRID6),
    ("baseline. k=11 one per tag", {t: [t] for t in TAGS}),
]


# ---------------------------------------------------------------------------
# Community detection: the separate-calibration objective IS modularity
# ---------------------------------------------------------------------------


def cooc_graph(prim, w) -> nx.Graph:
    g = nx.Graph()
    for i, t in enumerate(TAGS):
        g.add_node(t, criteria=prim[i])
    for i in range(N):
        for j in range(i + 1, N):
            if w[i][j] > 0:
                g.add_edge(TAGS[i], TAGS[j], weight=w[i][j])
    return g


def communities(g: nx.Graph) -> list[tuple[str, list[int]]]:
    """Run the standard weighted community-detection algorithms, if available."""
    out = []
    try:
        cs = nx.community.greedy_modularity_communities(g, weight="weight")
        out.append(("greedy modularity (CNM)", to_labels(cs)))
    except Exception as e:                                  # noqa: BLE001
        print(f"  (greedy modularity unavailable: {e})")
    try:
        cs = nx.community.louvain_communities(g, weight="weight", seed=0)
        out.append(("Louvain", to_labels(cs)))
    except Exception as e:                                  # noqa: BLE001
        print(f"  (Louvain unavailable: {e})")
    return out


def to_labels(comms) -> list[int]:
    lab = [-1] * N
    for bi, c in enumerate(sorted(comms, key=lambda s: sorted(s))):
        for t in c:
            lab[IDX[t]] = bi
    return lab


def modularity(g: nx.Graph, labels: list[int]) -> float:
    return nx.community.modularity(g, [set(b) for b in blocks(labels)], weight="weight")


# ---------------------------------------------------------------------------
# Exhaustive search over all set partitions of the 11 tags
# ---------------------------------------------------------------------------


def restricted_growth(n: int):
    """Yield every set partition of n elements as a canonical label vector.

    Bell(11) = 678,570, so exhaustive is cheap and there is no reason to sample.
    """
    a = [0] * n
    m = [0] * n            # m[i] = max(a[0..i]); a[i] may rise to m[i-1] + 1
    while True:
        yield a
        i = n - 1
        while i > 0 and a[i] == m[i - 1] + 1:
            i -= 1
        if i == 0:
            return
        a[i] += 1
        m[i] = m[i - 1] if a[i] < m[i - 1] else a[i]
        for j in range(i + 1, n):
            a[j] = 0
            m[j] = m[i]


def search(prim, pairs, w_tot, total, g, top=4, min_items=0) -> None:
    best: dict[int, list[dict]] = {}
    for labels in restricted_growth(N):
        k = max(labels) + 1
        if k > 6:
            continue
        s = score(labels, prim, pairs, w_tot, total)
        if s["min_items"] < min_items:
            continue
        bucket = best.setdefault(k, [])
        bucket.append(s)
        if len(bucket) > 400:                      # keep memory flat
            bucket.sort(key=lambda x: -x["composite"])
            del bucket[top:]

    print("\n" + "=" * 96)
    print("EXHAUSTIVE SEARCH -- maximise balance x cohesion  (all 678,570 set partitions)")
    print("=" * 96)
    for k in sorted(best):
        bucket = sorted(best[k], key=lambda x: -x["composite"])[:top]
        print(f"\n--- k = {k} ---")
        for rank, s in enumerate(bucket, 1):
            print(f"  #{rank}  comp {s['composite']:.4f}   cohesion {s['cohesion']:.3f}   "
                  f"bal {s['balance']:.3f}   Q {modularity(g, s['labels']):+.4f}   "
                  f"items {s['items']}")
            for b in blocks(s["labels"]):
                print(f"        {' + '.join(b)}")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def table(rows: list[tuple[str, dict]], g: nx.Graph) -> None:
    print(f"{'design':<32} {'G':>2} {'itemPar':>8} {'minItems':>9} {'bal':>6} "
          f"{'cohesion':>9} {'leak':>6} {'Q':>8} {'comp':>6}")
    print("-" * 96)
    for name, s in rows:
        print(f"{name:<32} {s['k']:>2} {s['params']:>8} {s['min_items']:>9} "
              f"{s['balance']:>6.3f} {s['cohesion']:>9.3f} {s['leak']:>6.3f} "
              f"{modularity(g, s['labels']):>+8.4f} {s['composite']:>6.3f}")


def detail(name: str, s: dict, pairs, w_tot: float) -> None:
    print(f"\n{name}: per-block detail")
    print(f"{'block':<52} {'items':>7} {'share':>7}")
    tot = sum(s["items"])
    for b, n in zip(blocks(s["labels"]), s["items"]):
        print(f"{' + '.join(b):<52} {n:>7} {n / tot:>6.1%}")
    # Cross-block co-tag weight doubles as the expected theta-correlation ordering.
    k = s["k"]
    cross = [[0.0] * k for _ in range(k)]
    for i, j, x in pairs:
        a, b = s["labels"][i], s["labels"][j]
        if a != b:
            cross[a][b] += x
            cross[b][a] += x
    print(f"\ncross-block co-tag weight (criteria; higher => stronger expected theta "
          f"correlation, and more information the split discards)")
    for a in range(k):
        for b in range(a + 1, k):
            print(f"  block {a} <-> block {b}: {cross[a][b]:>7.0f} "
                  f"({cross[a][b] / w_tot:>5.1%} of all co-tag weight)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--search", action="store_true",
                    help="Exhaustively search all set partitions for k=1..6.")
    ap.add_argument("--min-items", type=int, default=0,
                    help="Reject partitions whose smallest block has fewer criteria.")
    args = ap.parse_args()

    prim, w, total = load()
    pairs = pairs_of(w)
    w_tot = sum(x for _, _, x in pairs)
    g = cooc_graph(prim, w)

    print(f"criteria (total)        {total}")
    print(f"co-tag weight (total)   {w_tot:.0f}")
    print(f"respondents (models)    {N_MODELS}")
    print("\ncriteria by PRIMARY tag (this is what each separate calibration gets):")
    for i, t in enumerate(sorted(TAGS, key=lambda x: -prim[IDX[x]])):
        print(f"  {i + 1:>2} {t:<22} {prim[IDX[t]]:>6} {prim[IDX[t]] / total:>6.1%}")

    rows = []
    for name, design in NAMED:
        s = score(labels_of(design), prim, pairs, w_tot, total)
        rows.append((name, s))

    print("\n" + "=" * 96)
    print("SEPARATE UNIDIMENSIONAL CALIBRATIONS -- named partitions")
    print("=" * 96)
    table(rows, g)

    print("\n" + "=" * 96)
    print("COMMUNITY DETECTION on the weighted co-occurrence graph")
    print("=" * 96)
    comm_rows = []
    for name, labels in communities(g):
        s = score(labels, prim, pairs, w_tot, total)
        comm_rows.append((name, s))
        print(f"\n{name}: k={s['k']}  Q={modularity(g, labels):+.4f}  "
              f"cohesion={s['cohesion']:.3f}  bal={s['balance']:.3f}")
        for b, n in zip(blocks(labels), s["items"]):
            print(f"    {' + '.join(b):<52} {n:>6} criteria")

    best_named = max(rows[1:], key=lambda r: r[1]["composite"])
    detail(best_named[0], best_named[1], pairs, w_tot)

    if args.search:
        search(prim, pairs, w_tot, total, g, min_items=args.min_items)


if __name__ == "__main__":
    main()
