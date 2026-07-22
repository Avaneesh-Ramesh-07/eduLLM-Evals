"""LLM Judge: one direct pass/fail call per criterion (PRD "Judge Evaluation").

The judge receives the scenario prompt, the conversation context (if any), the
tutor's response, and the criterion text. It does NOT receive expected_evidence
(not produced in this submission) or the reference solution.

Transport is an OpenAI-compatible chat-completions endpoint (works with vLLM
serving Prometheus 2 7B, or any compatible server); endpoint/model/key are
configurable. Determinism: temperature 0, fixed seed, fixed prompt version.

Unscorable outputs (unparseable or judge-declared) are recorded as FAIL (y = 0)
with unscorable_reason set, per the PRD.
"""

from __future__ import annotations

import json
import os
import re
import time

import httpx

from .schemas import JudgeVerdict, Rubric, Scenario

PROMPT_VERSION = "judge-v1"

_SYSTEM_PROMPT = (
    "You are a strict, consistent grader of AI tutoring responses. "
    "You will be given a tutoring scenario, the tutor's response, and exactly ONE "
    "grading criterion. Decide only whether the response satisfies that criterion. "
    "Respond with ONLY a JSON object, no other text:\n"
    '{"verdict": "pass" or "fail", '
    '"evidence": "<short quote from the tutor response supporting your verdict>", '
    '"rationale": "<one sentence explaining the verdict>"}\n'
    "If the criterion cannot be evaluated against this response, output "
    '{"verdict": "fail", "evidence": "", "rationale": "<why>", '
    '"unscorable_reason": "<why it could not be scored>"}.'
)

_USER_TEMPLATE = """## Scenario (what the student asked the tutor)
{prompt}
{context_block}
## Tutor response being graded
{response}

## Criterion (grade ONLY this)
{criterion}

Output the JSON verdict now."""


def build_messages(scenario: Scenario, rubric: Rubric, response: str) -> list[dict[str, str]]:
    if scenario.conversation_context:
        turns = "\n".join(
            f"[{t.get('role', '?')}] {t.get('content', '')}" for t in scenario.conversation_context
        )
        context_block = f"\n## Prior conversation context\n{turns}\n"
    else:
        context_block = ""
    user = _USER_TEMPLATE.format(
        prompt=scenario.prompt,
        context_block=context_block,
        response=response,
        criterion=rubric.criterion,
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def parse_verdict(text: str) -> JudgeVerdict:
    """Parse the judge's output. Strict JSON first, then a keyword fallback;
    anything else is unscorable → fail (per the PRD)."""
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            verdict = str(obj.get("verdict", "")).strip().lower()
            if verdict in ("pass", "fail"):
                return JudgeVerdict(
                    verdict=verdict,
                    evidence=str(obj.get("evidence", "")),
                    rationale=str(obj.get("rationale", "")),
                    unscorable_reason=obj.get("unscorable_reason"),
                    raw_output=text,
                )
        except json.JSONDecodeError:
            pass

    # Keyword fallback for models that ignore the JSON instruction.
    lowered = text.lower()
    for token, verdict in (("[result] pass", "pass"), ("[result] fail", "fail")):
        if token in lowered:
            return JudgeVerdict(verdict=verdict, rationale=text.strip()[:500], raw_output=text)
    stripped = lowered.strip()
    if stripped.startswith("pass"):
        return JudgeVerdict(verdict="pass", rationale=text.strip()[:500], raw_output=text)
    if stripped.startswith("fail"):
        return JudgeVerdict(verdict="fail", rationale=text.strip()[:500], raw_output=text)

    return JudgeVerdict(
        verdict="fail",
        rationale="Judge output could not be parsed into a verdict.",
        unscorable_reason="unparseable_judge_output",
        raw_output=text,
    )


class OpenAICompatibleJudge:
    """Judge client for any OpenAI-compatible /chat/completions endpoint (e.g. vLLM)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key_env: str = "JUDGE_API_KEY",
        temperature: float = 0.0,
        max_tokens: int = 512,
        seed: int = 42,
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = os.environ.get(api_key_env, "")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.seed = seed
        self.prompt_version = PROMPT_VERSION
        self._client = httpx.Client(timeout=timeout)
        self._max_retries = max_retries

    @property
    def name(self) -> str:
        return self.model

    def evaluate(self, scenario: Scenario, rubric: Rubric, response: str) -> JudgeVerdict:
        payload = {
            "model": self.model,
            "messages": build_messages(scenario, rubric, response),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "seed": self.seed,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                r = self._client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=headers
                )
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"] or ""
                return parse_verdict(text)
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                last_error = e
                time.sleep(2.0 * (attempt + 1))
        raise RuntimeError(
            f"judge call failed after {self._max_retries} attempts "
            f"({scenario.scenario_id}/{rubric.criterion_id}): {last_error!r}"
        )
