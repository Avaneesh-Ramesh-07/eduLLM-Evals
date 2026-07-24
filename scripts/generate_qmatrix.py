"""
Synthetic Q-matrix generation for the reformatted TutorBench rubrics.

Each rubric criterion in ``data/rubrics.jsonl`` is labeled with a
Q-matrix row over three latent tutoring skills -- ``content``, ``diagnosis``,
``scaffolding`` -- indicating which skills a tutor *must* exercise to
satisfy that criterion, plus a ``q_rationale`` string. Labels come from Claude following
the project's Synthetic Q-Matrix Generation procedure: for every skill it marks ``1`` the
model must supply evidence from the item, an explanation of why the criterion cannot be met
without that skill, and a counterfactual describing why a model lacking the skill would
fail. That reasoning is emitted as JSON (not hidden thinking) so it is fully logged.

The four skill definitions and labeling examples live in this file as a versioned constant
(``PROMPT_VERSION``) and are written into the run log for reproducibility.

Routing (TrueFoundry AI Gateway)
--------------------------------
All AI access + keys go through the user's TrueFoundry gateway, which exposes an
*Anthropic-compatible* ``/v1/messages`` endpoint (verified: it returns 200). The Anthropic
SDK is pointed at it:

- ``ANTHROPIC_BASE_URL`` = the gateway root, ``https://tfy.promptlens.trilogy.com``. The SDK
  appends ``/v1/messages`` -- do NOT include a path suffix here.
- ``ANTHROPIC_AUTH_TOKEN`` = the TrueFoundry user API key. The gateway authenticates with
  ``Authorization: Bearer <key>`` (per its Guide), which the SDK sends when the credential is
  given as ``auth_token`` / ``ANTHROPIC_AUTH_TOKEN`` -- NOT ``ANTHROPIC_API_KEY`` (that would
  be sent as ``x-api-key``). This script forces Bearer auth regardless.
- ``--model`` is the TrueFoundry catalog slug (provider-group prefixed), e.g.
  ``claude-group/claude-opus-4-8`` (the default).

TrueFoundry does not proxy Anthropic's native Batch API, so the full run uses a bounded
thread pool of synchronous ``/v1/messages`` calls. ``cache_control`` is passed through by
the gateway, so the shared system prompt is still cached. Structured-output enforcement
(``output_config``) is opt-in (``--strict-schema``) since it is undocumented on the gateway;
by default the prompt specifies the JSON shape and the response is parsed defensively.

Usage
-----
.. code-block:: bash

    pip install anthropic
    export ANTHROPIC_BASE_URL="https://tfy.promptlens.trilogy.com"   # SDK appends /v1/messages
    export ANTHROPIC_AUTH_TOKEN="<TrueFoundry user key>"             # sent as Bearer
    unset ANTHROPIC_API_KEY                                          # avoid x-api-key precedence

    python generate_qmatrix.py --sample 50     # validate the prompt first (default model = claude-group/claude-opus-4-8)
    python generate_qmatrix.py --full          # label all criteria
    python generate_qmatrix.py --retry-failed  # re-label only rows that failed, merge back in

Outputs
-------
- ``data/rubrics_qmatrix.sample.{jsonl,json}`` (``--sample``) or
  ``data/rubrics_qmatrix.{jsonl,json}`` (``--full``): copies of the rubric
  records with ``q_mapping`` and ``q_rationale`` filled in. The original ``rubrics.jsonl``
  is never modified.
- ``qmatrix_logs/<mode>/``: one ``<criterion_id>.json`` per criterion (request + raw
  response), the exact system prompt, and a ``manifest.json`` run record.
"""

from __future__ import annotations

import argparse
import collections
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Paths and run constants
# ---------------------------------------------------------------------------

DATA_DIR = Path("data")
RUBRICS_PATH = DATA_DIR / "rubrics.jsonl"
SCENARIOS_PATH = DATA_DIR / "scenarios.jsonl"
LOG_DIR = Path("qmatrix_logs")

# TrueFoundry catalog slug for Opus 4.8 in this tenant (from Maat -> Profile -> Models).
DEFAULT_MODEL = "claude-group/claude-opus-4-8"
MAX_TOKENS = 3000
DEFAULT_CONCURRENCY = 8
# v2: dropped `adaptation` -> 3 skills. v3: added model-designated `primary_skill`.
PROMPT_VERSION = "qmatrix-v3-3skill-primary"

SKILLS = ("content", "diagnosis", "scaffolding")


# ---------------------------------------------------------------------------
# Structured output schema (used only with --strict-schema; strict, inline, no $ref)
# ---------------------------------------------------------------------------

QMATRIX_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "integer", "enum": [0, 1]},
        "diagnosis": {"type": "integer", "enum": [0, 1]},
        "scaffolding": {"type": "integer", "enum": [0, 1]},
        # The single most important skill among those marked 1; null iff all are 0.
        "primary_skill": {"type": ["string", "null"], "enum": list(SKILLS) + [None]},
        "justifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string", "enum": list(SKILLS)},
                    "evidence": {"type": "string"},
                    "why_required": {"type": "string"},
                    "counterfactual": {"type": "string"},
                },
                "required": ["skill", "evidence", "why_required", "counterfactual"],
                "additionalProperties": False,
            },
        },
        "rationale": {"type": "string"},
    },
    "required": ["content", "diagnosis", "scaffolding", "primary_skill", "justifications",
                 "rationale"],
    "additionalProperties": False,
}

OUTPUT_CONFIG = {"format": {"type": "json_schema", "schema": QMATRIX_JSON_SCHEMA}}


# ---------------------------------------------------------------------------
# Prompt (versioned; logged verbatim with every run)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert in educational measurement labeling a Q-matrix for a multidimensional
item-response-theory (MIRT) study of AI tutors. You will be shown a tutoring scenario and
a single rubric criterion used to score a tutor's response. Decide which of three latent
tutoring skills a competent tutor MUST exercise in order to satisfy that criterion.

THE THREE SKILLS
- content: Subject-matter correctness. The criterion turns on accurate domain knowledge --
  correct facts, definitions, computations, formulas, or solution steps.
    Positive: "The response correctly computes the second derivative."
    Negative: a criterion purely about tone or formatting that does not hinge on any
    domain fact.
- diagnosis: Reading the STUDENT's specific error, misconception, knowledge gap, or state
  of understanding from what they said or did -- not merely solving the problem.
    Positive: "The response identifies that the student added the denominators."
    Negative: "The response states the correct final answer" (no reading of a student error).
- scaffolding: Pedagogical structuring of the help -- decomposing into steps, giving hints
  instead of revealing answers, asking guiding questions, sequencing support, promoting
  active learning.
    Positive: "The response gives a hint without revealing the full solution."
    Negative: "The response is factually correct" (correctness, not structuring).

LABELING RULES
- Mark a skill 1 ONLY IF a tutor could not reliably satisfy the criterion without
  exercising that skill. Default to 0. Be conservative -- do not mark a skill just because
  it might plausibly help.
- Skills may overlap: a criterion can require several skills. A criterion may also
  legitimately require none of the three (all zeros) -- e.g. a pure formatting check.
- The criterion metadata (primary_skill, criticality, objectivity, explicitness) is a HINT
  from the source dataset, not ground truth. Judge from the criterion text and scenario.
- For EVERY skill you mark 1, add one entry to "justifications" containing:
    * evidence: a concrete quote or pointer from the scenario/criterion,
    * why_required: why the criterion cannot be satisfied without this skill,
    * counterfactual: why a tutor lacking this skill specifically could not satisfy it.
  Do NOT add justification entries for skills marked 0.
- Designate the single MOST important skill as "primary_skill" -- the one skill most central
  to satisfying this criterion. It MUST be one of the skills you marked 1. If you marked no
  skill 1, set "primary_skill" to null.

OUTPUT FORMAT
Return ONLY a single JSON object -- no prose, no markdown, no code fences -- with exactly
these keys:
{
  "content": 0 or 1,
  "diagnosis": 0 or 1,
  "scaffolding": 0 or 1,
  "primary_skill": "<the one skill marked 1 that matters most, or null if none are marked 1>",
  "justifications": [
    {"skill": "<content|diagnosis|scaffolding>",
     "evidence": "...", "why_required": "...", "counterfactual": "..."}
  ],
  "rationale": "one concise sentence summarizing the mapping decision"
}
Include one "justifications" entry per skill marked 1, and none for skills marked 0."""

SYSTEM_BLOCKS = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


# ---------------------------------------------------------------------------
# Loading / joining
# ---------------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSON-lines file into a list of dicts."""
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def index_scenarios(scenarios: list[dict]) -> dict[str, dict]:
    """Index scenario records by ``scenario_id`` for O(1) join with criteria."""
    return {s["scenario_id"]: s for s in scenarios}


def index_by_id(records: list[dict]) -> dict[str, dict]:
    """Index records by ``criterion_id`` for O(1) lookup and merge."""
    return {r["criterion_id"]: r for r in records}


def build_user_content(criterion: dict, scenario: dict | None) -> str:
    """Render the per-criterion user message: scenario context + the criterion to label."""
    lines: list[str] = []
    if scenario is not None:
        lines.append(f"SUBJECT: {scenario.get('subject')}")
        lines.append(f"USE CASE: {scenario.get('use_case')}")

        turns = scenario.get("conversation_context") or []
        if turns:
            lines.append("CONVERSATION SO FAR:")
            for turn in turns:
                lines.append(f"  [{turn.get('role')}] {turn.get('content')}")

        lines.append("TURN THE TUTOR IS EVALUATED ON (the tutor must respond to this):")
        lines.append(f"  {scenario.get('prompt')}")

        ref = scenario.get("reference_solution")
        if ref:
            lines.append("REFERENCE SOLUTION:")
            lines.append(f"  {ref}")
    else:
        lines.append("(scenario context unavailable)")

    lines.append("")
    lines.append("CRITERION TO LABEL:")
    lines.append(f'  "{criterion.get("criterion")}"')
    lines.append(
        "CRITERION METADATA (hints, not ground truth): "
        f"primary_skill={criterion.get('primary_skill')}, "
        f"criticality={criterion.get('criticality')}, "
        f"objectivity={criterion.get('objectivity')}, "
        f"explicitness={criterion.get('explicitness')}"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parsing / validating a returned label and turning it into the schema fields
# ---------------------------------------------------------------------------


def parse_json_object(text: str) -> dict:
    """
    Defensively decode a JSON object from model output.

    Tolerates markdown code fences and leading/trailing prose by extracting the first
    balanced ``{...}`` span. ``--strict-schema`` makes the response pure JSON, but this
    keeps the default (prompt-only) path robust across gateways.

    :raises ValueError: if no JSON object can be decoded.
    """
    t = text.strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in response text")
    return json.loads(t[start:end + 1])


def validate_label(obj: object) -> dict:
    """
    Validate a decoded label against the expected shape so malformed rows are recorded as
    failures rather than silently corrupting the output.

    :raises ValueError: if the object does not match the Q-matrix label shape.
    """
    if not isinstance(obj, dict):
        raise ValueError("label is not a JSON object")
    for s in SKILLS:
        if obj.get(s) not in (0, 1):
            raise ValueError(f"skill {s!r} is not 0/1: {obj.get(s)!r}")
    if not isinstance(obj.get("justifications"), list):
        raise ValueError("'justifications' is not a list")
    if not isinstance(obj.get("rationale"), str) or not obj["rationale"].strip():
        raise ValueError("'rationale' is missing or empty")

    # primary_skill must be the most-important marked skill (or null iff none are marked).
    active = [s for s in SKILLS if obj[s] == 1]
    primary = obj.get("primary_skill")
    if active:
        if primary not in active:
            raise ValueError(
                f"'primary_skill' {primary!r} must be one of the skills marked 1: {active}"
            )
    elif primary is not None:
        raise ValueError(f"'primary_skill' must be null when no skill is marked 1, got {primary!r}")
    return obj


def label_to_fields(label: dict) -> tuple[dict, str]:
    """
    Convert a validated label dict into ``(q_mapping, q_rationale)``.

    Per the project decision, all per-skill evidence and counterfactuals are packed into
    the single ``q_rationale`` string (the full structured form is preserved in the logs).
    """
    q_mapping = {skill: int(label[skill]) for skill in SKILLS}

    parts = [label["rationale"].strip()]
    just_by_skill = {
        j.get("skill"): j for j in label.get("justifications", []) if isinstance(j, dict)
    }
    active = [s for s in SKILLS if q_mapping[s] == 1]
    if active:
        parts.append("Skill justifications:")
        for skill in active:
            j = just_by_skill.get(skill)
            if j is None:
                parts.append(f"- {skill}: (model marked 1 but gave no justification)")
            else:
                parts.append(
                    f"- {skill}: evidence: {str(j.get('evidence', '')).strip()} | "
                    f"why required: {str(j.get('why_required', '')).strip()} | "
                    f"counterfactual: {str(j.get('counterfactual', '')).strip()}"
                )
    else:
        parts.append("(No skill strictly required for this criterion.)")

    return q_mapping, "\n".join(parts)


def extract_text(message) -> str:
    """Return the concatenated text blocks of a Messages response."""
    return "".join(b.text for b in message.content if b.type == "text")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def log_dir_for(mode: str) -> Path:
    """Return (and create) the per-mode log directory."""
    d = LOG_DIR / mode
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_item_log(mode: str, criterion_id: str, payload: dict) -> None:
    """Write the raw request/response record for one criterion."""
    path = log_dir_for(mode) / f"{criterion_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def now_iso() -> str:
    """Return a UTC ISO-8601 timestamp for run manifests."""
    return datetime.now(timezone.utc).isoformat()


def safe_to_dict(obj):
    """Best-effort plain-dict view of an SDK object for logging (public ``.to_dict()``)."""
    try:
        return obj.to_dict()
    except Exception:  # pragma: no cover - logging must never crash the run
        return str(obj)


# ---------------------------------------------------------------------------
# Output writers (mirror test.py's helpers)
# ---------------------------------------------------------------------------


def write_jsonl(path: Path, records: Iterable[dict]) -> None:
    """Write one JSON object per line."""
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_json(path: Path, records: list[dict]) -> None:
    """Write a single pretty-printed (multi-line) JSON array."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# Labeling one criterion (shared by both modes)
# ---------------------------------------------------------------------------


def label_one(client, model: str, mode: str, strict_schema: bool,
              criterion: dict, scenario: dict | None) -> tuple[dict, dict | None]:
    """
    Label a single criterion via one ``/v1/messages`` call.

    :returns: ``(record, failure)`` -- ``record`` is the rubric dict with ``q_mapping`` /
        ``q_rationale`` set (both ``None`` on failure); ``failure`` is ``None`` on success
        or a ``{"criterion_id", "error"}`` dict otherwise.
    """
    import anthropic

    cid = criterion["criterion_id"]
    user_content = build_user_content(criterion, scenario)
    record = dict(criterion)

    kwargs = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_BLOCKS,
        messages=[{"role": "user", "content": user_content}],
    )
    if strict_schema:
        kwargs["output_config"] = OUTPUT_CONFIG

    try:
        response = client.messages.create(**kwargs)
        text = extract_text(response)
        label = validate_label(parse_json_object(text))
        record["q_mapping"], record["q_rationale"] = label_to_fields(label)
        record["primary_skill"] = label["primary_skill"]  # overwrite TutorBench's value
        write_item_log(mode, cid, {
            "criterion_id": cid,
            "prompt_version": PROMPT_VERSION,
            "model": model,
            "strict_schema": strict_schema,
            "request": {"system": SYSTEM_PROMPT, "user": user_content},
            "response": safe_to_dict(response),
        })
        return record, None
    except (anthropic.APIError, ValueError) as e:  # network / server / parse / validation
        record["q_mapping"] = None
        record["q_rationale"] = None
        record["primary_skill"] = None
        write_item_log(mode, cid, {
            "criterion_id": cid,
            "model": model,
            "error": str(e),
            "request": {"system": SYSTEM_PROMPT, "user": user_content},
        })
        return record, {"criterion_id": cid, "error": str(e)}


# ---------------------------------------------------------------------------
# Concurrent runner
# ---------------------------------------------------------------------------


def run_items(client, model: str, mode: str, strict_schema: bool, concurrency: int,
              items: list[dict], scenarios: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Label ``items`` with a bounded thread pool, preserving original order."""
    results: dict[int, dict] = {}
    failures: list[dict] = []
    lock = threading.Lock()
    total = len(items)

    def work(idx: int, crit: dict):
        scenario = scenarios.get(crit["scenario_id"])
        return idx, label_one(client, model, mode, strict_schema, crit, scenario)

    print(f"{mode} mode: labeling {total} criteria with {model} "
          f"(concurrency={concurrency}, strict_schema={strict_schema}).")

    done = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(work, i, c) for i, c in enumerate(items)]
        for fut in as_completed(futures):
            idx, (record, failure) = fut.result()
            with lock:
                results[idx] = record
                if failure is not None:
                    failures.append(failure)
                done += 1
                if done % 25 == 0 or done == total:
                    print(f"  {done}/{total} done ({len(failures)} failed)")

    ordered = [results[i] for i in range(total)]
    return ordered, failures


def stride_sample(items: list[dict], n: int) -> list[dict]:
    """Take ``n`` evenly-spaced items so the sample spans use cases/subjects."""
    if n >= len(items):
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


# ---------------------------------------------------------------------------
# Finalization: write outputs, manifest, and a distribution summary
# ---------------------------------------------------------------------------


def finish(mode: str, model: str, strict_schema: bool, out_records: list[dict],
           failures: list[dict]) -> None:
    """Write output files, the run manifest, the system prompt, and print a summary."""
    stem = "rubrics_qmatrix.sample" if mode == "sample" else "rubrics_qmatrix"
    jsonl_path = DATA_DIR / f"{stem}.jsonl"
    json_path = DATA_DIR / f"{stem}.json"
    write_jsonl(jsonl_path, out_records)
    write_json(json_path, out_records)

    log_dir_for(mode)  # ensure LOG_DIR exists
    with open(LOG_DIR / f"system_prompt_{PROMPT_VERSION}.txt", "w", encoding="utf-8") as f:
        f.write(SYSTEM_PROMPT + "\n")

    labeled = [r for r in out_records if r.get("q_mapping") is not None]
    per_skill = {s: sum(r["q_mapping"][s] for r in labeled) for s in SKILLS}
    all_zero = sum(1 for r in labeled if all(r["q_mapping"][s] == 0 for s in SKILLS))
    primary_counts = collections.Counter(r.get("primary_skill") for r in labeled)
    primary_dist = {str(k): v for k, v in primary_counts.items()}

    manifest = {
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "mode": mode,
        "strict_schema": strict_schema,
        "timestamp_utc": now_iso(),
        "total": len(out_records),
        "labeled": len(labeled),
        "failed": len(failures),
        "per_skill_positive": per_skill,
        "primary_skill_distribution": primary_dist,
        "all_zero_rows": all_zero,
        "failures": failures,
    }
    with open(log_dir_for(mode) / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"\nWrote {len(out_records)} records -> {jsonl_path} and {json_path}")
    print(f"Labeled {len(labeled)}, failed {len(failures)}.")
    print("Per-skill positive counts (of labeled):")
    for s in SKILLS:
        rate = (per_skill[s] / len(labeled) * 100) if labeled else 0.0
        print(f"  {s:11s} {per_skill[s]:5d}  ({rate:4.1f}%)")
    print(f"All-zero rows: {all_zero}")
    print("Primary-skill distribution (of labeled):")
    for k in list(SKILLS) + [None]:
        print(f"  {str(k):11s} {primary_counts.get(k, 0):5d}")
    print(f"Logs + manifest: {log_dir_for(mode)}")

    if mode == "sample":
        print("\n--- example labels ---")
        for r in labeled[:3]:
            print(f"[{r['criterion_id']}] {r['criterion']}")
            print(f"  q_mapping: {r['q_mapping']}  primary_skill: {r['primary_skill']}")
            print(f"  q_rationale: {r['q_rationale']}")
            print()


# ---------------------------------------------------------------------------
# Retry: re-label only the rows that previously failed
# ---------------------------------------------------------------------------


def retry_failed(client, args, criteria: list[dict], scenarios: dict[str, dict]) -> None:
    """
    Re-label only the criteria that failed in the existing full output and merge the fresh
    results back in, leaving successful rows untouched.

    Targets are the rows with ``q_mapping is None`` in ``rubrics_qmatrix.jsonl`` (or the
    explicit ``--ids`` list). Each retried criterion is re-labeled from its *original* record
    in :data:`RUBRICS_PATH` (not the failed output row), its item log under
    ``qmatrix_logs/full/`` is overwritten, and the merged full record set is rewritten to the
    output files and manifest via :func:`finish`.

    :raises SystemExit: if the full output is missing or an ``--ids`` value is unknown.
    """
    mode = "full"
    jsonl_path = DATA_DIR / "rubrics_qmatrix.jsonl"
    if not jsonl_path.exists():
        raise SystemExit(f"{jsonl_path} not found -- run --full first.")

    existing = read_jsonl(jsonl_path)
    crit_by_id = index_by_id(criteria)

    if args.ids:
        target_ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    else:
        target_ids = [r["criterion_id"] for r in existing if r.get("q_mapping") is None]

    unknown = [cid for cid in target_ids if cid not in crit_by_id]
    if unknown:
        raise SystemExit(f"unknown criterion_ids (not in {RUBRICS_PATH}): {unknown}")
    if not target_ids:
        print("No failed rows to retry -- nothing to do.")
        return

    print(f"Retrying {len(target_ids)} criteria: {', '.join(target_ids)}")
    items = [crit_by_id[cid] for cid in target_ids]

    relabeled, failures = run_items(
        client, args.model, mode, args.strict_schema, args.concurrency, items, scenarios
    )

    # Merge fresh results into the full output, preserving the original row order.
    fresh_by_id = index_by_id(relabeled)
    merged = [fresh_by_id.get(r["criterion_id"], r) for r in existing]

    # Rebuild the failure list for the merged set, keeping precise per-row error messages.
    fail_by_id = {f["criterion_id"]: f for f in failures}
    all_failures = [
        fail_by_id.get(
            r["criterion_id"],
            {"criterion_id": r["criterion_id"], "error": "pre-existing failure (not retried)"},
        )
        for r in merged
        if r.get("q_mapping") is None
    ]
    finish(mode, args.model, args.strict_schema, merged, all_failures)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic Q-matrix labels for the reformatted TutorBench "
                    "rubrics, routed through the TrueFoundry AI Gateway."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sample", type=int, metavar="N",
                       help="Label N evenly-spaced criteria (prompt validation).")
    group.add_argument("--full", action="store_true",
                       help="Label all criteria via a bounded thread pool.")
    group.add_argument("--retry-failed", action="store_true",
                       help="Re-label only the rows that failed in the existing full output "
                            "(q_mapping is null) and merge results back in.")
    parser.add_argument("--ids", default=None,
                        help="With --retry-failed: comma-separated criterion_ids to re-label "
                             "instead of auto-detecting failed rows.")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="TrueFoundry catalog slug (default: %(default)s).")
    parser.add_argument("--base-url", default=None,
                        help="Gateway base URL (else ANTHROPIC_BASE_URL env). SDK appends "
                             "/v1/messages, so use the root, e.g. "
                             "https://tfy.promptlens.trilogy.com.")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                        help="Max concurrent requests (default: %(default)s).")
    parser.add_argument("--strict-schema", action="store_true",
                        help="Send output_config json_schema to enforce the response shape "
                             "(only on providers/gateways that support it).")
    args = parser.parse_args()

    import os

    import anthropic

    criteria = read_jsonl(RUBRICS_PATH)
    scenarios = index_scenarios(read_jsonl(SCENARIOS_PATH))

    # Explicit per-request timeout (not just the SDK default) so a stalled gateway call fails
    # fast and is retried, instead of a worker thread blocking indefinitely on a held-open
    # connection and deadlocking the whole pool.
    client_kwargs = {"max_retries": 5, "timeout": 120.0}
    if args.base_url:
        client_kwargs["base_url"] = args.base_url  # else resolved from ANTHROPIC_BASE_URL env.

    # The TrueFoundry gateway authenticates with `Authorization: Bearer <key>`. Force Bearer
    # auth: use ANTHROPIC_AUTH_TOKEN and drop any ANTHROPIC_API_KEY so the SDK does not send
    # `x-api-key` (which takes precedence and the gateway may not accept).
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if not auth_token:
        parser.error("set ANTHROPIC_AUTH_TOKEN to your TrueFoundry user key")
    os.environ.pop("ANTHROPIC_API_KEY", None)
    client_kwargs["auth_token"] = auth_token
    client_kwargs["api_key"] = None

    client = anthropic.Anthropic(**client_kwargs)

    if args.retry_failed:
        retry_failed(client, args, criteria, scenarios)
        return

    if args.sample is not None:
        items = stride_sample(criteria, args.sample)
        mode = "sample"
    else:
        items = criteria
        mode = "full"

    out_records, failures = run_items(
        client, args.model, mode, args.strict_schema, args.concurrency, items, scenarios
    )
    finish(mode, args.model, args.strict_schema, out_records, failures)


if __name__ == "__main__":
    main()
