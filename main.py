"""
Phenological Mismatch Observatory — FastAPI Backend
Run locally: uvicorn main:app --reload
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from dotenv import load_dotenv
import os
import json

load_dotenv()

app = FastAPI(
    title="Phenological Mismatch Observatory API",
    description="Real-time phenological mismatch scores for ecologically dependent species pairs.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten before production
    allow_methods=["GET"],
    allow_headers=["*"],
)


def get_db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


# ── Health ─────────────────────────────────────────────────────────────────


@app.get("/")
def root():
    return {
        "name": "Phenological Mismatch Observatory API",
        "version": "0.1.0",
        "docs": "/docs",
        "status": "live",
    }


@app.get("/health")
def health():
    try:
        db = get_db()
        db.table("taxon_registry").select("id").limit(1).execute()
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")


# ── Species pairs ──────────────────────────────────────────────────────────


@app.get("/species-pairs")
def get_species_pairs():
    """
    Returns all curated species dependency pairs with metadata.
    Each pair includes the ecological dependency type, fitness threshold
    (if documented), and presentation tier.
    """
    db = get_db()
    result = (
        db.table("species_pairs")
        .select(
            "pair_id, dependency_type, has_fitness_threshold, "
            "mismatch_threshold_days, fitness_impact_description, key_citation, "
            "taxon_a_id, taxon_b_id"
        )
        .execute()
    )

    # Enrich with taxon names
    taxa = (
        db.table("taxon_registry")
        .select(
            "id, scientific_name, common_name, phenophase_target, weibull_percentile"
        )
        .execute()
    )
    taxa_map = {t["id"]: t for t in taxa.data}

    pairs = []
    for pair in result.data:
        a = taxa_map.get(pair["taxon_a_id"], {})
        b = taxa_map.get(pair["taxon_b_id"], {})
        pairs.append(
            {
                "pair_id": pair["pair_id"],
                "dependency_type": pair["dependency_type"],
                "has_fitness_threshold": pair["has_fitness_threshold"],
                "mismatch_threshold_days": pair["mismatch_threshold_days"],
                "fitness_impact_description": pair["fitness_impact_description"],
                "key_citation": pair["key_citation"],
                "species_a": {
                    "scientific_name": a.get("scientific_name"),
                    "common_name": a.get("common_name"),
                    "phenophase": a.get("phenophase_target"),
                },
                "species_b": {
                    "scientific_name": b.get("scientific_name"),
                    "common_name": b.get("common_name"),
                    "phenophase": b.get("phenophase_target"),
                },
                "presentation_tier": (
                    "risk_indicator"
                    if pair["has_fitness_threshold"]
                    else "descriptive_only"
                ),
            }
        )

    return {"count": len(pairs), "pairs": pairs}


# ── Peak estimates ─────────────────────────────────────────────────────────


@app.get("/estimates")
def get_estimates(
    taxon_id: int = Query(..., description="taxon_registry id"),
    year: int = Query(None, description="Year (omit for pooled across all years)"),
):
    """
    Returns Weibull phenological peak estimates per H3 cell
    for a given species, optionally filtered by year.
    Year=0 means pooled (all years combined).
    """
    db = get_db()
    q = (
        db.table("peak_estimates")
        .select(
            "h3_index, year, estimate_doy, ci_low, ci_high, "
            "ci_width, n_obs, confidence_flag"
        )
        .eq("taxon_id", taxon_id)
        .neq("confidence_flag", "low_n")
        .neq("confidence_flag", "low_unique")
    )

    if year is not None:
        q = q.eq("year", year)

    result = q.execute()

    if not result.data:
        raise HTTPException(
            status_code=404,
            detail=f"No estimates found for taxon_id={taxon_id}"
            + (f", year={year}" if year else ""),
        )

    return {
        "taxon_id": taxon_id,
        "year": year or "pooled",
        "count": len(result.data),
        "estimates": result.data,
    }


# ── Mismatch scores ────────────────────────────────────────────────────────


@app.get("/mismatch")
def get_mismatch(
    pair_id: str = Query(..., description="e.g. OAK-TIT, CHE-BEE"),
    h3_index: str = Query(None, description="Specific H3 cell (optional)"),
    year: int = Query(None, description="Year (omit for pooled)"),
):
    """
    Returns mismatch gap scores for a species pair.
    Gap is in days. Direction tells you which species peaks first.
    Presentation tier indicates whether gap can be interpreted as
    ecological risk (threshold documented) or descriptive only.
    """
    db = get_db()
    q = (
        db.table("mismatch_scores")
        .select(
            "h3_index, year, gap_days, gap_ci, direction, "
            "taxon_a_estimate, taxon_b_estimate, presentation_tier"
        )
        .eq("pair_id", pair_id)
    )

    if h3_index:
        q = q.eq("h3_index", h3_index)
    if year is not None:
        q = q.eq("year", year)

    result = q.execute()

    if not result.data:
        raise HTTPException(
            status_code=404, detail=f"No mismatch scores found for pair_id={pair_id}"
        )

    # Get pair metadata for threshold context
    pair_meta = (
        db.table("species_pairs")
        .select(
            "has_fitness_threshold, mismatch_threshold_days, fitness_impact_description"
        )
        .eq("pair_id", pair_id)
        .execute()
    )

    meta = pair_meta.data[0] if pair_meta.data else {}

    # Add threshold flag to each score
    threshold = meta.get("mismatch_threshold_days")
    scores = []
    for row in result.data:
        gap = row.get("gap_days")
        above_threshold = gap > threshold if (threshold and gap is not None) else None
        scores.append({**row, "above_threshold": above_threshold})

    return {
        "pair_id": pair_id,
        "year": year or "pooled",
        "has_fitness_threshold": meta.get("has_fitness_threshold"),
        "mismatch_threshold_days": threshold,
        "fitness_impact_description": meta.get("fitness_impact_description"),
        "count": len(scores),
        "scores": scores,
    }


# ── Map endpoint (GeoJSON for Deck.gl) ────────────────────────────────────


@app.get("/map")
def get_map(
    pair_id: str = Query(None, description="Filter to one pair (optional)"),
    year: int = Query(None, description="Year (omit for pooled)"),
):
    """
    Returns GeoJSON FeatureCollection of H3 cells with mismatch scores.
    Suitable for Deck.gl H3HexagonLayer on the frontend.
    Each feature has: h3_index, gap_days, pair_id, direction, confidence.
    """
    db = get_db()
    q = db.table("mismatch_scores").select(
        "h3_index, pair_id, year, gap_days, direction, presentation_tier"
    )

    if pair_id:
        q = q.eq("pair_id", pair_id)
    if year is not None:
        q = q.eq("year", year)

    result = q.execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="No map data found")

    features = []
    for row in result.data:
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "h3_index": row["h3_index"],
                    "pair_id": row["pair_id"],
                    "year": row["year"],
                    "gap_days": row["gap_days"],
                    "direction": row["direction"],
                    "presentation_tier": row["presentation_tier"],
                },
                "geometry": None,  # Deck.gl H3Layer uses h3_index directly
            }
        )

    return {"type": "FeatureCollection", "count": len(features), "features": features}


# ── Trend endpoint ─────────────────────────────────────────────────────────


@app.get("/trend")
def get_trend(
    pair_id: str = Query(..., description="e.g. OAK-TIT"),
    h3_index: str = Query(None, description="Specific H3 cell (optional)"),
):
    """
    Returns OLS trend (days/year) for a species pair.
    Trend is significant only if p < 0.05 AND n_years >= 10.
    """
    db = get_db()
    q = (
        db.table("mismatch_trends")
        .select(
            "pair_id, h3_index, slope_days_per_year, p_value, "
            "r_squared, n_years, significant"
        )
        .eq("pair_id", pair_id)
    )

    if h3_index:
        q = q.eq("h3_index", h3_index)

    result = q.execute()

    if not result.data:
        raise HTTPException(
            status_code=404,
            detail=f"No trend data for pair_id={pair_id}. "
            "Trend computation requires n_years >= 10.",
        )

    return {"pair_id": pair_id, "count": len(result.data), "trends": result.data}


# ── Observations (raw) ─────────────────────────────────────────────────────


@app.get("/observations")
def get_observations(
    taxon_id: int = Query(...),
    h3_index: str = Query(None),
    year: int = Query(None),
    limit: int = Query(500, le=2000),
):
    """
    Returns raw observations for a taxon, optionally filtered by cell/year.
    Useful for the frontend to show raw data alongside estimates.
    """
    db = get_db()
    q = (
        db.table("observations")
        .select("source_id, doy, year, h3_index, lat, lon, phenophase_code, quality")
        .eq("taxon_id", taxon_id)
        .limit(limit)
    )

    if h3_index:
        q = q.eq("h3_index", h3_index)
    if year:
        q = q.eq("year", year)

    result = q.execute()
    return {
        "taxon_id": taxon_id,
        "count": len(result.data),
        "observations": result.data,
    }


# ── Export CSV ─────────────────────────────────────────────────────────────


@app.get("/export/csv")
def export_csv(pair_id: str = Query(...), year: int = Query(None)):
    """
    Returns mismatch scores as CSV text with attribution header.
    """
    from fastapi.responses import PlainTextResponse

    db = get_db()
    q = (
        db.table("mismatch_scores")
        .select(
            "h3_index, year, gap_days, gap_ci, direction, "
            "taxon_a_estimate, taxon_b_estimate"
        )
        .eq("pair_id", pair_id)
    )

    if year:
        q = q.eq("year", year)

    result = q.execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="No data found")

    # Build CSV
    lines = [
        "# Phenological Mismatch Observatory — phenomismatch.org",
        "# Data sources: iNaturalist (CC-BY), eBird (Cornell Lab of "
        "Ornithology), PEP725 (members of the PEP725 project)",
        "# Citation: Templ et al. (2018) Int. J. Biometeorology "
        "doi:10.1007/s00484-018-1512-8",
        "# Method: Weibull-parameterized estimator (Belitz et al. 2020 "
        "Methods Ecol Evol doi:10.1111/2041-210X.13448)",
        "#",
        f"# pair_id: {pair_id}",
        f"# year: {year or 'pooled'}",
        "#",
        "h3_index,year,gap_days,gap_ci,direction,"
        "taxon_a_estimate_doy,taxon_b_estimate_doy",
    ]

    for row in result.data:
        lines.append(
            f"{row['h3_index']},{row['year']},{row['gap_days']},"
            f"{row['gap_ci']},{row['direction']},"
            f"{row['taxon_a_estimate']},{row['taxon_b_estimate']}"
        )

    return PlainTextResponse(
        content="\n".join(lines),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=mismatch_{pair_id}_{year or 'pooled'}.csv"
        },
    )
