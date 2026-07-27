"""Offline contract tests for the transferable AWS judge bundle.

Nothing in this module starts a model, contacts S3, or writes inside the
handoff directory.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Iterator

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "aws_judge_handoff"
CASES_PATH = BUNDLE / "inputs" / "judge_cases.blinded.jsonl"
RUNNER_PATH = BUNDLE / "scripts" / "run_judge_validation.py"
LAUNCHER_PATH = BUNDLE / "run_judge_suite.sh"
STUDY_DESIGN_PATH = BUNDLE / "STUDY_DESIGN.json"
ZIP_PATH = ROOT / "aws_judge_handoff.zip"
CANONICAL_RUNNER_PATH = ROOT / "scripts" / "run_judge_validation.py"
STUDY_ID = "judge-validation-v3-evidence-gated"

EXPECTED_JUDGES = ("flow", "gemma", "prometheus", "qwen", "selene")
EXPECTED_WAVES = (
    {
        "wave": "canonical_r1",
        "prompt_variant": "canonical",
        "replicate_id": "r1",
    },
    {
        "wave": "canonical_r2",
        "prompt_variant": "canonical",
        "replicate_id": "r2",
    },
    {
        "wave": "canonical_r3",
        "prompt_variant": "canonical",
        "replicate_id": "r3",
    },
    {
        "wave": "whitespace_r1",
        "prompt_variant": "whitespace",
        "replicate_id": "r1",
    },
    {
        "wave": "header_synonyms_r1",
        "prompt_variant": "header_synonyms",
        "replicate_id": "r1",
    },
    {
        "wave": "instruction_politeness_r1",
        "prompt_variant": "instruction_politeness",
        "replicate_id": "r1",
    },
)
EXPECTED_HASHED_FILES = {
    "README.md",
    "STUDY_DESIGN.json",
    "inputs/judge_cases.blinded.jsonl",
    "requirements-aws.txt",
    "run_judge_suite.sh",
    "scripts/run_judge_validation.py",
}


def _load_bundled_runner():
    spec = importlib.util.spec_from_file_location("aws_handoff_runner", RUNNER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


runner = _load_bundled_runner()


def _load_canonical_runner():
    spec = importlib.util.spec_from_file_location(
        "canonical_judge_validation_runner", CANONICAL_RUNNER_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


canonical_runner = _load_canonical_runner()


def _load_cases() -> list[dict]:
    return runner.load_jsonl(CASES_PATH)


def _walk_keys(value: object, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}"
            yield nested_path, key_text
            yield from _walk_keys(nested, nested_path)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _walk_keys(nested, f"{path}[{index}]")


def _flag_value(command: list[str], flag: str) -> str:
    indexes = [index for index, token in enumerate(command) if token == flag]
    assert len(indexes) == 1, f"expected one {flag} in {command!r}"
    index = indexes[0]
    assert index + 1 < len(command), f"missing value after {flag}"
    return command[index + 1]


def test_bundle_has_exactly_261_valid_deeply_blinded_cases() -> None:
    cases = _load_cases()

    assert len(cases) == 261
    assert len({case["case_id"] for case in cases}) == 261
    runner.validate_judge_cases(cases)

    violations = [
        f"case {case_index + 1}: {path}"
        for case_index, case in enumerate(cases)
        for path, key in _walk_keys(case)
        if key in runner.FORBIDDEN_JUDGE_CASE_FIELDS
    ]
    assert violations == []


def test_bundled_runner_contains_only_five_judges_and_no_tutor_identity_map() -> None:
    assert tuple(sorted(runner.JUDGES)) == EXPECTED_JUDGES
    assert runner.TUTOR_MAP == {}
    assert runner.PROMPT_VERSION == "judge-validation-v3"
    assert runner.NORMALIZATION_VERSION == "judge-normalization-v3"
    assert runner.EVIDENCE_POLICY_VERSION == "criterion-evidence-gate-v1"


def test_bundled_prompts_match_canonical_runner_for_every_case_and_variant() -> None:
    assert runner.PROMPT_VERSION == canonical_runner.PROMPT_VERSION
    assert runner.NORMALIZATION_VERSION == canonical_runner.NORMALIZATION_VERSION
    assert runner.EVIDENCE_DECISION_POLICY == canonical_runner.EVIDENCE_DECISION_POLICY

    for case in _load_cases():
        for judge in EXPECTED_JUDGES:
            for variant in runner.PROMPT_VARIANTS:
                bundled = runner.build_variant_messages(
                    case, runner.JUDGES[judge], variant
                )
                canonical = canonical_runner.build_variant_messages(
                    case, canonical_runner.JUDGES[judge], variant
                )
                assert bundled == canonical, (case["case_id"], judge, variant)
                assert runner.stable_hash(bundled) == canonical_runner.stable_hash(
                    canonical
                )


def test_all_real_cases_receive_evidence_gate_without_authoring_hints() -> None:
    cases = _load_cases()
    assert all(case.get("expected_evidence") == [] for case in cases)

    for judge in EXPECTED_JUDGES:
        prompt = "\n".join(
            message["content"]
            for message in runner.build_messages(cases[0], runner.JUDGES[judge])
        )
        assert runner.EVIDENCE_DECISION_POLICY in prompt
        assert "Evidence must come from the candidate response itself" in prompt
        assert "negative or prohibition requirement" in prompt


@pytest.mark.parametrize(
    ("abbreviated_flag", "value"),
    [
        ("--temp", "0.7"),
        ("--model-i", "unapproved/model"),
        ("--prometheus-pass-th", "3"),
    ],
)
def test_bundled_runner_rejects_abbreviated_frozen_options(
    abbreviated_flag: str, value: str
) -> None:
    with pytest.raises(SystemExit):
        runner.build_parser().parse_args(
            [
                "run",
                "--cases",
                str(CASES_PATH),
                "--judge",
                "selene",
                "--output",
                "out.jsonl",
                abbreviated_flag,
                value,
            ]
        )


def test_study_design_freezes_the_six_expected_waves() -> None:
    design = json.loads(STUDY_DESIGN_PATH.read_text(encoding="utf-8"))
    waves = design["waves_per_judge"]

    assert design["case_count"] == 261
    assert design["study_version"] == (
        "judge-reliability-v2-evidence-gated-development"
    )
    assert design["study_id"] == STUDY_ID
    assert design["prompt_version"] == runner.PROMPT_VERSION
    assert design["normalization_version"] == runner.NORMALIZATION_VERSION
    assert design["evidence_policy_version"] == runner.EVIDENCE_POLICY_VERSION
    assert design["data_role"] == "development_recalibration"
    assert "unseen" in design["final_acceptance_requires"].lower()
    assert waves == list(EXPECTED_WAVES)
    assert len({wave["wave"] for wave in waves}) == 6
    assert {wave["prompt_variant"] for wave in waves} <= set(
        runner.PROMPT_VARIANTS
    )
    assert sum(wave["prompt_variant"] == "canonical" for wave in waves) == 3


def test_reliability_launcher_has_valid_bash_syntax() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(LAUNCHER_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("judge", EXPECTED_JUDGES)
def test_reliability_launcher_dry_run_emits_six_aligned_commands(
    tmp_path: Path, judge: str
) -> None:
    # Run an exact copy so the launcher's outputs/ mkdir remains outside the
    # handoff directory. The fake `python` records argv and performs no work.
    temporary_bundle = tmp_path / "aws_judge_handoff"
    temporary_bundle.mkdir()
    launcher = temporary_bundle / LAUNCHER_PATH.name
    shutil.copy2(LAUNCHER_PATH, launcher)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    command_log = tmp_path / "python-commands.tsv"
    fake_python = fake_bin / "python"
    fake_python.write_text(
        """#!/bin/sh
{
  for argument in "$@"; do
    printf '%s\\t' "$argument"
  done
  printf '\\n'
} >> "$FAKE_PYTHON_LOG"
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    environment = os.environ.copy()
    environment["PATH"] = str(fake_bin) + os.pathsep + environment.get("PATH", "")
    environment["FAKE_PYTHON_LOG"] = str(command_log)
    s3_root = "s3://validation-bucket/studies/"
    completed = subprocess.run(
        ["bash", str(launcher), judge, s3_root, "--limit", "3"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    commands = [
        line.removesuffix("\t").split("\t")
        for line in command_log.read_text(encoding="utf-8").splitlines()
    ]
    assert len(commands) == 6

    for command, wave in zip(commands, EXPECTED_WAVES):
        wave_name = wave["wave"]
        assert command[:2] == ["scripts/run_judge_validation.py", "run"]
        assert _flag_value(command, "--cases") == "inputs/judge_cases.blinded.jsonl"
        assert _flag_value(command, "--judge") == judge
        assert _flag_value(command, "--output") == (
            f"outputs/{STUDY_ID}/{judge}/{wave_name}.jsonl"
        )
        assert _flag_value(command, "--backend") == "vllm"
        assert _flag_value(command, "--prompt-variant") == wave["prompt_variant"]
        assert _flag_value(command, "--replicate-id") == wave["replicate_id"]
        assert _flag_value(command, "--s3-output-prefix") == (
            f"s3://validation-bucket/studies/{STUDY_ID}/blinded/"
            f"{judge}/{wave_name}"
        )
        assert command.count("--resume") == 1
        assert command.count("--require-s3-upload") == 1
        assert command[-2:] == ["--limit", "3"]

    assert completed.stdout.count(f"Starting {judge}/") == 6
    assert f"Completed all six waves for {judge}." in completed.stdout
    assert f"S3 study root: s3://validation-bucket/studies/{STUDY_ID}/blinded" in (
        completed.stdout
    )


def test_sha256sums_validates_once_final_inventory_is_regenerated() -> None:
    records: dict[str, str] = {}
    for line_number, line in enumerate(
        (BUNDLE / "SHA256SUMS").read_text(encoding="utf-8").splitlines(), 1
    ):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        assert match, f"SHA256SUMS:{line_number}: malformed record"
        digest, relative_path = match.groups()
        assert relative_path not in records
        records[relative_path] = digest

    assert set(records) == EXPECTED_HASHED_FILES

    for relative_path, expected_digest in records.items():
        payload = (BUNDLE / relative_path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_digest, relative_path


def test_zip_exactly_matches_bundle_without_macos_metadata() -> None:
    assert ZIP_PATH.is_file()
    expected = {
        f"{BUNDLE.name}/{relative_path}"
        for relative_path in EXPECTED_HASHED_FILES | {"SHA256SUMS"}
    }

    with zipfile.ZipFile(ZIP_PATH) as archive:
        file_names = {name for name in archive.namelist() if not name.endswith("/")}
        assert not any(
            name.startswith("__MACOSX/")
            or "/._" in name
            or name.endswith("/.DS_Store")
            for name in archive.namelist()
        )
        assert file_names == expected
        for name in expected:
            relative_path = name.removeprefix(f"{BUNDLE.name}/")
            assert archive.read(name) == (BUNDLE / relative_path).read_bytes()


def test_readme_namespaces_v3_and_marks_reused_cases_as_development() -> None:
    readme = (BUNDLE / "README.md").read_text(encoding="utf-8")
    assert STUDY_ID in readme
    assert "development/recalibration experiment" in readme
    assert "unseen scenario-level holdout" in readme
    assert "/v2/blinded" not in readme
