"""LLM Judge: one grading call per criterion (PRD "Judge Evaluation").

The judge receives the scenario prompt, the conversation context (if any), the
tutor's response, and ONE criterion rendered as a 1-5 score rubric. Prometheus 2
is fine-tuned for this absolute-grading format and replies with
"Feedback: ... [RESULT] <1-5>"; parse_verdict maps that score to pass/fail at
result_pass_threshold (default 4), so the pass/fail semantics are unchanged.
It does NOT receive expected_evidence or the reference solution.

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

PROMPT_VERSION = "judge-v2"  # Prometheus 2 native absolute grading (1-5 [RESULT])

# Graded judges (e.g. Prometheus 2) reply with a 1-5 "[RESULT] <n>" score even
# when asked for pass/fail; map score >= this threshold to a pass. Overridable
# via config (judge.result_pass_threshold) and recorded in every run manifest.
RESULT_PASS_THRESHOLD_DEFAULT = 4

# Prometheus 2's own absolute-grading format. Given an instruction, a response,
# and a 1-5 score rubric, it reliably emits "Feedback: ... [RESULT] <1-5>" — the
# format it was fine-tuned on. The earlier JSON prompt fought that training and
# produced mostly unparseable prose; this one works with the model, not against.
_SYSTEM_PROMPT = (
    "You are a fair judge assistant tasked with providing clear, objective "
    "feedback based on specific criteria, ensuring each assessment reflects the "
    "absolute standards set for performance."
)

# One binary criterion rendered as a 1-5 rubric. With the default threshold of 4,
# scores 4-5 (satisfies with at most minor gaps) map to pass; 1-3 map to fail.
_SCORE_RUBRIC = """[Does the tutor's response satisfy this criterion: {criterion}]
Score 1: The response does not satisfy the criterion at all.
Score 2: The response largely fails to satisfy the criterion.
Score 3: The response only partially satisfies the criterion.
Score 4: The response satisfies the criterion with only minor gaps.
Score 5: The response fully satisfies the criterion."""

_USER_TEMPLATE = """###Task Description:
An instruction (the tutoring scenario), a response to evaluate, and a score rubric representing one evaluation criterion are given.
1. Write detailed feedback that assesses the response strictly against the score rubric, not in general.
2. After the feedback, write a score that is an integer between 1 and 5, referring to the rubric.
3. The output format must be exactly: "Feedback: (feedback) [RESULT] (an integer between 1 and 5)"
4. Do not add any other opening, closing, or explanation.

###The instruction to evaluate:
{instruction}

###Response to evaluate:
{response}

###Score Rubrics:
{rubric}

###Feedback:"""


def build_messages(scenario: Scenario, rubric: Rubric, response: str) -> list[dict[str, str]]:
    instruction = scenario.prompt
    if scenario.conversation_context:
        turns = "\n".join(
            f"[{t.get('role', '?')}] {t.get('content', '')}" for t in scenario.conversation_context
        )
        instruction = f"{scenario.prompt}\n\nPrior conversation context:\n{turns}"
    user = _USER_TEMPLATE.format(
        instruction=instruction,
        response=response,
        rubric=_SCORE_RUBRIC.format(criterion=rubric.criterion),
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def parse_verdict(
    text: str, result_pass_threshold: int = RESULT_PASS_THRESHOLD_DEFAULT
) -> JudgeVerdict:
    """Parse the judge's output. Strict JSON first, then a Prometheus-style
    "[RESULT] <1-5>" score (mapped to pass/fail at ``result_pass_threshold``),
    then a keyword fallback; anything else is unscorable → fail (per the PRD)."""
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

    # We keep the pass/fail prompt, but Prometheus 2 (and similar graded judges)
    # often revert to their native "<feedback> [RESULT] <1-5>" format. Recover
    # those by mapping the score to pass/fail at a configurable threshold.
    result_match = re.search(r"\[RESULT\]\s*([1-5])", text, flags=re.IGNORECASE)
    if result_match:
        score = int(result_match.group(1))
        verdict = "pass" if score >= result_pass_threshold else "fail"
        return JudgeVerdict(
            verdict=verdict,
            rationale=text.strip()[:500],
            raw_output=text,
        )

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
        result_pass_threshold: int = RESULT_PASS_THRESHOLD_DEFAULT,
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = os.environ.get(api_key_env, "")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.seed = seed
        self.result_pass_threshold = result_pass_threshold
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
                return parse_verdict(text, self.result_pass_threshold)
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                last_error = e
                time.sleep(2.0 * (attempt + 1))
        raise RuntimeError(
            f"judge call failed after {self._max_retries} attempts "
            f"({scenario.scenario_id}/{rubric.criterion_id}): {last_error!r}"
        )
