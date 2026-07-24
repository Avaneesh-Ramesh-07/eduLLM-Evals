"""
Verification stage for the synthetic Q-matrix labels produced by ``generate_qmatrix.py``.

The generator emitted **one** label per rubric criterion (Claude Opus 4.8). This script adds
two *independent* verifiers that re-label each criterion **blind** -- they never see the
generator's answer, they perform the identical task from scratch -- so that the generator and
the two verifiers form three independent raters per skill. From those three votes it:

- measures inter-rater reliability (raw agreement, Cohen's kappa vs each verifier, Fleiss'
  kappa across all raters), computed by hand (no scipy/sklearn dependency);
- writes a priority-sorted **human-review queue** of every non-unanimous criterion;
- derives a **0-label false-negative audit** (generator marked 0, a verifier marked 1) with no
  extra API pass, since blind re-labeling already produces that signal.

The generator's labels are treated as one rater and are never modified.

Cross-provider routing (TrueFoundry AI Gateway)
-----------------------------------------------
Both verifiers go through the same gateway (``https://tfy.promptlens.trilogy.com``), which
speaks both wire formats:

- **Claude verifier** -> Anthropic SDK, ``base_url`` = gateway root, ``auth_token`` = the
  TrueFoundry user key (Bearer). Model slug e.g. ``claude-group/claude-opus-4-8``.
- **GPT-5.5 verifier** -> **OpenAI SDK** (``pip install openai``), ``base_url`` = gateway root
  + ``/v1``, ``api_key`` = the *same* TrueFoundry key (the OpenAI SDK sends it as Bearer).
  Model slug = the TrueFoundry catalog id for GPT-5.5 -- CONFIRM this from Maat -> Profile ->
  Models and pass it via ``--verifier``; the built-in default ``openai-group/gpt-5.5`` is a
  placeholder guess.

This makes the script deliberately mixed-provider (Anthropic SDK for the Claude rater, OpenAI
SDK for the GPT rater) -- that is the whole point of cross-model verification: a different
vendor catches model-specific systematic bias the generator shares with a same-vendor check.
``openai`` is imported lazily, so a Claude-only verifier set needs no new dependency.

Usage
-----
.. code-block:: bash

    pip install anthropic openai
    export ANTHROPIC_BASE_URL="https://tfy.promptlens.trilogy.com"   # gateway root
    export ANTHROPIC_AUTH_TOKEN="<TrueFoundry user key>"             # sent as Bearer to both
    unset ANTHROPIC_API_KEY                                          # avoid x-api-key precedence

    # Validate on the generation sample first:
    python verify_qmatrix.py --sample \
        --verifier anthropic:claude-group/claude-opus-4-8 \
        --verifier openai:openai-group/gpt-5.5            # <-- confirm this slug

    # Then the full set:
    python verify_qmatrix.py --full

    # Recompute stats + review queue from existing logs, no API calls:
    python verify_qmatrix.py --full --report-only

Outputs
-------
- ``data/rubrics_qmatrix_verified[.sample].{jsonl,json}``: each generation
  record plus a ``verification`` block. Generator fields untouched.
- ``data/review_queue[.sample].{jsonl,tsv}``: non-unanimous criteria,
  priority-sorted, full context inline (JSONL) + a compact triage sheet (TSV).
- ``qmatrix_verify_logs/<mode>/``: per-criterion raw request + per-verifier response, the
  system prompt, and ``manifest.json`` (IRR table, per-verifier success/fail, failures).
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Reuse the generator's constants + helpers so the verifiers see byte-identical input and do
# the identical task -- this is what makes the inter-rater comparison apples-to-apples.
import generate_qmatrix as gq
from generate_qmatrix import (
    DATA_DIR,
    MAX_TOKENS,
    OUTPUT_CONFIG,
    PROMPT_VERSION,
    QMATRIX_JSON_SCHEMA,
    RUBRICS_PATH,
    SCENARIOS_PATH,
    SKILLS,
    SYSTEM_BLOCKS,
    SYSTEM_PROMPT,
)

# ---------------------------------------------------------------------------
# Paths / run constants
# ---------------------------------------------------------------------------

VERIFY_LOG_DIR = Path("qmatrix_verify_logs")

# provider:model. GPT-5.5 slug is a placeholder -- confirm against the TrueFoundry catalog.
DEFAULT_VERIFIERS = [
    "anthropic:claude-group/claude-opus-4-8",
    "openai:openai-group/gpt-5.5",
]

SCENARIO_PROMPT_TRUNC = 400  # chars of scenario prompt shown in the review queue


# ---------------------------------------------------------------------------
# Verifier abstraction (one per configured model; shares a client per provider)
# ---------------------------------------------------------------------------


class Verifier:
    """
    One blind verifier: a (provider, model) that re-labels a criterion from scratch.

    ``label`` sends the shared system prompt + the per-criterion user message and returns
    ``(label_or_None, log_entry)``. Any error (bad model slug, network, parse, validation) is
    swallowed into ``log_entry["error"]`` and ``label`` is ``None`` so that this rater simply
    drops out for that criterion instead of aborting the run.
    """

    def __init__(self, name: str, provider: str, model: str, client, strict_schema: bool):
        self.name = name
        self.provider = provider
        self.model = model
        self.client = client
        self.strict_schema = strict_schema

    def _call_anthropic(self, user_content: str) -> tuple[str, dict]:
        kwargs = dict(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_BLOCKS,
            messages=[{"role": "user", "content": user_content}],
        )
        if self.strict_schema:
            kwargs["output_config"] = OUTPUT_CONFIG
        response = self.client.messages.create(**kwargs)
        return gq.extract_text(response), gq.safe_to_dict(response)

    def _call_openai(self, user_content: str) -> tuple[str, dict]:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        base = dict(model=self.model, messages=messages)
        if self.strict_schema:
            base["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "qmatrix_label",
                    "schema": QMATRIX_JSON_SCHEMA,
                    "strict": True,
                },
            }
        # Newer OpenAI models want `max_completion_tokens`; older ones/gateways want
        # `max_tokens`. Try the modern name first, fall back only if the API complains
        # specifically about the token-limit parameter.
        last_exc: Exception | None = None
        for token_param in ("max_completion_tokens", "max_tokens"):
            try:
                response = self.client.chat.completions.create(
                    **base, **{token_param: MAX_TOKENS}
                )
                text = response.choices[0].message.content or ""
                return text, gq.safe_to_dict(response)
            except Exception as e:  # noqa: BLE001 - narrowed by message below
                msg = str(e).lower()
                if token_param == "max_completion_tokens" and "max" in msg and "token" in msg:
                    last_exc = e
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    def label(self, user_content: str) -> tuple[dict | None, dict]:
        entry = {
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "text": None,
            "raw": None,
            "error": None,
        }
        try:
            if self.provider == "anthropic":
                text, raw = self._call_anthropic(user_content)
            else:
                text, raw = self._call_openai(user_content)
            entry["text"] = text
            entry["raw"] = raw
            label = gq.validate_label(gq.parse_json_object(text))
            return label, entry
        except Exception as e:  # noqa: BLE001 - degrade gracefully; this rater drops out
            entry["error"] = f"{type(e).__name__}: {e}"
            return None, entry


def parse_verifier_spec(spec: str) -> tuple[str, str]:
    """Split a ``provider:model`` spec, validating the provider."""
    provider, _, model = spec.partition(":")
    provider = provider.strip().lower()
    model = model.strip()
    if provider not in ("anthropic", "openai") or not model:
        raise ValueError(
            f"bad --verifier {spec!r}; expected 'anthropic:<slug>' or 'openai:<slug>'"
        )
    return provider, model


def build_verifiers(specs: list[str], base_url_root: str, key: str,
                    strict_schema: bool) -> list[Verifier]:
    """Construct one :class:`Verifier` per spec, sharing a single client per provider."""
    anthropic_client = None
    openai_client = None
    verifiers: list[Verifier] = []
    used_names: dict[str, int] = collections.Counter()

    for spec in specs:
        provider, model = parse_verifier_spec(spec)
        if provider == "anthropic":
            if anthropic_client is None:
                import anthropic

                anthropic_client = anthropic.Anthropic(
                    base_url=base_url_root, auth_token=key, api_key=None,
                    max_retries=5, timeout=120.0,
                )
            client = anthropic_client
        else:
            if openai_client is None:
                try:
                    import openai
                except ImportError as e:
                    raise SystemExit(
                        f"verifier {spec!r} needs the OpenAI SDK -- `pip install openai` "
                        "(only required for openai:* verifiers)."
                    ) from e

                openai_client = openai.OpenAI(
                    base_url=base_url_root.rstrip("/") + "/v1", api_key=key,
                    max_retries=5, timeout=120.0,
                )
            client = openai_client

        # Short, readable name (last path segment); de-duplicate if two slugs collide.
        short = model.split("/")[-1]
        used_names[short] += 1
        name = short if used_names[short] == 1 else f"{short}#{used_names[short]}"

        # Gemini's GCP-backed gateway route rejects our JSON response schema (it wants enum
        # values as strings, not the integers 0/1, and chokes on the nested "type" field), so
        # a strict-schema request 400s on *every* row. Silently disable strict-schema for
        # gemini models only -- gpt and claude still get it. Without the response schema gemini
        # free-forms the JSON and parses fine (~97% on the full run), so it stays in the vote.
        eff_strict = strict_schema and "gemini" not in model.lower()
        if strict_schema and not eff_strict:
            print(f"note: strict-schema disabled for {name} "
                  "(gemini route rejects the response schema); it will free-form JSON instead.")
        verifiers.append(Verifier(name, provider, model, client, eff_strict))

    return verifiers


# ---------------------------------------------------------------------------
# Kappa / agreement statistics (hand-computed; no scipy/sklearn)
# ---------------------------------------------------------------------------


def cohen_kappa(a: list[int], b: list[int]) -> float | None:
    """Cohen's kappa for two raters over binary labels; ``None`` if undefined (no variance)."""
    n = len(a)
    if n == 0:
        return None
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa = sum(a) / n
    pb = sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1.0:
        return None  # one/both raters constant -> kappa undefined (report raw agreement)
    return (po - pe) / (1 - pe)


def fleiss_kappa(count_rows: list[tuple[int, int]], raters: int) -> float | None:
    """
    Fleiss' kappa for a fixed number of raters over two categories (0/1).

    :param count_rows: per-item ``(n_zeros, n_ones)`` with ``n_zeros + n_ones == raters``.
    :param raters: raters per item (constant).
    :returns: kappa, or ``None`` if undefined.
    """
    n = len(count_rows)
    if n == 0 or raters < 2:
        return None
    total = n * raters
    p1 = sum(r[1] for r in count_rows) / total
    p0 = sum(r[0] for r in count_rows) / total
    pe = p0 * p0 + p1 * p1
    p_i = [(r[0] ** 2 + r[1] ** 2 - raters) / (raters * (raters - 1)) for r in count_rows]
    p_bar = sum(p_i) / n
    if pe >= 1.0:
        return None
    return (p_bar - pe) / (1 - pe)


# ---------------------------------------------------------------------------
# Per-criterion aggregation
# ---------------------------------------------------------------------------


def summarize_verifier(entry: dict, label: dict | None) -> dict:
    """Trim a verifier's result for the output record (raw text/response stay in the logs)."""
    if label is None:
        return {
            "name": entry["name"], "provider": entry["provider"], "model": entry["model"],
            "q_mapping": None, "primary_skill": None, "rationale": None,
            "error": entry.get("error"),
        }
    return {
        "name": entry["name"], "provider": entry["provider"], "model": entry["model"],
        "q_mapping": {s: int(label[s]) for s in SKILLS},
        "primary_skill": label.get("primary_skill"),
        "rationale": label.get("rationale"),
        "error": None,
    }


def aggregate(gen_record: dict, per_verifier: "list[tuple[dict, dict | None]]") -> dict:
    """
    Combine the generator's label with the verifier labels into a ``verification`` block.

    :param gen_record: the generation output record (its ``q_mapping`` is the generator vote).
    :param per_verifier: list of ``(log_entry, label_or_None)`` in configured order.
    """
    gen_map = {s: int(gen_record["q_mapping"][s]) for s in SKILLS}

    verifier_summaries = [summarize_verifier(e, lbl) for (e, lbl) in per_verifier]
    good = [(e["name"], {s: int(lbl[s]) for s in SKILLS}, lbl.get("primary_skill"))
            for (e, lbl) in per_verifier if lbl is not None]

    votes = {s: [gen_map[s]] + [vm[s] for (_, vm, _) in good] for s in SKILLS}
    # Per-skill majority vote. A strict majority needs more than half the votes on one side;
    # an even split (ones*2 == n, e.g. generator + a single verifier that disagree) has NO
    # majority and resolves toward 0 -- the conservative choice -- and is always needs_review.
    consensus = {s: (1 if sum(votes[s]) * 2 > len(votes[s]) else 0) for s in SKILLS}
    tied_skills = [s for s in SKILLS if sum(votes[s]) * 2 == len(votes[s])]
    agreement = {s: len(set(votes[s])) == 1 for s in SKILLS}
    disputed = [s for s in SKILLS if not agreement[s]]

    unanimous = len(good) >= 1 and not disputed
    needs_review = (len(good) == 0) or bool(disputed)

    # Stamped final label for downstream MIRT = the majority vote. `resolution` records how it
    # was reached: `unanimous` (all raters agree, gold), `majority` (strict >half majority --
    # only reachable with >=3 raters), `tie` (even split with no majority, label defaulted to 0
    # -- the usual disagreement case with a single verifier), or `generator_only` (no verifier
    # succeeded). Everything but `unanimous`/`majority` should be adjudicated, not trusted.
    final_q_mapping = dict(consensus)
    if len(good) == 0:
        resolution = "generator_only"
    elif unanimous:
        resolution = "unanimous"
    elif tied_skills:
        resolution = "tie"
    else:
        resolution = "majority"

    fn_skills = [s for s in SKILLS if gen_map[s] == 0 and any(vm[s] == 1 for (_, vm, _) in good)]
    fp_skills = [s for s in SKILLS if gen_map[s] == 1 and any(vm[s] == 0 for (_, vm, _) in good)]

    primary_skills = {"generator": gen_record.get("primary_skill")}
    for name, _, ps in good:
        primary_skills[name] = ps

    return {
        "verifiers": verifier_summaries,
        "successful_raters": ["generator"] + [n for (n, _, _) in good],
        "votes": votes,
        "consensus": consensus,
        "final_q_mapping": final_q_mapping,
        "resolution": resolution,
        "agreement": agreement,
        "disputed_skills": disputed,
        "tied_skills": tied_skills,
        "unanimous": unanimous,
        "needs_review": needs_review,
        "insufficient_verifiers": len(good) == 0,
        "false_negative_skills": fn_skills,
        "false_positive_skills": fp_skills,
        "primary_skills": primary_skills,
    }


# ---------------------------------------------------------------------------
# Item selection / blind input
# ---------------------------------------------------------------------------


def input_path_for(mode: str, override: str | None) -> Path:
    """Resolve the generation-output file to verify."""
    if override:
        return Path(override)
    stem = "rubrics_qmatrix.sample" if mode == "sample" else "rubrics_qmatrix"
    return DATA_DIR / f"{stem}.jsonl"


def build_blind_inputs(gen_records: list[dict]) -> tuple[list[dict], dict[int, str], list[dict]]:
    """
    Split generation records into verifiable/unverifiable and render the blind user message.

    The blind input is built from the ORIGINAL ``rubrics.jsonl`` (identical to what the
    generator saw), not from the generation output -- the output overwrote ``primary_skill``
    with the model's own choice, which would leak the generator's answer to a verifier.

    :returns: ``(labeled, user_contents, skipped, scenarios)`` -- ``labeled`` are records with a
        non-null generator ``q_mapping``; ``user_contents`` maps their index -> blind message;
        ``skipped`` are records the generator failed to label (recorded as unverifiable);
        ``scenarios`` is the ``scenario_id`` -> scenario index (reused by the review queue).
    """
    orig_by_id = {r["criterion_id"]: r for r in gq.read_jsonl(RUBRICS_PATH)}
    scenarios = gq.index_scenarios(gq.read_jsonl(SCENARIOS_PATH))

    labeled: list[dict] = []
    skipped: list[dict] = []
    user_contents: dict[int, str] = {}

    for rec in gen_records:
        if rec.get("q_mapping") is None:
            skipped.append(rec)
            continue
        idx = len(labeled)
        labeled.append(rec)
        orig = orig_by_id.get(rec["criterion_id"], rec)
        scenario = scenarios.get(orig.get("scenario_id"))
        user_contents[idx] = gq.build_user_content(orig, scenario)

    return labeled, user_contents, skipped, scenarios


# ---------------------------------------------------------------------------
# Concurrent verification runner
# ---------------------------------------------------------------------------


def run_verification(verifiers: list[Verifier], mode: str, concurrency: int,
                     labeled: list[dict], user_contents: dict[int, str]
                     ) -> dict[int, list[tuple[dict, dict | None]]]:
    """
    Run every ``(criterion, verifier)`` pair through a single bounded thread pool.

    :returns: ``{idx: [(log_entry, label_or_None), ...]}`` with entries in verifier order.
    """
    results: dict[int, dict[int, tuple[dict, dict | None]]] = collections.defaultdict(dict)
    lock = threading.Lock()
    tasks = [(idx, vi) for idx in range(len(labeled)) for vi in range(len(verifiers))]
    total = len(tasks)

    def work(idx: int, vi: int):
        label, entry = verifiers[vi].label(user_contents[idx])
        return idx, vi, label, entry

    print(f"{mode} mode: verifying {len(labeled)} criteria x {len(verifiers)} verifiers "
          f"= {total} calls (concurrency={concurrency}).")
    print("verifiers: " + ", ".join(f"{v.name} [{v.provider}:{v.model}]" for v in verifiers))

    done = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(work, idx, vi) for idx, vi in tasks]
        for fut in as_completed(futures):
            idx, vi, label, entry = fut.result()
            with lock:
                results[idx][vi] = (entry, label)
                done += 1
                if done % 50 == 0 or done == total:
                    print(f"  {done}/{total} calls done")

    ordered: dict[int, list[tuple[dict, dict | None]]] = {}
    for idx in range(len(labeled)):
        ordered[idx] = [results[idx][vi] for vi in range(len(verifiers))]
    return ordered


# ---------------------------------------------------------------------------
# Report-only: reconstruct verifier labels from existing per-item logs
# ---------------------------------------------------------------------------


def load_from_logs(mode: str, labeled: list[dict]
                   ) -> dict[int, list[tuple[dict, dict | None]]]:
    """Rebuild ``(log_entry, label)`` per criterion from ``qmatrix_verify_logs/<mode>/``."""
    log_dir = VERIFY_LOG_DIR / mode
    ordered: dict[int, list[tuple[dict, dict | None]]] = {}
    for idx, rec in enumerate(labeled):
        path = log_dir / f"{rec['criterion_id']}.json"
        entries: list[tuple[dict, dict | None]] = []
        if path.exists():
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            for entry in payload.get("verifiers", []):
                label = None
                if entry.get("text") and not entry.get("error"):
                    try:
                        label = gq.validate_label(gq.parse_json_object(entry["text"]))
                    except Exception:  # noqa: BLE001 - treat unparseable log as a drop-out
                        label = None
                entries.append((entry, label))
        ordered[idx] = entries
    return ordered


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def write_item_log(mode: str, criterion_id: str, user_content: str,
                   per_verifier: list[tuple[dict, dict | None]]) -> None:
    """Persist the blind request + every verifier's raw response for one criterion."""
    d = VERIFY_LOG_DIR / mode
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "criterion_id": criterion_id,
        "prompt_version": PROMPT_VERSION,
        "mode": mode,
        "request": {"system": SYSTEM_PROMPT, "user": user_content},
        "verifiers": [entry for (entry, _label) in per_verifier],
    }
    with open(d / f"{criterion_id}.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Statistics over all criteria
# ---------------------------------------------------------------------------


def compute_stats(labeled: list[dict], blocks: list[dict],
                  verifier_names: list[str]) -> dict:
    """Compute per-skill IRR, confusion, and the derived 0-label false-negative audit."""
    stats: dict = {"per_skill": {}, "verifier_names": verifier_names}

    for s in SKILLS:
        gen_vals: list[int] = []
        per_verifier_pairs = {name: {"gen": [], "ver": []} for name in verifier_names}
        fleiss_rows: list[tuple[int, int]] = []  # rows where every verifier succeeded
        any_fn = any_fp = 0
        gen0_total = gen0_any1 = gen0_majority1 = 0

        for rec, block in zip(labeled, blocks):
            gen = int(rec["q_mapping"][s])
            gen_vals.append(gen)
            present = {v["name"]: v["q_mapping"][s] for v in block["verifiers"]
                       if v["q_mapping"] is not None}
            for name, val in present.items():
                per_verifier_pairs[name]["gen"].append(gen)
                per_verifier_pairs[name]["ver"].append(int(val))
            if present:
                vervals = list(present.values())
                if gen == 0 and any(v == 1 for v in vervals):
                    any_fn += 1
                if gen == 1 and any(v == 0 for v in vervals):
                    any_fp += 1
                if gen == 0:
                    gen0_total += 1
                    if any(v == 1 for v in vervals):
                        gen0_any1 += 1
                    if sum(v == 1 for v in vervals) * 2 > len(vervals):
                        gen0_majority1 += 1
            if len(present) == len(verifier_names) and verifier_names:
                ones = gen + sum(int(v) for v in present.values())
                raters = 1 + len(verifier_names)
                fleiss_rows.append((raters - ones, ones))

        cohen = {}
        raw_agree = {}
        for name in verifier_names:
            a = per_verifier_pairs[name]["gen"]
            b = per_verifier_pairs[name]["ver"]
            cohen[name] = cohen_kappa(a, b)
            raw_agree[name] = (sum(1 for x, y in zip(a, b) if x == y) / len(a)) if a else None

        stats["per_skill"][s] = {
            "generator_positive": sum(gen_vals),
            "cohen_kappa_vs_generator": cohen,
            "raw_agreement_vs_generator": raw_agree,
            "fleiss_kappa_all_raters": fleiss_kappa(fleiss_rows, 1 + len(verifier_names)),
            "fleiss_n": len(fleiss_rows),
            "false_negative_candidates_any": any_fn,
            "false_positive_candidates_any": any_fp,
            "zero_label_audit": {
                "generator_zero_cells": gen0_total,
                "flagged_by_any_verifier": gen0_any1,
                "flagged_by_majority": gen0_majority1,
                "any_rate": (gen0_any1 / gen0_total) if gen0_total else None,
                "majority_rate": (gen0_majority1 / gen0_total) if gen0_total else None,
            },
        }
    return stats


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

_CRIT_RANK = {"critical_negative": 0, "critical": 1, "not_critical": 2}


def _compact_map(m: dict | None) -> str:
    """Render a q_mapping as ``content/diagnosis/scaffolding`` bits, e.g. ``100``."""
    if m is None:
        return "..."
    return "".join(str(int(m[s])) for s in SKILLS)


def _sanitize_tsv(text: str) -> str:
    return (text or "").replace("\t", " ").replace("\n", " ").replace("\r", " ").strip()


def build_review_queue(labeled: list[dict], blocks: list[dict],
                       scenarios: dict[str, dict]) -> list[dict]:
    """Build the priority-sorted human-review queue of non-unanimous criteria."""
    queue: list[dict] = []
    for rec, block in zip(labeled, blocks):
        if not block["needs_review"]:
            continue
        scenario = scenarios.get(rec.get("scenario_id"))
        prompt = (scenario or {}).get("prompt") or ""
        maps = {"generator": {s: int(rec["q_mapping"][s]) for s in SKILLS}}
        rationales = {"generator": rec.get("q_rationale")}
        for v in block["verifiers"]:
            maps[v["name"]] = v["q_mapping"]
            rationales[v["name"]] = v["rationale"] if v["q_mapping"] else f"(no label: {v['error']})"

        crit = rec.get("criticality")
        queue.append({
            "criterion_id": rec["criterion_id"],
            "scenario_id": rec.get("scenario_id"),
            "criticality": crit,
            "objectivity": rec.get("objectivity"),
            "explicitness": rec.get("explicitness"),
            "criterion": rec.get("criterion"),
            "scenario_prompt": prompt[:SCENARIO_PROMPT_TRUNC],
            "disputed_skills": block["disputed_skills"],
            "false_negative_skills": block["false_negative_skills"],
            "false_positive_skills": block["false_positive_skills"],
            "insufficient_verifiers": block["insufficient_verifiers"],
            "maps": maps,
            "consensus": block["consensus"],
            "primary_skills": block["primary_skills"],
            "rationales": rationales,
            "_sort": (
                _CRIT_RANK.get(crit, 3),
                -len(block["disputed_skills"]),
                0 if rec.get("explicitness") == "implicit" else 1,
                0 if rec.get("objectivity") == "subjective" else 1,
            ),
        })

    queue.sort(key=lambda q: q["_sort"])
    for q in queue:
        del q["_sort"]
    return queue


def write_review_tsv(path: Path, queue: list[dict], verifier_names: list[str]) -> None:
    """Write a compact one-line-per-criterion triage sheet (maps as content/diagnosis/scaffolding bits)."""
    cols = ["criterion_id", "criticality", "disputed", "gen"] + verifier_names + \
           ["fn", "fp", "criterion", "scenario_prompt"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for q in queue:
            row = [
                q["criterion_id"],
                q["criticality"] or "",
                ",".join(q["disputed_skills"]),
                _compact_map(q["maps"].get("generator")),
            ]
            row += [_compact_map(q["maps"].get(name)) for name in verifier_names]
            row += [
                ",".join(q["false_negative_skills"]),
                ",".join(q["false_positive_skills"]),
                _sanitize_tsv(q["criterion"]),
                _sanitize_tsv(q["scenario_prompt"]),
            ]
            f.write("\t".join(row) + "\n")


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------


def finish(mode: str, verifiers_desc: list[dict], verifier_names: list[str],
           gen_records: list[dict], labeled: list[dict], blocks: list[dict],
           skipped: list[dict], scenarios: dict[str, dict], strict_schema: bool,
           report_only: bool) -> None:
    """Write verified output, review queue, manifest, and print a summary."""
    # Verified output: every input record, with a verification block on the labeled ones.
    block_by_id = {rec["criterion_id"]: blk for rec, blk in zip(labeled, blocks)}
    verified: list[dict] = []
    for rec in gen_records:
        out = dict(rec)
        cid = rec["criterion_id"]
        if cid in block_by_id:
            out["verification"] = block_by_id[cid]
        else:
            out["verification"] = {"skipped": "generator produced no label", "needs_review": True}
        verified.append(out)

    suffix = ".sample" if mode == "sample" else ""
    verified_jsonl = DATA_DIR / f"rubrics_qmatrix_verified{suffix}.jsonl"
    verified_json = DATA_DIR / f"rubrics_qmatrix_verified{suffix}.json"
    gq.write_jsonl(verified_jsonl, verified)
    gq.write_json(verified_json, verified)

    queue = build_review_queue(labeled, blocks, scenarios)
    review_jsonl = DATA_DIR / f"review_queue{suffix}.jsonl"
    review_tsv = DATA_DIR / f"review_queue{suffix}.tsv"
    gq.write_jsonl(review_jsonl, queue)
    write_review_tsv(review_tsv, queue, verifier_names)

    stats = compute_stats(labeled, blocks, verifier_names)

    # Per-verifier success/failure tallies.
    per_verifier_counts = {name: {"ok": 0, "failed": 0} for name in verifier_names}
    failures: list[dict] = []
    for rec, blk in zip(labeled, blocks):
        for v in blk["verifiers"]:
            bucket = per_verifier_counts[v["name"]]
            if v["q_mapping"] is not None:
                bucket["ok"] += 1
            else:
                bucket["failed"] += 1
                failures.append({"criterion_id": rec["criterion_id"], "verifier": v["name"],
                                 "error": v["error"]})

    unanimous = sum(1 for b in blocks if b["unanimous"])
    needs_review = sum(1 for b in blocks if b["needs_review"])
    insufficient = sum(1 for b in blocks if b["insufficient_verifiers"])
    resolution_counts = collections.Counter(b["resolution"] for b in blocks)

    log_dir = VERIFY_LOG_DIR / mode
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / f"system_prompt_{PROMPT_VERSION}.txt", "w", encoding="utf-8") as f:
        f.write(SYSTEM_PROMPT + "\n")

    manifest = {
        "prompt_version": PROMPT_VERSION,
        "mode": mode,
        "report_only": report_only,
        "strict_schema": strict_schema,
        "timestamp_utc": gq.now_iso(),
        "verifiers": verifiers_desc,
        "input_records": len(gen_records),
        "verified": len(labeled),
        "unverifiable_generation_failures": len(skipped),
        "unanimous": unanimous,
        "needs_review": needs_review,
        "insufficient_verifiers": insufficient,
        "resolution_counts": dict(resolution_counts),
        "review_queue_size": len(queue),
        "per_verifier_counts": per_verifier_counts,
        "stats": stats,
        "failures": failures,
    }
    with open(log_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # ---- printed summary ----
    def fmt(x):
        return "  n/a" if x is None else f"{x:+.3f}" if isinstance(x, float) else str(x)

    print(f"\nWrote {len(verified)} records -> {verified_jsonl}")
    print(f"Review queue: {len(queue)} criteria -> {review_jsonl} (+ {review_tsv.name})")
    print(f"Verified {len(labeled)}, unanimous {unanimous}, needs_review {needs_review}, "
          f"insufficient_verifiers {insufficient}, gen-failures skipped {len(skipped)}.")
    print("Final labels by resolution: " +
          ", ".join(f"{k}={resolution_counts[k]}"
                    for k in ("unanimous", "majority", "tie", "generator_only")
                    if resolution_counts.get(k)))
    print("\nPer-verifier calls (ok/failed):")
    for name in verifier_names:
        c = per_verifier_counts[name]
        print(f"  {name:22s} {c['ok']:5d} ok / {c['failed']:4d} failed")

    print("\nInter-rater reliability (generator + verifiers):")
    for s in SKILLS:
        ps = stats["per_skill"][s]
        print(f"  [{s}]  Fleiss kappa(all)={fmt(ps['fleiss_kappa_all_raters'])} (n={ps['fleiss_n']})")
        for name in verifier_names:
            print(f"       vs {name:20s} Cohen kappa={fmt(ps['cohen_kappa_vs_generator'][name])}  "
                  f"raw agr={fmt(ps['raw_agreement_vs_generator'][name])}")

    print("\n0-label false-negative audit (generator=0 cells a verifier flagged 1):")
    for s in SKILLS:
        za = stats["per_skill"][s]["zero_label_audit"]
        print(f"  [{s}]  {za['flagged_by_any_verifier']}/{za['generator_zero_cells']} any "
              f"({fmt(za['any_rate'])}), majority {za['flagged_by_majority']} "
              f"({fmt(za['majority_rate'])})")

    print(f"\nLogs + manifest: {log_dir}")


# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------


def list_models(base_url_root: str, key: str) -> None:
    """
    Print the model ids this key can call, via the gateway's OpenAI-compatible ``/v1/models``.

    Hits the exact base URL + auth the ``openai:`` verifiers use, so whatever prints here is a
    slug you can paste straight into ``--verifier openai:<id>`` without a 403.
    """
    try:
        import openai
    except ImportError as e:
        raise SystemExit("`pip install openai` to list models.") from e

    client = openai.OpenAI(base_url=base_url_root.rstrip("/") + "/v1", api_key=key)
    try:
        ids = sorted(m.id for m in client.models.list().data)
    except Exception as e:  # noqa: BLE001 - surface the gateway error verbatim
        raise SystemExit(f"could not list models from {base_url_root}/v1/models: {e}") from e

    print(f"{len(ids)} models available to this key via {base_url_root}/v1 :")
    for i in ids:
        print(f"  {i}")
    print("\nUse a non-Claude id as  --verifier openai:<id>  (Claude ids -> anthropic:<id>).")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_gateway(parser, args) -> tuple[str, str]:
    """Resolve (base_url_root, key) from flags/env and force Bearer auth for the Anthropic client."""
    base_url_root = args.base_url or os.environ.get("ANTHROPIC_BASE_URL")
    if not base_url_root:
        parser.error("set ANTHROPIC_BASE_URL (or --base-url) to the TrueFoundry gateway root")
    key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        parser.error("set ANTHROPIC_AUTH_TOKEN to your TrueFoundry user key")
    os.environ.pop("ANTHROPIC_API_KEY", None)  # force Bearer for the Anthropic client
    return base_url_root, key


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify synthetic Q-matrix labels with two blind LLM verifiers "
                    "(cross-model via the TrueFoundry gateway) and build a human-review queue."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sample", action="store_true",
                       help="Verify the generation sample (rubrics_qmatrix.sample.jsonl).")
    group.add_argument("--full", action="store_true",
                       help="Verify the full generation output (rubrics_qmatrix.jsonl).")
    group.add_argument("--list-models", action="store_true",
                       help="Print the model ids this key can call and exit (no verification).")
    parser.add_argument("--verifier", action="append", metavar="PROVIDER:MODEL",
                        help="A verifier as 'anthropic:<slug>' or 'openai:<slug>'. Repeatable. "
                             f"Default: {DEFAULT_VERIFIERS}")
    parser.add_argument("--input", default=None,
                        help="Override the generation-output file to verify.")
    parser.add_argument("--base-url", default=None,
                        help="Gateway root (else ANTHROPIC_BASE_URL). Anthropic uses it as-is; "
                             "the OpenAI client appends /v1.")
    parser.add_argument("--concurrency", type=int, default=gq.DEFAULT_CONCURRENCY,
                        help="Max concurrent calls (default: %(default)s).")
    parser.add_argument("--strict-schema", action="store_true",
                        help="Send provider-native structured-output constraints (opt-in).")
    parser.add_argument("--report-only", action="store_true",
                        help="Recompute stats + review queue from existing logs; no API calls.")
    args = parser.parse_args()

    if args.list_models:
        base_url_root, key = _resolve_gateway(parser, args)
        list_models(base_url_root, key)
        return

    mode = "sample" if args.sample else "full"
    specs = args.verifier or DEFAULT_VERIFIERS

    input_path = input_path_for(mode, args.input)
    if not input_path.exists():
        parser.error(f"generation output not found: {input_path} (run generate_qmatrix.py first)")

    gen_records = gq.read_jsonl(input_path)
    labeled, user_contents, skipped, scenarios = build_blind_inputs(gen_records)

    if not args.report_only:
        base_url_root, key = _resolve_gateway(parser, args)
        verifiers = build_verifiers(specs, base_url_root, key, args.strict_schema)
        verifier_names = [v.name for v in verifiers]
        verifiers_desc = [{"name": v.name, "provider": v.provider, "model": v.model}
                          for v in verifiers]

        per_criterion = run_verification(verifiers, mode, args.concurrency, labeled, user_contents)
        for idx, rec in enumerate(labeled):
            write_item_log(mode, rec["criterion_id"], user_contents[idx], per_criterion[idx])
    else:
        per_criterion = load_from_logs(mode, labeled)
        # Derive verifier identity from the logs (order preserved as first-seen).
        seen: "dict[str, dict]" = {}
        for idx in range(len(labeled)):
            for entry, _lbl in per_criterion[idx]:
                seen.setdefault(entry["name"],
                                {"name": entry["name"], "provider": entry.get("provider"),
                                 "model": entry.get("model")})
        verifiers_desc = list(seen.values())
        verifier_names = [d["name"] for d in verifiers_desc]
        if not verifier_names:
            parser.error(f"no logs found under {VERIFY_LOG_DIR / mode}; run without --report-only first")

    # Aggregate per criterion. In report-only, pad missing verifiers so every block lists them.
    blocks: list[dict] = []
    for idx, rec in enumerate(labeled):
        entries = per_criterion[idx]
        if args.report_only:
            have = {e["name"] for (e, _l) in entries}
            for d in verifiers_desc:
                if d["name"] not in have:
                    entries = entries + [({"name": d["name"], "provider": d["provider"],
                                           "model": d["model"], "error": "no log entry"}, None)]
        blocks.append(aggregate(rec, entries))

    finish(mode, verifiers_desc, verifier_names, gen_records, labeled, blocks, skipped,
           scenarios, args.strict_schema, args.report_only)


if __name__ == "__main__":
    main()
