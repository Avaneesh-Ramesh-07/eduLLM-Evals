"""WildBench skill co-occurrence graph: one node per tag, one edge per co-tagged scenario.

Nodes are the WildBench task-type tags. An edge joins Y and Z when at least one
scenario carries both, where "carries" means the tag appears in `primary_tag` OR
in `secondary_tags`. Because `primary_tag` is a single value, two tags can never
both be primary, so an edge is always either primary-secondary or
secondary-secondary -- exactly the relation asked for.

Read from the cached parquet, not from data/WildBench/rubrics.jsonl: the built
rubrics currently encode only the primary tag in `q_mapping`, so the secondary
tags (which every edge here depends on) exist only upstream.

The canonical WildBench taxonomy has a 12th tag, "Others", which is dropped here: it
is carried by exactly one scenario (WB_0520, 10 criteria) and only ever as a secondary
tag, so build_wildbench.py omits it from TAGS and it is not a q_mapping column. Kept
in, it is a pendant node whose single edge is the graph's only bridge and whose only
neighbour is the graph's only articulation point -- topology that is an artifact of one
scenario rather than a property of the taxonomy.

The graph turns out to be near-complete, so the informative half of the figure is
the *complement*: the handful of tag pairs that never co-occur. Both panels are
drawn on a circular layout -- a force-directed layout of a 53-edge, 11-node graph
is an unreadable hairball, and circular placement keeps every node label legible.

Both edge weightings are computed and `--weight` picks which one drives the drawing
and the rankings:

    scenarios  (default) distinct co-tagged scenarios. "How many separate prompts
               exercise both skills", unconfounded by checklist length. The right
               weight for questions about the tag structure itself.
    criteria   criteria in co-tagged scenarios. The unit the MIRT model actually
               scores. Confounded by checklist length: scenarios run 6-33 criteria,
               so a few long scenarios can outweigh many short ones.

Neither is the "unnormalised" option -- both are raw counts, so an edge backed by few
scenarios is light under either. What changes between them is only the weight given to
long scenarios; compare_weightings() reports how much that distorts the ranking.

`--min-edge-frac F` prunes against each tag's OWN support rather than against one
global bar: an edge dies only if it is below F x the support of both endpoints. See
prune() for why, and for the worked example.

Run:
    llm-from-scratch/Scripts/python.exe scripts/skill_graph.py
    llm-from-scratch/Scripts/python.exe scripts/skill_graph.py --weight criteria
    llm-from-scratch/Scripts/python.exe scripts/skill_graph.py --min-edge-frac 0.10
    llm-from-scratch/Scripts/python.exe scripts/skill_graph.py --prune-sweep
"""
from __future__ import annotations

import argparse
import itertools
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # no display in this environment; write straight to PNG
import matplotlib.pyplot as plt
import networkx as nx
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "cache" / "wildbench" / "v2_test-00000-of-00001.parquet"
OUT_DIR = ROOT / "plots"

# Short labels for the plot only; the graph itself keys on the full tag names.
SHORT = {
    "Advice seeking": "Advice\nseeking",
    "Brainstorming": "Brain-\nstorming",
    "Coding & Debugging": "Coding &\nDebugging",
    "Creative Writing": "Creative\nWriting",
    "Data Analysis": "Data\nAnalysis",
    "Editing": "Editing",
    "Information seeking": "Information\nseeking",
    "Math": "Math",
    "Planning": "Planning",
    "Reasoning": "Reasoning",
    "Role playing": "Role\nplaying",
}


def load() -> list[tuple[str, frozenset[str], int]]:
    """Return (scenario_id, tag set, criterion count) per scenario, minus "Others".

    Dropping "Others" is unconditional -- see the module docstring. It is not a
    q_mapping column downstream, so keeping it here would put a node in the figure
    that no calibration can ever use.
    """
    if not PARQUET.exists():
        raise SystemExit(f"missing {PARQUET} -- see build_wildbench.py for the fetch command")
    rows = pq.read_table(PARQUET).to_pylist()
    out = []
    for i, r in enumerate(rows):
        tags = ({r["primary_tag"]} | set(r["secondary_tags"] or [])) - {"Others"}
        out.append((f"WB_{i:04d}", frozenset(tags), len(r["checklist"])))
    return out


def build_graph(scen) -> nx.Graph:
    """Undirected graph; node attrs = support, edge attrs = co-occurrence counts."""
    sup_s, sup_c = Counter(), Counter()
    edge_s, edge_c = Counter(), Counter()
    for _, tags, n in scen:
        for t in tags:
            sup_s[t] += 1
            sup_c[t] += n
        # sorted() so each unordered pair keys once
        for y, z in itertools.combinations(sorted(tags), 2):
            edge_s[(y, z)] += 1
            edge_c[(y, z)] += n

    g = nx.Graph()
    for t in sorted(sup_s):
        g.add_node(t, scenarios=sup_s[t], criteria=sup_c[t])
    for (y, z), c in edge_c.items():
        g.add_edge(y, z, scenarios=edge_s[(y, z)], criteria=c)
    return g


def report(g: nx.Graph, wkey: str = "criteria") -> None:
    n, m = g.number_of_nodes(), g.number_of_edges()
    full = n * (n - 1) // 2
    comps = sorted(nx.connected_components(g), key=len, reverse=True)

    print(f"nodes            {n}")
    print(f"edges            {m} of {full} possible  (density {m / full:.3f})")
    print(f"complete graph?  {'YES' if m == full else f'no -- {full - m} pair(s) never co-occur'}")
    print(f"\nDISJOINT GRAPHS (connected components): {len(comps)}")
    for i, c in enumerate(comps, 1):
        crit = sum(g.nodes[t]["criteria"] for t in c)
        print(f"  component {i}: {len(c)} node(s), {crit} criteria")
        for t in sorted(c):
            print(f"      {t}")

    # Unweighted degree is nearly flat in a graph this dense, so rank on three keys:
    #   degree    = how many distinct tags it ever pairs with (topology)
    #   strength  = sum of edge weights in the chosen unit (volume)
    #   coload    = strength / own support, i.e. average number of *other* tag-loads
    #               carried by one unit of this tag. This is the only one of the three
    #               that is not confounded by the tag's own frequency.
    unit = "crit" if wkey == "criteria" else "scen"
    strength = {t: sum(d[wkey] for _, _, d in g.edges(t, data=True)) for t in g}
    print(f"\n(all rankings below weighted by {wkey.upper()})")
    print(f"\nRANKED BY DEGREE (ties broken by strength)")
    print(f"{'#':>2} {'tag':<22} {'deg':>4} {'strength':>9} {unit:>6} {'coload':>7}  non-neighbours")
    order = sorted(g, key=lambda x: (-g.degree(x), -strength[x]))
    for i, t in enumerate(order, 1):
        missing = sorted(set(g) - set(g[t]) - {t})
        co = strength[t] / g.nodes[t][wkey]
        print(f"{i:>2} {t:<22} {g.degree(t):>4} {strength[t]:>9} "
              f"{g.nodes[t][wkey]:>6} {co:>7.2f}  {', '.join(missing) or '-'}")

    print(f"\nRANKED BY STRENGTH (weighted degree, co-tagged {wkey})")
    print(f"{'#':>2} {'tag':<22} {'strength':>9} {'share':>7} {'deg':>4}")
    tot = sum(strength.values())
    for i, t in enumerate(sorted(g, key=lambda x: -strength[x]), 1):
        print(f"{i:>2} {t:<22} {strength[t]:>9} {strength[t] / tot:>6.1%} {g.degree(t):>4}")

    print(f"\nRANKED BY CO-LOAD RATIO (strength / own {wkey} -- frequency-adjusted)")
    print(f"{'#':>2} {'tag':<22} {'coload':>7} {unit:>6} {'deg':>4}")
    for i, t in enumerate(sorted(g, key=lambda x: -strength[x] / g.nodes[x][wkey]), 1):
        print(f"{i:>2} {t:<22} {strength[t] / g.nodes[t][wkey]:>7.2f} "
              f"{g.nodes[t][wkey]:>6} {g.degree(t):>4}")

    print(f"\nabsent edges ({full - m}):")
    for y, z in itertools.combinations(sorted(g), 2):
        if not g.has_edge(y, z):
            print(f"  {y}  --  {z}")

    print(f"\ntop edges by co-tagged {wkey}:")
    for y, z, d in sorted(g.edges(data=True), key=lambda e: -e[2][wkey])[:10]:
        print(f"  {y + '  --  ' + z:<48} {d['criteria']:>5} crit  {d['scenarios']:>4} scen"
              f"  {d['criteria'] / d['scenarios']:>5.1f} crit/scen")

    # Bridges / articulation points would be the merge-relevant weak spots.
    arts = sorted(nx.articulation_points(g))
    print(f"\narticulation points (removal disconnects the graph): {arts or 'none'}")
    print(f"bridges (single edges holding the graph together):    "
          f"{[f'{y}--{z}' for y, z in nx.bridges(g)] or 'none'}")


def compare_weightings(g: nx.Graph) -> None:
    """How much does checklist length distort the criteria-weighted ranking?

    Criteria-weighting is scenario-weighting scaled per edge by that edge's mean
    checklist length. If every scenario were the same length the two rankings would
    be identical, so the spread of crit/scen across edges bounds how far they can
    diverge -- and the rank shifts below show how far they actually do.
    """
    ranks = {}
    for key in ("criteria", "scenarios"):
        order = sorted(g.edges(data=True), key=lambda e: -e[2][key])
        ranks[key] = {frozenset((y, z)): i for i, (y, z, _) in enumerate(order, 1)}

    rows = []
    for y, z, d in g.edges(data=True):
        k = frozenset((y, z))
        rows.append((ranks["criteria"][k], ranks["scenarios"][k],
                     ranks["scenarios"][k] - ranks["criteria"][k], y, z, d))

    # Spearman rho by hand (no scipy dependency); n is 53-54 so the formula is exact.
    n = len(rows)
    dsq = sum((a - b) ** 2 for a, b, *_ in rows)
    rho = 1 - 6 * dsq / (n * (n * n - 1))

    cps = [d["criteria"] / d["scenarios"] for _, _, _, _, _, d in rows]
    print(f"\n{'=' * 88}")
    print("CRITERIA- vs SCENARIO-WEIGHTING")
    print(f"{'=' * 88}")
    print(f"Spearman rho between the two edge rankings: {rho:.4f}  (n={n} edges)")
    print(f"mean checklist length per edge: min {min(cps):.1f}  max {max(cps):.1f}  "
          f"spread {max(cps) / min(cps):.2f}x")
    print("\nedges whose rank moves most when you switch to scenario weighting:")
    print(f"{'critRk':>6} {'scenRk':>7} {'shift':>6}  {'edge':<46} {'crit':>6} {'scen':>5} {'c/s':>5}")
    for cr, sr, shift, y, z, d in sorted(rows, key=lambda r: -abs(r[2]))[:10]:
        print(f"{cr:>6} {sr:>7} {shift:>+6}  {y + ' -- ' + z:<46} "
              f"{d['criteria']:>6} {d['scenarios']:>5} {d['criteria'] / d['scenarios']:>5.1f}")


def prune(g: nx.Graph, wkey: str, frac: float) -> tuple[nx.Graph, str, list]:
    """Drop an edge only when it is negligible to BOTH of the tags it joins.

    Each tag sets its own bar from its own support: bar(t) = frac * support(t).
    An edge (y, z) of weight w is dropped iff

        w < frac * support(y)   AND   w < frac * support(z)

    so surviving means clearing at least one endpoint's bar. Worked example, frac=0.10:
    Creative Writing has 100 scenarios (bar 10), Coding 90 (bar 9), and 5 scenarios
    carry both -- 5 < 10 and 5 < 9, so the edge goes. Had Coding only 10 scenarios
    (bar 1), the same 5 clears Coding's bar and the edge stays: it is a rounding error
    to Creative Writing but a fifth of everything Coding does.

    That asymmetry is the whole point. A single global bar confounds tag frequency with
    tag association -- a rare tag's edges are all small in absolute terms, so a rare
    pair gets cut for being rare rather than for being unrelated. Dividing by the
    endpoint's own support removes the confound, because w / support(y) is exactly the
    association-rule confidence P(z | y). Keeping on EITHER endpoint (rather than both)
    is what lets a rare tag defend its own edges against a hub's volume.

    Nodes are never removed, so a tag that loses every edge shows up as an isolate
    rather than silently vanishing -- that is the informative outcome, not an error.
    """
    dropped, keep_edges = [], []
    for y, z, d in g.edges(data=True):
        w = d[wkey]
        # keep iff max(conf) >= frac, i.e. NOT (below y's bar AND below z's bar)
        if max(w / g.nodes[y][wkey], w / g.nodes[z][wkey]) >= frac:
            keep_edges.append((y, z))
        else:
            dropped.append((y, z, d))

    gp = g.copy()
    gp.remove_edges_from([(y, z) for y, z, _ in dropped])
    label = (f"{wkey} >= {frac:.0%} of EITHER endpoint's own support "
             f"(drop only if below both)")
    return gp, label, sorted(dropped, key=lambda e: -e[2][wkey])


def report_prune(g: nx.Graph, gp: nx.Graph, wkey: str, label: str, dropped,
                 frac: float) -> None:
    print(f"\n{'=' * 100}")
    print(f"PRUNE RULE: keep iff {label}")
    print(f"{'=' * 100}")
    print(f"per-node bar at {frac:.0%} of own support:")
    for t in sorted(g, key=lambda x: -g.nodes[x][wkey]):
        print(f"  {t:<22} support {g.nodes[t][wkey]:>5} {wkey}  ->  bar "
              f"{frac * g.nodes[t][wkey]:>6.1f}")

    print(f"\nedges: {g.number_of_edges()} -> {gp.number_of_edges()} "
          f"({len(dropped)} dropped)")
    kept_w = sum(d[wkey] for _, _, d in gp.edges(data=True))
    tot_w = sum(d[wkey] for _, _, d in g.edges(data=True))
    # The headline: how little weight the many thin edges actually carried.
    print(f"weight retained: {kept_w}/{tot_w} {wkey} = {kept_w / tot_w:.1%}")
    iso = sorted(nx.isolates(gp))
    print(f"isolated nodes: {iso or 'none'}")
    ncomp = nx.number_connected_components(gp)
    print(f"\nDISJOINT GRAPHS (connected components): {ncomp}")
    for i, c in enumerate(sorted(nx.connected_components(gp), key=len, reverse=True), 1):
        print(f"  component {i}: {len(c)} node(s)  {sorted(c)}")
    print(f"articulation points: {sorted(nx.articulation_points(gp)) or 'none'}")
    print(f"bridges: {[f'{y}--{z}' for y, z in nx.bridges(gp)] or 'none'}")

    print(f"\nDROPPED EDGES ({len(dropped)}), heaviest first")
    print(f"  {'edge':<48} {wkey[:5]:>6} {'conf(y|z)':>10} {'conf(z|y)':>10} {'max':>7}")
    for y, z, d in dropped:
        w = d[wkey]
        cy, cz = w / g.nodes[y][wkey], w / g.nodes[z][wkey]
        print(f"  {y + ' -- ' + z:<48} {w:>6} {cy:>9.1%} {cz:>10.1%} "
              f"{max(cy, cz):>6.1%}")

    print(f"\nSURVIVING EDGES ({gp.number_of_edges()}), heaviest first")
    print(f"  {'edge':<48} {wkey[:5]:>6} {'conf(y|z)':>10} {'conf(z|y)':>10} {'max':>7}")
    for y, z, d in sorted(gp.edges(data=True), key=lambda e: -e[2][wkey]):
        w = d[wkey]
        cy, cz = w / g.nodes[y][wkey], w / g.nodes[z][wkey]
        print(f"  {y + ' -- ' + z:<48} {w:>6} {cy:>9.1%} {cz:>10.1%} "
              f"{max(cy, cz):>6.1%}")

    print(f"\ndegree in the pruned graph:")
    strength = {t: sum(d[wkey] for _, _, d in gp.edges(t, data=True)) for t in gp}
    for i, t in enumerate(sorted(gp, key=lambda x: (-gp.degree(x), -strength[x])), 1):
        lost = sorted(set(g[t]) - set(gp[t]))
        print(f"{i:>2} {t:<22} {gp.degree(t):>3} (was {g.degree(t):>2})  "
              f"strength {strength[t]:>5}  lost: {', '.join(lost) or '-'}")


def report_lost_scenarios(scen, gp: nx.Graph) -> None:
    """Scenarios whose every tag pair was pruned -- co-tagging fully discarded.

    A single-tag scenario induces no pair at all, so it can never lose one; counting
    it as "lost" would inflate the number with rows the pruning never touched.
    """
    single, partial, lost = 0, [], []
    for sid, tags, n in scen:
        pairs = list(itertools.combinations(sorted(tags), 2))
        if not pairs:
            single += 1
            continue
        kept = [p for p in pairs if gp.has_edge(*p)]
        if not kept:
            lost.append((sid, sorted(tags), n, len(pairs)))
        elif len(kept) < len(pairs):
            partial.append((sid, len(pairs) - len(kept), len(pairs)))

    tot = len(scen)
    multi = tot - single
    print(f"\n{'=' * 100}")
    print("SCENARIO-LEVEL EFFECT")
    print(f"{'=' * 100}")
    print(f"scenarios total                     {tot}")
    print(f"  single-tag (induce no edge)       {single}  ({single / tot:.1%})")
    print(f"  multi-tag                         {multi}  ({multi / tot:.1%})")
    print(f"    all pairs survive               {multi - len(partial) - len(lost)}")
    print(f"    some pairs pruned               {len(partial)}")
    print(f"    ALL pairs pruned (fully lost)   {len(lost)}"
          f"  ({len(lost) / multi:.1%} of multi-tag)")
    crit = sum(n for _, _, n, _ in lost)
    print(f"      criteria in fully-lost rows   {crit}")
    print(f"\nFULLY-PRUNED SCENARIOS ({len(lost)}) -- every tag pair below the bar:")
    print(f"  {'id':<9} {'crit':>5} {'pairs':>6}  tags")
    for sid, tags, n, npairs in sorted(lost, key=lambda r: -r[2]):
        print(f"  {sid:<9} {n:>5} {npairs:>6}  {', '.join(tags)}")


def prune_sweep(g: nx.Graph, wkey: str,
                fracs=(0.0, 0.05, 0.10, 0.15, 0.20, 0.30)) -> None:
    """Does pruning expose community structure the dense graph was hiding?

    At density 0.964 modularity is necessarily near zero -- almost every pair is
    connected, so no partition can look better than random. Thin edges are mostly
    incidental co-tagging, so cutting them is a noise filter, and Q should rise until
    the cut starts removing real structure. Where the two algorithms AGREE and Q peaks
    is the most defensible block structure the data supports.
    """
    tot = sum(d[wkey] for _, _, d in g.edges(data=True))
    full = g.number_of_nodes() * (g.number_of_nodes() - 1) // 2
    print(f"\n{'=' * 88}")
    print(f"PRUNE SWEEP -- community structure vs. threshold (weighted by {wkey})")
    print(f"{'=' * 88}")
    for frac in fracs:
        gp, _, _ = prune(g, wkey, frac)
        m = gp.number_of_edges()
        kept = sum(d[wkey] for _, _, d in gp.edges(data=True))
        print(f"\nfrac {frac:.2f}   edges {m}/{full} "
              f"(density {m / full:.3f})   weight {kept / tot:.1%}   "
              f"components {nx.number_connected_components(gp)}")
        if m == 0:
            continue
        found = {}
        for name, fn in (
            ("Louvain", lambda G: nx.community.louvain_communities(
                G, weight=wkey, seed=0)),
            ("greedyCNM", lambda G: nx.community.greedy_modularity_communities(
                G, weight=wkey)),
        ):
            try:
                cs = fn(gp)
            except Exception as e:                          # noqa: BLE001
                print(f"  {name}: unavailable ({e})")
                continue
            q = nx.community.modularity(gp, cs, weight=wkey)
            key = tuple(sorted(tuple(sorted(c)) for c in cs))
            found[name] = key
            print(f"  {name:<10} k={len(cs)}  Q={q:+.4f}")
            for c in sorted(cs, key=lambda s: -len(s)):
                print(f"      {' + '.join(sorted(c))}")
        if len(found) == 2 and len(set(found.values())) == 1:
            print("  ** both algorithms agree on this partition **")


def draw(g: nx.Graph, path: Path, wkey: str = "scenarios",
         gp: nx.Graph | None = None, cut_label: str = "") -> None:
    """Panel 1 draws `gp` (pruned, or `g` if no pruning); panel 2 draws what is absent."""
    pos = nx.circular_layout(g)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(19, 9.5))
    drawn = gp if gp is not None else g

    # ---- panel 1: the graph itself ----------------------------------------
    # wmax stays on the FULL graph so colours/widths are comparable across thresholds.
    weights = [d[wkey] for _, _, d in drawn.edges(data=True)]
    wmax = max(d[wkey] for _, _, d in g.edges(data=True))
    # sqrt so the heaviest edge does not swamp the lightest ones (1892 vs 11 criteria,
    # or 174 vs 1 scenario -- a ~170x range either way, so linear width is unreadable)
    widths = [0.4 + 7.0 * (w / wmax) ** 0.5 for w in weights]
    # Node area on the same unit as the edges, normalised so the constants do not have
    # to be retuned when wkey changes (criteria peak ~5.6k, scenarios peak ~0.5k).
    smax = max(g.nodes[t][wkey] for t in g)
    sizes = [120 + 2350 * (g.nodes[t][wkey] / smax) ** 0.62 for t in g]

    nx.draw_networkx_edges(drawn, pos, ax=ax1, width=widths, edge_color=weights,
                           edge_cmap=plt.cm.viridis, edge_vmin=0, edge_vmax=wmax, alpha=0.75)
    nx.draw_networkx_nodes(drawn, pos, ax=ax1, node_size=sizes, node_color="#ffffff",
                           edgecolors="#222222", linewidths=1.8)
    nx.draw_networkx_labels(drawn, pos, ax=ax1, labels={t: SHORT.get(t, t) for t in g},
                            font_size=9, font_weight="bold")
    n, m = drawn.number_of_nodes(), drawn.number_of_edges()
    full = n * (n - 1) // 2
    ncomp = nx.number_connected_components(drawn)
    cut = f"\npruned: keep iff {cut_label}" if gp is not None else ""
    ax1.set_title(f"WildBench skill co-occurrence  (weighted by {wkey})\n{n} nodes, "
                  f"{m}/{full} edges, "
                  f"{ncomp} connected component{'s' if ncomp != 1 else ''}{cut}",
                  fontsize=13, fontweight="bold")
    sm = plt.cm.ScalarMappable(cmap=plt.cm.viridis,
                               norm=plt.Normalize(vmin=0, vmax=wmax))
    label = ("criteria in scenarios carrying both tags" if wkey == "criteria"
             else "distinct scenarios carrying both tags")
    fig.colorbar(sm, ax=ax1, fraction=0.04, pad=0.02, label=label)

    # ---- panel 2: everything panel 1 does NOT show -------------------------
    # Two distinct reasons an edge is missing, drawn differently because they mean
    # different things: structurally absent (no scenario ever pairs them) vs. merely
    # thin (paired, but below the cut). Collapsing them into one complement graph
    # would hide which is which.
    absent = [(y, z) for y, z in itertools.combinations(sorted(g), 2)
              if not g.has_edge(y, z)]
    thin = [(y, z) for y, z, d in g.edges(data=True) if not drawn.has_edge(y, z)]
    nx.draw_networkx_edges(g, pos, ax=ax2, edgelist=absent, width=2.4,
                           edge_color="#c0392b", style="dashed", alpha=0.95)
    if thin:
        nx.draw_networkx_edges(
            g, pos, ax=ax2, edgelist=thin, style="dotted", alpha=0.85,
            edge_color="#7f8c8d",
            # widths on the same sqrt scale as panel 1, so "thin here" is literal
            width=[0.4 + 7.0 * (g[y][z][wkey] / wmax) ** 0.5 for y, z in thin])
    nx.draw_networkx_nodes(g, pos, ax=ax2, node_size=sizes, node_color="#ffffff",
                           edgecolors="#222222", linewidths=1.8)
    nx.draw_networkx_labels(g, pos, ax=ax2, labels={t: SHORT.get(t, t) for t in g},
                            font_size=9, font_weight="bold")
    ax2.set_title(f"What panel 1 omits: {len(absent)} pair(s) that NEVER co-occur "
                  f"(red dashed)\n" + (f"+ {len(thin)} pruned as too thin (grey dotted)"
                                       if thin else "(no pruning applied)"),
                  fontsize=13, fontweight="bold")

    for ax in (ax1, ax2):
        ax.set_axis_off()
        ax.margins(0.16)
    fig.tight_layout()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"\nwrote {path.relative_to(ROOT)}  ({path.stat().st_size / 1e3:.0f} KB)")


# ---------------------------------------------------------------------------
# Directed view: primary -> secondary
# ---------------------------------------------------------------------------
#
# The undirected graph treats "both tags present" as one relation. It is really two:
#
#   primary -> secondary     y is the scenario's primary tag, z one of its secondaries.
#                            Directional -- y is what the prompt IS, z is what it also
#                            touches. 1244 pair-instances, 73.7% of the mass.
#   secondary -- secondary   both tags are secondary on the same scenario. Only one tag
#                            can be primary, so there is NO primary/secondary relation
#                            between these two. 445 instances, 26.3%, 50 pairs.
#
# The second kind cannot be drawn as an arrow without inventing a direction the data
# does not contain, so it is excluded from panel 1 and drawn explicitly in panel 2
# rather than dropped silently -- a quarter of the co-occurrence is not a rounding error.


def load_directed() -> list[tuple[str, str, list[str], int]]:
    """(scenario_id, primary tag, secondary tags, criterion count), minus "Others".

    The primary tag is also removed from the secondary list: a handful of rows repeat
    it there, which would otherwise produce a self-loop.
    """
    if not PARQUET.exists():
        raise SystemExit(f"missing {PARQUET} -- see build_wildbench.py for the fetch command")
    out = []
    for i, r in enumerate(pq.read_table(PARQUET).to_pylist()):
        p = r["primary_tag"]
        sec = sorted(set(r["secondary_tags"] or []) - {"Others"} - {p})
        out.append((f"WB_{i:04d}", p, sec, len(r["checklist"])))
    return out


def build_digraph(scend) -> tuple[nx.DiGraph, nx.Graph]:
    """Return (primary->secondary digraph, secondary--secondary residual graph).

    Node attrs carry BOTH denominators, because the directed view needs a different
    one than the undirected view did:
        scenarios / criteria           total support (tag present at all) -- node size,
                                       kept on the same scale as the undirected figures
                                       so nodes are comparable across every plot.
        prim_scenarios / prim_criteria times the tag is PRIMARY. This is the denominator
                                       for an out-edge's confidence, and it bounds how
                                       much a tag can emit at all.
    """
    sup_s, sup_c = Counter(), Counter()
    pri_s, pri_c = Counter(), Counter()
    e_s, e_c = Counter(), Counter()
    ss_s, ss_c = Counter(), Counter()

    for _, p, sec, n in scend:
        pri_s[p] += 1
        pri_c[p] += n
        for t in {p, *sec}:
            sup_s[t] += 1
            sup_c[t] += n
        for z in sec:
            e_s[(p, z)] += 1
            e_c[(p, z)] += n
        for y, z in itertools.combinations(sec, 2):   # already sorted
            ss_s[(y, z)] += 1
            ss_c[(y, z)] += n

    dg = nx.DiGraph()
    for t in sorted(sup_s):
        dg.add_node(t, scenarios=sup_s[t], criteria=sup_c[t],
                    prim_scenarios=pri_s[t], prim_criteria=pri_c[t])
    for (y, z), c in e_c.items():
        dg.add_edge(y, z, scenarios=e_s[(y, z)], criteria=c)

    ss = nx.Graph()
    ss.add_nodes_from(dg.nodes(data=True))
    for (y, z), c in ss_c.items():
        ss.add_edge(y, z, scenarios=ss_s[(y, z)], criteria=c)
    return dg, ss


def conf_dir(dg: nx.DiGraph, y: str, z: str, wkey: str) -> float:
    """P(z is a secondary tag | y is the primary tag), in percent.

    Direction removes the ambiguity the undirected rule had to paper over with max():
    an arrow already names which endpoint is the denominator, so there is exactly one
    confidence per edge rather than two competing ones.
    """
    pk = "prim_scenarios" if wkey == "scenarios" else "prim_criteria"
    return 100.0 * dg[y][z][wkey] / dg.nodes[y][pk]


def report_directed(dg: nx.DiGraph, ss: nx.Graph, wkey: str) -> None:
    tot_dir = sum(d[wkey] for _, _, d in dg.edges(data=True))
    tot_ss = sum(d[wkey] for _, _, d in ss.edges(data=True))
    unit = "crit" if wkey == "criteria" else "scen"
    pk = "prim_scenarios" if wkey == "scenarios" else "prim_criteria"

    print("\n" + "=" * 100)
    print(f"DIRECTED VIEW: primary -> secondary   (weighted by {wkey.upper()})")
    print("=" * 100)
    print(f"ordered pairs with an arrow: {dg.number_of_edges()} of 110 possible")
    print(f"directional weight:  {tot_dir:>6}  ({tot_dir / (tot_dir + tot_ss):.1%})")
    print(f"sec--sec weight:     {tot_ss:>6}  ({tot_ss / (tot_dir + tot_ss):.1%}) "
          f"over {ss.number_of_edges()} pairs -- no direction exists, panel 2 only")

    print(f"\nDRIVER vs ACCOMPANIMENT")
    print(f"{'#':>2} {'tag':<22} {'primary':>8} {'support':>8} {'prim%':>6} "
          f"{'out':>5} {'in':>5} {'outStr':>7} {'inStr':>7} {'net':>7}")
    rows = []
    for t in dg:
        o = sum(d[wkey] for _, _, d in dg.out_edges(t, data=True))
        i = sum(d[wkey] for _, _, d in dg.in_edges(t, data=True))
        rows.append((t, dg.nodes[t][pk], dg.nodes[t][wkey],
                     dg.out_degree(t), dg.in_degree(t), o, i, o - i))
    for n, (t, p, s, od, idg, o, i, net) in enumerate(
            sorted(rows, key=lambda r: -r[7]), 1):
        print(f"{n:>2} {t:<22} {p:>8} {s:>8} {p / s:>5.0%} "
              f"{od:>5} {idg:>5} {o:>7} {i:>7} {net:>+7}")

    print(f"\nTOP DIRECTED EDGES ({unit}, conf = P(secondary | this primary))")
    top = sorted(dg.edges(data=True), key=lambda e: -e[2][wkey])[:20]
    for n, (y, z, d) in enumerate(top, 1):
        rev = dg[z][y][wkey] if dg.has_edge(z, y) else 0
        print(f"{n:>2} {y:>22}  ->  {z:<22} {d[wkey]:>5} {unit}  "
              f"conf {conf_dir(dg, y, z, wkey):>5.1f}%   reverse {rev:>4}")

    print(f"\nMOST ONE-SIDED PAIRS (both tags co-occur, arrow runs mostly one way)")
    seen, asym = set(), []
    for y, z, d in dg.edges(data=True):
        if (z, y) in seen or (y, z) in seen:
            continue
        seen.add((y, z))
        rev = dg[z][y][wkey] if dg.has_edge(z, y) else 0
        asym.append((y, z, d[wkey], rev, d[wkey] - rev))
    for y, z, a, b, net in sorted(asym, key=lambda r: -abs(r[4]))[:12]:
        hi, lo = (y, z) if a >= b else (z, y)
        print(f"   {hi:>22} -> {lo:<22} {max(a, b):>4} vs {min(a, b):>4} back "
              f"  net {abs(net):>+4}")


def rank_conf(dg: nx.DiGraph, wkey: str) -> None:
    """Rank every arrow by conf = w(y->z) / n_primary(y) -- the directed confidence.

    This is a *rate*, not a mass: the denominator is how often y is primary at all, so
    a tag that is rarely primary reaches a high rate on very few scenarios. Advice
    seeking is primary 21 times; one arrow of weight 7 already reads 33%. The n column
    is printed alongside precisely so a high rate on a thin base is visible rather
    than flattering, and the summary at the end splits the table on that base.

    Sum of conf over all arrows out of y = (secondary slots emitted by y) / n_primary(y)
    = the mean number of secondary tags a y-primary scenario carries. That is a
    property of how the annotators tagged y, not of any one edge, so it is reported
    separately -- it sets the ceiling that y's individual arrows share out.
    """
    pk = "prim_scenarios" if wkey == "scenarios" else "prim_criteria"
    unit = "crit" if wkey == "criteria" else "scen"

    rows = []
    for y, z, d in dg.edges(data=True):
        c = conf_dir(dg, y, z, wkey)
        rev = conf_dir(dg, z, y, wkey) if dg.has_edge(z, y) else 0.0
        rows.append((c, y, z, d[wkey], dg.nodes[y][pk], rev))
    rows.sort(key=lambda r: -r[0])

    print("\n" + "=" * 100)
    print(f"ARROWS RANKED BY conf = P(z secondary | y primary)   "
          f"[{wkey}, n = y's primary count]")
    print("=" * 100)
    print(f"{'#':>3} {'y (primary)':>22}      {'z (secondary)':<22} "
          f"{'w':>5} {'n':>5} {'conf':>7} {'rev':>7}")
    print("-" * 100)
    for n, (c, y, z, w, npri, rev) in enumerate(rows, 1):
        print(f"{n:>3} {y:>22}  ->  {z:<22} {w:>5} {npri:>5} "
              f"{c:>6.1f}% {rev:>6.1f}%")

    print(f"\ntotal outgoing confidence per tag  (= mean secondary tags per "
          f"{unit[:4]} where that tag is primary)")
    tots = sorted(((sum(conf_dir(dg, y, z, wkey) for z in dg.successors(y)),
                    y, dg.nodes[y][pk], dg.out_degree(y)) for y in dg),
                  key=lambda r: -r[0])
    for n, (s, y, npri, od) in enumerate(tots, 1):
        print(f"{n:>2} {y:<22} {s / 100:>5.2f} secondaries/scenario   "
              f"n={npri:>4}  spread over {od:>2} arrows")

    thin = [r for r in rows if r[4] < 40]
    print(f"\n{len(rows)} arrows.  {sum(1 for r in rows if r[0] >= 20)} at conf >= 20%, "
          f"{sum(1 for r in rows if r[0] >= 50)} at >= 50%.")
    print(f"{len(thin)} arrows come from a tag with fewer than 40 primary scenarios "
          f"(Advice seeking 21, Brainstorming 24, Role playing 30, Data Analysis 33) "
          f"-- their rates rest on a thin base.")


def draw_digraph(dg: nx.DiGraph, ss: nx.Graph, path: Path, wkey: str,
                 lo: int | None = None, hi: int | None = None) -> int:
    """Two-panel directed figure. With lo/hi, restrict panel 1 to a confidence band.

    Arrows are drawn on curved arcs so that y->z and z->y stay separately visible --
    41 of the 91 ordered pairs are reciprocal, and on straight lines those would be
    two arrowheads on top of one segment.
    """
    banded = lo is not None
    keep, below, above = [], [], []
    for y, z in dg.edges():
        if not banded:
            keep.append((y, z))
            continue
        c = conf_dir(dg, y, z, wkey)
        (keep if in_band(c, lo, hi) else above if c > hi else below).append((y, z))

    pos = nx.circular_layout(dg)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(19, 9.5))

    wmax = max(d[wkey] for _, _, d in dg.edges(data=True))
    smax = max(dg.nodes[t][wkey] for t in dg)
    sizes = [120 + 2350 * (dg.nodes[t][wkey] / smax) ** 0.62 for t in dg]

    def widths(edges, graph):
        return [0.4 + 6.0 * (graph[y][z][wkey] / wmax) ** 0.5 for y, z in edges]

    # ---- panel 1: the arrows ----------------------------------------------
    if keep:
        nx.draw_networkx_edges(
            dg, pos, ax=ax1, edgelist=keep, width=widths(keep, dg),
            edge_color=[dg[y][z][wkey] for y, z in keep],
            edge_cmap=plt.cm.viridis, edge_vmin=0, edge_vmax=wmax, alpha=0.8,
            arrows=True, arrowstyle="-|>", arrowsize=17,
            connectionstyle="arc3,rad=0.13", node_size=sizes,
            min_source_margin=2, min_target_margin=12)
    nx.draw_networkx_nodes(dg, pos, ax=ax1, node_size=sizes, node_color="#ffffff",
                           edgecolors="#222222", linewidths=1.8)
    nx.draw_networkx_labels(dg, pos, ax=ax1, labels={t: SHORT.get(t, t) for t in dg},
                            font_size=9, font_weight="bold")

    und = nx.Graph(); und.add_nodes_from(dg); und.add_edges_from(keep)
    ncomp = nx.number_connected_components(und)
    band_line = ""
    if banded:
        rng = f"[{lo}%, {hi}%]" if lo == 0 else f"({lo}%, {hi}%]"
        band_line = (f"\nkept iff conf in {rng}  --  "
                     f"conf = P(secondary | this tag is primary)")
    ax1.set_title(f"WildBench skill co-occurrence, DIRECTED primary -> secondary"
                  f"{f'  band {lo}-{hi}%' if banded else ''}  (weighted by {wkey})\n"
                  f"{dg.number_of_nodes()} nodes, {len(keep)}/{dg.number_of_edges()} "
                  f"arrows{' in band' if banded else ' of 110 possible'}, "
                  f"{ncomp} weakly connected component{'s' if ncomp != 1 else ''}"
                  f"{band_line}", fontsize=13, fontweight="bold")
    sm = plt.cm.ScalarMappable(cmap=plt.cm.viridis, norm=plt.Normalize(0, wmax))
    lab = ("criteria: primary -> secondary" if wkey == "criteria"
           else "scenarios: this tag primary, that tag secondary")
    fig.colorbar(sm, ax=ax1, fraction=0.04, pad=0.02, label=lab)

    # ---- panel 2: what has no arrow ---------------------------------------
    # The sec--sec pairs are the headline omission here: they are real co-occurrence
    # that the directed model structurally cannot express, not weak edges.
    ss_edges = list(ss.edges())
    never = [(y, z) for y, z in itertools.combinations(sorted(dg), 2)
             if not dg.has_edge(y, z) and not dg.has_edge(z, y) and not ss.has_edge(y, z)]
    if ss_edges:
        nx.draw_networkx_edges(ss, pos, ax=ax2, edgelist=ss_edges,
                               width=widths(ss_edges, ss), edge_color="#8e44ad",
                               style="solid", alpha=0.55)
    nx.draw_networkx_edges(dg, pos, ax=ax2, edgelist=never, width=2.4,
                           edge_color="#c0392b", style="dashed", alpha=0.95, arrows=False)
    if below:
        nx.draw_networkx_edges(dg, pos, ax=ax2, edgelist=below, width=widths(below, dg),
                               edge_color="#95a5a6", style="dotted", alpha=0.8,
                               arrows=True, arrowstyle="-|>", arrowsize=10,
                               connectionstyle="arc3,rad=0.13", node_size=sizes)
    if above:
        nx.draw_networkx_edges(dg, pos, ax=ax2, edgelist=above, width=widths(above, dg),
                               edge_color="#2471a3", style="dashed", alpha=0.8,
                               arrows=True, arrowstyle="-|>", arrowsize=10,
                               connectionstyle="arc3,rad=0.13", node_size=sizes)
    nx.draw_networkx_nodes(dg, pos, ax=ax2, node_size=sizes, node_color="#ffffff",
                           edgecolors="#222222", linewidths=1.8)
    nx.draw_networkx_labels(dg, pos, ax=ax2, labels={t: SHORT.get(t, t) for t in dg},
                            font_size=9, font_weight="bold")
    extra = ""
    if banded:
        extra = (f"\n+ {len(below)} arrows weaker than the band (grey dotted)"
                 f" + {len(above)} stronger (blue dashed)")
    ax2.set_title(f"What panel 1 omits: {ss.number_of_edges()} secondary--secondary "
                  f"pair(s) (purple)\nno primary/secondary relation exists between "
                  f"them -- undirectable\n+ {len(never)} pair(s) that never co-occur "
                  f"at all (red dashed){extra}", fontsize=13, fontweight="bold")

    for ax in (ax1, ax2):
        ax.set_axis_off()
        ax.margins(0.16)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return len(keep)


def draw_directed_bands(dg: nx.DiGraph, ss: nx.Graph, wkey: str, out_dir: Path) -> None:
    print("\n" + "=" * 100)
    print(f"DIRECTED THRESHOLD BANDS -> {out_dir.relative_to(ROOT)}")
    print("=" * 100)
    total = 0
    for lo, hi in BANDS:
        members = sorted(
            ((y, z, dg[y][z][wkey], conf_dir(dg, y, z, wkey)) for y, z in dg.edges()
             if in_band(conf_dir(dg, y, z, wkey), lo, hi)), key=lambda e: -e[3])
        name = f"band_{lo:02d}_{hi:02d}pct.png"
        n = draw_digraph(dg, ss, out_dir / name, wkey, lo, hi)
        total += n
        print(f"\n{lo}-{hi}%  ({n} arrow{'s' if n != 1 else ''})  -> {name}")
        if not members:
            print("    (empty)")
        for y, z, w, c in members:
            print(f"    {y:>22} -> {z:<22} {w:>4}   conf {c:>5.1f}%")
    print(f"\npartition check: {total} banded / {dg.number_of_edges()} arrows")


# ---------------------------------------------------------------------------
# Threshold bands: partition the edges by strength instead of thresholding them
# ---------------------------------------------------------------------------

# Ten right-closed bands over max-confidence, in percent. The first is closed at
# both ends so an edge at exactly 0 is not orphaned; every other band is (lo, hi],
# which is what "0-10, 11-20, ..." means read as integers. Together they partition
# all 53 edges -- each edge appears in exactly one figure, unlike the nested
# subgraphs --min-edge-frac produces.
BANDS = [(lo, lo + 10) for lo in range(0, 100, 10)]


def conf_max(g: nx.Graph, y: str, z: str, wkey: str) -> float:
    """The pruning statistic, in percent: max(w/support(y), w/support(z)) x 100.

    This -- not the raw weight -- is what --min-edge-frac tests, so banding on it
    makes band k exactly the set of edges that die when the threshold crosses k.
    """
    w = g[y][z][wkey]
    return 100.0 * max(w / g.nodes[y][wkey], w / g.nodes[z][wkey])


def in_band(c: float, lo: int, hi: int) -> bool:
    return lo <= c <= hi if lo == 0 else lo < c <= hi


def draw_band(g: nx.Graph, path: Path, wkey: str, lo: int, hi: int) -> int:
    """One two-panel figure for a single strength band; returns the edge count.

    Same layout, colour map, and width scale as draw(), so the ten figures are
    directly comparable to each other and to the --min-edge-frac ones. Panel 2
    splits the omitted pairs three ways rather than two: an edge above the band is
    a *stronger* relationship, so filing it under "too thin" (as the cumulative
    figure's panel 2 does, correctly, since there everything omitted IS thinner)
    would invert its meaning.
    """
    # Assign in one pass with an else-branch rather than three independent
    # predicates, so the three lists are exhaustive by construction: an edge
    # sitting exactly on a boundary lands somewhere instead of vanishing from
    # both panels.
    band, below, above = [], [], []
    for y, z in g.edges():
        c = conf_max(g, y, z, wkey)
        (band if in_band(c, lo, hi) else above if c > hi else below).append((y, z))
    absent = [(y, z) for y, z in itertools.combinations(sorted(g), 2)
              if not g.has_edge(y, z)]

    pos = nx.circular_layout(g)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(19, 9.5))

    # Scales pinned to the FULL graph, never to the band, so a thick edge means the
    # same number of scenarios in every one of the ten figures.
    wmax = max(d[wkey] for _, _, d in g.edges(data=True))
    smax = max(g.nodes[t][wkey] for t in g)
    sizes = [120 + 2350 * (g.nodes[t][wkey] / smax) ** 0.62 for t in g]

    def widths(edges):
        return [0.4 + 7.0 * (g[y][z][wkey] / wmax) ** 0.5 for y, z in edges]

    # ---- panel 1: the band ------------------------------------------------
    gb = nx.Graph()
    gb.add_nodes_from(g.nodes(data=True))
    gb.add_edges_from((y, z, g[y][z]) for y, z in band)
    if band:
        nx.draw_networkx_edges(gb, pos, ax=ax1, edgelist=band, width=widths(band),
                               edge_color=[g[y][z][wkey] for y, z in band],
                               edge_cmap=plt.cm.viridis, edge_vmin=0, edge_vmax=wmax,
                               alpha=0.75)
    nx.draw_networkx_nodes(gb, pos, ax=ax1, node_size=sizes, node_color="#ffffff",
                           edgecolors="#222222", linewidths=1.8)
    nx.draw_networkx_labels(gb, pos, ax=ax1, labels={t: SHORT.get(t, t) for t in g},
                            font_size=9, font_weight="bold")
    rng = f"[{lo}%, {hi}%]" if lo == 0 else f"({lo}%, {hi}%]"
    ncomp = nx.number_connected_components(gb)
    ax1.set_title(f"WildBench skill co-occurrence  band {lo}-{hi}%  "
                  f"(weighted by {wkey})\n{gb.number_of_nodes()} nodes, "
                  f"{len(band)}/{g.number_of_edges()} edges in band, "
                  f"{ncomp} connected component{'s' if ncomp != 1 else ''}\n"
                  f"kept iff max(conf) in {rng}  --  conf = scenarios / endpoint's "
                  f"own support", fontsize=13, fontweight="bold")
    sm = plt.cm.ScalarMappable(cmap=plt.cm.viridis, norm=plt.Normalize(0, wmax))
    lab = ("criteria in scenarios carrying both tags" if wkey == "criteria"
           else "distinct scenarios carrying both tags")
    fig.colorbar(sm, ax=ax1, fraction=0.04, pad=0.02, label=lab)

    # ---- panel 2: everything panel 1 omits --------------------------------
    nx.draw_networkx_edges(g, pos, ax=ax2, edgelist=absent, width=2.4,
                           edge_color="#c0392b", style="dashed", alpha=0.95)
    if below:
        nx.draw_networkx_edges(g, pos, ax=ax2, edgelist=below, width=widths(below),
                               edge_color="#95a5a6", style="dotted", alpha=0.85)
    if above:
        nx.draw_networkx_edges(g, pos, ax=ax2, edgelist=above, width=widths(above),
                               edge_color="#2471a3", style="dashed", alpha=0.85)
    nx.draw_networkx_nodes(g, pos, ax=ax2, node_size=sizes, node_color="#ffffff",
                           edgecolors="#222222", linewidths=1.8)
    nx.draw_networkx_labels(g, pos, ax=ax2, labels={t: SHORT.get(t, t) for t in g},
                            font_size=9, font_weight="bold")
    ax2.set_title(f"What panel 1 omits: {len(absent)} pair(s) that NEVER co-occur "
                  f"(red dashed)\n+ {len(below)} weaker than the band (grey dotted)"
                  f"\n+ {len(above)} stronger than the band (blue dashed)",
                  fontsize=13, fontweight="bold")

    for ax in (ax1, ax2):
        ax.set_axis_off()
        ax.margins(0.16)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return len(band)


def draw_bands(g: nx.Graph, wkey: str, out_dir: Path) -> None:
    """Write one figure per band and print the edge membership of each."""
    print("\n" + "=" * 100)
    print(f"THRESHOLD BANDS -- edges partitioned by max(conf), written to "
          f"{out_dir.relative_to(ROOT)}")
    print("=" * 100)

    total = 0
    for lo, hi in BANDS:
        members = sorted(
            ((y, z, g[y][z][wkey], conf_max(g, y, z, wkey)) for y, z in g.edges()
             if in_band(conf_max(g, y, z, wkey), lo, hi)),
            key=lambda e: -e[3])
        name = f"band_{lo:02d}_{hi:02d}pct.png"
        n = draw_band(g, out_dir / name, wkey, lo, hi)
        total += n
        print(f"\n{lo}-{hi}%  ({n} edge{'s' if n != 1 else ''})  -> {name}")
        if not members:
            print("    (empty -- no edge has a max confidence in this range)")
        for y, z, w, c in members:
            print(f"    {y} -- {z:<24} {w:>4} scen   max conf {c:>5.1f}%")

    print(f"\npartition check: {total} banded / {g.number_of_edges()} total edges")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weight", choices=("criteria", "scenarios"), default="scenarios",
                    help="Edge/node weight unit for the drawing and the rankings. "
                         "'scenarios' is unconfounded by checklist length; 'criteria' "
                         "is what the MIRT model scores (default: scenarios).")
    ap.add_argument("--min-edge-frac", type=float, default=0.0, metavar="F",
                    help="Drop an edge only when its weight is below F x the support "
                         "of BOTH endpoints (e.g. 0.10 keeps any edge worth >= 10%% of "
                         "either tag's own total). Nodes are kept, so a tag can end up "
                         "isolated. Default 0 (draw every edge).")
    ap.add_argument("--prune-sweep", action="store_true",
                    help="Sweep the threshold and run community detection at each "
                         "level, to see whether pruning exposes block structure.")
    ap.add_argument("--bands", action="store_true",
                    help="Write one figure per 10%% band of max-confidence "
                         "(0-10, 11-20, ... 91-100) into plots/threshold_bands/. "
                         "Unlike --min-edge-frac these are disjoint, not nested: "
                         "every edge appears in exactly one figure.")
    ap.add_argument("--directed", action="store_true",
                    help="Draw primary -> secondary arrows instead of undirected "
                         "co-occurrence. Secondary--secondary pairs have no direction "
                         "and move to panel 2. Combine with --bands for the 10 "
                         "directed band figures.")
    ap.add_argument("--rank-conf", action="store_true",
                    help="With --directed: print every arrow ranked by "
                         "w(y->z) / n_primary(y), the directed confidence. "
                         "Text only -- no figure is written.")
    ap.add_argument("--out", type=Path, default=None, help="Output PNG path.")
    args = ap.parse_args()

    if args.directed:
        dg, ss = build_digraph(load_directed())
        if args.rank_conf:
            rank_conf(dg, args.weight)
            return
        report_directed(dg, ss, args.weight)
        out = args.out or OUT_DIR / "directed"
        if args.bands:
            draw_directed_bands(dg, ss, args.weight, out)
        else:
            draw_digraph(dg, ss, out / "wildbench_skill_digraph.png", args.weight)
            print(f"\nwrote {(out / 'wildbench_skill_digraph.png').relative_to(ROOT)}")
        return

    scen = load()
    g = build_graph(scen)
    report(g, args.weight)
    compare_weightings(g)
    if args.prune_sweep:
        prune_sweep(g, args.weight)

    if args.bands:
        draw_bands(g, args.weight, args.out or OUT_DIR / "threshold_bands")
        return

    gp, cut_label = None, ""
    if args.min_edge_frac > 0:
        gp, cut_label, dropped = prune(g, args.weight, args.min_edge_frac)
        report_prune(g, gp, args.weight, cut_label, dropped, args.min_edge_frac)
        report_lost_scenarios(scen, gp)

    wsuffix = "" if args.weight == "scenarios" else f"_by_{args.weight}"
    psuffix = ("" if gp is None
               else f"_min{int(round(args.min_edge_frac * 100))}pct")
    draw(g, args.out or OUT_DIR / f"wildbench_skill_graph{wsuffix}{psuffix}.png",
         args.weight, gp, cut_label)


if __name__ == "__main__":
    main()
