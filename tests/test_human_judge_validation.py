from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

from tutor_cat.schemas import Scenario


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "prepare_human_judge_validation.py"
SPEC = importlib.util.spec_from_file_location("prepare_human_judge_validation", SCRIPT_PATH)
hjv = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = hjv
SPEC.loader.exec_module(hjv)


def _load_inputs():
    scenarios = hjv.load_jsonl(ROOT / "data" / "scenarios.jsonl")
    rubrics = hjv.load_jsonl(ROOT / "data" / "rubrics_qmatrix_final.jsonl")
    return scenarios, rubrics


def _fake_response_units(scenario_rows):
    scenario_objects = {
        row["scenario_id"]: Scenario.from_json(row)
        for row in hjv.selected_scenarios(scenario_rows)
    }

    def responder(tutor, scenario):
        return f"fake response from {tutor.anonymous_tutor} for {scenario.scenario_id}"

    return hjv.collect_tutor_responses(scenario_objects, hjv.TUTOR_MODELS, responder)


def test_selected_scenarios_match_split_and_subject_coverage():
    scenarios, rubrics = _load_inputs()

    hjv.validate_selection(scenarios, rubrics)

    selected = hjv.selected_scenarios(scenarios)
    use_case_counts = {}
    subjects = set()
    for scenario in selected:
        use_case_counts[scenario["use_case"]] = use_case_counts.get(scenario["use_case"], 0) + 1
        subjects.add(scenario["subject"])
    assert use_case_counts == {
        "adaptive_explanation": 3,
        "feedback": 4,
        "hint_generation": 3,
    }
    assert hjv.EXPECTED_SUBJECTS <= subjects


def test_packet_assignments_cover_each_scenario_tutor_once():
    assigned = [
        (scenario_id, anonymous_tutor)
        for assignments in hjv.PACKET_ASSIGNMENTS.values()
        for scenario_id, anonymous_tutor in assignments
    ]
    expected = [
        (scenario_id, tutor.anonymous_tutor)
        for scenario_id in hjv.SELECTED_SCENARIO_IDS
        for tutor in hjv.TUTOR_MODELS
    ]

    assert len(assigned) == 30
    assert sorted(assigned) == sorted(expected)
    assert len(set(assigned)) == 30
    assert all(len(assignments) == 5 for assignments in hjv.PACKET_ASSIGNMENTS.values())


def test_write_outputs_creates_grader_packets_and_anonymizes(tmp_path):
    scenarios, rubrics = _load_inputs()
    response_units = _fake_response_units(scenarios)

    hjv.write_outputs(
        tmp_path,
        scenarios,
        rubrics,
        response_units,
        {"scenarios": "data/scenarios.jsonl", "rubrics": "data/rubrics_qmatrix_final.jsonl"},
    )

    packet_dir = tmp_path / "grader_packets"
    assert len(list(packet_dir.glob("grader_*.md"))) == 6
    assert len(list(packet_dir.glob("grader_*.csv"))) == 6

    all_rows = []
    for csv_path in packet_dir.glob("grader_*.csv"):
        with csv_path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows
        assert set(rows[0]) == {
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
        }
        assert all(row["grade"] == "" for row in rows)
        all_rows.extend(rows)

    assigned_units = {
        (row["assignment_id"], row["scenario_id"], row["anonymous_tutor"])
        for row in all_rows
    }
    assert len(assigned_units) == 30

    markdown_text = "\n".join(path.read_text(encoding="utf-8") for path in packet_dir.glob("*.md"))
    assert "Tutor A" in markdown_text
    assert "Tutor B" in markdown_text
    assert "Tutor C" in markdown_text
    assert "openai-group/gpt-5.5" not in markdown_text
    assert "claude-group/claude-opus-4-8" not in markdown_text
    assert "gemini-group/gemini-3.5-flash" not in markdown_text

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    slugs = {entry["model_slug"] for entry in manifest["tutor_mapping"]}
    assert slugs == {
        "openai-group/gpt-5.5",
        "claude-group/claude-opus-4-8",
        "gemini-group/gemini-3.5-flash",
    }
    assert manifest["credential_env"] == {
        "base_url": "MODEL_API_BASE",
        "api_key": "MODEL_API_KEY",
    }
