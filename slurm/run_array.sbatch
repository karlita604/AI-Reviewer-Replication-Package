#!/bin/bash
# Array job: one task per patch. Each task loads the model once and runs all
# trials for that patch (4 conditions × 5 runs = 20 trials per task).
#
# Usage:
#   sbatch slurm/run_array.sbatch                          # default model (llama3_70b)
#   sbatch slurm/run_array.sbatch model=qwen2_5_72b
#   MODEL=/path/to/snapshot sbatch slurm/run_array.sbatch  # force a specific snapshot
#
# Set --array upper bound first:
#   python run_experiment.py mode=count_trials
#
#SBATCH --job-name=pr-framing
#SBATCH --output=slurm/logs/%x-%j_%a.out
#SBATCH --error=slurm/logs/%x-%j_%a.err
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --gpus-per-task=1
#SBATCH --mem=128G
#SBATCH --partition=dgxh100long
#SBATCH --array=0-49                # update to n_patches - 1

set -euo pipefail

cd "${SLURM_SUBMIT_DIR:-$PWD}"

# --- Conda ------------------------------------------------------------------
CONDA_ENV=${CONDA_ENV:-pr-framing}
source "$HOME/miniconda3/bin/activate" "$CONDA_ENV"

# --- HuggingFace cache ------------------------------------------------------
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
mkdir -p "$HF_HOME"

# Compute node has no internet — model must be pre-downloaded on the login node
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

# --- Model ------------------------------------------------------------------
# Model selection comes from the Hydra config (model=llama3_70b | qwen2_5_72b).
# HF_HUB_OFFLINE=1 resolves the HF model ID from the local cache — models must
# be pre-downloaded on the login node. Export MODEL=<path-or-id> only to force
# a specific snapshot, e.g. for a model with no conf/model/ entry.
MODEL_OVERRIDE=()
[[ -n "${MODEL:-}" ]] && MODEL_OVERRIDE=(model.name="$MODEL")

echo "[slurm] node=$(hostname)"
nvidia-smi -L || true
echo "[slurm] array_task=${SLURM_ARRAY_TASK_ID}  job=${SLURM_JOB_ID}"
echo "[slurm] env=${CONDA_ENV}  model_override=${MODEL:-<none, using Hydra config>}"

srun python run_experiment.py "${MODEL_OVERRIDE[@]}" "$@"
