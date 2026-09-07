"""
H3 indexing + Supabase upsert
Phenological Mismatch Observatory

Adds H3 resolution-4 index to iNaturalist observations
and upserts them into the Supabase observations table.

Run: python h3_and_upsert.py

Requires:
    pip install h3 supabase python-dotenv
    .env file with SUPABASE_URL and SUPABASE_SERVICE_KEY
"""

import pandas as pd
import h3
import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────
H3_RESOLUTION = 4

# Maps CSV → (taxon_id in DB, scientific_name, phenophase_code)
# taxon_db_id must match id column in taxon_registry
DATASETS = [
    {
        "csv": "oak_germany_annotated.csv",
        "taxon_db_id": 1,
        "scientific_name": "Quercus robur",
        "phenophase_code": "leafing",
        "source": "inaturalist",
    },
    {
        "csv": "cherry_germany_spring.csv",
        "taxon_db_id": 4,
        "scientific_name": "Prunus avium",
        "phenophase_code": "flowering",
        "source": "inaturalist",
    },
]


# ── Connect ────────────────────────────────────────────────────────────────
def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise EnvironmentError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be in .env file"
        )
    return create_client(url, key)


# ── H3 indexing ────────────────────────────────────────────────────────────
def add_h3_index(df, resolution=4):
    df = df.copy()
    df["h3_index"] = df.apply(
        lambda row: h3.latlng_to_cell(row["lat"], row["lon"], resolution), axis=1
    )
    return df


# ── Build rows ─────────────────────────────────────────────────────────────
def build_rows(df, dataset):
    rows = []
    for _, obs in df.iterrows():
        rows.append(
            {
                "source": dataset["source"],
                "source_id": str(obs["source_id"]),
                "taxon_id": dataset["taxon_db_id"],
                "scientific_name": dataset["scientific_name"],
                "doy": int(obs["doy"]),
                "year": int(obs["year"]),
                "h3_index": obs["h3_index"],
                "lat": float(obs["lat"]),
                "lon": float(obs["lon"]),
                "phenophase_code": dataset["phenophase_code"],
                "quality": "research",
            }
        )
    return rows


# ── Upsert in batches ──────────────────────────────────────────────────────
def upsert_batch(client, rows, batch_size=200):
    inserted, errors = 0, 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        try:
            client.table("observations").upsert(
                batch, on_conflict="source,source_id"
            ).execute()
            inserted += len(batch)
            print(f"    Upserted rows {i + 1}–{min(i + batch_size, len(rows))}")
        except Exception as e:
            print(f"    ERROR on batch {i}–{i + batch_size}: {e}")
            errors += len(batch)
    return {"inserted": inserted, "errors": errors}


# ── Verify ─────────────────────────────────────────────────────────────────
def verify(client, taxon_db_id, scientific_name):
    result = (
        client.table("observations")
        .select("year, h3_index, doy")
        .eq("taxon_id", taxon_db_id)
        .execute()
    )
    rows = result.data
    if not rows:
        print(f"    WARNING: no rows in DB for {scientific_name}")
        return
    df = pd.DataFrame(rows)
    print(f"    DB rows   : {len(df)}")
    print(f"    Year range: {df['year'].min()}–{df['year'].max()}")
    print(f"    H3 cells  : {df['h3_index'].nunique()}")
    print(f"    DOY mean  : {df['doy'].mean():.1f}")


# ── H3 summary ─────────────────────────────────────────────────────────────
def h3_summary(df, scientific_name):
    summary = (
        df.groupby(["h3_index", "year"])
        .agg(n_obs=("doy", "count"), mean_doy=("doy", "mean"))
        .reset_index()
    )
    cell_years_15 = summary[summary["n_obs"] >= 15]
    print(f"\n  H3 summary for {scientific_name}:")
    print(f"    Total cells          : {summary['h3_index'].nunique()}")
    print(f"    Cell-years with n≥15 : {len(cell_years_15)}")
    print(f"    (Weibull needs n≥15 on 8+ unique days)")

    top = (
        summary.groupby("h3_index")["n_obs"].sum().sort_values(ascending=False).head(5)
    )
    print(f"\n    Top 5 cells by total obs:")
    for cell, n in top.items():
        lat, lon = h3.cell_to_latlng(cell)
        print(f"      {cell}  ({lat:.2f}N, {lon:.2f}E)  {n} obs")


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    print("=" * 58)
    print("H3 indexing + Supabase upsert")
    print(f"H3 resolution: {H3_RESOLUTION} (~50km cells)")
    print("=" * 58)

    client = get_client()
    print("Supabase connected.\n")

    for dataset in DATASETS:
        name = dataset["scientific_name"]
        csv = dataset["csv"]
        print(f"── {name} " + "─" * (42 - len(name)))

        if not os.path.exists(csv):
            print(f"  SKIP: {csv} not found\n")
            continue

        df = pd.read_csv(csv)
        df = add_h3_index(df, H3_RESOLUTION)
        print(f"  Loaded    : {len(df)} obs from {csv}")
        print(f"  H3 cells  : {df['h3_index'].nunique()} unique cells")

        h3_summary(df, name)

        rows = build_rows(df, dataset)
        print(f"\n  Upserting {len(rows)} rows...")
        result = upsert_batch(client, rows)
        print(f"  Done: {result['inserted']} inserted, {result['errors']} errors")

        print(f"  Verifying...")
        verify(client, dataset["taxon_db_id"], name)
        print()

    print("=" * 58)
    print("Complete. Next: run weibull_per_cell.py")


if __name__ == "__main__":
    main()
