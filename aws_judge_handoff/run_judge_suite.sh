#!/usr/bin/env bash
set -euo pipefail

bundle_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$bundle_dir"

study_id="judge-validation-v3-evidence-gated"

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 JUDGE S3_PROJECT_ROOT [additional runner options]" >&2
  echo "JUDGE: selene | flow | prometheus | qwen | gemma" >&2
  echo "Outputs are placed under S3_PROJECT_ROOT/${study_id}/blinded." >&2
  exit 2
fi

judge="$1"
s3_project_root="${2%/}"
s3_study_root="${s3_project_root}/${study_id}/blinded"
shift 2

case "$judge" in
  selene|flow|prometheus|qwen|gemma) ;;
  *)
    echo "Unknown judge: $judge" >&2
    exit 2
    ;;
esac

for option in "$@"; do
  case "$option" in
    --cases|--cases=*|--judge|--judge=*|--output|--output=*|\
    --prompt-variant|--prompt-variant=*|--replicate-id|--replicate-id=*|\
    --s3-output-prefix|--s3-output-prefix=*|--backend|--backend=*|\
    --model-id|--model-id=*|--revision|--revision=*|--served-model|\
    --served-model=*|--base-url|--base-url=*|--temperature|--temperature=*|\
    --top-p|--top-p=*|--seed|--seed=*|--max-tokens|--max-tokens=*|\
    --prometheus-pass-threshold|--prometheus-pass-threshold=*)
      echo "The reliability launcher controls $option; do not override it." >&2
      exit 2
      ;;
  esac
done

waves=(
  "canonical_r1|canonical|r1"
  "canonical_r2|canonical|r2"
  "canonical_r3|canonical|r3"
  "whitespace_r1|whitespace|r1"
  "header_synonyms_r1|header_synonyms|r1"
  "instruction_politeness_r1|instruction_politeness|r1"
)

mkdir -p "outputs/${study_id}/${judge}"

for wave_spec in "${waves[@]}"; do
  IFS='|' read -r wave prompt_variant replicate_id <<< "$wave_spec"
  echo "Starting ${judge}/${wave}"
  python scripts/run_judge_validation.py run \
    --cases inputs/judge_cases.blinded.jsonl \
    --judge "$judge" \
    --output "outputs/${study_id}/${judge}/${wave}.jsonl" \
    --backend vllm \
    --prompt-variant "$prompt_variant" \
    --replicate-id "$replicate_id" \
    --resume \
    --s3-output-prefix "${s3_study_root}/${judge}/${wave}" \
    --require-s3-upload \
    "$@"
done

echo "Completed all six waves for ${judge}."
echo "S3 study root: ${s3_study_root}"
