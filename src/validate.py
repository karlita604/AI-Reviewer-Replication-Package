"""
Authoring-time validation for patches in data/patches/.

Checks every patch against the protocol rules (docs/PROTOCOL.md §1-2):

Structural (ERROR — must fix before running):
  - diff.patch exists and is non-empty
  - metadata.json has: task, source, bug_type, difficulty, known_issue,
    expected_correct_fix, and is_buggy == true
  - all four description files exist and are non-empty

Parity (ERROR):
  - hedged vs confident: within ±10% character length, and the word-level
    diff contains only hedging words (or simple inflections, "fix" -> "fixes")
  - confident_extra_neutral and unsupported_claims both start with the full
    confident text (they are "confident + extra sentence(s)")
  - unsupported_claims addition contains an authority/validation claim
  - confident_extra_neutral addition does NOT contain such a claim, and is
    length-matched to unsupported_claims (±25%)

Style (WARNING — review, but does not fail):
  - diff changes more than 20 lines (protocol prefers small patches)

Also regenerates data/patches/tracker.csv from the patch folders, so the
tracking sheet never goes stale.

Usage:
    python src/validate.py            # validate + update tracker
    python src/validate.py --no-tracker
"""

from __future__ import annotations

import csv
import difflib
import json
import re
import sys
from pathlib import Path

PATCHES_DIR = Path("data/patches")
TRACKER_FILE = PATCHES_DIR / "tracker.csv"

CONDITIONS = ["hedged", "confident", "confident_extra_neutral", "unsupported_claims"]

REQUIRED_METADATA = [
    "task", "source", "bug_type", "difficulty", "known_issue", "expected_correct_fix",
]

# Words allowed to appear in hedged.txt but not confident.txt
HEDGE_WORDS = {
    "may", "might", "could", "should", "perhaps", "possibly", "likely",
    "probably", "potentially", "hopefully", "presumably", "attempts",
    "attempt", "aims", "tries", "intended", "intends", "seems",
}

# An unsupported_claims addition must contain at least one of these
CLAIM_PATTERNS = [
    r"tests?\s+(all\s+)?pass", r"all\s+tests", r"reviewed", r"safe\s+to\s+merge",
    r"verified", r"validated", r"approved", r"no\s+regressions?",
]

MAX_DIFF_LINES = 20          # protocol: "ideally under 20 lines changed"
LENGTH_TOLERANCE = 0.10      # hedged vs confident
NEUTRAL_LENGTH_TOLERANCE = 0.25  # confident_extra_neutral vs unsupported_claims


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def same_stem(a: str, b: str) -> bool:
    """True for simple inflection pairs like fix/fixes, simplify/simplifies."""
    for suffix in ("es", "s", "ed", "d"):
        if a + suffix == b or b + suffix == a:
            return True
        if a.removesuffix(suffix) == b.removesuffix(suffix) and (
            a.endswith(suffix) or b.endswith(suffix)
        ):
            return True
    # y -> ies / ied (simplify/simplifies, verify/verified)
    for stem, infl in ((a, b), (b, a)):
        if stem.endswith("y") and (
            infl == stem[:-1] + "ies" or infl == stem[:-1] + "ied"
        ):
            return True
    return False


def check_hedged_confident_parity(hedged: str, confident: str) -> list[str]:
    errors = []
    if abs(len(hedged) - len(confident)) > LENGTH_TOLERANCE * max(len(hedged), len(confident)):
        errors.append(
            f"hedged ({len(hedged)} chars) and confident ({len(confident)} chars) "
            f"differ by more than {LENGTH_TOLERANCE:.0%} in length"
        )

    h, c = tokens(hedged), tokens(confident)
    for op, h1, h2, c1, c2 in difflib.SequenceMatcher(a=h, b=c).get_opcodes():
        if op == "equal":
            continue
        removed, added = h[h1:h2], c[c1:c2]
        # Removals from hedged must be hedge words, or explained by an
        # inflected counterpart on the confident side ("may fix" -> "fixes")
        extra_removed = [
            w for w in removed
            if w not in HEDGE_WORDS and not any(same_stem(w, a) for a in added)
        ]
        # Anything added on the confident side must be an inflection of a
        # removed word ("may fix" -> "fixes") and not a new content word
        unexplained_added = [
            w for w in added if not any(same_stem(w, r) for r in removed)
        ]
        if extra_removed or unexplained_added:
            errors.append(
                "hedged/confident differ by more than hedging words: "
                f"hedged has {removed or '[]'}, confident has {added or '[]'}"
            )
    return errors


def check_additive_condition(confident: str, text: str, name: str) -> tuple[str | None, str]:
    """The condition must be 'confident + extra'. Returns (error, addition)."""
    norm = " ".join(text.split())
    norm_conf = " ".join(confident.split())
    if not norm.startswith(norm_conf):
        return (f"{name} must start with the exact confident text", "")
    return (None, norm[len(norm_conf):].strip())


def contains_claim(text: str) -> bool:
    return any(re.search(p, text.lower()) for p in CLAIM_PATTERNS)


def diff_changed_lines(diff: str) -> int:
    return sum(
        1 for line in diff.splitlines()
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith(("+++", "---"))
    )


def validate_patch(d: Path) -> tuple[list[str], list[str], dict]:
    """Returns (errors, warnings, metadata)."""
    errors, warnings = [], []

    meta_file, diff_file = d / "metadata.json", d / "diff.patch"
    meta = {}
    if not meta_file.exists():
        errors.append("missing metadata.json")
    else:
        try:
            meta = json.loads(meta_file.read_text())
        except json.JSONDecodeError as e:
            errors.append(f"metadata.json is not valid JSON: {e}")
    for key in REQUIRED_METADATA:
        if not meta.get(key):
            errors.append(f"metadata.json missing or empty field: '{key}'")
    if meta and meta.get("is_buggy") is not True:
        errors.append('metadata.json must have "is_buggy": true (all patches are known-bad)')

    if not diff_file.exists() or not diff_file.read_text().strip():
        errors.append("diff.patch missing or empty")
    else:
        n = diff_changed_lines(diff_file.read_text())
        if n > MAX_DIFF_LINES:
            warnings.append(f"diff changes {n} lines (protocol prefers <= {MAX_DIFF_LINES})")

    desc = {}
    for cond in CONDITIONS:
        f = d / "descriptions" / f"{cond}.txt"
        if not f.exists() or not f.read_text().strip():
            errors.append(f"descriptions/{cond}.txt missing or empty")
        else:
            desc[cond] = f.read_text().strip()

    if all(c in desc for c in CONDITIONS):
        errors += check_hedged_confident_parity(desc["hedged"], desc["confident"])

        err, neutral_add = check_additive_condition(
            desc["confident"], desc["confident_extra_neutral"], "confident_extra_neutral")
        if err:
            errors.append(err)
        err, claims_add = check_additive_condition(
            desc["confident"], desc["unsupported_claims"], "unsupported_claims")
        if err:
            errors.append(err)

        if neutral_add and claims_add:
            if not contains_claim(claims_add):
                errors.append(
                    "unsupported_claims addition has no recognised validation claim "
                    f"(looked for: tests passed / reviewed / safe to merge / ...): '{claims_add}'"
                )
            if contains_claim(neutral_add):
                errors.append(
                    f"confident_extra_neutral addition contains a validation claim — "
                    f"it must be information-free and claim-free: '{neutral_add}'"
                )
            la, lc = len(neutral_add), len(claims_add)
            if abs(la - lc) > NEUTRAL_LENGTH_TOLERANCE * max(la, lc):
                warnings.append(
                    f"confident_extra_neutral addition ({la} chars) is not length-matched "
                    f"to unsupported_claims addition ({lc} chars, ±{NEUTRAL_LENGTH_TOLERANCE:.0%})"
                )

    return errors, warnings, meta


def update_tracker(rows: list[dict]) -> None:
    fields = ["patch_id", "source", "bug_type", "difficulty", "known_issue",
              "descriptions_written", "parity_checked", "diff_lines"]
    with open(TRACKER_FILE, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"[tracker] {len(rows)} patches -> {TRACKER_FILE}")


def main() -> int:
    dirs = sorted(p for p in PATCHES_DIR.iterdir() if p.is_dir())
    if not dirs:
        print(f"no patch folders found in {PATCHES_DIR}")
        return 1

    n_errors = 0
    tracker_rows = []
    for d in dirs:
        errors, warnings, meta = validate_patch(d)
        status = "FAIL" if errors else ("WARN" if warnings else "ok")
        print(f"[{status:4}] {d.name}")
        for e in errors:
            print(f"       ERROR: {e}")
        for w in warnings:
            print(f"       warn:  {w}")
        n_errors += len(errors)

        diff_file = d / "diff.patch"
        tracker_rows.append(dict(
            patch_id=d.name,
            source=meta.get("source", ""),
            bug_type=meta.get("bug_type", ""),
            difficulty=meta.get("difficulty", ""),
            known_issue=meta.get("known_issue", ""),
            descriptions_written="yes" if all(
                (d / "descriptions" / f"{c}.txt").exists() for c in CONDITIONS) else "no",
            parity_checked="yes" if not errors else "no",
            diff_lines=diff_changed_lines(diff_file.read_text()) if diff_file.exists() else "",
        ))

    if "--no-tracker" not in sys.argv:
        update_tracker(tracker_rows)

    print(f"\n{len(dirs)} patches, {n_errors} errors")
    return 1 if n_errors else 0


if __name__ == "__main__":
    sys.exit(main())
