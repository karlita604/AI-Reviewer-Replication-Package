#!/bin/bash
# Phase 6b experiment 1: the direct bug question.
# One job per model; 50 patches x 4 conditions = 200 forward passes, no array.
#
# Usage:
#   sbatch slurm/bug_question.sbatch                        # default model (llama3_70b)
#   sbatch slurm/bug_question.sbatch model=qwen2_5_72b
#
#SBATCH --job-name=pr-framing-bugq
#SBATCH --output=slurm/logs/%x-%j.out
#SBATCH --error=slurm/logs/%x-%j.err
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --gpus-per-task=1
#SBATCH --mem=128G
#SBATCH --partition=dgxh100long

set -euo pipefail

cd "${SLURM_SUBMIT_DIR:-$PWD}"

# --- Conda ------------------------------------------------------------------
CONDA_ENV=${CONDA_ENV:-pr-framing}
source "$HOME/miniconda3/bin/activate" "$CONDA_ENV"

# --- HuggingFace cache ------------------------------------------------------
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"

# Compute node has no internet — model must be pre-downloaded on the login node
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

echo "[slurm] node=$(hostname)"
nvidia-smi -L || true
echo "[slurm] job=${SLURM_JOB_ID}  env=${CONDA_ENV}"

srun python run_experiment.py mode=bug_question "$@"
