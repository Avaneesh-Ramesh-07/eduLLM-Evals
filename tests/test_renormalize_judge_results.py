from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT_PATH = ROOT / "scripts" / "renormalize_judge_results.py"
SPEC = importlib.util.spec_from_file_location("renormalize_judge_results", SCRIPT_PATH)
assert SPEC and SPEC.loader
renormalizer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = renormalizer
SPEC.loader.exec_module(renormalizer)
runner = renormalizer.runner


def _write_source_wave(root: Path, wave: str) -> tuple[Path, Path]:
    variant, replicate = wave.rsplit("_", 1)
    if variant.startswith("canonical"):
        variant = "canonical"
    output = root / "qwen" / wave / f"{wave}.jsonl"
    output.parent.mkdir(parents=True)
    configuration = {
        "judge_name": "qwen",
        "model_id": runner.JUDGES["qwen"].model_id,
        "revision": runner.JUDGES["qwen"].revision,
        "adapter": "generic-binary",
        "prompt_version": "judge-validation-v3",
        "prompt_variant": variant,
        "replicate_id": replicate,
        "normalization_version": "judge-normalization-v2",
        "runner_sha256": "source-runner",
        "prometheus_pass_threshold": 4,
    }
    configuration_hash = runner.stable_hash(configuration)
    frozen_hash = runner.frozen_configuration_hash(configuration)
    base = {
        "response_id": "response",
        "scenario_id": "scenario",
        "criterion_id": "criterion",
        "judge_name": "qwen",
        "judge_model": runner.JUDGES["qwen"].model_id,
        "judge_revision": runner.JUDGES["qwen"].revision,
        "adapter": "generic-binary",
        "prompt_version": "judge-validation-v3",
        "prompt_variant": variant,
        "replicate_id": replicate,
        "normalization_version": "judge-normalization-v2",
        "configuration_hash": configuration_hash,
        "frozen_configuration_hash": frozen_hash,
        "input_hash": "input",
        "prompt_hash": "prompt",
    }
    rows = [
        {
            **base,
            "case_id": "unchanged",
            "verdict": "pass",
            "native_score": 1,
            "status": "ok",
            "error": None,
            "raw_output": '{"verdict":"pass"}',
        },
        {
            **base,
            "case_id": "recovered",
            "verdict": "no_decision",
            "native_score": None,
            "status": "parse_error",
            "error": "old parser rejected malformed JSON",
            "raw_output": (
                '{"verdict":"fail","rationale":"invalid \\(",'
                '"evidence":"NONE"}'
            ),
        },
        {
            **base,
            "case_id": "invalidated",
            "verdict": "fail",
            "native_score": 0,
            "status": "ok",
            "error": None,
            "raw_output": '{"verdict":"pass","verdict":"fail"}',
        },
        {
            **base,
            "case_id": "unresolved",
            "verdict": "no_decision",
            "native_score": None,
            "status": "parse_error",
            "error": "no verdict",
            "raw_output": "rationale only",
        },
        {
            **base,
            "case_id": "generation_error",
            "verdict": "no_decision",
            "native_score": None,
            "status": "generation_error",
            "error": "model failed",
            "raw_output": "",
        },
    ]
    renormalizer.write_jsonl(output, rows)
    status_counts = {"generation_error": 1, "ok": 2, "parse_error": 2}
    manifest = {
        "status": "complete_with_errors",
        "case_count": len(rows),
        "configuration": configuration,
        "configuration_hash": configuration_hash,
        "frozen_configuration_hash": frozen_hash,
        "usable_decisions": 2,
        "no_decision_rows": 3,
        "status_counts": status_counts,
        "s3": {
            "artifacts": {
                output.name: {
                    "local_path": str(output),
                    "sha256": runner.file_sha256(output),
                }
            }
        },
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return output, manifest_path


def _write_source_tree(root: Path) -> list[tuple[Path, Path]]:
    return [
        _write_source_wave(root, wave) for wave in sorted(renormalizer.EXPECTED_WAVES)
    ]


def test_renormalize_tree_preserves_sources_and_audits_actions(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_files = _write_source_tree(source_root)
    source_hashes = {
        path: runner.file_sha256(path)
        for pair in source_files
        for path in pair
    }
    output_root = tmp_path / "derived"

    summary = renormalizer.renormalize_tree(source_root, output_root)

    assert summary["normalization_actions"] == {
        "invalidated_ambiguous": 6,
        "recovered": 6,
        "unchanged": 6,
        "unresolved": 12,
    }
    assert all(runner.file_sha256(path) == digest for path, digest in source_hashes.items())
    rows = renormalizer.load_jsonl(
        output_root / "qwen" / "canonical_r1" / "canonical_r1.jsonl"
    )
    by_id = {row["case_id"]: row for row in rows}
    assert by_id["recovered"]["verdict"] == "fail"
    assert by_id["recovered"]["status"] == "ok"
    assert by_id["invalidated"]["verdict"] == "no_decision"
    assert by_id["unresolved"]["status"] == "parse_error"
    assert by_id["generation_error"]["status"] == "generation_error"
    assert {row["normalization_version"] for row in rows} == {
        "judge-normalization-v3"
    }
    manifests = [
        renormalizer.load_json(path)
        for path in output_root.glob("*/*/*.manifest.json")
    ]
    assert len(manifests) == 6
    assert all(Path(manifest["output_file"]).is_file() for manifest in manifests)
    assert len({manifest["frozen_configuration_hash"] for manifest in manifests}) == 1
    for manifest in manifests:
        assert runner.stable_hash(manifest["configuration"]) == manifest[
            "configuration_hash"
        ]
    csv_rows = (output_root / "renormalization_summary.csv").read_text().splitlines()
    assert "unchanged" in csv_rows[0].split(",")


def test_renormalize_tree_refuses_existing_output(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _write_source_tree(source_root)
    output_root = tmp_path / "derived"
    output_root.mkdir()

    with pytest.raises(FileExistsError):
        renormalizer.renormalize_tree(source_root, output_root)


def test_renormalize_tree_requires_all_six_waves(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _write_source_wave(source_root, "canonical_r1")

    with pytest.raises(ValueError, match="exactly six"):
        renormalizer.renormalize_tree(source_root, tmp_path / "derived")


def test_renormalize_tree_requires_source_artifact_hash(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_files = _write_source_tree(source_root)
    _, manifest_path = source_files[0]
    manifest = renormalizer.load_json(manifest_path)
    manifest["s3"]["artifacts"] = {}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256 is missing"):
        renormalizer.renormalize_tree(source_root, tmp_path / "derived")


def test_parser_flip_aborts_without_publishing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    source_files = _write_source_tree(source_root)
    source_hashes = {
        path: runner.file_sha256(path)
        for pair in source_files
        for path in pair
    }
    output_root = tmp_path / "derived"
    original = runner.parse_judgment

    def flip_pass(text: str, spec: object, **kwargs: object):
        parsed = original(text, spec, **kwargs)
        if parsed.verdict == "pass":
            return runner.ParsedJudgment(verdict="fail", native_score=0)
        return parsed

    monkeypatch.setattr(runner, "parse_judgment", flip_pass)
    with pytest.raises(ValueError, match="would flip accepted verdict"):
        renormalizer.renormalize_tree(source_root, output_root)

    assert not output_root.exists()
    assert all(runner.file_sha256(path) == digest for path, digest in source_hashes.items())
