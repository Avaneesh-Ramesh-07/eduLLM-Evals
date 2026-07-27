"""Offline contract tests for the local six-wave comparison wrapper."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
WRAPPER = ROOT / "scripts" / "compare_reliability_suite.sh"
JUDGES = ("selene", "flow", "prometheus", "qwen", "gemma")
WAVES = (
    "canonical_r1",
    "canonical_r2",
    "canonical_r3",
    "whitespace_r1",
    "header_synonyms_r1",
    "instruction_politeness_r1",
)


def create_wave_tree(root: Path) -> list[Path]:
    paths = []
    for judge in JUDGES:
        for wave in WAVES:
            path = root / judge / wave / f"{wave}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")
            paths.append(path)
    return paths


def test_wrapper_expands_all_waves_outputs_and_passthrough_options(tmp_path: Path):
    results_root = tmp_path / "downloaded waves"
    create_wave_tree(results_root)
    human_labels = tmp_path / "human labels.csv"
    human_labels.write_text("case_id,human_label\n", encoding="utf-8")
    json_out = tmp_path / "reports" / "reliability report.json"
    csv_out = tmp_path / "reports" / "reliability summary.csv"

    capture = tmp_path / "python-argv.bin"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python"
    fake_python.write_text(
        "#!/bin/sh\nprintf '%s\\0' \"$@\" > \"$CAPTURE_ARGS\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    environment = dict(os.environ)
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}"
    environment["CAPTURE_ARGS"] = str(capture)

    completed = subprocess.run(
        [
            "bash",
            str(WRAPPER),
            str(human_labels),
            str(results_root) + "/",
            str(json_out),
            str(csv_out),
            "--bootstrap-samples",
            "17",
            "--seed",
            "91",
            "--enforce-thresholds",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    argv = capture.read_bytes().rstrip(b"\0").decode().split("\0")
    assert argv[:2] == [
        str(ROOT / "scripts" / "compare_judge_reliability.py"),
        str(human_labels),
    ]
    wave_indexes = [index for index, value in enumerate(argv) if value == "--wave"]
    assert len(wave_indexes) == 30
    assert [argv[index + 1] for index in wave_indexes] == [
        f"{judge}:{wave}:{results_root / judge / wave / f'{wave}.jsonl'}"
        for judge in JUDGES
        for wave in WAVES
    ]
    json_index = argv.index("--json-out")
    csv_index = argv.index("--csv-out")
    assert argv[json_index + 1] == str(json_out)
    assert argv[csv_index + 1] == str(csv_out)
    assert argv[csv_index + 2 :] == [
        "--bootstrap-samples",
        "17",
        "--seed",
        "91",
        "--enforce-thresholds",
    ]


def test_wrapper_fails_before_python_when_a_wave_file_is_missing(tmp_path: Path):
    results_root = tmp_path / "waves"
    paths = create_wave_tree(results_root)
    missing = paths[-1]
    missing.unlink()
    human_labels = tmp_path / "human_labels.csv"
    human_labels.write_text("case_id,human_label\n", encoding="utf-8")
    capture = tmp_path / "should-not-exist"

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python"
    fake_python.write_text(
        "#!/bin/sh\ntouch \"$CAPTURE_ARGS\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    environment = dict(os.environ)
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}"
    environment["CAPTURE_ARGS"] = str(capture)

    completed = subprocess.run(
        [
            "bash",
            str(WRAPPER),
            str(human_labels),
            str(results_root),
            str(tmp_path / "report.json"),
            str(tmp_path / "summary.csv"),
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "Missing 1 reliability wave file(s):" in completed.stderr
    assert str(missing) in completed.stderr
    assert not capture.exists()
