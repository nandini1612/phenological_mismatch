"""
eBird ingestion — Parus major (Great tit), Germany, 2010–2022
Phenological Mismatch Observatory

Reads the EBD flat file, filters to complete checklists,
extracts first-of-year arrival DOY per H3 cell per year,
and upserts into Supabase observations table.

Run: python ingest_ebird_greattit.py

Requires: .env with SUPABASE_URL and SUPABASE_SERVICE_KEY
"""

import pandas as pd
import numpy as np
import h3
import os
from datetime import datetime
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────

# Update these paths to match your exact filenames
EBD_FOLDER = r".\ebd_DE_gretit1_201001_202212_unv_smp_relJun-2026"

# Find the two main files automatically
import glob

all_txt = glob.glob(os.path.join(EBD_FOLDER, "ebd_DE_gretit1*.txt"))
# Observations file is the larger one (~47MB), sampling is ~97MB
# Actually sampling event file has "smp" in second part — let's detect
OBS_FILE = None
SAMPLING_FILE = None
for f in all_txt:
    size = os.path.getsize(f)
    if size > 90_000_000:  # >90MB = sampling events
        SAMPLING_FILE = f
    elif size > 40_000_000:  # >40MB = observations
        OBS_FILE = f

if not OBS_FILE or not SAMPLING_FILE:
    # Fallback: let user set manually
    print("Auto-detect failed. Files found:")
    for f in all_txt:
        print(f"  {os.path.basename(f)}  ({os.path.getsize(f) // 1024} KB)")
    raise FileNotFoundError(
        "Could not auto-detect obs and sampling files. "
        "Set OBS_FILE and SAMPLING_FILE manually."
    )

print(f"Observations : {os.path.basename(OBS_FILE)}")
print(f"Sampling     : {os.path.basename(SAMPLING_FILE)}")

TAXON_DB_ID = 3  # Parus major in taxon_registry
H3_RESOLUTION = 4
YEARS = range(2010, 2023)

# Spring window for great tit arrival in Germany
# Great tit is resident but breeding activity peaks Feb–May
# We want first-of-year detection during breeding season
SPRING_MONTHS = {3, 4, 5}  # March, April, May

# ── Load sampling events (complete checklists only) ────────────────────────

print("\nLoading sampling events...")
smp = pd.read_csv(
    SAMPLING_FILE,
    sep="\t",
    usecols=[
        "SAMPLING EVENT IDENTIFIER",
        "ALL SPECIES REPORTED",
        "PROTOCOL CODE",
        "OBSERVATION DATE",
        "LATITUDE",
        "LONGITUDE",
    ],
    on_bad_lines="skip",
)

print(f"  Total sampling events: {len(smp):,}")

# Filter to complete checklists only
# ALL SPECIES REPORTED = 1 means observer recorded all species seen
# Remove the APPROVED condition — sampling file doesn't have it
complete = smp[smp["ALL SPECIES REPORTED"] == 1]["SAMPLING EVENT IDENTIFIER"].tolist()

complete_set = set(complete)
print(f"  Complete checklists  : {len(complete_set):,}")
del smp  # free memory

# ── Load observations ──────────────────────────────────────────────────────

print("\nLoading observations...")
obs = pd.read_csv(
    OBS_FILE,
    sep="\t",
    usecols=[
        "GLOBAL UNIQUE IDENTIFIER",
        "SCIENTIFIC NAME",
        "OBSERVATION DATE",
        "LATITUDE",
        "LONGITUDE",
        "SAMPLING EVENT IDENTIFIER",
        "ALL SPECIES REPORTED",
        "APPROVED",
        "OBSERVATION COUNT",
    ],
    on_bad_lines="skip",
)

print(f"  Total observations: {len(obs):,}")

# Filter to Parus major only (should already be filtered but confirm)
obs = obs[obs["SCIENTIFIC NAME"] == "Parus major"].copy()
print(f"  Parus major obs   : {len(obs):,}")

# Filter to complete checklists
obs = obs[obs["SAMPLING EVENT IDENTIFIER"].isin(complete_set)].copy()
print(f"  On complete lists : {len(obs):,}")

# Filter approved only
obs = obs[obs["APPROVED"] == 1].copy()
print(f"  Approved          : {len(obs):,}")

# ── Parse dates ───────────────────────────────────────────────────────────


def parse_doy(date_str):
    try:
        dt = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        return dt.timetuple().tm_yday, dt.year, dt.month
    except:
        return None, None, None


obs["doy"], obs["year"], obs["month"] = zip(*obs["OBSERVATION DATE"].map(parse_doy))

# Filter to spring months only
obs = obs[obs["month"].isin(SPRING_MONTHS)].copy()
print(f"  Spring obs (Mar–May): {len(obs):,}")

# Filter to year range
obs = obs[obs["year"].isin(YEARS)].copy()
print(f"  In year range       : {len(obs):,}")

# Drop rows with missing coordinates
obs = obs.dropna(subset=["LATITUDE", "LONGITUDE", "doy", "year"])
print(f"  With valid coords   : {len(obs):,}")

# ── Add H3 index ──────────────────────────────────────────────────────────

print("\nAdding H3 indices...")
obs["h3_index"] = obs.apply(
    lambda row: h3.latlng_to_cell(row["LATITUDE"], row["LONGITUDE"], H3_RESOLUTION),
    axis=1,
)
print(f"  Unique H3 cells: {obs['h3_index'].nunique()}")

# ── Summary before upsert ─────────────────────────────────────────────────

print("\n── Per-year counts ─────────────────────────────────────────────────")
year_summary = (
    obs.groupby("year")
    .agg(
        n_obs=("doy", "count"),
        mean_doy=("doy", "mean"),
        min_doy=("doy", "min"),
        n_cells=("h3_index", "nunique"),
    )
    .round(1)
)
print(year_summary.to_string())

# Sanity check — great tit spring DOY in Germany should be 60–150
mean_doy = obs["doy"].mean()
print(f"\nOverall DOY mean: {mean_doy:.1f}")
if mean_doy < 60 or mean_doy > 150:
    print("  WARNING: DOY mean outside expected range for spring great tit")
else:
    print("  DOY mean looks plausible for spring Parus major Germany")

# Lat range check
print(f"Lat range: {obs['LATITUDE'].min():.2f}–{obs['LATITUDE'].max():.2f}")
if obs["LATITUDE"].min() < 47 or obs["LATITUDE"].max() > 55.5:
    print("  WARNING: observations outside Germany lat range")

# ── Save to CSV (checkpoint) ──────────────────────────────────────────────

OUTPUT_CSV = "greattit_germany_spring.csv"
obs_out = obs[
    [
        "GLOBAL UNIQUE IDENTIFIER",
        "doy",
        "year",
        "month",
        "LATITUDE",
        "LONGITUDE",
        "h3_index",
    ]
].rename(
    columns={
        "GLOBAL UNIQUE IDENTIFIER": "source_id",
        "LATITUDE": "lat",
        "LONGITUDE": "lon",
    }
)
obs_out.to_csv(OUTPUT_CSV, index=False)
print(f"\nSaved checkpoint: {OUTPUT_CSV} ({len(obs_out):,} rows)")

# ── Upsert to Supabase ────────────────────────────────────────────────────

print("\nConnecting to Supabase...")
client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

rows = []
for _, row in obs_out.iterrows():
    rows.append(
        {
            "source": "ebird",
            "source_id": str(row["source_id"]),
            "taxon_id": TAXON_DB_ID,
            "scientific_name": "Parus major",
            "doy": int(row["doy"]),
            "year": int(row["year"]),
            "h3_index": row["h3_index"],
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
            "phenophase_code": "spring_detection",
            "quality": "research",
        }
    )

print(f"Upserting {len(rows):,} rows in batches of 500...")
inserted, errors = 0, 0

for i in range(0, len(rows), 500):
    batch = rows[i : i + 500]
    try:
        client.table("observations").upsert(
            batch, on_conflict="source,source_id"
        ).execute()
        inserted += len(batch)
        if i % 5000 == 0:
            print(f"  {inserted:,} inserted...")
    except Exception as e:
        print(f"  ERROR batch {i}: {e}")
        errors += len(batch)

print(f"\nDone: {inserted:,} inserted, {errors:,} errors")

# ── Verify ─────────────────────────────────────────────────────────────────

result = (
    client.table("observations")
    .select("year, h3_index, doy")
    .eq("taxon_id", TAXON_DB_ID)
    .execute()
)

if result.data:
    df_check = pd.DataFrame(result.data)
    print(f"\nDB verification:")
    print(f"  Rows in DB    : {len(df_check):,}")
    print(f"  Year range    : {df_check['year'].min()}–{df_check['year'].max()}")
    print(f"  H3 cells      : {df_check['h3_index'].nunique()}")
    print(f"  DOY mean      : {df_check['doy'].mean():.1f}")

    # How many cells have n>=15 for Weibull?
    cell_counts = df_check.groupby(["h3_index", "year"]).size()
    qualifying = (cell_counts >= 15).sum()
    print(f"  Cell-years n≥15: {qualifying}")
    pooled = df_check.groupby("h3_index").size()
    print(f"  Cells n≥15 pooled: {(pooled >= 15).sum()}")
