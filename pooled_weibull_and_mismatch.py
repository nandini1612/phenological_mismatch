"""
Pooled Weibull estimates + first mismatch scores
Phenological Mismatch Observatory

Instead of per cell per year, computes Weibull per cell
pooled across ALL years. Gives a long-run baseline estimate.
Then computes mismatch scores where both species have estimates
in the same H3 cell.

Run: python pooled_weibull_and_mismatch.py
"""

import pandas as pd
import numpy as np
from scipy.stats import weibull_min
from supabase import create_client
from dotenv import load_dotenv
import os

load_dotenv()

SPECIES = [
    {
        "taxon_db_id": 1,
        "scientific_name": "Quercus robur",
        "weibull_pct": 0.25,
        "doy_min": 74,
        "doy_max": 151,
    },
    {
        "taxon_db_id": 4,
        "scientific_name": "Prunus avium",
        "weibull_pct": 0.50,
        "doy_min": 50,
        "doy_max": 150,
    },
]

PAIR = {
    "pair_id": "OAK-CHE",  # oak leaf vs cherry bloom — same plant tier
    "taxon_a_db_id": 1,  # Quercus robur
    "taxon_b_db_id": 4,  # Prunus avium
    "has_fitness_threshold": False,
    "presentation_tier": "descriptive_only",
    "note": (
        "Pooled baseline: oak leaf unfolding vs cherry full bloom. "
        "These are not a dependency pair — this is a synchrony sanity check. "
        "Cherry should precede oak by ~7 days in Germany."
    ),
}

# ── Weibull ────────────────────────────────────────────────────────────────


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
        return (
            round(float(est), 1),
            round(float(ci_low), 1),
            round(float(ci_high), 1),
            len(doys),
            unique,
            flag,
        )
    except Exception as e:
        return None, None, None, len(doys), 0, f"error:{e}"


# ── Main ───────────────────────────────────────────────────────────────────


def main():
    client = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
    )
    print("Supabase connected.\n")

    pooled_estimates = {}  # taxon_db_id → {h3_index: estimate_dict}

    # ── Step 1: Pooled Weibull per cell ───────────────────────────────────
    print("=" * 58)
    print("STEP 1 — Pooled Weibull estimates (all years combined)")
    print("=" * 58)

    for sp in SPECIES:
        name = sp["scientific_name"]
        db_id = sp["taxon_db_id"]
        pct = sp["weibull_pct"]

        print(f"\n{name}:")

        result = (
            client.table("observations")
            .select("doy, h3_index")
            .eq("taxon_id", db_id)
            .execute()
        )

        if not result.data:
            print(f"  No data in DB")
            continue

        df = pd.DataFrame(result.data)
        print(f"  Total obs: {len(df)} across {df['h3_index'].nunique()} cells")

        pooled_estimates[db_id] = {}
        computed, skipped = 0, 0

        for h3_idx, group in df.groupby("h3_index"):
            doys = group["doy"].tolist()
            est, ci_low, ci_high, n_obs, n_unique, flag = weibull_estimate(
                doys, pct, sp["doy_min"], sp["doy_max"]
            )

            if est is None:
                skipped += 1
                continue

            pooled_estimates[db_id][h3_idx] = {
                "estimate_doy": est,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "ci_width": round(ci_high - ci_low, 1),
                "n_obs": n_obs,
                "n_unique_days": n_unique,
                "confidence_flag": flag,
            }
            computed += 1

        print(f"  Computed: {computed} cells  Skipped: {skipped}")

        if computed > 0:
            print(f"\n  {'H3 cell':<20} {'DOY est':>8} {'CI':>6} {'N':>5} {'Flag'}")
            print(f"  {'-' * 55}")
            for cell, e in sorted(
                pooled_estimates[db_id].items(), key=lambda x: -x[1]["n_obs"]
            )[:10]:
                print(
                    f"  {cell:<20} {e['estimate_doy']:>8.1f} "
                    f"±{e['ci_width'] / 2:>4.1f} "
                    f"{e['n_obs']:>5} {e['confidence_flag']}"
                )

    # ── Step 2: Mismatch where both species overlap ────────────────────────
    print("\n" + "=" * 58)
    print("STEP 2 — Mismatch scores (shared H3 cells)")
    print("=" * 58)

    est_a = pooled_estimates.get(PAIR["taxon_a_db_id"], {})
    est_b = pooled_estimates.get(PAIR["taxon_b_db_id"], {})

    shared_cells = set(est_a.keys()) & set(est_b.keys())
    print(f"\nShared cells with estimates: {len(shared_cells)}")

    if not shared_cells:
        print("\nNo shared cells — both species need estimates in the same cell.")
        print("Options:")
        print("  1. Lower H3 resolution to 3 (bigger cells, more obs per cell)")
        print("  2. Pull more years of iNaturalist data (2005–2009)")
        print("  3. Remove 8-unique-days requirement (risky — lowers quality)")
        return

    mismatch_rows = []
    print(
        f"\n{'H3 cell':<20} {'Oak DOY':>8} {'Cherry DOY':>11} "
        f"{'Gap':>6} {'Direction':<15} {'Flag A':<12} {'Flag B'}"
    )
    print("-" * 85)

    for cell in sorted(shared_cells):
        a = est_a[cell]
        b = est_b[cell]
        gap = round(abs(a["estimate_doy"] - b["estimate_doy"]), 1)
        direction = (
            "A_before_B" if a["estimate_doy"] < b["estimate_doy"] else "B_before_A"
        )
        # Cherry (B) should precede oak (A) — so expect B_before_A
        expected = "B_before_A"
        correct = "✓" if direction == expected else "✗ unexpected"

        print(
            f"{cell:<20} {a['estimate_doy']:>8.1f} {b['estimate_doy']:>11.1f} "
            f"{gap:>6.1f} {direction:<15} "
            f"{a['confidence_flag']:<12} {b['confidence_flag']} {correct}"
        )

        mismatch_rows.append(
            {
                "pair_id": "OAK-CHE",
                "h3_index": cell,
                "year": 0,  # 0 = pooled across all years
                "gap_days": gap,
                "gap_ci": round((a["ci_width"] + b["ci_width"]) / 2, 1),
                "direction": direction,
                "taxon_a_estimate": a["estimate_doy"],
                "taxon_b_estimate": b["estimate_doy"],
                "presentation_tier": PAIR["presentation_tier"],
            }
        )

    # ── Sanity check ───────────────────────────────────────────────────────
    print(f"\n── Sanity check ────────────────────────────────────────")
    print(f"PEP725 ground truth (Germany):")
    print(f"  Quercus robur   BBCH 11: mean DOY 119.6 (Apr 29)")
    print(f"  Prunus avium    BBCH 65: mean DOY 112.8 (Apr 23)")
    print(f"  Expected gap: cherry ~7 days BEFORE oak")
    print(f"  Expected direction: B_before_A (cherry before oak)")

    if mismatch_rows:
        gaps = [r["gap_days"] for r in mismatch_rows]
        directions = [r["direction"] for r in mismatch_rows]
        b_before_a = directions.count("B_before_A")
        print(f"\niNaturalist pooled estimates:")
        print(f"  Mean gap across cells: {np.mean(gaps):.1f} days")
        print(f"  Cells where cherry before oak: {b_before_a}/{len(directions)}")
        if b_before_a == len(directions):
            print(f"  ✓ Direction correct in all cells")
        else:
            print(f"  ✗ Some cells show unexpected direction — check those cells' data")

    # Note: not upserting OAK-CHE to mismatch_scores since it's not
    # a real dependency pair — it's a validation sanity check only.
    # Real mismatch scores (OAK-TIT, CHE-BEE) come after eBird ingestion.

    print(f"\n" + "=" * 58)
    print("Done.")
    print("\nWhat these results mean:")
    print("  - Pooled estimates give you a baseline per cell")
    print("  - Cherry preceding oak confirms phenological calendar")
    print("    is working correctly")
    print("  - Real mismatch pairs (OAK-TIT, CHE-BEE) need eBird data")
    print("  - Next step: ingest Parus major from eBird for Germany")


if __name__ == "__main__":
    main()
