#!/bin/bash
# Consolidate raw JSON results into verdicts.csv and run analysis.
# CPU-only — adjust --partition if your cluster has a dedicated CPU partition.
#
# Usage:
#   sbatch slurm/analyze.sbatch
#   # Or chain directly after the array job:
#   sbatch --dependency=afterok:<ARRAY_JOB_ID> slurm/analyze.sbatch
#
#SBATCH --job-name=pr-framing-analyze
#SBATCH --output=slurm/logs/%x-%j.out
#SBATCH --error=slurm/logs/%x-%j.err
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --partition=lsrcpushort     # CPU partition — consolidation/analysis needs no GPU

set -euo pipefail

cd "${SLURM_SUBMIT_DIR:-$PWD}"

CONDA_ENV=${CONDA_ENV:-pr-framing}
source "$HOME/miniconda3/bin/activate" "$CONDA_ENV"

echo "[analyze] node=$(hostname)"
echo "[analyze] consolidating raw results..."
python run_experiment.py mode=consolidate

echo "[analyze] running analysis..."
python src/analyze.py results/verdicts.csv

echo "[analyze] done. Results in results/"
