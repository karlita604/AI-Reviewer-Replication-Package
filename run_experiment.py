"""
Main experiment entrypoint.

SLURM_ARRAY_TASK_ID maps to a patch index. Each array task loads the model
once and runs all trials for that patch (all conditions × n_runs). This avoids
reloading a 70B model for every single trial.

Usage:
    # Count trials and get the right --array bound:
    python run_experiment.py mode=count_trials

    # Single patch locally (patch index 0):
    python run_experiment.py

    # On cluster:
    sbatch slurm/run_array.sbatch

    # After all jobs finish:
    python run_experiment.py mode=consolidate
"""

from __future__ import annotations

import csv
import json
import os
import zlib
from dataclasses import asdict
from pathlib import Path

import hydra
from omegaconf import DictConfig
from transformers import set_seed

from src.patches import load_all_patches, Patch, validate_patches
from src.reviewer import BugQuestionScorer
from src.api_reviewer import make_reviewer


# ── Trial building ────────────────────────────────────────────────────────────

def build_patch_trials(patch: Patch, cfg: DictConfig) -> list[dict]:
    """All trials for one patch: every condition repeated n_runs times."""
    trials = []
    for condition in cfg.experiment.conditions:
        if condition not in patch.descriptions:
            raise ValueError(
                f"Patch '{patch.patch_id}' is missing description for condition '{condition}'. "
                f"Expected file: data/patches/{patch.patch_id}/descriptions/{condition}.txt"
            )
        for run_index in range(cfg.experiment.n_runs):
            trials.append(dict(
                patch_id=patch.patch_id,
                condition=condition,
                run_index=run_index,
            ))
    return trials


# ── Result I/O ────────────────────────────────────────────────────────────────

VERDICT_FIELDS = [
    "patch_id", "model", "condition", "run_index",
    "verdict", "confidence", "problems_found", "main_reason", "suggested_changes",
]


def raw_path(cfg: DictConfig, patch_id: str, condition: str, run_index: int) -> Path:
    slug = cfg.model.name.replace("/", "__")
    return Path(cfg.data.raw_dir) / f"{patch_id}__{condition}__run{run_index}__{slug}.json"


def write_raw(cfg: DictConfig, trial: dict, result) -> None:
    path = raw_path(cfg, trial["patch_id"], trial["condition"], trial["run_index"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({**trial, "model": cfg.model.name, **asdict(result)}, f, indent=2)


def already_done(cfg: DictConfig, trial: dict) -> bool:
    return raw_path(cfg, trial["patch_id"], trial["condition"], trial["run_index"]).exists()


def trial_seed(cfg: DictConfig, trial: dict) -> int:
    """
    Deterministic per-trial seed derived from (global seed, patch, condition, run).
    This makes every trial reproducible independently — rerunning a partially
    completed array task regenerates identical outputs for the remaining trials,
    regardless of execution order.
    """
    key = f"{trial['patch_id']}|{trial['condition']}|{trial['run_index']}"
    return (int(cfg.seed) + zlib.crc32(key.encode())) % (2**31)


# ── Modes ─────────────────────────────────────────────────────────────────────

def count_trials(patches: list[Patch], cfg: DictConfig) -> None:
    n_conditions = len(cfg.experiment.conditions)
    n_runs = cfg.experiment.n_runs
    n_trials_per_patch = n_conditions * n_runs
    total = len(patches) * n_trials_per_patch
    print(f"Patches:            {len(patches)}")
    print(f"Conditions:         {n_conditions}  {list(cfg.experiment.conditions)}")
    print(f"Runs per condition: {n_runs}")
    print(f"Trials per patch:   {n_trials_per_patch}")
    print(f"Total trials:       {total}")
    print(f"\nSet in sbatch:  --array=0-{len(patches) - 1}")


def consolidate(cfg: DictConfig) -> None:
    rows = []
    for p in sorted(Path(cfg.data.raw_dir).glob("*.json")):
        data = json.loads(p.read_text())
        rows.append({k: data.get(k, "") for k in VERDICT_FIELDS})
    out = Path(cfg.data.verdicts_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=VERDICT_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"[consolidate] {len(rows)} rows → {out}")


def bug_question(patches: list[Patch], cfg: DictConfig) -> None:
    """
    Phase 6b experiment 1: for every patch x condition, ask "does this diff
    contain a bug?" and record the probability of answering yes vs no.
    One deterministic forward pass per input — no runs, no seeds. A single
    job covers all patches for one model (50 x 4 = 200 passes).
    """
    slug = cfg.model.name.replace("/", "__")
    out = Path(cfg.data.get("bug_question_dir", "results/bug_question")) / f"scores__{slug}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)

    fields = ["patch_id", "model", "condition",
              "p_yes", "p_no", "p_yes_renorm", "coverage", "top_token"]
    done: set[tuple[str, str]] = set()
    if out.exists():
        with open(out) as f:
            done = {(r["patch_id"], r["condition"]) for r in csv.DictReader(f)}
        print(f"[bug-question] resuming: {len(done)} rows already in {out}", flush=True)

    scorer = BugQuestionScorer(cfg)
    with open(out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not done:
            w.writeheader()
        for patch in patches:
            for condition in cfg.experiment.conditions:
                if (patch.patch_id, condition) in done:
                    continue
                s = scorer.score(
                    task=patch.task,
                    description=patch.descriptions[condition],
                    diff=patch.diff,
                )
                w.writerow(dict(patch_id=patch.patch_id, model=cfg.model.name,
                                condition=condition, p_yes=s.p_yes, p_no=s.p_no,
                                p_yes_renorm=s.p_yes_renorm, coverage=s.coverage,
                                top_token=s.top_token))
                f.flush()
                print(f"  [scored] {patch.patch_id} {condition}: "
                      f"P(yes)={s.p_yes_renorm:.3f} coverage={s.coverage:.3f}", flush=True)
    print(f"[bug-question] done -> {out}", flush=True)


def _run_pending(patch: Patch, pending: list[dict], n_trials: int,
                 cfg: DictConfig, reviewer) -> None:
    """Run the not-yet-done trials for one patch with an already-built reviewer."""
    print(f"[job] patch={patch.patch_id}  {len(pending)}/{n_trials} trials to run", flush=True)
    for trial in pending:
        # Seed per trial so each trial is independently reproducible. (No effect
        # on the API reviewer, which the vendor does not let us seed; the raw
        # response is stored for auditability instead — see protocols/FRONTIER-PLAN.md.)
        set_seed(trial_seed(cfg, trial))
        print(f"  [trial] condition={trial['condition']} run={trial['run_index']}", flush=True)
        result = reviewer.review(
            task=patch.task,
            description=patch.descriptions[trial["condition"]],
            diff=patch.diff,
        )
        write_raw(cfg, trial, result)
        print(f"  [done]  verdict={result.verdict}", flush=True)


def run(patches: list[Patch], cfg: DictConfig) -> None:
    patch_idx = int(os.environ.get("SLURM_ARRAY_TASK_ID", 0))
    if patch_idx >= len(patches):
        print(f"[skip] patch index {patch_idx} >= {len(patches)} patches")
        return

    patch = patches[patch_idx]
    trials = build_patch_trials(patch, cfg)

    pending = [t for t in trials if not already_done(cfg, t)]
    if not pending:
        print(f"[skip] all trials already done for {patch.patch_id}")
        return

    # Build the reviewer only when work remains, so an already-done SLURM array
    # task does not pay to load a large local model.
    reviewer = make_reviewer(cfg)
    _run_pending(patch, pending, len(trials), cfg, reviewer)


def run_all(patches: list[Patch], cfg: DictConfig) -> None:
    """
    Run every patch in one process, building the reviewer once. This is the
    no-SLURM path used by the API reviewer (protocols/FRONTIER-PLAN.md), and also
    works for a local model when an array job is not wanted.
    """
    reviewer = make_reviewer(cfg)
    for patch in patches:
        trials = build_patch_trials(patch, cfg)
        pending = [t for t in trials if not already_done(cfg, t)]
        if not pending:
            print(f"[skip] all trials already done for {patch.patch_id}")
            continue
        _run_pending(patch, pending, len(trials), cfg, reviewer)


# ── Entry point ───────────────────────────────────────────────────────────────

@hydra.main(version_base=None, config_path="conf", config_name="config")
def main(cfg: DictConfig) -> None:
    patches = load_all_patches(cfg.data.patches_dir)
    validate_patches(patches, cfg)  # fail fast on missing descriptions

    if cfg.mode == "count_trials":
        count_trials(patches, cfg)
    elif cfg.mode == "consolidate":
        consolidate(cfg)
    elif cfg.mode == "bug_question":
        bug_question(patches, cfg)
    elif cfg.mode == "run_all":
        run_all(patches, cfg)
    else:
        run(patches, cfg)


if __name__ == "__main__":
    main()
