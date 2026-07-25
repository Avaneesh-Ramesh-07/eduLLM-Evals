"""Prepare human grading packets for the TutorBench judge-validation sample.

This script selects a fixed 10-scenario TutorBench sample, runs three tutor
models through the TrueFoundry OpenAI-compatible endpoint, and writes six
human-grading packets with five tutor responses each.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tutor_cat.schemas import Scenario  # noqa: E402
from tutor_cat.tutors import build_tutor  # noqa: E402


SELECTED_SCENARIO_IDS = [
    "tb_0001",
    "tb_0003",
    "tb_0012",
    "tb_0335",
    "tb_0336",
    "tb_0340",
    "tb_0355",
    "tb_0497",
    "tb_0500",
    "tb_0507",
]

EXPECTED_USE_CASE_COUNTS = {
    "adaptive_explanation": 3,
    "feedback": 4,
    "hint_generation": 3,
}

EXPECTED_SUBJECTS = {
    "biology",
    "calculus",
    "chemistry",
    "computer_science",
    "physics",
    "statistics",
}


@dataclass(frozen=True)
class TutorModel:
    alias: str
    model_slug: str
    anonymous_tutor: str
    max_tokens: int | None = None


TUTOR_MODELS = [
    TutorModel("gpt-5.5", "openai-group/gpt-5.5", "Tutor A"),
    TutorModel("opus-4.8", "claude-group/claude-opus-4-8", "Tutor B", max_tokens=4096),
    TutorModel("gemini-3.5-flash", "gemini-group/gemini-3.5-flash", "Tutor C"),
]


PACKET_ASSIGNMENTS = {
    "grader_01": [
        ("tb_0001", "Tutor A"),
        ("tb_0003", "Tutor B"),
        ("tb_0336", "Tutor C"),
        ("tb_0340", "Tutor A"),
        ("tb_0497", "Tutor B"),
    ],
    "grader_02": [
        ("tb_0001", "Tutor B"),
        ("tb_0012", "Tutor C"),
        ("tb_0336", "Tutor A"),
        ("tb_0355", "Tutor B"),
        ("tb_0500", "Tutor C"),
    ],
    "grader_03": [
        ("tb_0001", "Tutor C"),
        ("tb_0003", "Tutor A"),
        ("tb_0340", "Tutor B"),
        ("tb_0335", "Tutor C"),
        ("tb_0507", "Tutor A"),
    ],
    "grader_04": [
        ("tb_0003", "Tutor C"),
        ("tb_0336", "Tutor B"),
        ("tb_0355", "Tutor A"),
        ("tb_0497", "Tutor C"),
        ("tb_0500", "Tutor A"),
    ],
    "grader_05": [
        ("tb_0012", "Tutor A"),
        ("tb_0340", "Tutor C"),
        ("tb_0335", "Tutor B"),
        ("tb_0497", "Tutor A"),
        ("tb_0507", "Tutor B"),
    ],
    "grader_06": [
        ("tb_0012", "Tutor B"),
        ("tb_0355", "Tutor C"),
        ("tb_0335", "Tutor A"),
        ("tb_0500", "Tutor B"),
        ("tb_0507", "Tutor C"),
    ],
}


@dataclass
class ResponseUnit:
    response_id: str
    scenario_id: str
    tutor_alias: str
    anonymous_tutor: str
    model_slug: str
    response: str


def load_jsonl(path: str | Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {e}") from e
    return rows


def write_jsonl(path: str | Path, rows: list[dict]) -> None:
    with Path(path).open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_config(path: str | Path) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_selection(
    scenario_rows: list[dict],
    rubric_rows: list[dict],
    selected_ids: list[str] = SELECTED_SCENARIO_IDS,
) -> None:
    scenarios = {s["scenario_id"]: s for s in scenario_rows}
    rubrics = {r["criterion_id"]: r for r in rubric_rows}

    missing = [sid for sid in selected_ids if sid not in scenarios]
    if missing:
        raise ValueError(f"selected scenarios missing from scenario file: {missing}")

    use_case_counts: dict[str, int] = {}
    subjects: set[str] = set()
    seen = set()
    for sid in selected_ids:
        if sid in seen:
            raise ValueError(f"duplicate selected scenario_id: {sid}")
        seen.add(sid)

        scenario = scenarios[sid]
        if scenario.get("modality") != "text":
            raise ValueError(f"{sid}: expected modality=text, got {scenario.get('modality')!r}")

        use_case = scenario.get("use_case", "")
        use_case_counts[use_case] = use_case_counts.get(use_case, 0) + 1
        subjects.add(scenario.get("subject", ""))

        criterion_ids = scenario.get("criterion_ids") or []
        if not criterion_ids:
            raise ValueError(f"{sid}: no criterion_ids")
        for cid in criterion_ids:
            rubric = rubrics.get(cid)
            if rubric is None:
                raise ValueError(f"{sid}: linked rubric {cid} is missing")
            if rubric.get("scenario_id") != sid:
                raise ValueError(f"{sid}: linked rubric {cid} points at {rubric.get('scenario_id')}")

    if use_case_counts != EXPECTED_USE_CASE_COUNTS:
        raise ValueError(
            f"selected scenarios must match {EXPECTED_USE_CASE_COUNTS}, got {use_case_counts}"
        )
    if not EXPECTED_SUBJECTS <= subjects:
        raise ValueError(
            f"selected scenarios must cover {sorted(EXPECTED_SUBJECTS)}, got {sorted(subjects)}"
        )


def selected_scenarios(scenario_rows: list[dict]) -> list[dict]:
    by_id = {s["scenario_id"]: s for s in scenario_rows}
    return [by_id[sid] for sid in SELECTED_SCENARIO_IDS]


def selected_rubrics(scenario_rows: list[dict], rubric_rows: list[dict]) -> list[dict]:
    selected_cids = []
    scenario_by_id = {s["scenario_id"]: s for s in scenario_rows}
    for sid in SELECTED_SCENARIO_IDS:
        selected_cids.extend(scenario_by_id[sid]["criterion_ids"])
    rubric_by_id = {r["criterion_id"]: r for r in rubric_rows}
    return [rubric_by_id[cid] for cid in selected_cids]


def _response_id(scenario_id: str, tutor_alias: str) -> str:
    safe_alias = tutor_alias.replace(".", "_").replace("-", "_")
    return f"{scenario_id}__{safe_alias}"


def _text(value: object) -> str:
    return "" if value is None else str(value)


def collect_tutor_responses(
    scenarios_by_id: dict[str, Scenario],
    tutors: list[TutorModel],
    responder: Callable[[TutorModel, Scenario], str],
) -> list[ResponseUnit]:
    units = []
    for sid in SELECTED_SCENARIO_IDS:
        scenario = scenarios_by_id[sid]
        for tutor in tutors:
            units.append(
                ResponseUnit(
                    response_id=_response_id(sid, tutor.alias),
                    scenario_id=sid,
                    tutor_alias=tutor.alias,
                    anonymous_tutor=tutor.anonymous_tutor,
                    model_slug=tutor.model_slug,
                    response=responder(tutor, scenario),
                )
            )
    return units


def make_truefoundry_responder(cache_dir: str | Path) -> Callable[[TutorModel, Scenario], str]:
    base_url = os.environ.get("MODEL_API_BASE")
    api_key = os.environ.get("MODEL_API_KEY")
    if not base_url:
        raise RuntimeError("MODEL_API_BASE must be set to the TrueFoundry OpenAI-compatible base URL")
    if not api_key:
        raise RuntimeError("MODEL_API_KEY must be set to the TrueFoundry API key")

    clients = {}
    for tutor in TUTOR_MODELS:
        spec = {
            "name": tutor.alias,
            "provider": "openai",
            "model": tutor.model_slug,
            "base_url": base_url,
            "api_key_env": "MODEL_API_KEY",
            "temperature": 0.0,
        }
        if tutor.max_tokens is not None:
            spec["max_tokens"] = tutor.max_tokens
        clients[tutor.alias] = build_tutor(spec, cache_dir)

    def respond(tutor: TutorModel, scenario: Scenario) -> str:
        return clients[tutor.alias].respond(scenario)

    return respond


def _format_context(context: list[dict[str, str]]) -> str:
    if not context:
        return "_No prior conversation context._"
    parts = []
    for idx, turn in enumerate(context, 1):
        role = _text(turn.get("role", "?"))
        content = _text(turn.get("content", ""))
        parts.append(f"**Turn {idx} ({role})**\n\n{content}")
    return "\n\n".join(parts)


def _rubrics_for_scenario(rubrics_by_scenario: dict[str, list[dict]], scenario_id: str) -> list[dict]:
    return sorted(rubrics_by_scenario[scenario_id], key=lambda r: r["criterion_id"])


def _write_packet(
    packet_name: str,
    packet_units: list[ResponseUnit],
    scenarios_by_id: dict[str, dict],
    rubrics_by_scenario: dict[str, list[dict]],
    packet_dir: Path,
) -> None:
    md_path = packet_dir / f"{packet_name}.md"
    csv_path = packet_dir / f"{packet_name}.csv"

    md_lines = [
        f"# Human Grading Packet {packet_name}",
        "",
        "For each criterion, enter `P` if the tutor response satisfies it and `F` if it does not.",
        "Use the companion CSV as the official grading sheet. Leave brief notes only when useful.",
        "",
    ]

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "assignment_id",
                "scenario_id",
                "anonymous_tutor",
                "use_case",
                "subject",
                "criterion_id",
                "primary_skill",
                "criticality",
                "criterion",
                "grade",
                "notes",
            ],
        )
        writer.writeheader()

        for item_idx, unit in enumerate(packet_units, 1):
            scenario = scenarios_by_id[unit.scenario_id]
            assignment_id = f"{packet_name}_item_{item_idx:02d}"
            rubrics = _rubrics_for_scenario(rubrics_by_scenario, unit.scenario_id)

            md_lines.extend(
                [
                    f"## {assignment_id}",
                    "",
                    f"- Scenario ID: `{unit.scenario_id}`",
                    f"- Use case: `{_text(scenario.get('use_case', ''))}`",
                    f"- Subject: `{_text(scenario.get('subject', ''))}`",
                    f"- Tutor: `{unit.anonymous_tutor}`",
                    "",
                    "### Scenario Prompt",
                    "",
                    _text(scenario.get("prompt", "")),
                    "",
                    "### Conversation Context",
                    "",
                    _format_context(scenario.get("conversation_context") or []),
                    "",
                    "### Reference Solution",
                    "",
                    _text(scenario.get("reference_solution", "")),
                    "",
                    "### Tutor Response",
                    "",
                    _text(unit.response),
                    "",
                    "### Criteria To Grade",
                    "",
                ]
            )

            for rubric in rubrics:
                md_lines.extend(
                    [
                        f"#### {rubric['criterion_id']}",
                        "",
                        f"- Criterion: {_text(rubric.get('criterion', ''))}",
                        f"- Primary skill: `{_text(rubric.get('primary_skill', ''))}`",
                        f"- Criticality: `{_text(rubric.get('criticality', ''))}`",
                        "- Grade (P/F): ____",
                        "- Notes: ____",
                        "",
                    ]
                )
                writer.writerow(
                    {
                        "assignment_id": assignment_id,
                        "scenario_id": unit.scenario_id,
                        "anonymous_tutor": unit.anonymous_tutor,
                        "use_case": scenario.get("use_case", ""),
                        "subject": scenario.get("subject", ""),
                        "criterion_id": rubric["criterion_id"],
                        "primary_skill": rubric.get("primary_skill", ""),
                        "criticality": rubric.get("criticality", ""),
                        "criterion": rubric.get("criterion", ""),
                        "grade": "",
                        "notes": "",
                    }
                )

    md_path.write_text("\n".join(md_lines), encoding="utf-8")


def write_outputs(
    out_dir: str | Path,
    scenario_rows: list[dict],
    rubric_rows: list[dict],
    response_units: list[ResponseUnit],
    data_paths: dict[str, str],
) -> None:
    out = Path(out_dir)
    packet_dir = out / "grader_packets"
    packet_dir.mkdir(parents=True, exist_ok=True)

    scenarios = selected_scenarios(scenario_rows)
    rubrics = selected_rubrics(scenario_rows, rubric_rows)
    scenarios_by_id = {s["scenario_id"]: s for s in scenarios}
    rubrics_by_scenario: dict[str, list[dict]] = {}
    for rubric in rubrics:
        rubrics_by_scenario.setdefault(rubric["scenario_id"], []).append(rubric)

    write_jsonl(out / "sample_scenarios.jsonl", scenarios)
    write_jsonl(out / "sample_rubrics.jsonl", rubrics)
    write_jsonl(out / "tutor_responses.jsonl", [asdict(unit) for unit in response_units])

    units_by_assignment_key = {
        (unit.scenario_id, unit.anonymous_tutor): unit for unit in response_units
    }
    all_assigned = []
    for packet_name, assignment_keys in PACKET_ASSIGNMENTS.items():
        packet_units = []
        for key in assignment_keys:
            if key not in units_by_assignment_key:
                raise ValueError(f"{packet_name}: missing response unit for {key}")
            packet_units.append(units_by_assignment_key[key])
            all_assigned.append(key)
        _write_packet(packet_name, packet_units, scenarios_by_id, rubrics_by_scenario, packet_dir)

    if len(all_assigned) != len(set(all_assigned)):
        raise ValueError("packet assignments contain duplicate response units")
    if len(all_assigned) != len(response_units):
        raise ValueError(
            f"packet assignments cover {len(all_assigned)} units, expected {len(response_units)}"
        )

    manifest = {
        "workflow": "human_judge_validation_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "selected_scenario_ids": SELECTED_SCENARIO_IDS,
        "expected_use_case_counts": EXPECTED_USE_CASE_COUNTS,
        "expected_subjects": sorted(EXPECTED_SUBJECTS),
        "data": data_paths,
        "credential_env": {
            "base_url": "MODEL_API_BASE",
            "api_key": "MODEL_API_KEY",
        },
        "tutor_mapping": [
            {
                "alias": tutor.alias,
                "anonymous_tutor": tutor.anonymous_tutor,
                "model_slug": tutor.model_slug,
            }
            for tutor in TUTOR_MODELS
        ],
        "packet_assignments": {
            packet: [
                {"scenario_id": sid, "anonymous_tutor": anon}
                for sid, anon in assignments
            ]
            for packet, assignments in PACKET_ASSIGNMENTS.items()
        },
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_env() -> None:
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        pass
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--out", default="runs/judge_validation_v1")
    args = parser.parse_args(argv)

    _load_env()
    cfg = load_config(args.config)
    scenarios_path = cfg.get("data", {}).get("scenarios", "data/scenarios.jsonl")
    rubrics_path = cfg.get("data", {}).get("rubrics", "data/rubrics_qmatrix_final.jsonl")
    cache_dir = cfg.get("cache_dir", "cache")

    scenario_rows = load_jsonl(scenarios_path)
    rubric_rows = load_jsonl(rubrics_path)
    validate_selection(scenario_rows, rubric_rows)

    scenario_objects = {
        row["scenario_id"]: Scenario.from_json(row)
        for row in selected_scenarios(scenario_rows)
    }
    responder = make_truefoundry_responder(cache_dir)
    response_units = collect_tutor_responses(scenario_objects, TUTOR_MODELS, responder)
    write_outputs(
        args.out,
        scenario_rows,
        rubric_rows,
        response_units,
        {"scenarios": scenarios_path, "rubrics": rubrics_path},
    )
    print(f"wrote human judge-validation packets to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
