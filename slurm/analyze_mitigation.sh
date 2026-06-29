#!/bin/bash
# Phase 11 mitigation analysis pipeline (docs/MITIGATION-PLAN.md §6-§7). CPU only.
#
# Runs after the mitigation array jobs finish (slurm/launch_mitigation.sh). Safe
# to run on partial data: cells without a verdicts.csv are skipped, and
# mitigation_analysis.py omits arms whose files are not yet present.
#
#   1. consolidate each mitigation cell's raw/ -> verdicts.csv
#   2. verdict SDT per arm (criterion/d') via the existing src/sdt.py — the
#      registered mechanism check (criterion returns toward Arm A hedged, d' flat)
#   3. mitigation_analysis.py — lift, condition x arm GEE interaction, cost,
#      specificity guard, RQ-M3 ceiling, and the §6 scorecard
#
# Detection-judge sanity (bad-patch Arm B and Arm C) is NOT run here: it uses the
# Anthropic batch API (src/judge_detection.py), not Slurm. Run it separately.
#
# Usage (sbatch, or as a dependency on the array jobs):
#   sbatch slurm/analyze_mitigation.sh
#   sbatch --dependency=afterok:<lastArrayJobId> slurm/analyze_mitigation.sh
#
#SBATCH --job-name=mitig-analyze
#SBATCH --output=slurm/logs/%x-%j.out
#SBATCH --error=slurm/logs/%x-%j.err
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --partition=lsrcpushort

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)}"

CONDA_ENV=${CONDA_ENV:-pr-framing}
source "$HOME/miniconda3/bin/activate" "$CONDA_ENV"

# Arm tag -> experiment/data config pairs and result roots.
declare -A EXP=( [instruction]=mitig_instruction [terse]=mitig_terse [diff_only]=mitig_diffonly )
declare -A BAD=( [instruction]=mitig_instruction_bad [terse]=mitig_terse_bad [diff_only]=mitig_diffonly_bad )
declare -A GOOD=( [instruction]=mitig_instruction_good [terse]=mitig_terse_good [diff_only]=mitig_diffonly_good )

echo "[analyze] node=$(hostname)"

# --- 1. consolidate each cell that has raw output -----------------------------
for arm in instruction terse diff_only; do
  for set_cfg in "${BAD[$arm]}" "${GOOD[$arm]}"; do
    case "$set_cfg" in
      *_bad)  sub=bad ;;
      *_good) sub=good ;;
    esac
    rawdir="results/mitigation/${arm}/${sub}/raw"
    if compgen -G "$rawdir/*.json" > /dev/null; then
      echo "[consolidate] $arm/$sub"
      python run_experiment.py mode=consolidate experiment="${EXP[$arm]}" data="$set_cfg"
    else
      echo "[skip] $arm/$sub — no raw output yet"
    fi
  done
done

# --- 2. verdict SDT per arm (only if both bad+good verdicts exist) -------------
# Arm A baseline (large + small) is the reference; its SDT already lives under
# results/analysis/sdt and results/phase8_good/analysis/sdt.
sdt_arm() {  # $1=arm tag  $2=out subdir
  local bad="results/mitigation/$1/bad/verdicts.csv"
  local good="results/mitigation/$1/good/verdicts.csv"
  if [[ -f "$bad" && -f "$good" ]]; then
    echo "[sdt] $1"
    python src/sdt.py --bad "$bad" --good "$good" \
      --out-dir "results/mitigation/$1/analysis/sdt"
  else
    echo "[skip sdt] $1 — need both bad and good verdicts"
  fi
}
sdt_arm instruction
sdt_arm terse
sdt_arm diff_only

# --- 3. mitigation scorecard --------------------------------------------------
echo "[analyze] scorecard"
python src/mitigation_analysis.py --out-dir results/mitigation/analysis

echo "[analyze] done -> results/mitigation/analysis/ and results/mitigation/*/analysis/sdt/"
