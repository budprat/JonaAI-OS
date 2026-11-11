# ABOUTME: Collection-catalog honesty — recommend_collection discloses which collections
# ABOUTME: are verified to load real pixels (sentinel-2-l2a) vs catalog-only listings.
import asyncio
import json

from openeo_ai.tools.discovery_tools import create_discovery_tools


def _recommend(question):
    handler = create_discovery_tools()["recommend_collection"]
    out = asyncio.run(handler({"research_question": question}))
    return json.loads(out["content"][0]["text"])


def test_recommendations_disclose_pixel_loading_status():
    result = _recommend("burn severity after a wildfire")
    recs = {r["collection_id"]: r for r in result["recommended_collections"]}
    assert "sentinel-2-l2a" in recs and "landsat-c2-l2" in recs
    assert recs["sentinel-2-l2a"]["pixel_loading"].startswith("verified")
    assert recs["landsat-c2-l2"]["pixel_loading"].startswith("unverified")


def test_no_match_listing_discloses_pixel_loading_status():
    result = _recommend("zzz qqq nothing matches this")
    cols = {c["id"]: c for c in result["available_collections"]}
    assert cols["sentinel-2-l2a"]["pixel_loading"].startswith("verified")
    assert any(c["pixel_loading"].startswith("unverified") for c in cols.values())
