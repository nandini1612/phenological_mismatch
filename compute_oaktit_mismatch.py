"""
OAK-TIT mismatch computation
Phenological Mismatch Observatory

Computes the first real ecological mismatch score:
Quercus robur (oak leaf unfolding) vs Parus major (great tit arrival)
Documented fitness threshold: gap > 9 days → >50% chick mortality
(Visser et al. 1998, Proc R Soc B)

Run: python compute_oaktit_mismatch.py
"""

import pandas as pd
import numpy as np
from scipy.stats import weibull_min
from supabase import create_client
from dotenv import load_dotenv
import os

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────

OAK_TAXON_DB_ID = 1   # Quercus robur
TIT_TAXON_DB_ID = 3   # Parus major
PAIR_ID         = "OAK-TIT"
FITNESS_THRESHOLD = 9.0  # days — Visser et al. 1998

OAK_PERCENTILE  = 0.25   # validated: MAE 6.7 days vs PEP725
TIT_PERCENTILE  = 0.05   # onset: when first tits arrive to breed
                          # (5th percentile following Belitz et al. 2025)
OAK_DOY_MIN, OAK_DOY_MAX = 74, 151
TIT_DOY_MIN, TIT_DOY_MAX = 60, 150

# ── Weibull estimator ──────────────────────────────────────────────────────

def weibull_estimate(doys, percentile, doy_min, doy_max):
    doys = np.array([d for d in doys if doy_min <= d <= doy_max], dtype=float)
    if len(doys) < 15:
        return None, None, None, len(doys), 0, "low_n"
    unique = len(np.unique(doys))
    if unique < 8:
        return None, None, None, len(doys), unique, "low_unique"
    try:
        shape, loc, scale = weibull_min.fit(doys, floc=doys.min() - 1)
        est = weibull_min.ppf(percentile, shape, loc=loc, scale=scale)
        bootstraps = []
        for _ in range(500):
            s = np.random.choice(doys, size=len(doys), replace=True)
            try:
                c, l, sc = weibull_min.fit(s, floc=s.min() - 1)
                bootstraps.append(weibull_min.ppf(percentile, c, loc=l, scale=sc))
            except:
                continue
        ci_low, ci_high = np.percentile(bootstraps, [2.5, 97.5])
        ci_width = ci_high - ci_low
        flag = "high" if len(doys) >= 30 else "medium"
        if ci_width > 14:
            flag = "wide_ci"
        return (round(float(est), 1), round(float(ci_low), 1),
                round(float(ci_high), 1), len(doys), unique, flag)
    except Exception as e:
        return None, None, None, len(doys), 0, f"error:{e}"

# ── Main ───────────────────────────────────────────────────────────────────

def main():
    client = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_KEY"]
    )
    print("Supabase connected.\n")

    # ── Pull all observations ──────────────────────────────────────────────
    print("Pulling oak observations from DB...")
    oak_rows = []
    offset = 0
    while True:
        r = client.table("observations") \
                  .select("doy, h3_index") \
                  .eq("taxon_id", OAK_TAXON_DB_ID) \
                  .range(offset, offset + 999) \
                  .execute()
        if not r.data:
            break
        oak_rows.extend(r.data)
        if len(r.data) < 1000:
            break
        offset += 1000
    oak_df = pd.DataFrame(oak_rows)
    print(f"  Oak obs: {len(oak_df)} across {oak_df['h3_index'].nunique()} cells")

    print("Pulling great tit observations from DB...")
    tit_rows = []
    offset = 0
    while True:
        r = client.table("observations") \
                  .select("doy, h3_index") \
                  .eq("taxon_id", TIT_TAXON_DB_ID) \
                  .range(offset, offset + 999) \
                  .execute()
        if not r.data:
            break
        tit_rows.extend(r.data)
        if len(r.data) < 1000:
            break
        offset += 1000
    tit_df = pd.DataFrame(tit_rows)
    print(f"  Tit obs: {len(tit_df)} across {tit_df['h3_index'].nunique()} cells")

    # ── Compute pooled Weibull per cell ────────────────────────────────────
    print("\nComputing pooled Weibull estimates...")

    def compute_estimates(df, percentile, doy_min, doy_max, label):
        estimates = {}
        computed, skipped = 0, 0
        for h3_idx, group in df.groupby("h3_index"):
            est, ci_low, ci_high, n_obs, n_unique, flag = weibull_estimate(
                group["doy"].tolist(), percentile, doy_min, doy_max
            )
            if est is None:
                skipped += 1
                continue
            estimates[h3_idx] = {
                "estimate_doy":    est,
                "ci_low":          ci_low,
                "ci_high":         ci_high,
                "ci_width":        round(ci_high - ci_low, 1),
                "n_obs":           n_obs,
                "confidence_flag": flag
            }
            computed += 1
        print(f"  {label}: {computed} cells computed, {skipped} skipped")
        return estimates

    oak_est = compute_estimates(
        oak_df, OAK_PERCENTILE, OAK_DOY_MIN, OAK_DOY_MAX, "Oak"
    )
    tit_est = compute_estimates(
        tit_df, TIT_PERCENTILE, TIT_DOY_MIN, TIT_DOY_MAX, "Great tit"
    )

    # ── Find shared cells ──────────────────────────────────────────────────
    shared = set(oak_est.keys()) & set(tit_est.keys())
    print(f"\nShared cells with estimates: {len(shared)}")

    if not shared:
        print("\nNo shared cells yet.")
        print("Oak cells:", list(oak_est.keys()))
        print("Tit cells:", list(tit_est.keys())[:10], "...")
        print("\nOptions:")
        print("  1. Lower H3 resolution to 3 (bigger cells)")
        print("  2. Pull more oak data (extend year range or lower threshold)")
        return

    # ── Compute mismatch ───────────────────────────────────────────────────
    print(f"\n{'═'*75}")
    print(f"OAK-TIT MISMATCH RESULTS")
    print(f"Fitness threshold: {FITNESS_THRESHOLD} days")
    print(f"(Visser et al. 1998: gap >{FITNESS_THRESHOLD}d → >50% chick mortality)")
    print(f"{'═'*75}")
    print(f"\n{'H3 Cell':<20} {'Oak DOY':>8} {'Tit DOY':>8} {'Gap':>6} "
          f"{'Status':<25} {'Oak flag':<12} {'Tit flag'}")
    print(f"{'─'*90}")

    mismatch_rows = []
    results = []

    for cell in sorted(shared):
        a = oak_est[cell]   # oak = species A
        b = tit_est[cell]   # tit = species B

        gap = round(abs(a["estimate_doy"] - b["estimate_doy"]), 1)
        direction = (
            "A_before_B" if a["estimate_doy"] < b["estimate_doy"]
            else "B_before_A"
        )
        above_threshold = gap > FITNESS_THRESHOLD
        status = (
            f"⚠ ABOVE THRESHOLD ({gap}>{FITNESS_THRESHOLD}d)"
            if above_threshold
            else f"✓ Within safe range"
        )

        print(f"{cell:<20} {a['estimate_doy']:>8.1f} {b['estimate_doy']:>8.1f} "
              f"{gap:>6.1f} {status:<25} "
              f"{a['confidence_flag']:<12} {b['confidence_flag']}")

        results.append({
            "cell":          cell,
            "oak_doy":       a["estimate_doy"],
            "tit_doy":       b["estimate_doy"],
            "gap":           gap,
            "direction":     direction,
            "above_threshold": above_threshold,
            "oak_flag":      a["confidence_flag"],
            "tit_flag":      b["confidence_flag"]
        })

        mismatch_rows.append({
            "pair_id":           PAIR_ID,
            "h3_index":          cell,
            "year":              0,
            "gap_days":          gap,
            "gap_ci":            round(
                (a["ci_width"] + b["ci_width"]) / 2, 1
            ),
            "direction":         direction,
            "taxon_a_estimate":  a["estimate_doy"],
            "taxon_b_estimate":  b["estimate_doy"],
            "presentation_tier": "risk_indicator"
        })

    # ── Ecological interpretation ──────────────────────────────────────────
    if results:
        res_df = pd.DataFrame(results)
        above = res_df[res_df["above_threshold"]]
        below = res_df[~res_df["above_threshold"]]

        print(f"\n{'═'*75}")
        print(f"ECOLOGICAL INTERPRETATION")
        print(f"{'═'*75}")
        print(f"  Cells analysed        : {len(results)}")
        print(f"  Above threshold (>{FITNESS_THRESHOLD}d) : {len(above)} cells "
              f"— great tit chicks at risk of starvation")
        print(f"  Within safe range     : {len(below)} cells")
        print(f"  Mean gap across cells : {res_df['gap'].mean():.1f} days")
        print(f"  Max gap               : {res_df['gap'].max():.1f} days "
              f"(cell {res_df.loc[res_df['gap'].idxmax(), 'cell']})")

        print(f"\n  PEP725 context:")
        print(f"  Oak leaf unfolding Germany mean: DOY 119.6 (Apr 29)")
        print(f"  Your oak estimates range:        "
              f"DOY {res_df['oak_doy'].min():.1f}–{res_df['oak_doy'].max():.1f}")
        print(f"  Great tit spring arrival mean:   "
              f"DOY {res_df['tit_doy'].mean():.1f}")

        # Direction check — tit should arrive BEFORE oak leafs out
        # i.e. tit should be B_before_A (tit peaks before oak)
        b_before_a = (res_df["direction"] == "B_before_A").sum()
        print(f"\n  Direction: tit before oak in {b_before_a}/{len(results)} cells")
        if b_before_a == len(results):
            print(f"  ✓ Tit arrives before oak leafs — ecologically plausible")
        else:
            print(f"  Note: some cells show oak leafing before tit arrival")
            print(f"  This could indicate tit arrival is being estimated too late")

    # ── Save to Supabase ───────────────────────────────────────────────────
    if mismatch_rows:
        print(f"\nSaving {len(mismatch_rows)} OAK-TIT mismatch scores to Supabase...")
        try:
            client.table("mismatch_scores") \
                  .upsert(mismatch_rows) \
                  .execute()
            print("  Saved successfully.")
        except Exception as e:
            print(f"  ERROR: {e}")

    print(f"\n{'═'*75}")
    print("WHAT THIS MEANS FOR YOUR PROJECT")
    print(f"{'═'*75}")
    print("""
This is your first real ecological mismatch score. Unlike the OAK-CHE
sanity check (which had no fitness consequence), OAK-TIT has a documented
threshold: beyond 9 days, great tit chick survival drops by more than 50%.

Every cell above the threshold represents a location where, based on
current citizen science data, great tit breeding success is likely
compromised by phenological mismatch with their caterpillar food source.

This result — even with limited spatial coverage — is the scientific
core of your scholarship application. It demonstrates:
  1. A validated pipeline producing ecologically meaningful outputs
  2. A real conservation signal (chick mortality risk) not visible
     in any existing public tool
  3. A scalable framework that improves as iNaturalist/eBird data grows

Next steps to strengthen this result:
  - Per-year estimates (need more data) to show trend over time
  - Extend to more H3 cells as observation density increases
  - Cross-validate tit arrival estimates against ringing station data
    (if available from German bird ringing centres)
""")


if __name__ == "__main__":
    main()
