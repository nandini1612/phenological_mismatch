"""
iNaturalist ingestion — Quercus robur, Germany, 2015–2022
Phenological Mismatch Observatory

Run: python ingest_oak_germany.py
Output: oak_germany_raw.csv + a summary printed to console

Dependencies: pip install requests pandas h3
"""

# import requests
# import pandas as pd
# import time
# import json
# import os
# from datetime import datetime

# # ── Config ────────────────────────────────────────────────────────────────────

# TAXON_ID = 56133  # Quercus robur — verified
# PLACE_ID = 7207  # Germany — verified 2025-05-21 (NOT 97391 which is Europe)
# YEARS = range(2015, 2023)
# OUTPUT_CSV = "oak_germany_raw.csv"
# BASE_URL = "https://api.inaturalist.org/v1/observations"

# # ── Helpers ───────────────────────────────────────────────────────────────────


# def doy_from_date(date_str: str) -> int | None:
#     """Convert 'YYYY-MM-DD' to day-of-year. Returns None if unparseable."""
#     try:
#         dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
#         return dt.timetuple().tm_yday
#     except Exception:
#         return None


# def parse_location(location_str: str) -> tuple[float | None, float | None]:
#     """Parse 'lat,lon' string into (lat, lon) floats."""
#     try:
#         lat, lon = location_str.split(",")
#         return float(lat), float(lon)
#     except Exception:
#         return None, None


# def fetch_year(taxon_id: int, place_id: int, year: int) -> list[dict]:
#     """
#     Fetch all research-grade observations for one species, place, year.
#     Paginates automatically. Sleeps 0.7s between pages to respect rate limit.
#     """
#     observations = []
#     page = 1
#     total = None

#     print(f"  Fetching {year}...", end=" ", flush=True)

#     while True:
#         params = {
#             "taxon_id": taxon_id,
#             "place_id": place_id,
#             "quality_grade": "research",
#             "year": year,
#             "per_page": 200,
#             "page": page,
#             "fields": "id,observed_on,location,taxon,annotations",
#         }

#         try:
#             r = requests.get(BASE_URL, params=params, timeout=30)
#             r.raise_for_status()
#             data = r.json()
#         except requests.exceptions.RequestException as e:
#             print(f"\n  ERROR on page {page}: {e}")
#             time.sleep(5)
#             continue

#         if total is None:
#             total = data.get("total_results", 0)

#         results = data.get("results", [])
#         if not results:
#             break

#         for obs in results:
#             date_str = obs.get("observed_on", "")
#             doy = doy_from_date(date_str)
#             loc_str = obs.get("location", "")
#             lat, lon = parse_location(loc_str)

#             # Only keep obs with a valid date and location
#             if doy is None or lat is None:
#                 continue

#             observations.append(
#                 {
#                     "source_id": obs.get("id"),
#                     "date": date_str[:10],
#                     "year": year,
#                     "doy": doy,
#                     "lat": lat,
#                     "lon": lon,
#                 }
#             )

#         observations_so_far = len(observations)
#         if observations_so_far >= total:
#             break

#         page += 1
#         time.sleep(0.7)  # 100 req/min limit — 0.7s gives comfortable headroom

#     print(f"{len(observations)} valid obs (of {total} total)")
#     return observations


# # ── Main ──────────────────────────────────────────────────────────────────────


# def main():
#     print("=" * 55)
#     print("iNaturalist ingestion — Quercus robur, Germany")
#     print(f"Taxon ID : {TAXON_ID}  |  Place ID : {PLACE_ID}")
#     print(f"Years    : {min(YEARS)}–{max(YEARS)}")
#     print("=" * 55)

#     all_obs = []

#     for year in YEARS:
#         year_obs = fetch_year(TAXON_ID, PLACE_ID, year)
#         all_obs.extend(year_obs)

#     if not all_obs:
#         print("\nNo observations fetched. Check taxon_id and place_id.")
#         return

#     df = pd.DataFrame(all_obs)

#     # ── Sanity checks ──────────────────────────────────────────────────────
#     print("\n── Summary ─────────────────────────────────────────────")
#     print(f"Total observations : {len(df)}")
#     print(f"Year range         : {df['year'].min()}–{df['year'].max()}")
#     print(f"DOY range          : {df['doy'].min()}–{df['doy'].max()}")
#     print(f"DOY mean           : {df['doy'].mean():.1f}")
#     print(f"Lat range          : {df['lat'].min():.2f}–{df['lat'].max():.2f}")
#     print(f"Lon range          : {df['lon'].min():.2f}–{df['lon'].max():.2f}")

#     # Flag if DOY mean is wildly off — oak leaf unfolding Germany ~110–130
#     mean_doy = df["doy"].mean()
#     if mean_doy < 80 or mean_doy > 200:
#         print(f"\n  WARNING: mean DOY {mean_doy:.1f} is outside expected range")
#         print("  (expected ~110–130 for Quercus robur Germany)")
#         print("  Check place_id and taxon_id are correct.")
#     else:
#         print(f"\n  DOY mean looks plausible for oak leafing in Germany.")

#     # Flag if lat range looks wrong for Germany (should be ~47–55°N)
#     if df["lat"].min() < 45 or df["lat"].max() > 57:
#         print(f"\n  WARNING: lat range {df['lat'].min():.2f}–{df['lat'].max():.2f}")
#         print("  suggests observations outside Germany. Check place_id.")

#     # ── Save ──────────────────────────────────────────────────────────────
#     df.to_csv(OUTPUT_CSV, index=False)
#     print(f"\nSaved to {OUTPUT_CSV}")
#     print(f"Columns: {list(df.columns)}")

#     # ── Per-year summary ──────────────────────────────────────────────────
#     print("\n── Per-year observation counts ─────────────────────────")
#     print(df.groupby("year")["doy"].agg(["count", "mean", "std"]).round(1).to_string())


# if __name__ == "__main__":
#     main()

# ingest_oak_annotated.py
import requests
import pandas as pd
import time
from datetime import datetime

TAXON_ID = 56133
PLACE_ID = 7207
YEARS = range(2015, 2023)
OUTPUT = "oak_germany_annotated_1.csv"

# iNaturalist Plant Phenology annotation IDs
# term_id=12 = Plant Phenology
# value_id=13 = Flower Budding
# value_id=14 = Flowering
# value_id=15 = Fruiting
# value_id=16 = No Evidence of Flowering
# For leaf phenology:
# term_id=9  = Plant Life Stage (some observers use this instead)
# The leaf annotation we want is actually "Breaking leaf buds" but
# iNaturalist doesn't have a standardised leaf-out annotation for trees.
# Best proxy: filter to April 1 – May 31 only (DOY 91–151) AND
# exclude observations with fruiting/flowering annotations (wrong season)


def doy_from_date(s):
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").timetuple().tm_yday
    except:
        return None


def parse_loc(s):
    try:
        lat, lon = s.split(",")
        return float(lat), float(lon)
    except:
        return None, None


def fetch_spring_oak(year):
    obs_out, page, total = [], 1, None
    while True:
        r = requests.get(
            "https://api.inaturalist.org/v1/observations",
            params={
                "taxon_id": TAXON_ID,
                "place_id": PLACE_ID,
                "quality_grade": "research",
                "d1": f"{year}-03-15",  # mid-March
                "d2": f"{year}-05-31",  # end of May
                "per_page": 200,
                "page": page,
                "order_by": "observed_on",
            },
            timeout=30,
        )
        data = r.json()
        if total is None:
            total = data.get("total_results", 0)
        results = data.get("results", [])
        if not results:
            break
        for obs in results:
            doy = doy_from_date(obs.get("observed_on", ""))
            lat, lon = parse_loc(obs.get("location", ""))
            if not doy or not lat:
                continue
            # Skip if DOY outside realistic leaf-out window for Germany
            if not (74 <= doy <= 151):  # Mar 15 – May 31
                continue
            obs_out.append(
                {
                    "source_id": obs["id"],
                    "date": obs.get("observed_on", "")[:10],
                    "year": year,
                    "doy": doy,
                    "lat": lat,
                    "lon": lon,
                }
            )
        if len(obs_out) >= total:
            break
        page += 1
        time.sleep(0.7)
    return obs_out, total


all_obs = []
print("=" * 55)
print(f"Spring-only ingestion | Quercus robur | Germany")
print("=" * 55)

for year in YEARS:
    obs, total = fetch_spring_oak(year)
    print(f"  {year}: {len(obs)} spring obs (of {total} total in window)")
    all_obs.extend(obs)

df = pd.DataFrame(all_obs)
if df.empty:
    print("No data.")
else:
    df.to_csv(OUTPUT, index=False)
    print(f"\nSaved: {len(df)} obs to {OUTPUT}")
    print(f"DOY mean: {df['doy'].mean():.1f}  std: {df['doy'].std():.1f}")
    print(f"Lat range: {df['lat'].min():.2f}–{df['lat'].max():.2f}")
    print("\nPer-year:")
    print(df.groupby("year")["doy"].agg(["count", "mean", "std"]).round(1))
