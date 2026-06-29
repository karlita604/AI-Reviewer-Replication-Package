"""
Load and validate patches from data/patches/.

Each patch folder must contain:
    diff.patch
    metadata.json           (must include "task" key)
    descriptions/
        hedged.txt
        confident.txt
        confident_extra_neutral.txt
        unsupported_claims.txt
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import re

from omegaconf import DictConfig


@dataclass
class Patch:
    patch_id: str
    diff: str
    task: str
    metadata: dict
    descriptions: dict[str, str]    # condition -> text


def load_all_patches(patches_dir: str | Path) -> list[Patch]:
    patches = []
    for d in sorted(Path(patches_dir).iterdir()):
        if not d.is_dir() or not re.fullmatch(r"patch_\d{3}", d.name):  # if not d.is_dir():
            continue
        meta = json.loads((d / "metadata.json").read_text())
        descriptions = {
            f.stem: f.read_text().strip()
            for f in sorted((d / "descriptions").glob("*.txt"))
        }
        patches.append(Patch(
            patch_id=d.name,
            diff=(d / "diff.patch").read_text(),
            task=meta.get("task", ""),
            metadata=meta,
            descriptions=descriptions,
        ))
    return patches


def validate_patches(patches: list[Patch], cfg: DictConfig) -> None:
    """Fail fast if any patch is missing required descriptions or fields."""
    errors = []
    for patch in patches:
        if not patch.task:
            errors.append(f"{patch.patch_id}: missing 'task' in metadata.json")
        if not patch.diff.strip():
            errors.append(f"{patch.patch_id}: diff.patch is empty")
        for condition in cfg.experiment.conditions:
            if condition not in patch.descriptions:
                errors.append(
                    f"{patch.patch_id}: missing descriptions/{condition}.txt"
                )
    if errors:
        raise ValueError("Patch validation failed:\n" + "\n".join(f"  - {e}" for e in errors))
