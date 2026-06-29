#!/bin/bash
# Phase 11 mitigation launcher (docs/MITIGATION-PLAN.md §3, §9).
#
# Submits one array job per (model × arm × patch-set) cell. The full matrix is
# 8 models × 6 cells = 48 array jobs of 50 tasks each. Each array task loads the
# model once and runs every trial for one patch (run_experiment.py::run), and
# skips trials whose raw JSON already exists — so resubmitting a partially
# finished cell is safe and cheap.
#
# Cells:
#   Arm B  (instruction): mitig_instruction × {bad, good}   2 conds → 500 trials/cell
#   Arm B2 (terse):       mitig_terse        × {bad, good}   2 conds → 500 trials/cell
#   Arm C  (diff-only):   mitig_diffonly     × {bad, good}   1 cond  → 250 trials/cell
#
# Usage:
#   slurm/launch_mitigation.sh --dry-run                 # print sbatch lines, submit nothing
#   slurm/launch_mitigation.sh                           # submit the whole matrix
#   slurm/launch_mitigation.sh --models "qwen2_5_3b"     # one model (e.g. smoke first)
#   slurm/launch_mitigation.sh --arms "diff_only"        # one arm
#   slurm/launch_mitigation.sh --models "qwen2_5_3b qwen2_5_7b" --arms "instruction terse"
#
# A cell whose raw/ already holds the expected number of files for the model is
# reported [done] and skipped — so this script doubles as a resume/status tool.

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)}"

N_PATCHES=50
N_RUNS=5
ARRAY="0-$((N_PATCHES - 1))"

ALL_MODELS=(qwen2_5_3b qwen2_5_7b qwen2_5_14b qwen2_5_32b qwen2_5_72b \
            llama3_1_8b llama3_70b gemma2_9b)

# Each cell: "arm experiment data rawdir n_conditions"
CELLS=(
  "instruction mitig_instruction mitig_instruction_bad  results/mitigation/instruction/bad/raw  2"
  "instruction mitig_instruction mitig_instruction_good results/mitigation/instruction/good/raw 2"
  "terse       mitig_terse        mitig_terse_bad        results/mitigation/terse/bad/raw        2"
  "terse       mitig_terse        mitig_terse_good       results/mitigation/terse/good/raw       2"
  "diff_only   mitig_diffonly     mitig_diffonly_bad     results/mitigation/diff_only/bad/raw    1"
  "diff_only   mitig_diffonly     mitig_diffonly_good    results/mitigation/diff_only/good/raw   1"
)

# --- args -------------------------------------------------------------------
DRY_RUN=0
MODELS=("${ALL_MODELS[@]}")
ARMS=(instruction terse diff_only)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --models)  read -r -a MODELS <<< "$2"; shift 2 ;;
    --arms)    read -r -a ARMS   <<< "$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

in_list() { local x="$1"; shift; for e in "$@"; do [[ "$e" == "$x" ]] && return 0; done; return 1; }

# HF model id (for the raw-file slug) from conf/model/<name>.yaml.
hf_name() { grep -E '^\s*name:' "conf/model/$1.yaml" | head -1 | sed -E 's/.*name:\s*//; s/\s*$//'; }

submitted=0; skipped=0; queued_jobs=()
echo "[launch] matrix: ${#MODELS[@]} models × $(for c in "${CELLS[@]}"; do set -- $c; in_list "$1" "${ARMS[@]}" && echo x; done | wc -l) cells   dry_run=$DRY_RUN"
echo

for model in "${MODELS[@]}"; do
  in_list "$model" "${ALL_MODELS[@]}" || { echo "[warn] unknown model '$model' — skipping"; continue; }
  slug="$(hf_name "$model" | sed 's#/#__#g')"
  for cell in "${CELLS[@]}"; do
    set -- $cell
    arm="$1"; exp="$2"; data="$3"; rawdir="$4"; nc="$5"
    in_list "$arm" "${ARMS[@]}" || continue

    expected=$((N_PATCHES * nc * N_RUNS))
    have=0
    [[ -d "$rawdir" ]] && have=$(find "$rawdir" -maxdepth 1 -name "*__${slug}.json" | wc -l)
    tag="$arm/$(basename "$(dirname "$rawdir")")  $model"

    if (( have >= expected )); then
      printf "  [done] %-34s %d/%d\n" "$tag" "$have" "$expected"
      skipped=$((skipped + 1)); continue
    fi

    jobname="mitig-${arm}-$(basename "$(dirname "$rawdir")")-${model}"
    cmd=(sbatch --array="$ARRAY" --job-name="$jobname" slurm/run_array.sbatch
         "experiment=$exp" "data=$data" "model=$model")
    if (( DRY_RUN )); then
      printf "  [dry ] %-34s %d/%d  ->  %s\n" "$tag" "$have" "$expected" "${cmd[*]}"
    else
      out="$("${cmd[@]}")"; echo "  [sub ] $tag  ($have/$expected)  ->  $out"
      queued_jobs+=("${out##* }")
    fi
    submitted=$((submitted + 1))
  done
done

echo
echo "[launch] submitted=$submitted  skipped(done)=$skipped"
(( ${#queued_jobs[@]} )) && echo "[launch] job ids: ${queued_jobs[*]}"
echo "[launch] when all finish: python run_experiment.py mode=consolidate experiment=<exp> data=<data>  (per cell)"
