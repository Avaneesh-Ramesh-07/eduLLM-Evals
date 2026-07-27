#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -lt 4 ]]; then
  echo "Usage: $0 HUMAN_LABELS RESULTS_ROOT JSON_OUT CSV_OUT [comparison options]" >&2
  exit 2
fi

human_labels="$1"
results_root="${2%/}"
json_out="$3"
csv_out="$4"
shift 4

judges=(selene flow prometheus qwen gemma)
waves=(
  canonical_r1
  canonical_r2
  canonical_r3
  whitespace_r1
  header_synonyms_r1
  instruction_politeness_r1
)
wave_args=()
missing=()

for judge in "${judges[@]}"; do
  for wave in "${waves[@]}"; do
    path="${results_root}/${judge}/${wave}/${wave}.jsonl"
    if [[ ! -f "$path" ]]; then
      missing+=("$path")
    fi
    wave_args+=(--wave "${judge}:${wave}:${path}")
  done
done

if (( ${#missing[@]} )); then
  echo "Missing ${#missing[@]} reliability wave file(s):" >&2
  printf '  %s\n' "${missing[@]}" >&2
  exit 2
fi

python "$script_dir/compare_judge_reliability.py" \
  "$human_labels" \
  "${wave_args[@]}" \
  --json-out "$json_out" \
  --csv-out "$csv_out" \
  "$@"
