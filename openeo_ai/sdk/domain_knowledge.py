# ABOUTME: Consolidated domain knowledge for Earth Observation analysis.
# Maps 16 EO domains to indices, collections, colormaps, thresholds, and tips.
# Used by _build_specialist_prompt() to inject domain context into specialist agents.

"""
Domain knowledge for Earth Observation analysis.

Consolidates domain-specific knowledge from discovery_tools.py (USE_CASE_MAPPING),
client.py (specialist routing), and viz_extractor.py (INDEX_COLORMAP_MAP) into a
single structured dict that can be injected into specialist prompts.

Thresholds are sourced from peer-reviewed literature:
  - NDVI: USGS Remote Sensing Phenology, ResearchGate classification tables
  - NDWI/MNDWI: McFeeters 1996, Xu 2006
  - dNBR: USGS/Key & Benson 2006 (RMRS-GTR-164)
  - NDBI: Zha et al. 2003 (92.6% accuracy)
  - NDSI: Dozier 1989 (0.4 global standard)
  - NDMI: EOS/Agricolus water stress classification
  - BSI: ClimateEngine, MDPI studies
  - NDCI: ScienceDirect algal bloom mapping (R^2=0.84)
"""

from typing import Optional


# 16 supported EO analysis domains with comprehensive metadata.
# Sources: USE_CASE_MAPPING (discovery_tools.py), INDEX_COLORMAP_MAP (viz_extractor.py),
# SYSTEM_PROMPT (client.py), peer-reviewed EO literature, and OpenEO processing best practices.
DOMAIN_CONTEXTS = {
    "vegetation": {
        "indices": [
            "NDVI", "EVI", "SAVI", "MSAVI", "LAI", "GNDVI", "ARVI", "NDRE", "CHL_RE",
        ],
        "collections": ["sentinel-2-l2a"],
        "default_index": "NDVI",
        "default_colormap": "ndvi",
        "thresholds": {
            "NDVI": {
                "bare_soil_rock": "< 0.1",
                "sparse_vegetation": "0.1 - 0.3",
                "moderate_vegetation": "0.3 - 0.6",
                "dense_vegetation": "0.6 - 0.9",
            },
            "EVI": {
                "poor_growth": "< 0.15",
                "healthy_vegetation": "0.2 - 0.8",
            },
        },
        "value_ranges": {
            "ndvi": (-1.0, 1.0), "evi": (-1.0, 1.0), "savi": (-1.0, 1.0),
            "gndvi": (-1.0, 1.0), "ndre": (-1.0, 1.0), "lai": (0.0, 8.0),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.5, "max_cloud_cover": 0.3,
            "expected_mean_range": {"ndvi": (0.1, 0.8), "evi": (0.1, 0.6)},
        },
        "tips": (
            "Use cloud-free imagery during growing season. "
            "Sentinel-2 10m NIR band gives best detail. "
            "Apply cloud masking (SCL 8-11) before computing indices. "
            "NDVI saturates in dense canopies — use EVI instead for high-biomass. "
            "MSAVI is better than NDVI in areas with exposed soil background."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "spectral_index_calculate",
            "viz_show_ndvi_map",
        ],
        "requirements": None,
    },
    "water": {
        "indices": ["NDWI", "MNDWI", "AWEI"],
        "collections": ["sentinel-2-l2a"],
        "default_index": "NDWI",
        "default_colormap": "coolwarm",
        "thresholds": {
            "NDWI": {
                "land": "< 0.0",
                "water_boundary": "~0.0",
                "water": "> 0.0",
                "deep_water": "> 0.5",
            },
            "MNDWI": {
                "non_water": "< 0.0",
                "water": "> 0.0",
            },
        },
        "value_ranges": {
            "ndwi": (-1.0, 1.0), "mndwi": (-1.0, 1.0),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.4, "max_cloud_cover": 0.3,
            "expected_mean_range": {"ndwi": (-0.5, 0.5), "mndwi": (-0.5, 0.5)},
        },
        "tips": (
            "MNDWI better for urban areas (reduces built-up false positives). "
            "AWEI (Automated Water Extraction Index) provides higher accuracy than NDWI. "
            "Use SCL band to mask clouds. "
            "For change detection, compare same season across years. "
            "Optimal NDWI threshold ~0.3 for robust water extraction."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "spectral_index_calculate",
            "viz_show_map",
        ],
        "requirements": None,
    },
    "fire": {
        "indices": ["NBR", "dNBR", "BAI"],
        "collections": ["sentinel-2-l2a", "landsat-c2-l2"],
        "default_index": "dNBR",
        "default_colormap": "inferno",
        "thresholds": {
            "dNBR": {
                "enhanced_regrowth": "< -0.25",
                "low_regrowth": "-0.25 - -0.1",
                "unburned": "-0.1 - 0.1",
                "low_severity": "0.1 - 0.27",
                "moderate_low_severity": "0.27 - 0.44",
                "moderate_high_severity": "0.44 - 0.66",
                "high_severity": "> 0.66",
            },
            "NBR": {
                "dense_healthy_vegetation": "> 0.3",
                "sparse_or_stressed_vegetation": "0.1 - 0.3",
                "bare_or_very_low_cover": "0 - 0.1",
                "likely_burned_charred_or_bare": "< 0",
            },
        },
        "value_ranges": {
            "nbr": (-1.0, 1.0), "dnbr": (-1.0, 1.3),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.5, "max_cloud_cover": 0.4,
            "expected_mean_range": {"dnbr": (-0.1, 0.8)},
        },
        "tips": (
            "Compare pre-fire and post-fire NBR. dNBR > 0.27 indicates moderate+ severity. "
            "Negative dNBR values indicate vegetation recovery/regrowth. "
            "Use SWIR bands (swir16, swir22) for burn detection. "
            "Ensure pre-fire and post-fire images are from similar seasons. "
            "BAI (Burn Area Index) highlights recently burned areas using red + NIR."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "spectral_index_calculate",
            "viz_show_map",
        ],
        "requirements": "TWO temporal windows (pre-fire + post-fire)",
    },
    "urban": {
        "indices": ["NDBI", "BSI", "UI"],
        "collections": ["sentinel-2-l2a", "landsat-c2-l2"],
        "default_index": "NDBI",
        "default_colormap": "coolwarm",
        "thresholds": {
            "NDBI": {
                "non_urban": "< 0.0",
                "sparse_built": "0.0 - 0.1",
                "built_up": "> 0.1",
            },
        },
        "value_ranges": {
            "ndbi": (-1.0, 1.0), "bsi": (-1.0, 1.0),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.5, "max_cloud_cover": 0.3,
            "expected_mean_range": {"ndbi": (-0.3, 0.3)},
        },
        "tips": (
            "NDBI highlights built-up areas (92.6% accuracy per Zha et al. 2003). "
            "NDBI cannot distinguish urban from bare land — combine with NDVI. "
            "Use Landsat thermal band for urban heat island studies. "
            "Multi-temporal analysis reveals urban sprawl patterns. "
            "UI (Urban Index) combines SWIR2 and NIR for better discrimination."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "spectral_index_calculate",
            "viz_show_map",
        ],
        "requirements": None,
    },
    "agriculture": {
        "indices": ["NDVI", "EVI", "SAVI", "LAI", "NDRE", "CHL_RE"],
        "collections": ["sentinel-2-l2a"],
        "default_index": "NDVI",
        "default_colormap": "ndvi",
        "thresholds": {
            "NDVI": {
                "bare_field": "< 0.15",
                "early_growth": "0.15 - 0.4",
                "active_growth": "0.4 - 0.7",
                "peak_canopy": "> 0.7",
            },
            "EVI": {
                "poor_growth": "< 0.15",
                "healthy_crop": "0.2 - 0.8",
            },
        },
        "value_ranges": {
            "ndvi": (-1.0, 1.0), "evi": (-1.0, 1.0), "savi": (-1.0, 1.0),
            "ndre": (-1.0, 1.0), "lai": (0.0, 8.0),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.6, "max_cloud_cover": 0.3,
            "expected_mean_range": {"ndvi": (0.15, 0.75), "evi": (0.1, 0.6)},
        },
        "tips": (
            "Use red edge bands (B8A / nir08) for crop health assessment. "
            "5-day revisit is good for phenology tracking. "
            "EVI is more sensitive than NDVI in high-biomass areas. "
            "SAVI corrects for soil background in sparse canopy fields. "
            "CHL_RE uses red-edge bands for chlorophyll content estimation. "
            "Consider aggregate_temporal_period with period='month' for seasonal trends."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "spectral_index_calculate",
            "temporal_phenology",
        ],
        "requirements": None,
    },
    "snow": {
        "indices": ["NDSI"],
        "collections": ["sentinel-2-l2a", "landsat-c2-l2"],
        "default_index": "NDSI",
        "default_colormap": "coolwarm",
        "thresholds": {
            "NDSI": {
                "no_snow": "< 0.2",
                "mixed_pixels": "0.2 - 0.4",
                "snow_covered": "0.4 - 0.6",
                "high_confidence_snow": "> 0.6",
            },
        },
        "value_ranges": {
            "ndsi": (-1.0, 1.0),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.4, "max_cloud_cover": 0.4,
            "expected_mean_range": {"ndsi": (0.0, 0.8)},
        },
        "tips": (
            "NDSI >= 0.4 is the Dozier 1989 global standard for snow detection. "
            "Values 0.2-0.4 represent mixed/transitional pixels (patchy snow). "
            "NOAA confidence levels: low(>=0.4), medium(>=0.5), high(>=0.6). "
            "Combine with elevation from DEM for snowline analysis. "
            "Use green and SWIR bands for NDSI calculation."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "spectral_index_calculate",
            "viz_show_map",
        ],
        "requirements": None,
    },
    "climate": {
        "indices": ["NDVI", "NDMI", "EVI"],
        "collections": ["landsat-c2-l2"],
        "default_index": "NDVI",
        "default_colormap": "ndvi",
        "thresholds": {},
        "value_ranges": {
            "ndvi": (-1.0, 1.0), "ndmi": (-1.0, 1.0), "evi": (-1.0, 1.0),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.3, "max_cloud_cover": 0.5,
            "expected_mean_range": {"ndvi": (0.0, 0.7)},
        },
        "tips": (
            "Landsat provides 40+ years of data for long-term climate impact analysis. "
            "Use annual or seasonal composites to reduce noise. "
            "Compare decadal averages for robust trend detection. "
            "EVI avoids NDVI saturation in dense canopies for long-term monitoring."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "temporal_trend_analysis",
        ],
        "requirements": "Long temporal range recommended (5+ years)",
    },
    "soil": {
        "indices": ["BSI", "NDVI", "SAVI", "NDTI"],
        "collections": ["sentinel-2-l2a"],
        "default_index": "BSI",
        "default_colormap": "plasma",
        "thresholds": {
            "BSI": {
                "vegetated": "< 0.0",
                "mixed": "0.0 - 0.2",
                "bare_soil": "> 0.2",
            },
        },
        "value_ranges": {
            "bsi": (-1.0, 1.0), "ndvi": (-1.0, 1.0), "savi": (-1.0, 1.0),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.5, "max_cloud_cover": 0.3,
            "expected_mean_range": {"bsi": (-0.2, 0.5)},
        },
        "tips": (
            "BSI detects bare soil. SWIR bands sensitive to soil moisture and mineral content. "
            "Use dry season imagery for clearest bare soil detection. "
            "Combine BSI with NDVI to distinguish bare soil from urban areas. "
            "SAVI (L=0.5) corrects soil background in sparse vegetation areas. "
            "NDTI (Normalized Difference Tillage Index) detects agricultural tillage."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "spectral_index_calculate",
            "viz_show_map",
        ],
        "requirements": None,
    },
    "coastal": {
        "indices": ["NDWI", "MNDWI"],
        "collections": ["sentinel-2-l2a"],
        "default_index": "NDWI",
        "default_colormap": "coolwarm",
        "thresholds": {
            "NDWI": {
                "land": "< 0.0",
                "shoreline": "~0.0",
                "water": "> 0.0",
            },
        },
        "value_ranges": {
            "ndwi": (-1.0, 1.0), "mndwi": (-1.0, 1.0),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.3, "max_cloud_cover": 0.4,
            "expected_mean_range": {"ndwi": (-0.3, 0.5)},
        },
        "tips": (
            "Blue band penetrates water for bathymetry. "
            "Monitor shoreline changes with NDWI time series. "
            "Use high-tide/low-tide image pairs for coastal dynamics. "
            "MNDWI suppresses built-up land noise for coastal urban areas."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "spectral_index_calculate",
            "viz_show_map",
        ],
        "requirements": None,
    },
    "drought": {
        "indices": ["NDVI", "NDMI", "NDWI", "VCI", "TCI"],
        "collections": ["sentinel-2-l2a", "landsat-c2-l2"],
        "default_index": "NDMI",
        "default_colormap": "coolwarm",
        "thresholds": {
            "NDMI": {
                "severe_stress": "< -0.3",
                "moderate_stress": "-0.3 - 0.0",
                "normal": "0.0 - 0.3",
                "well_watered": "> 0.3",
            },
        },
        "value_ranges": {
            "ndvi": (-1.0, 1.0), "ndmi": (-1.0, 1.0), "ndwi": (-1.0, 1.0),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.5, "max_cloud_cover": 0.4,
            "expected_mean_range": {"ndmi": (-0.3, 0.3)},
        },
        "tips": (
            "Monitor NDVI/NDMI time series. Compare current values to long-term averages. "
            "NDMI is more sensitive to water stress than NDVI — detects stress before visible symptoms. "
            "VCI (Vegetation Condition Index) and TCI (Temperature Condition Index) are drought-specific. "
            "Use temporal_anomaly_detect to find statistically significant dry periods."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "temporal_anomaly_detect",
        ],
        "requirements": "Baseline period needed for anomaly comparison",
    },
    "deforestation": {
        "indices": ["NDVI", "NBR", "EVI", "SAVI"],
        "collections": ["sentinel-2-l2a", "landsat-c2-l2"],
        "default_index": "NDVI",
        "default_colormap": "ndvi",
        "thresholds": {
            "NDVI": {
                "cleared_land": "< 0.2",
                "degraded_forest": "0.2 - 0.5",
                "healthy_forest": "> 0.5",
            },
        },
        "value_ranges": {
            "ndvi": (-1.0, 1.0), "nbr": (-1.0, 1.0), "evi": (-1.0, 1.0),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.5, "max_cloud_cover": 0.3,
            "expected_mean_range": {"ndvi": (0.2, 0.8)},
        },
        "tips": (
            "Combine Sentinel-2 (recent detail) with Landsat (40-year baseline). "
            "Use annual composites to reduce seasonal noise. "
            "NBR change detection reveals forest degradation more clearly than NDVI alone. "
            "SAVI corrects for soil influence in partially deforested areas."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "geoai_detect_change",
        ],
        "requirements": "TWO temporal windows (baseline + current)",
    },
    "terrain": {
        "indices": [],
        "collections": ["cop-dem-glo-30", "cop-dem-glo-90"],
        "default_index": None,
        "default_colormap": "terrain",
        "thresholds": {},
        "value_ranges": {},
        "quality_thresholds": {
            "min_valid_pixels": 0.8, "max_cloud_cover": 1.0,
            "expected_mean_range": {},
        },
        "tips": (
            "30m for detailed local analysis, 90m for regional studies. "
            "Derive slope, aspect, hillshade from DEM. "
            "Use for flood risk zones, watershed delineation, and visibility analysis."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "viz_show_map",
        ],
        "requirements": None,
    },
    "flood": {
        "indices": ["NDWI", "MNDWI"],
        "collections": ["sentinel-2-l2a", "cop-dem-glo-30"],
        "default_index": "NDWI",
        "default_colormap": "coolwarm",
        "thresholds": {
            "NDWI": {
                "dry_land": "< 0.0",
                "wet_area": "0.0 - 0.3",
                "flooded": "> 0.3",
            },
        },
        "value_ranges": {
            "ndwi": (-1.0, 1.0), "mndwi": (-1.0, 1.0),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.3, "max_cloud_cover": 0.5,
            "expected_mean_range": {"ndwi": (-0.3, 0.5)},
        },
        "tips": (
            "Pre/post flood NDWI comparison reveals inundation extent. "
            "Use dNDWI (difference NDWI) between pre- and post-flood for change mapping. "
            "DEM for flow accumulation and risk zones. "
            "Cloud cover often high during flood events — use multiple acquisitions."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "spectral_index_calculate",
            "viz_show_map",
        ],
        "requirements": "TWO temporal windows (pre-flood + post-flood)",
    },
    # --- New domains added from research (2025-2026 literature) ---
    "wetland": {
        "indices": ["NDWI", "MNDWI", "NDVI", "WIW"],
        "collections": ["sentinel-2-l2a", "landsat-c2-l2"],
        "default_index": "NDWI",
        "default_colormap": "coolwarm",
        "thresholds": {
            "NDWI": {
                "vegetation": "< -0.1",
                "mixed_wetland": "-0.1 - 0.2",
                "open_water": "> 0.2",
            },
        },
        "value_ranges": {
            "ndwi": (-1.0, 1.0), "mndwi": (-1.0, 1.0), "ndvi": (-1.0, 1.0),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.4, "max_cloud_cover": 0.4,
            "expected_mean_range": {"ndwi": (-0.3, 0.4)},
        },
        "tips": (
            "Distinct from water domain — focuses on water-vegetation mixtures. "
            "WIW (Water In Wetlands) = (blue - nir) / (blue + nir), superior for vegetated wetlands. "
            "Multi-temporal analysis distinguishes permanent vs seasonal wetlands. "
            "Combine NDWI, MNDWI, and NDVI for multi-feature classification. "
            "Cloud masking critical for wetland areas."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "spectral_index_calculate",
            "viz_show_map",
        ],
        "requirements": "Multi-temporal analysis recommended (seasonal comparison)",
    },
    "algal_bloom": {
        "indices": ["NDCI", "NDWI", "MNDWI"],
        "collections": ["sentinel-2-l2a"],
        "default_index": "NDCI",
        "default_colormap": "viridis",
        "thresholds": {
            "NDCI": {
                "oligotrophic": "< -0.2",
                "mesotrophic": "-0.2 - 0.0",
                "eutrophic": "0.0 - 0.2",
                "hypereutrophic": "> 0.2",
            },
        },
        "value_ranges": {
            "ndci": (-1.0, 1.0), "ndwi": (-1.0, 1.0),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.4, "max_cloud_cover": 0.3,
            "expected_mean_range": {"ndci": (-0.3, 0.3)},
        },
        "tips": (
            "NDCI = (red_edge - red) / (red_edge + red) — highest chlorophyll-a accuracy (R^2=0.84). "
            "Sentinel-2 red-edge bands (B5@704nm, B6@740nm, B7@783nm) essential. "
            "Captures greater spatial/temporal heterogeneity than in-situ sampling. "
            "Best performance in small reservoirs and inland lakes. "
            "Green, red, and red-edge bands (B3, B4, B5) most influential."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "spectral_index_calculate",
            "viz_show_map",
        ],
        "requirements": None,
    },
    "mining": {
        "indices": ["BSI", "NDVI"],
        "collections": ["sentinel-2-l2a", "landsat-c2-l2"],
        "default_index": "BSI",
        "default_colormap": "plasma",
        "thresholds": {
            "BSI": {
                "vegetated": "< 0.1",
                "natural_bare": "0.1 - 0.3",
                "excavated_area": "> 0.3",
            },
        },
        "value_ranges": {
            "bsi": (-1.0, 1.0), "ndvi": (-1.0, 1.0),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.5, "max_cloud_cover": 0.3,
            "expected_mean_range": {"bsi": (0.0, 0.5)},
        },
        "tips": (
            "Mining areas show similar spectral response to exposed soils — use multi-temporal analysis. "
            "SWIR bands differentiate excavated from natural bare soil. "
            "Temporal NDVI change detection tracks vegetation removal and restoration. "
            "Combine BSI with NDVI to separate mining from agricultural bare land. "
            "Average detection accuracy 72-74% with optical imagery alone."
        ),
        "tool_chain": [
            "openeo_generate_graph",
            "openeo_create_job",
            "openeo_get_results",
            "spectral_index_calculate",
            "geoai_detect_change",
        ],
        "requirements": "Multi-temporal analysis recommended for change detection",
    },
}


# Keyword mapping for domain classification.
# Reuses the same keywords as _USE_CASE_KEYWORDS in discovery_tools.py.
_DOMAIN_KEYWORDS = {
    "vegetation": [
        "vegetation", "ndvi", "evi", "plant", "green", "canopy", "biomass",
        "leaf", "phenology", "chlorophyll",
    ],
    "deforestation": [
        "deforestation", "forest loss", "logging", "tree cover", "forest change",
        "clearing",
    ],
    "water": [
        "water", "lake", "river", "pond", "reservoir", "aquatic",
    ],
    "fire": [
        "fire", "burn", "wildfire", "burnt", "burned", "blaze", "combustion",
    ],
    "urban": [
        "urban", "city", "built-up", "building", "settlement", "impervious", "town",
    ],
    "drought": [
        "drought", "dry", "arid", "moisture stress", "water deficit", "desiccation",
    ],
    "agriculture": [
        "agriculture", "crop", "farm", "field", "harvest", "irrigation", "yield",
        "cereal", "wheat", "rice", "maize",
    ],
    "snow": [
        "snow", "ice", "glacier", "frozen", "snowpack", "cryosphere", "permafrost",
    ],
    "terrain": [
        "terrain", "elevation", "dem", "slope", "aspect", "hillshade",
        "topography", "relief", "mountain",
    ],
    "flood": [
        "flood", "inundation", "overflow", "submersion", "flash flood", "floodplain",
    ],
    "climate": [
        "climate", "long-term", "decadal", "historical", "trend", "baseline",
        "multi-year",
    ],
    "soil": [
        "soil", "bare earth", "mineral", "sediment", "erosion", "land degradation",
    ],
    "coastal": [
        "coastal", "coast", "shoreline", "beach", "bathymetry", "marine",
        "ocean", "sea", "tidal",
    ],
    # New domains
    "wetland": [
        "wetland", "marsh", "swamp", "bog", "peatland", "seasonal water",
        "water vegetation",
    ],
    "algal_bloom": [
        "algal bloom", "chlorophyll", "phytoplankton", "eutrophic", "water quality",
        "harmful algal bloom", "algae", "cyanobacteria",
    ],
    "mining": [
        "mining", "quarry", "excavation", "open-pit", "tailings", "ore",
        "land disturbance",
    ],
}


def classify_domain(prompt: str) -> Optional[str]:
    """Classify user prompt into a domain. Returns domain key or None.

    Uses keyword matching with score-based ranking.
    Checks longer keywords first to avoid substring collision
    (e.g., "algal_bloom" before "algal", "soil_moisture" before "soil").
    """
    prompt_lower = prompt.lower()
    scores: dict[str, int] = {}

    for domain, keywords in _DOMAIN_KEYWORDS.items():
        score = 0
        # Check longer keywords first to avoid substring collision
        for keyword in sorted(keywords, key=len, reverse=True):
            if keyword in prompt_lower:
                score += 1
        # Bonus if domain name itself appears (handle underscore → space too)
        if domain in prompt_lower or domain.replace("_", " ") in prompt_lower:
            score += 2
        if score > 0:
            scores[domain] = score

    if not scores:
        return None

    # Return the highest-scoring domain
    return max(scores, key=lambda k: scores[k])


def get_domain_context(domain: str) -> Optional[str]:
    """Format domain context as a prompt-injectable text block.

    Returns None if domain is not recognized.
    """
    ctx = DOMAIN_CONTEXTS.get(domain)
    if ctx is None:
        return None

    lines = [f"Domain: {domain}"]

    if ctx["indices"]:
        lines.append(f"Recommended indices: {', '.join(ctx['indices'])}")
    if ctx["collections"]:
        lines.append(f"Collections: {', '.join(ctx['collections'])}")
    if ctx["default_index"]:
        lines.append(f"Default index: {ctx['default_index']}")
    if ctx["default_colormap"]:
        lines.append(f"Default colormap: {ctx['default_colormap']}")

    # Format thresholds
    if ctx["thresholds"]:
        threshold_parts = []
        for index_name, levels in ctx["thresholds"].items():
            level_strs = [f"{k}({v})" for k, v in levels.items()]
            threshold_parts.append(f"{index_name} — {', '.join(level_strs)}")
        lines.append(f"Thresholds: {'; '.join(threshold_parts)}")

    if ctx["tips"]:
        lines.append(f"Tips: {ctx['tips']}")

    if ctx["requirements"]:
        lines.append(f"Requirements: {ctx['requirements']}")

    return "\n".join(lines)
