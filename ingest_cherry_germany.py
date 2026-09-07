"""
iNaturalist ingestion — Prunus avium, Germany, spring window only
Phenological Mismatch Observatory

Run: python ingest_cherry_germany.py
Output: cherry_germany_spring.csv
"""

import requests
import pandas as pd
import time
from datetime import datetime

TAXON_ID = 61964  # Prunus avium — verify before running
PLACE_ID = 7207  # Germany — verified 2025-05-21
YEARS = range(2015, 2023)
OUTPUT = "cherry_germany_spring.csv"


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


def verify_taxon(taxon_id):
    r = requests.get(f"https://api.inaturalist.org/v1/taxa/{taxon_id}")
    name = r.json()["results"][0]["name"]
    print(f"Taxon check: {taxon_id} = {name}")
    assert name == "Prunus avium", (
        f"WRONG TAXON: expected Prunus avium, got {name}. Find correct ID first."
    )


def fetch_spring_cherry(year):
    obs_out, page, total = [], 1, None
    while True:
        r = requests.get(
            "https://api.inaturalist.org/v1/observations",
            params={
                "taxon_id": TAXON_ID,
                "place_id": PLACE_ID,
                "quality_grade": "research",
                "d1": f"{year}-03-01",  # March 1
                "d2": f"{year}-05-15",  # May 15
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
            # PEP725 cherry BBCH 65 bounds: 50–150
            if not (50 <= doy <= 150):
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


def main():
    print("=" * 55)
    print("iNaturalist ingestion — Prunus avium, Germany")
    print(f"Taxon ID : {TAXON_ID}  |  Place ID : {PLACE_ID}")
    print(f"Years    : {min(YEARS)}–{max(YEARS)}")
    print("=" * 55)

    verify_taxon(TAXON_ID)

    all_obs = []
    for year in YEARS:
        obs, total = fetch_spring_cherry(year)
        print(f"  {year}: {len(obs)} spring obs (of {total} in window)")
        all_obs.extend(obs)

    if not all_obs:
        print("No data — check taxon_id and place_id.")
        return

    df = pd.DataFrame(all_obs)
    df.to_csv(OUTPUT, index=False)

    print(f"\nSaved: {len(df)} obs → {OUTPUT}")
    print(f"DOY mean : {df['doy'].mean():.1f}  std: {df['doy'].std():.1f}")
    print(f"Lat range: {df['lat'].min():.2f}–{df['lat'].max():.2f}")

    # Sanity check — cherry bloom Germany should be ~100–115
    mean_doy = df["doy"].mean()
    if mean_doy < 85 or mean_doy > 135:
        print(f"\n  WARNING: mean DOY {mean_doy:.1f} outside expected range")
        print("  (expected ~100–115 for Prunus avium Germany spring obs)")
    else:
        print(f"\n  DOY mean looks plausible for cherry bloom in Germany.")

    print("\nPer-year:")
    print(df.groupby("year")["doy"].agg(["count", "mean", "std"]).round(1))


if __name__ == "__main__":
    main()
