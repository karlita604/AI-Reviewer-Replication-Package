"""
Signal-detection analysis (Phase 7 -> 8 step 3): does the framing make the
reviewer worse at telling good code from bad (sensitivity loss), or just more
willing to approve everything (criterion shift)?

The setup. Each patch is either bad (a bug is present — the "signal") or good
(no bug). The reviewer's detection response is "reject" = it does NOT approve
(verdict != approve); approving is "signal absent". Two rates per model per
condition, pooled over the 50 paired patches x 5 runs:

    hit rate   H = P(reject | bad patch)    — bugs correctly caught
    false alarm F = P(reject | good patch)  — correct code wrongly rejected

From these, two numbers that pull apart the two explanations:

    d'  (sensitivity)   = z(H) - z(F)
        how well the reviewer separates bad code from good. Bigger = better.
    c   (criterion)     = -0.5 * (z(H) + z(F))
        the bar for rejecting. Higher c = more reluctant to reject = a LOWER
        bar to approve (more lenient). Lower c = quicker to reject.
    (z is the inverse normal CDF — the z-score of a probability.)

Reading: if the claims mainly LOWER THE BAR, d' stays flat and c rises from the
hedged baseline. If they BLIND the reviewer to bugs, d' falls. Both can happen.

Edge cases. Rates of 0 or 1 give infinite z. We use the standard log-linear
correction (Hautus 1995): add 0.5 to each reject count and 1 to each total
before forming H and F.

Uncertainty. The hedged->claims change in d' and c gets a 95% interval from a
cluster bootstrap that resamples the 50 patches with replacement (the patch is
the unit; its good and bad reviews move together), 2000 resamples.

Second, belief-based SDT (optional, --bad-belief / --good-belief). The
bug-question probe asks the model only "does this diff contain a bug?" and
records P(yes) — the model's belief that a bug exists, with no verdict at
stake. P(yes) is graded (often well below 0.5 for small models), so instead of
thresholding we measure how well the belief separates the two patch sets:

    d'_belief = (mean P(yes | bad) - mean P(yes | good)) / pooled SD
    level     = 0.5 * (mean P(yes | bad) + mean P(yes | good))   — overall
                bug-suspicion; a drop = less bug-suspicious across the board.

Reading: if the claims drop the LEVEL while d'_belief stays flat, the belief
falls uniformly on good and bad alike (the belief analog of lowering the bar).
If d'_belief falls, the belief itself can no longer tell good from bad (the
belief analog of going blind to the bug). This is the measure where the
"stops noticing the bug" component is expected to surface, since the
verdict-based d' above sees only the final approve/reject decision.

Pairs the bad-patch verdicts (the main study / phase8) with the good-patch
verdicts (Phase 7 / phase8_good) by model. Run once per scale:

    python src/sdt.py \\
        --bad results/verdicts.csv --good results/good_verdicts.csv \\
        --out-dir results/analysis/sdt                 # the two 70B reviewers
    python src/sdt.py \\
        --bad results/phase8/verdicts.csv --good results/phase8_good/verdicts.csv \\
        --bad-belief results/phase8/bug_question \\
        --good-belief results/phase8_good/bug_question \\
        --out-dir results/phase8_good/analysis/sdt     # six small models + belief
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

CONDITIONS = ["hedged", "confident", "confident_extra_neutral", "unsupported_claims"]
REFERENCE = "hedged"
TREATMENT = "unsupported_claims"
N_BOOT = 2000
SEED = 0


def reject_flag(df: pd.DataFrame) -> pd.DataFrame:
    """'reject' = did not approve. Returns df with an int 'reject' column."""
    out = df.copy()
    out["reject"] = (out["verdict"] != "approve").astype(int)
    return out


def rates(n_reject_bad: int, n_bad: int, n_reject_good: int, n_good: int):
    """Log-linear-corrected hit/false-alarm rates, then d' and criterion."""
    H = (n_reject_bad + 0.5) / (n_bad + 1)
    F = (n_reject_good + 0.5) / (n_good + 1)
    zH, zF = norm.ppf(H), norm.ppf(F)
    d_prime = zH - zF
    criterion = -0.5 * (zH + zF)
    return H, F, d_prime, criterion


def sdt_for(bad: pd.DataFrame, good: pd.DataFrame):
    """d'/criterion for one model x one condition from its bad and good reviews."""
    return rates(int(bad["reject"].sum()), len(bad),
                 int(good["reject"].sum()), len(good))


def boot_delta(bad: pd.DataFrame, good: pd.DataFrame, rng: np.random.Generator):
    """
    Cluster bootstrap of (d'_treatment - d'_reference) and the same for c.
    Resamples patch_ids with replacement; a patch carries both its good and
    bad reviews. Returns (dd_lo, dd_hi, dc_lo, dc_hi) 95% percentile bounds.
    """
    patches = np.array(sorted(set(bad["patch_id"]) | set(good["patch_id"])))
    bad_by = {c: bad[bad["condition"] == c].groupby("patch_id") for c in (REFERENCE, TREATMENT)}
    good_by = {c: good[good["condition"] == c].groupby("patch_id") for c in (REFERENCE, TREATMENT)}

    def grab(groups, ids):
        frames = [groups.get_group(p) for p in ids if p in groups.groups]
        return pd.concat(frames) if frames else pd.DataFrame(columns=bad.columns)

    dd, dc = [], []
    for _ in range(N_BOOT):
        ids = rng.choice(patches, size=len(patches), replace=True)
        _, _, dR, cR = sdt_for(grab(bad_by[REFERENCE], ids), grab(good_by[REFERENCE], ids))
        _, _, dT, cT = sdt_for(grab(bad_by[TREATMENT], ids), grab(good_by[TREATMENT], ids))
        dd.append(dT - dR)
        dc.append(cT - cR)
    return (*np.percentile(dd, [2.5, 97.5]), *np.percentile(dc, [2.5, 97.5]))


def load_belief(path: str) -> pd.DataFrame:
    """Read all bug-question scores__*.csv under a dir (or a glob) into one df."""
    p = Path(path)
    files = sorted(p.glob("scores__*.csv")) if p.is_dir() else sorted(Path().glob(path))
    if not files:
        return pd.DataFrame(columns=["model", "patch_id", "condition", "p_yes_renorm"])
    return pd.concat([pd.read_csv(f) for f in files], ignore_index=True)


def belief_sdt(bad_vals: np.ndarray, good_vals: np.ndarray):
    """
    Continuous belief SDT from P(yes) on bad vs good patches.
      d' = (mean_bad - mean_good) / pooled SD   (equal-variance Gaussian)
      level = 0.5 * (mean_bad + mean_good)      (overall bug-suspicion)
    pooled SD floored by a small epsilon so near-constant beliefs don't blow up.
    """
    mb, mg = bad_vals.mean(), good_vals.mean()
    pooled = np.sqrt(0.5 * (bad_vals.var(ddof=1) + good_vals.var(ddof=1)))
    d_prime = (mb - mg) / max(pooled, 1e-6)
    return d_prime, 0.5 * (mb + mg)


def belief_auc(bad_vals: np.ndarray, good_vals: np.ndarray):
    """
    Assumption-free discrimination. d' above is only the right sensitivity index
    if the good- and bad-patch distributions are equal-variance Gaussian; AUC
    makes no such assumption. AUC = P(a random bad patch scores higher P(yes)
    than a random good one) — the full empirical ROC area; 0.5 = chance, 1 =
    perfect. var_ratio = SD(good)/SD(bad) is the z-ROC-slope proxy: a value near
    1 is what the equal-variance assumption behind d' requires.
    """
    b, g = bad_vals[:, None], good_vals[None, :]
    auc = float((np.sum(b > g) + 0.5 * np.sum(b == g)) / (len(bad_vals) * len(good_vals)))
    var_ratio = float(good_vals.std(ddof=1) / max(bad_vals.std(ddof=1), 1e-9))
    return auc, var_ratio


def boot_belief_delta(b_m: pd.DataFrame, g_m: pd.DataFrame, rng: np.random.Generator):
    """Patch cluster bootstrap for (d'_belief, level) change hedged->claims."""
    patches = np.array(sorted(set(b_m["patch_id"]) | set(g_m["patch_id"])))
    def vals(df, cond):
        return df[df.condition == cond].set_index("patch_id")["p_yes_renorm"]
    bR, bT = vals(b_m, REFERENCE), vals(b_m, TREATMENT)
    gR, gT = vals(g_m, REFERENCE), vals(g_m, TREATMENT)
    dd, dl, da = [], [], []
    for _ in range(N_BOOT):
        ids = rng.choice(patches, size=len(patches), replace=True)
        bRv, gRv = bR.reindex(ids).dropna().values, gR.reindex(ids).dropna().values
        bTv, gTv = bT.reindex(ids).dropna().values, gT.reindex(ids).dropna().values
        dR, lR = belief_sdt(bRv, gRv)
        dT, lT = belief_sdt(bTv, gTv)
        aR, _ = belief_auc(bRv, gRv)
        aT, _ = belief_auc(bTv, gTv)
        dd.append(dT - dR)
        dl.append(lT - lR)
        da.append(aT - aR)
    return (*np.percentile(dd, [2.5, 97.5]), *np.percentile(dl, [2.5, 97.5]),
            *np.percentile(da, [2.5, 97.5]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bad", default="results/verdicts.csv",
                        help="bad-patch verdicts CSV (signal present)")
    parser.add_argument("--good", default="results/good_verdicts.csv",
                        help="good-patch verdicts CSV (signal absent)")
    parser.add_argument("--bad-belief", default=None,
                        help="dir/glob of bad-patch bug-question scores__*.csv "
                             "(optional: enables the belief-based SDT)")
    parser.add_argument("--good-belief", default=None,
                        help="dir/glob of good-patch bug-question scores__*.csv")
    parser.add_argument("--out-dir", default="results/analysis/sdt")
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    bad = reject_flag(pd.read_csv(args.bad))
    good = reject_flag(pd.read_csv(args.good))
    models = sorted(set(bad["model"]) & set(good["model"]))

    lines: list[str] = []
    def emit(s: str = "") -> None:
        print(s)
        lines.append(s)

    emit("Signal-detection analysis: reject = not-approve = 'I report a bug'.")
    emit("Hit = P(reject | bad), False alarm = P(reject | good). "
         "Log-linear corrected.")
    emit("d' = sensitivity (good-vs-bad discrimination); "
         "c = criterion (higher = more lenient / lower bar to approve).")
    emit("CAVEAT: this verdict d'/c split is single-operating-point and assumes "
         "equal-variance Gaussian; the verdict 'confidence' field is too "
         "degenerate (near-constant 'high') to trace an ROC and test it, so the "
         "model-free result is the approval odds ratio, with d'/c as its gloss. "
         "The belief SDT below adds an assumption-free AUC.")
    emit(f"Models with both good and bad data: {len(models)}.")

    table_rows, delta_rows = [], []
    for model in models:
        b_m, g_m = bad[bad.model == model], good[good.model == model]
        per_cond = {}
        emit(f"\n=== {model.split('/')[-1]} ===")
        for cond in [c for c in CONDITIONS if c in set(b_m.condition)]:
            H, F, d, c = sdt_for(b_m[b_m.condition == cond], g_m[g_m.condition == cond])
            per_cond[cond] = (d, c)
            table_rows.append(dict(model=model, condition=cond,
                                   hit_rate=round(H, 4), fa_rate=round(F, 4),
                                   d_prime=round(d, 4), criterion=round(c, 4)))
            emit(f"  {cond:<26} hit={H:.3f} fa={F:.3f}  d'={d:+.3f}  c={c:+.3f}")

        if REFERENCE in per_cond and TREATMENT in per_cond:
            dR, cR = per_cond[REFERENCE]
            dT, cT = per_cond[TREATMENT]
            rng = np.random.default_rng(SEED)
            dd_lo, dd_hi, dc_lo, dc_hi = boot_delta(b_m, g_m, rng)
            emit(f"  {REFERENCE} -> {TREATMENT}:  "
                 f"d d'={dT-dR:+.3f} [{dd_lo:+.3f}, {dd_hi:+.3f}]   "
                 f"d c={cT-cR:+.3f} [{dc_lo:+.3f}, {dc_hi:+.3f}]")
            verdict = ("criterion shift (lower bar)" if (dc_lo > 0 and dd_lo < 0 < dd_hi)
                       else "sensitivity loss (blinding)" if dd_hi < 0
                       else "both" if (dc_lo > 0 and dd_hi < 0)
                       else "neither clearly")
            emit(f"  -> {verdict}")
            delta_rows.append(dict(model=model,
                                   d_prime_hedged=round(dR, 4), d_prime_claims=round(dT, 4),
                                   delta_d_prime=round(dT - dR, 4),
                                   delta_d_lo=round(dd_lo, 4), delta_d_hi=round(dd_hi, 4),
                                   criterion_hedged=round(cR, 4), criterion_claims=round(cT, 4),
                                   delta_criterion=round(cT - cR, 4),
                                   delta_c_lo=round(dc_lo, 4), delta_c_hi=round(dc_hi, 4),
                                   reading=verdict))

    pd.DataFrame(table_rows).to_csv(out / "sdt_table.csv", index=False)
    pd.DataFrame(delta_rows).to_csv(out / "sdt_deltas.csv", index=False)
    saved = ["sdt_table.csv", "sdt_deltas.csv"]

    # ── Second SDT: belief-based (bug-question P(yes)), if data given ──
    if args.bad_belief and args.good_belief:
        bb = load_belief(args.bad_belief)
        gb = load_belief(args.good_belief)
        bmodels = sorted(set(bb["model"]) & set(gb["model"])) if len(bb) and len(gb) else []
        emit("\n\n##### Belief-based SDT (bug-question P(yes)) #####")
        emit("d'_belief = standardized separation of P(yes) on bad vs good "
             "(equal-variance Gaussian); AUC = same separation but assumption-free "
             "(full empirical ROC area); var_ratio = SD(good)/SD(bad), ~1 supports "
             "equal variance; level = overall bug-suspicion. The discrimination "
             "verdict uses AUC, not d'.")
        emit(f"Models with both good and bad belief data: {len(bmodels)}.")
        b_table, b_delta = [], []
        for model in bmodels:
            b_m, g_m = bb[bb.model == model], gb[gb.model == model]
            per = {}
            emit(f"\n=== {model.split('/')[-1]} ===")
            for cond in [c for c in CONDITIONS if c in set(b_m.condition)]:
                bv = b_m[b_m.condition == cond]["p_yes_renorm"].values
                gv = g_m[g_m.condition == cond]["p_yes_renorm"].values
                d, lvl = belief_sdt(bv, gv)
                auc, vr = belief_auc(bv, gv)
                per[cond] = (d, lvl, auc)
                b_table.append(dict(model=model, condition=cond,
                                    mean_pyes_bad=round(float(bv.mean()), 4),
                                    mean_pyes_good=round(float(gv.mean()), 4),
                                    d_prime_belief=round(d, 4), level=round(lvl, 4),
                                    auc=round(auc, 4), var_ratio=round(vr, 3)))
                emit(f"  {cond:<26} P(yes) bad={bv.mean():.3f} good={gv.mean():.3f}  "
                     f"d'={d:+.3f}  AUC={auc:.3f} (var_ratio={vr:.2f})  level={lvl:.3f}")
            if REFERENCE in per and TREATMENT in per:
                (dR, lR, aR), (dT, lT, aT) = per[REFERENCE], per[TREATMENT]
                rng = np.random.default_rng(SEED)
                dd_lo, dd_hi, dl_lo, dl_hi, da_lo, da_hi = boot_belief_delta(b_m, g_m, rng)
                emit(f"  {REFERENCE} -> {TREATMENT}:  "
                     f"d d'={dT-dR:+.3f} [{dd_lo:+.3f}, {dd_hi:+.3f}]   "
                     f"d AUC={aT-aR:+.3f} [{da_lo:+.3f}, {da_hi:+.3f}]   "
                     f"d level={lT-lR:+.3f} [{dl_lo:+.3f}, {dl_hi:+.3f}]")
                # AUC is assumption-free; prefer it for the discrimination verdict.
                reading = ("uniform belief drop (lower bar)" if (dl_hi < 0 and da_lo < 0 < da_hi)
                           else "belief discrimination loss (blinding)" if da_hi < 0
                           else "both" if (dl_hi < 0 and da_hi < 0)
                           else "neither clearly")
                emit(f"  -> {reading}")
                b_delta.append(dict(model=model,
                                    d_prime_belief_hedged=round(dR, 4),
                                    d_prime_belief_claims=round(dT, 4),
                                    delta_d_belief=round(dT - dR, 4),
                                    delta_d_lo=round(dd_lo, 4), delta_d_hi=round(dd_hi, 4),
                                    auc_hedged=round(aR, 4), auc_claims=round(aT, 4),
                                    delta_auc=round(aT - aR, 4),
                                    delta_auc_lo=round(da_lo, 4), delta_auc_hi=round(da_hi, 4),
                                    level_hedged=round(lR, 4), level_claims=round(lT, 4),
                                    delta_level=round(lT - lR, 4),
                                    delta_level_lo=round(dl_lo, 4), delta_level_hi=round(dl_hi, 4),
                                    reading=reading))
        if b_table:
            pd.DataFrame(b_table).to_csv(out / "sdt_belief_table.csv", index=False)
            pd.DataFrame(b_delta).to_csv(out / "sdt_belief_deltas.csv", index=False)
            saved += ["sdt_belief_table.csv", "sdt_belief_deltas.csv"]

    (out / "sdt_summary.txt").write_text("\n".join(lines) + "\n")
    saved.append("sdt_summary.txt")
    print(f"\n[saved] {out}/ " + ", ".join(saved))


if __name__ == "__main__":
    main()
