"""TutorBench prompt construction: per-use_case system prompt + message building
with consecutive-user-turn coalescing.

Why coalescing: 333/662 scenarios (feedback + hint_generation) have a single
student context turn followed by the student's prompt — i.e. two consecutive
`user` turns. Strict chat templates (Gemma, Mistral, Llama-3) reject
non-alternating roles and raise, so the two user turns are merged into one with
a labeled separator. adaptive_explanation already alternates
(student -> tutor -> student) and is left untouched.

Pure module: uses only the scenario's .use_case / .conversation_context /
.prompt attributes (duck-typed), so message construction is testable without
torch/transformers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # import only for type hints; avoids a hard dep at runtime
    from ..schemas import Scenario

# Verbatim TutorBench system prompts (Appendix A.6, text-only variants), keyed by
# use_case. The model is TOLD the use case through this system prompt; the rubric
# and reference solution are judge-only and never appear here.
SYSTEM_PROMPTS: dict[str, str] = {
    "adaptive_explanation": (
        "You are an AI tutor helping a high school student understand a concept. "
        "Answer their question clearly and adjust your explanation based on what "
        "the student says they're confused about."
    ),
    "feedback": (
        "You are an AI tutor reviewing a student's answer to a question. Evaluate "
        "whether it is correct, identify any mistakes, and explain your reasoning "
        "clearly. Provide an assessment of the student incorrect solution in the "
        "first response."
    ),
    "hint_generation": (
        "You are an AI tutor helping a student who got stuck partway through a "
        "problem. Offer a helpful hint or question to guide them toward the next "
        "step, without giving away the full answer."
    ),
}

_DEFAULT_USE_CASE = "adaptive_explanation"

# Dataset context roles -> chat-template roles (same map as tutors._ROLE_MAP).
_ROLE_MAP = {
    "student": "user",
    "tutor": "assistant",
    "user": "user",
    "assistant": "assistant",
    "system": "system",
}

# When two user turns are merged, label the second (the scenario prompt) so the
# model can tell the problem statement from the student's own work.
_COALESCE_LABEL = {
    "feedback": "Student's solution",
    "hint_generation": "Student's work so far",
}

# Flat role labels for base (no chat template) models.
_BASE_ROLE_LABEL = {"user": "Student", "assistant": "Tutor", "system": "System"}


def system_prompt_for(use_case: str) -> str:
    return SYSTEM_PROMPTS.get(use_case, SYSTEM_PROMPTS[_DEFAULT_USE_CASE])


def _separator(use_case: str, role: str) -> str:
    """Separator inserted between two merged same-role turns."""
    label = _COALESCE_LABEL.get(use_case)
    if role == "user" and label:
        return f"\n\n---\n{label}:\n"
    return "\n\n"


def _coalesce(messages: list[dict[str, str]], use_case: str) -> list[dict[str, str]]:
    """Merge adjacent same-role turns (never system) so the sequence alternates."""
    out: list[dict[str, str]] = []
    for m in messages:
        if out and out[-1]["role"] == m["role"] and m["role"] != "system":
            sep = _separator(use_case, m["role"])
            out[-1] = {
                "role": m["role"],
                "content": out[-1]["content"] + sep + m["content"],
            }
        else:
            out.append(dict(m))
    return out


def build_chat_messages(scenario: "Scenario") -> list[dict[str, str]]:
    """[system, ...role-mapped context, user(prompt)], coalesced to alternate.

    feedback / hint_generation collapse to exactly [system, user]; adaptive_
    explanation stays [system, user, assistant, user].
    """
    use_case = getattr(scenario, "use_case", "") or _DEFAULT_USE_CASE
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt_for(use_case)}
    ]
    for turn in getattr(scenario, "conversation_context", None) or []:
        role = _ROLE_MAP.get(turn.get("role", "user"), "user")
        messages.append({"role": role, "content": turn.get("content", "")})
    messages.append({"role": "user", "content": scenario.prompt})
    return _coalesce(messages, use_case)


def render_base_prompt(scenario: "Scenario") -> str:
    """Flat role-labeled transcript for base models with no chat template. Ends
    on 'Tutor:' so the model continues the assistant turn."""
    lines: list[str] = []
    for m in build_chat_messages(scenario):
        label = _BASE_ROLE_LABEL.get(m["role"], m["role"].title())
        lines.append(f"{label}: {m['content']}")
    lines.append("Tutor:")
    return "\n\n".join(lines)
