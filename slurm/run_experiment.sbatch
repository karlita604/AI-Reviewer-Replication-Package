#!/bin/bash
# Single-patch run — useful for smoke testing before launching the full array.
# Runs all trials for patch index 0 (or SLURM_ARRAY_TASK_ID if set).
#
# Usage:
#   sbatch slurm/run_experiment.sbatch
#   MODEL=/path/to/snapshot sbatch slurm/run_experiment.sbatch
#
#SBATCH --job-name=pr-framing-single
#SBATCH --output=slurm/logs/%x-%j.out
#SBATCH --error=slurm/logs/%x-%j.err
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --gpus-per-task=1
#SBATCH --mem=128G
#SBATCH --partition=dgxh100long

set -euo pipefail

cd "${SLURM_SUBMIT_DIR:-$PWD}"

CONDA_ENV=${CONDA_ENV:-pr-framing}
source "$HOME/miniconda3/bin/activate" "$CONDA_ENV"

export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
mkdir -p "$HF_HOME"

export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

MODEL_OVERRIDE=()
[[ -n "${MODEL:-}" ]] && MODEL_OVERRIDE=(model.name="$MODEL")

echo "[slurm] node=$(hostname)"
nvidia-smi -L || true
echo "[slurm] job=${SLURM_JOB_ID}  env=${CONDA_ENV}  model_override=${MODEL:-<none, using Hydra config>}"

srun python run_experiment.py "${MODEL_OVERRIDE[@]}" "$@"
