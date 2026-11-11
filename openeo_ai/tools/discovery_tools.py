# ABOUTME: Smart discovery tools for recommending EO collections, explaining band capabilities,
# and runtime process discovery from the backend registry.

"""
Smart discovery tools for Earth Observation data.

Provides tools for recommending collections based on research questions,
explaining collection band capabilities using a built-in knowledge base,
and runtime discovery of available OpenEO processes from the backend registry.
"""

import json
import inspect
from typing import Any, Dict, List, Optional


COLLECTION_KNOWLEDGE = {
    "sentinel-2-l2a": {
        "name": "Sentinel-2 Level-2A",
        "provider": "ESA / Copernicus",
        "resolution": {"10m": ["blue (B02)", "green (B03)", "red (B04)", "nir (B08)"],
                       "20m": ["nir08 (B8A)", "swir16 (B11)", "swir22 (B12)", "scl"],
                       "60m": ["coastal (B01)", "water_vapour (B09)"]},
        "revisit": "5 days (2 satellites)",
        "coverage": "Global land (56S to 84N)",
        "start_date": "June 2015",
        "best_for": ["Vegetation monitoring (NDVI, EVI, LAI)", "Land cover classification",
                     "Agriculture and crop monitoring", "Water body detection",
                     "Urban area mapping", "Forest health assessment",
                     "Change detection (deforestation, urbanization)"],
        "bands_explained": {
            "blue": "Band 2 (490nm) - Water penetration, bathymetry, atmospheric scattering",
            "green": "Band 3 (560nm) - Peak vegetation reflectance, water quality",
            "red": "Band 4 (665nm) - Chlorophyll absorption, vegetation discrimination",
            "nir": "Band 8 (842nm) - Vegetation health, biomass estimation, water boundaries",
            "nir08": "Band 8A (865nm) - Red edge, chlorophyll content, LAI",
            "swir16": "Band 11 (1610nm) - Moisture content, fire detection, snow/ice",
            "swir22": "Band 12 (2190nm) - Soil/mineral analysis, burn severity",
            "scl": "Scene Classification Layer - Cloud/shadow/vegetation/water/soil masks"
        },
        "common_indices": ["NDVI", "EVI", "NDWI", "MNDWI", "NBR", "NDBI", "SAVI", "NDMI", "NDSI"]
    },
    "sentinel-2-l1c": {
        "name": "Sentinel-2 Level-1C",
        "provider": "ESA / Copernicus",
        "resolution": {"same as L2A": []},
        "revisit": "5 days",
        "coverage": "Global land",
        "start_date": "June 2015",
        "best_for": ["When custom atmospheric correction is needed",
                     "Atmospheric studies", "Comparison with other TOA products"],
        "bands_explained": {"Same bands as L2A": "but Top-of-Atmosphere (TOA) reflectance instead of surface reflectance"},
        "common_indices": ["Same as L2A"]
    },
    "landsat-c2-l2": {
        "name": "Landsat Collection 2 Level-2",
        "provider": "USGS / NASA",
        "resolution": {"30m": ["blue", "green", "red", "nir08", "swir16", "swir22"],
                       "100m": ["thermal (Landsat 8/9)"],
                       "120m": ["thermal (Landsat 7)"]},
        "revisit": "16 days per satellite (8 days combined Landsat 8+9)",
        "coverage": "Global",
        "start_date": "1982 (Landsat 4), continuous record",
        "best_for": ["Long-term change analysis (40+ years)",
                     "Historical baselines", "Deforestation over decades",
                     "Thermal/urban heat island studies",
                     "Glacial retreat over time", "Desertification trends"],
        "bands_explained": {
            "blue": "~480nm - Water penetration, atmospheric correction",
            "green": "~560nm - Vegetation vigor",
            "red": "~655nm - Vegetation discrimination",
            "nir08": "~865nm - Biomass, shoreline detection (use 'nir08' NOT 'nir')",
            "swir16": "~1610nm - Moisture, burn mapping (use 'swir16' NOT 'swir1')",
            "swir22": "~2200nm - Soil, minerals (use 'swir22' NOT 'swir2')",
            "thermal": "~10.9um - Surface temperature, urban heat"
        },
        "common_indices": ["NDVI", "NDWI", "NBR", "NDBI", "NDMI"]
    },
    "cop-dem-glo-30": {
        "name": "Copernicus DEM Global 30m",
        "provider": "ESA / Airbus",
        "resolution": {"30m": ["elevation"]},
        "revisit": "Static dataset (regularly updated)",
        "coverage": "Global",
        "start_date": "2021",
        "best_for": ["Terrain analysis (slope, aspect, hillshade)",
                     "Flood risk modeling", "Watershed delineation",
                     "Visibility analysis", "Infrastructure planning"],
        "bands_explained": {
            "elevation": "Height above ellipsoid in meters, derived from TanDEM-X radar mission"
        },
        "common_indices": []
    },
    "cop-dem-glo-90": {
        "name": "Copernicus DEM Global 90m",
        "provider": "ESA / Airbus",
        "resolution": {"90m": ["elevation"]},
        "revisit": "Static",
        "coverage": "Global",
        "start_date": "2021",
        "best_for": ["Regional/continental terrain analysis",
                     "Large-scale hydrological modeling",
                     "Coarse terrain visualization"],
        "bands_explained": {
            "elevation": "Height above ellipsoid in meters (lower resolution variant)"
        },
        "common_indices": []
    }
}

# Catalog honesty (priority-roadmap Tier-1): only sentinel-2-l2a is verified to load
# real pixels in this deployment; every other entry is a catalog listing until proven.
_PIXEL_VERIFIED = {"sentinel-2-l2a"}


def _pixel_loading(cid: str) -> str:
    if cid in _PIXEL_VERIFIED:
        return "verified — loads real pixels in this deployment"
    return ("unverified — catalog listing only; sentinel-2-l2a is the only collection "
            "verified to load real pixels in this deployment")


USE_CASE_MAPPING = {
    "vegetation": {"collections": ["sentinel-2-l2a"], "indices": ["NDVI", "EVI", "SAVI", "MSAVI", "LAI", "GNDVI", "ARVI", "NDRE", "CHL_RE"], "tips": "Use cloud-free imagery during growing season. Sentinel-2 10m NIR band gives best detail. NDVI saturates in dense canopies — use EVI instead."},
    "deforestation": {"collections": ["sentinel-2-l2a", "landsat-c2-l2"], "indices": ["NDVI", "NBR", "EVI", "SAVI"], "tips": "Combine Sentinel-2 (recent detail) with Landsat (40-year baseline). Use annual composites. SAVI corrects for soil influence in partially deforested areas."},
    "water": {"collections": ["sentinel-2-l2a"], "indices": ["NDWI", "MNDWI", "AWEI"], "tips": "MNDWI better for urban areas. AWEI provides higher accuracy than NDWI. Use SCL band to mask clouds."},
    "fire": {"collections": ["sentinel-2-l2a", "landsat-c2-l2"], "indices": ["NBR", "dNBR", "BAI"], "tips": "Compare pre-fire and post-fire NBR. dNBR > 0.27 indicates moderate-high severity. BAI highlights recently burned areas using red + NIR."},
    "urban": {"collections": ["sentinel-2-l2a", "landsat-c2-l2"], "indices": ["NDBI", "BSI", "UI"], "tips": "NDBI highlights built-up areas (92.6% accuracy per Zha et al. 2003). Combine with NDVI for urban green space analysis. UI combines SWIR2 and NIR."},
    "drought": {"collections": ["sentinel-2-l2a", "landsat-c2-l2"], "indices": ["NDVI", "NDMI", "NDWI", "VCI", "TCI"], "tips": "Monitor NDVI/NDMI time series. Compare current values to long-term averages. NDMI detects stress before visible symptoms."},
    "agriculture": {"collections": ["sentinel-2-l2a"], "indices": ["NDVI", "EVI", "SAVI", "LAI", "NDRE", "CHL_RE"], "tips": "Use red edge bands (B8A) for crop health. 5-day revisit good for phenology tracking. CHL_RE uses red-edge for chlorophyll estimation."},
    "snow": {"collections": ["sentinel-2-l2a", "landsat-c2-l2"], "indices": ["NDSI"], "tips": "NDSI >= 0.4 is the Dozier 1989 global standard for snow. Combine with elevation from DEM. NOAA confidence levels: low(>=0.4), medium(>=0.5), high(>=0.6)."},
    "terrain": {"collections": ["cop-dem-glo-30", "cop-dem-glo-90"], "indices": [], "tips": "30m for detailed local analysis, 90m for regional studies. Derive slope, aspect, hillshade."},
    "flood": {"collections": ["sentinel-2-l2a", "cop-dem-glo-30"], "indices": ["NDWI", "MNDWI"], "tips": "Pre/post flood NDWI comparison. Use dNDWI for change mapping. DEM for flow accumulation and risk zones."},
    "climate": {"collections": ["landsat-c2-l2"], "indices": ["NDVI", "NDMI", "EVI"], "tips": "Landsat provides 40+ years of data for long-term climate impact analysis. EVI avoids NDVI saturation in dense canopies."},
    "soil": {"collections": ["sentinel-2-l2a"], "indices": ["BSI", "NDVI", "SAVI", "NDTI"], "tips": "BSI detects bare soil. SWIR bands sensitive to soil moisture and mineral content. NDTI detects agricultural tillage."},
    "coastal": {"collections": ["sentinel-2-l2a"], "indices": ["NDWI", "MNDWI"], "tips": "Blue band penetrates water for bathymetry. Monitor shoreline changes with NDWI time series. MNDWI suppresses built-up noise."},
    # New domains (synced from domain_knowledge.py)
    "wetland": {"collections": ["sentinel-2-l2a", "landsat-c2-l2"], "indices": ["NDWI", "MNDWI", "NDVI", "WIW"], "tips": "Focuses on water-vegetation mixtures. WIW (Water In Wetlands) superior for vegetated wetlands. Multi-temporal analysis distinguishes permanent vs seasonal."},
    "algal_bloom": {"collections": ["sentinel-2-l2a"], "indices": ["NDCI", "NDWI", "MNDWI"], "tips": "NDCI = (red_edge - red) / (red_edge + red) — highest chlorophyll-a accuracy (R²=0.84). Best for small reservoirs and inland lakes."},
    "mining": {"collections": ["sentinel-2-l2a", "landsat-c2-l2"], "indices": ["BSI", "NDVI"], "tips": "Mining areas show similar spectral response to exposed soils — use multi-temporal analysis. Combine BSI with NDVI to separate mining from agricultural bare land."},
}

_USE_CASE_KEYWORDS = {
    "vegetation": ["vegetation", "ndvi", "evi", "plant", "green", "canopy", "biomass", "leaf", "phenology", "chlorophyll"],
    "deforestation": ["deforestation", "forest loss", "logging", "tree cover", "forest change", "clearing"],
    "water": ["water", "lake", "river", "pond", "reservoir", "aquatic"],
    "fire": ["fire", "burn", "wildfire", "burnt", "burned", "blaze", "combustion"],
    "urban": ["urban", "city", "built-up", "building", "settlement", "impervious", "town"],
    "drought": ["drought", "dry", "arid", "moisture stress", "water deficit", "desiccation"],
    "agriculture": ["agriculture", "crop", "farm", "field", "harvest", "irrigation", "yield", "cereal", "wheat", "rice", "maize"],
    "snow": ["snow", "ice", "glacier", "frozen", "snowpack", "cryosphere", "permafrost"],
    "terrain": ["terrain", "elevation", "dem", "slope", "aspect", "hillshade", "topography", "relief", "mountain"],
    "flood": ["flood", "inundation", "overflow", "submersion", "flash flood", "floodplain"],
    "climate": ["climate", "long-term", "decadal", "historical", "trend", "baseline", "multi-year"],
    "soil": ["soil", "bare earth", "mineral", "sediment", "erosion", "land degradation"],
    "coastal": ["coastal", "coast", "shoreline", "beach", "bathymetry", "marine", "ocean", "sea", "tidal"],
    # New domains (synced from domain_knowledge.py)
    "wetland": ["wetland", "marsh", "swamp", "bog", "peatland", "seasonal water", "water vegetation"],
    "algal_bloom": ["algal bloom", "phytoplankton", "eutrophic", "water quality", "harmful algal bloom", "algae", "cyanobacteria"],
    "mining": ["mining", "quarry", "excavation", "open-pit", "tailings", "ore", "land disturbance"],
}

# Analyses that our STAC catalog CANNOT support — returns warnings instead of false matches
UNSUPPORTED_ANALYSIS = {
    "aod": {
        "keywords": ["aod", "aerosol optical depth", "aerosol thickness", "aerosol loading"],
        "warning": "Aerosol Optical Depth (AOD) analysis requires dedicated atmospheric sensors.",
        "required_sensor": "MODIS (MOD04/MYD04), Sentinel-5P TROPOMI, or AERONET ground stations",
        "our_limitation": "Sentinel-2 and Landsat are surface reflectance sensors — they do NOT measure column-integrated aerosol properties. Using the blue band as an AOD proxy is scientifically invalid for quantitative analysis.",
        "alternatives": [
            "Use NASA Worldview or Giovanni for MODIS AOD data",
            "Use Copernicus Atmosphere Monitoring Service (CAMS) for reanalysis AOD",
            "Use AERONET for ground-truth AOD measurements",
        ]
    },
    "air_quality": {
        "keywords": ["air quality", "aqi", "pm2.5", "pm10", "particulate matter", "pollution index", "air pollution"],
        "warning": "Air quality monitoring requires atmospheric chemistry sensors or ground networks.",
        "required_sensor": "Sentinel-5P TROPOMI, MODIS, or government air quality monitoring networks",
        "our_limitation": "Our collections (Sentinel-2, Landsat, DEMs) measure surface properties, not atmospheric pollutant concentrations.",
        "alternatives": [
            "Use Copernicus Atmosphere Monitoring Service (CAMS) for modeled AQI",
            "Use OpenAQ for ground-station air quality data",
            "Use Sentinel-5P data via Copernicus Data Space for tropospheric pollutants",
        ]
    },
    "trace_gases": {
        "keywords": ["no2", "nitrogen dioxide", "so2", "sulfur dioxide", "co concentration",
                     "carbon monoxide", "methane", "ch4", "ozone column", "o3 concentration",
                     "formaldehyde", "hcho", "tropospheric"],
        "warning": "Trace gas concentration mapping requires atmospheric chemistry sensors.",
        "required_sensor": "Sentinel-5P TROPOMI, Aura OMI, GOSAT, or OCO-2",
        "our_limitation": "Sentinel-2 and Landsat do not have spectral bands in the UV/thermal-IR ranges needed for trace gas retrieval.",
        "alternatives": [
            "Use Sentinel-5P via Copernicus Data Space for NO2, SO2, CO, CH4, O3",
            "Use NASA GES DISC for Aura OMI and OCO-2 data",
            "Use Google Earth Engine for Sentinel-5P TROPOMI processing",
        ]
    },
    "sea_surface_temp": {
        "keywords": ["sea surface temperature", "sst", "ocean temperature", "marine thermal"],
        "warning": "Sea surface temperature requires thermal infrared ocean sensors.",
        "required_sensor": "Sentinel-3 SLSTR, MODIS, or AVHRR",
        "our_limitation": "While Landsat has a thermal band, it is designed for land surface temperature at 100m resolution — not suitable for oceanic SST mapping.",
        "alternatives": [
            "Use Copernicus Marine Service for Sentinel-3 SST products",
            "Use NASA PO.DAAC for MODIS and GHRSST SST data",
        ]
    },
    "soil_moisture": {
        "keywords": ["soil moisture", "volumetric water content", "soil wetness", "smap"],
        "warning": "Quantitative soil moisture estimation requires microwave/SAR sensors.",
        "required_sensor": "SMAP, Sentinel-1 SAR, or SMOS",
        "our_limitation": "Optical sensors can detect surface moisture stress indirectly (via NDMI/NDWI) but cannot measure volumetric soil moisture content.",
        "alternatives": [
            "Use NASA SMAP data for soil moisture at 9km/36km resolution",
            "Use Copernicus Climate Data Store for ERA5 soil moisture reanalysis",
            "We CAN compute NDMI (moisture stress index) as a vegetation moisture proxy — ask if interested",
        ]
    },
    "precipitation": {
        "keywords": ["precipitation", "rainfall estimation", "rain rate", "gpm", "trmm"],
        "warning": "Precipitation estimation requires dedicated precipitation radar or microwave sensors.",
        "required_sensor": "GPM (Global Precipitation Measurement), TRMM, or rain gauge networks",
        "our_limitation": "Optical satellites cannot measure rainfall. They can detect flood extent AFTER precipitation events.",
        "alternatives": [
            "Use NASA GPM data via GES DISC for precipitation estimates",
            "Use CHIRPS for merged gauge-satellite rainfall data",
            "We CAN help with post-flood analysis using NDWI if that's useful",
        ]
    },
    "ocean_color": {
        "keywords": ["ocean color", "chlorophyll-a", "chl-a", "phytoplankton", "ocean productivity",
                     "marine chlorophyll", "algal bloom ocean"],
        "warning": "Ocean color and chlorophyll-a concentration requires dedicated ocean color sensors.",
        "required_sensor": "Sentinel-3 OLCI, MODIS Ocean Color, or SeaWiFS heritage",
        "our_limitation": "Sentinel-2 can detect nearshore algal blooms in lakes/reservoirs but is not calibrated for open ocean chlorophyll-a retrieval.",
        "alternatives": [
            "Use Copernicus Marine Service for Sentinel-3 OLCI ocean color",
            "Use NASA Ocean Color Web for MODIS Aqua data",
            "For inland water quality, we CAN use Sentinel-2 green/red bands — ask if interested",
        ]
    },
    "sar": {
        "keywords": ["sar", "synthetic aperture radar", "radar backscatter", "insar",
                     "interferometry", "radar imagery"],
        "warning": "SAR (Synthetic Aperture Radar) data is not in our STAC catalog.",
        "required_sensor": "Sentinel-1 SAR, ALOS-2 PALSAR, or TerraSAR-X",
        "our_limitation": "Our catalog contains only optical (Sentinel-2, Landsat) and DEM data. SAR requires separate data access.",
        "alternatives": [
            "Use Copernicus Data Space for Sentinel-1 SAR data",
            "Use Alaska Satellite Facility (ASF) for SAR data search and download",
            "Use Google Earth Engine for Sentinel-1 processing",
        ]
    },
}


def _check_unsupported(research_question: str) -> List[Dict[str, Any]]:
    """Check if a research question involves unsupported analysis types.

    Returns a list of matching unsupported analysis warnings (empty if all supported).
    """
    question_lower = research_question.lower()
    warnings = []

    for analysis_key, info in UNSUPPORTED_ANALYSIS.items():
        for keyword in info["keywords"]:
            if keyword in question_lower:
                warnings.append({
                    "analysis_type": analysis_key,
                    "matched_keyword": keyword,
                    "warning": info["warning"],
                    "required_sensor": info["required_sensor"],
                    "our_limitation": info["our_limitation"],
                    "alternatives": info["alternatives"],
                })
                break  # Only match once per analysis type

    return warnings


def _match_use_cases(research_question: str) -> List[str]:
    """Match a research question to use case keys by keyword analysis."""
    question_lower = research_question.lower()
    scores: Dict[str, int] = {}

    for use_case, keywords in _USE_CASE_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            if keyword in question_lower:
                score += 1
        if use_case in question_lower:
            score += 2
        if score > 0:
            scores[use_case] = score

    sorted_cases = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
    return sorted_cases


# Process categorization for runtime discovery
PROCESS_CATEGORIES = {
    "math": ["add", "subtract", "multiply", "divide", "normalized_difference",
             "min", "max", "sum", "mean", "median", "sd", "variance", "product",
             "quantiles", "extrema", "floor", "ceil", "int", "round", "exp",
             "log", "ln", "sqrt", "power", "clip", "mod", "absolute", "sgn",
             "linear_scale_range", "e", "pi", "nan", "constant",
             "cos", "sin", "tan", "arccos", "arcsin", "arctan", "arctan2",
             "cosh", "sinh", "tanh", "arcosh", "arsinh", "artanh",
             "cumsum", "cumproduct", "cummin", "cummax"],
    "array": ["array_element", "array_create", "array_create_labeled",
              "array_modify", "array_concat", "array_append", "array_contains",
              "array_find", "array_find_label", "array_filter", "array_labels",
              "array_apply", "array_interpolate_linear",
              "first", "last", "order", "rearrange", "sort", "count"],
    "comparison": ["eq", "neq", "gt", "gte", "lt", "lte", "between",
                   "is_infinite", "is_valid", "is_nan", "is_nodata"],
    "logic": ["and", "or", "not", "xor", "if", "any", "all"],
    "datacube": ["create_data_cube", "drop_dimension", "dimension_labels",
                 "add_dimension", "rename_dimension", "rename_labels", "trim_cube"],
    "filter": ["filter_temporal", "filter_bands", "filter_bbox",
               "filter_spatial", "filter_labels"],
    "reduce": ["reduce_dimension", "reduce_spatial"],
    "apply": ["apply", "apply_dimension", "apply_kernel", "apply_polygon"],
    "aggregate": ["aggregate_temporal", "aggregate_temporal_period",
                  "aggregate_spatial"],
    "load": ["load_collection", "load_stac", "load_url"],
    "mask": ["mask", "mask_polygon"],
    "merge": ["merge_cubes"],
    "resample": ["resample_spatial", "resample_cube_spatial",
                 "resample_cube_temporal"],
    "indices": ["ndvi"],
    "geometry": ["load_geojson", "vector_buffer", "vector_reproject"],
    "date": ["date_between", "date_difference", "date_shift"],
    "text": ["text_begins", "text_contains", "text_ends", "text_concat"],
    "output": ["save_result"],
    "debug": ["inspect"],
}

# Reverse lookup: process_id -> category
_PROCESS_TO_CATEGORY = {}
for _cat, _procs in PROCESS_CATEGORIES.items():
    for _proc in _procs:
        _PROCESS_TO_CATEGORY[_proc] = _cat


def _categorize_process(process_id: str) -> str:
    """Get the category for a process ID."""
    return _PROCESS_TO_CATEGORY.get(process_id, "other")


# Usage hints with examples for key processes
PROCESS_USAGE_HINTS = {
    "reduce_dimension": {
        "usage": "Reduce a dimension using an aggregation function",
        "example": {
            "reduce_time": {
                "process_id": "reduce_dimension",
                "arguments": {
                    "data": {"from_node": "load"},
                    "dimension": "time",
                    "reducer": {
                        "process_graph": {
                            "median1": {
                                "process_id": "median",
                                "arguments": {"data": {"from_parameter": "data"}},
                                "result": True
                            }
                        }
                    }
                }
            }
        },
        "notes": "Simple reducers (mean, median, min, max, sum, std, var) work reliably. Complex callback reducers with band math may not execute correctly yet."
    },
    "array_element": {
        "usage": "Select a band by label (name) or index position",
        "example": {
            "select_nir": {
                "process_id": "array_element",
                "arguments": {
                    "data": {"from_parameter": "data"},
                    "label": "nir"
                }
            }
        },
        "notes": "Always prefer label= over index=. Band ordering varies by collection."
    },
    "mask": {
        "usage": "Mask a datacube: pixels where mask=True become NaN (nodata). Do NOT invert the mask — mask() already replaces True values with NaN.",
        "example": {
            "get_scl": {
                "process_id": "array_element",
                "arguments": {
                    "data": {"from_node": "load"},
                    "label": "scl"
                }
            },
            "cloud_mask": {
                "process_id": "between",
                "arguments": {
                    "x": {"from_node": "get_scl"},
                    "min": 8,
                    "max": 11
                }
            },
            "apply_mask": {
                "process_id": "mask",
                "arguments": {
                    "data": {"from_node": "load"},
                    "mask": {"from_node": "cloud_mask"}
                }
            }
        },
        "notes": "CRITICAL cloud masking rules: (1) mask() replaces True→NaN, so pass cloud_mask directly to mask() — do NOT invert with not(). between(scl, 8, 11) is True for clouds, which is exactly what mask() needs. (2) Apply cloud masking BEFORE temporal aggregation (reduce_dimension), not after. SCL is per-timestep classification — median of SCL values is meaningless. Correct order: load → array_element(scl) → between(8,11) → mask(data, cloud_mask) → NDVI → reduce_dimension(time)."
    },
    "filter_bands": {
        "usage": "Select specific bands from a datacube",
        "example": {
            "select_bands": {
                "process_id": "filter_bands",
                "arguments": {
                    "data": {"from_node": "load"},
                    "bands": ["red", "nir", "swir16"]
                }
            }
        },
    },
    "merge_cubes": {
        "usage": "Merge two datacubes along shared or new dimensions",
        "example": {
            "merge": {
                "process_id": "merge_cubes",
                "arguments": {
                    "cube1": {"from_node": "data1"},
                    "cube2": {"from_node": "data2"}
                }
            }
        },
        "notes": "Use overlap_resolver for conflicting values."
    },
    "normalized_difference": {
        "usage": "Compute (x-y)/(x+y) for indices like NDVI, NDWI, NBR",
        "example": {
            "nd": {
                "process_id": "normalized_difference",
                "arguments": {
                    "x": {"from_node": "nir_band"},
                    "y": {"from_node": "red_band"}
                }
            }
        },
        "notes": "Our implementation adds epsilon protection and clips to [-1,1]."
    },
    "apply": {
        "usage": "Apply a unary process to every pixel",
        "example": {
            "scale": {
                "process_id": "apply",
                "arguments": {
                    "data": {"from_node": "load"},
                    "process": {
                        "process_graph": {
                            "multiply1": {
                                "process_id": "multiply",
                                "arguments": {
                                    "x": {"from_parameter": "x"},
                                    "y": 0.0001
                                },
                                "result": True
                            }
                        }
                    }
                }
            }
        },
        "notes": "WARNING: apply callbacks may have issues with the double-argument bug. Simple unary operations work."
    },
    "aggregate_temporal_period": {
        "usage": "Aggregate data over regular time periods (month, year, etc.)",
        "example": {
            "monthly": {
                "process_id": "aggregate_temporal_period",
                "arguments": {
                    "data": {"from_node": "load"},
                    "reducer": {
                        "process_graph": {
                            "mean1": {
                                "process_id": "mean",
                                "arguments": {"data": {"from_parameter": "data"}},
                                "result": True
                            }
                        }
                    },
                    "period": "month"
                }
            }
        },
        "notes": "Supports: hour, day, week, month, season, year, decade, dekad"
    },
    "resample_spatial": {
        "usage": "Resample/reproject to different resolution or CRS",
        "example": {
            "resample": {
                "process_id": "resample_spatial",
                "arguments": {
                    "data": {"from_node": "load"},
                    "resolution": 30,
                    "method": "bilinear"
                }
            }
        },
        "notes": "13 resampling methods available including nearest, bilinear, cubic"
    },
    "ndvi": {
        "usage": "Built-in NDVI computation (preferred over manual band math)",
        "example": {
            "compute_ndvi": {
                "process_id": "ndvi",
                "arguments": {
                    "data": {"from_node": "load"},
                    "nir": "nir",
                    "red": "red",
                    "target_band": "ndvi"
                }
            }
        },
        "notes": "Simplest way to compute NDVI. Automatically handles band selection."
    },
    "filter_temporal": {
        "usage": "Filter datacube by time range",
        "example": {
            "filter_time": {
                "process_id": "filter_temporal",
                "arguments": {
                    "data": {"from_node": "load"},
                    "extent": ["2024-01-01", "2024-06-30"]
                }
            }
        },
    },
    "filter_bbox": {
        "usage": "Filter datacube by bounding box",
        "example": {
            "filter_area": {
                "process_id": "filter_bbox",
                "arguments": {
                    "data": {"from_node": "load"},
                    "extent": {"west": 76.0, "south": 10.0, "east": 77.0, "north": 11.0}
                }
            }
        },
    },
    "eq": {
        "usage": "Check equality (x == y). Use for creating masks from SCL values.",
        "example": {
            "is_cloud": {
                "process_id": "eq",
                "arguments": {
                    "x": {"from_parameter": "x"},
                    "y": 3
                }
            }
        },
        "notes": "SCL values: 3=cloud shadow, 4=vegetation, 5=bare soil, 6=water, 8=cloud medium, 9=cloud high"
    },
    "save_result": {
        "usage": "Save datacube as output file",
        "example": {
            "save": {
                "process_id": "save_result",
                "arguments": {
                    "data": {"from_node": "result"},
                    "format": "GTiff"
                }
            }
        },
        "notes": "Supported formats: GTiff (recommended), netCDF, JSON"
    },
    "load_collection": {
        "usage": "Load an Earth Observation data collection as a datacube",
        "example": {
            "load": {
                "process_id": "load_collection",
                "arguments": {
                    "id": "sentinel-2-l2a",
                    "spatial_extent": {"west": 76.0, "south": 10.0, "east": 77.0, "north": 11.0},
                    "temporal_extent": ["2024-01-01", "2024-06-30"],
                    "bands": ["red", "nir", "swir16"]
                }
            }
        },
        "notes": "Starting point for all analyses. Always specify bands to reduce data volume. Use common band names (blue, green, red, nir, swir16, swir22, scl)."
    },
    "filter_spatial": {
        "usage": "Filter datacube by a GeoJSON geometry (polygon, multipolygon)",
        "example": {
            "filter_geo": {
                "process_id": "filter_spatial",
                "arguments": {
                    "data": {"from_node": "load"},
                    "geometries": {"type": "Polygon", "coordinates": [[[76.0, 10.0], [77.0, 10.0], [77.0, 11.0], [76.0, 11.0], [76.0, 10.0]]]}
                }
            }
        },
        "notes": "Use for irregular boundaries (not bounding boxes). Accepts GeoJSON geometries."
    },
    "aggregate_spatial": {
        "usage": "Compute zonal statistics over spatial regions",
        "example": {
            "zonal_stats": {
                "process_id": "aggregate_spatial",
                "arguments": {
                    "data": {"from_node": "ndvi_result"},
                    "geometries": {"from_node": "load_geojson1"},
                    "reducer": {
                        "process_graph": {
                            "mean1": {
                                "process_id": "mean",
                                "arguments": {"data": {"from_parameter": "data"}},
                                "result": True
                            }
                        }
                    }
                }
            }
        },
        "notes": "Aggregates pixel values within each polygon. Useful for computing mean NDVI per field/district."
    },
    "apply_dimension": {
        "usage": "Apply a process along a specific dimension (e.g., temporal smoothing)",
        "example": {
            "smooth_time": {
                "process_id": "apply_dimension",
                "arguments": {
                    "data": {"from_node": "load"},
                    "dimension": "time",
                    "process": {
                        "process_graph": {
                            "mean1": {
                                "process_id": "mean",
                                "arguments": {"data": {"from_parameter": "data"}},
                                "result": True
                            }
                        }
                    }
                }
            }
        },
        "notes": "WARNING: Complex callbacks may have issues. Simple aggregations (mean, median) work reliably."
    },
    "mask_polygon": {
        "usage": "Mask a datacube using a polygon geometry (pixels outside become nodata)",
        "example": {
            "mask_area": {
                "process_id": "mask_polygon",
                "arguments": {
                    "data": {"from_node": "load"},
                    "mask": {"type": "Polygon", "coordinates": [[[76.0, 10.0], [77.0, 10.0], [77.0, 11.0], [76.0, 11.0], [76.0, 10.0]]]}
                }
            }
        },
        "notes": "Sets pixels outside the polygon to nodata. Use for cropping to irregular boundaries."
    },
    "aggregate_temporal": {
        "usage": "Aggregate data over specific time intervals",
        "example": {
            "seasonal": {
                "process_id": "aggregate_temporal",
                "arguments": {
                    "data": {"from_node": "load"},
                    "intervals": [["2024-01-01", "2024-04-01"], ["2024-04-01", "2024-07-01"]],
                    "reducer": {
                        "process_graph": {
                            "median1": {
                                "process_id": "median",
                                "arguments": {"data": {"from_parameter": "data"}},
                                "result": True
                            }
                        }
                    }
                }
            }
        },
        "notes": "For regular periods (monthly, yearly) prefer aggregate_temporal_period. This is for custom intervals."
    },
    "add": {
        "usage": "Add two values or datacubes element-wise (x + y)",
        "example": {
            "sum_bands": {
                "process_id": "add",
                "arguments": {
                    "x": {"from_node": "band1"},
                    "y": {"from_node": "band2"}
                }
            }
        },
        "notes": "Works with scalars and datacubes. Used in band math expressions."
    },
    "subtract": {
        "usage": "Subtract two values or datacubes element-wise (x - y)",
        "example": {
            "diff": {
                "process_id": "subtract",
                "arguments": {
                    "x": {"from_node": "post_event"},
                    "y": {"from_node": "pre_event"}
                }
            }
        },
        "notes": "Common for change detection: subtract pre-event from post-event values."
    },
    "multiply": {
        "usage": "Multiply two values or datacubes element-wise (x * y)",
        "example": {
            "scale": {
                "process_id": "multiply",
                "arguments": {
                    "x": {"from_parameter": "x"},
                    "y": 0.0001
                }
            }
        },
        "notes": "Commonly used for scale factor application (e.g., Sentinel-2 surface reflectance * 0.0001)."
    },
    "divide": {
        "usage": "Divide two values or datacubes element-wise (x / y)",
        "example": {
            "ratio": {
                "process_id": "divide",
                "arguments": {
                    "x": {"from_node": "band_ratio_num"},
                    "y": {"from_node": "band_ratio_den"}
                }
            }
        },
        "notes": "Used in band ratio calculations. Watch for division by zero."
    },
    "gt": {
        "usage": "Greater than comparison (x > y), returns boolean datacube",
        "example": {
            "threshold": {
                "process_id": "gt",
                "arguments": {
                    "x": {"from_node": "ndvi_result"},
                    "y": 0.3
                }
            }
        },
        "notes": "Returns True/False datacube. Use for thresholding (e.g., NDVI > 0.3 = vegetation)."
    },
    "lt": {
        "usage": "Less than comparison (x < y), returns boolean datacube",
        "example": {
            "low_values": {
                "process_id": "lt",
                "arguments": {
                    "x": {"from_node": "ndvi_result"},
                    "y": 0.1
                }
            }
        },
        "notes": "Returns True/False datacube. Use for thresholding."
    },
    "between": {
        "usage": "Check if value is between min and max (inclusive)",
        "example": {
            "range_check": {
                "process_id": "between",
                "arguments": {
                    "x": {"from_node": "ndvi_result"},
                    "min": 0.2,
                    "max": 0.8
                }
            }
        },
        "notes": "Inclusive by default. Useful for classification ranges."
    },
    "and": {
        "usage": "Logical AND of two boolean values/datacubes",
        "example": {
            "combined_mask": {
                "process_id": "and",
                "arguments": {
                    "x": {"from_node": "veg_mask"},
                    "y": {"from_node": "cloud_free_mask"}
                }
            }
        },
        "notes": "Combine multiple boolean masks. Both must be True for result to be True."
    },
    "or": {
        "usage": "Logical OR of two boolean values/datacubes",
        "example": {
            "any_mask": {
                "process_id": "or",
                "arguments": {
                    "x": {"from_node": "cloud_mask"},
                    "y": {"from_node": "shadow_mask"}
                }
            }
        },
        "notes": "Either being True makes result True. Use to combine cloud + shadow masks."
    },
    "not": {
        "usage": "Logical NOT - invert a boolean value/datacube",
        "example": {
            "invert": {
                "process_id": "not",
                "arguments": {
                    "x": {"from_node": "cloud_mask"}
                }
            }
        },
        "notes": "Inverts True/False. Use to create clear-sky mask from cloud mask."
    },
    "if": {
        "usage": "Conditional: return 'accept' if value is True, else 'reject'",
        "example": {
            "conditional": {
                "process_id": "if",
                "arguments": {
                    "value": {"from_node": "is_vegetation"},
                    "accept": {"from_node": "ndvi_result"},
                    "reject": None
                }
            }
        },
        "notes": "If-then-else for datacubes. Set reject=None to mask out non-matching pixels as nodata."
    },
    "clip": {
        "usage": "Clamp values to a range [min, max]",
        "example": {
            "clamp": {
                "process_id": "clip",
                "arguments": {
                    "x": {"from_node": "ndvi_result"},
                    "min": -1.0,
                    "max": 1.0
                }
            }
        },
        "notes": "Values below min become min, above max become max. Use after index calculation."
    },
    "count": {
        "usage": "Count valid (non-nodata) values",
        "example": {
            "valid_count": {
                "process_id": "count",
                "arguments": {
                    "data": {"from_parameter": "data"},
                    "ignore_nodata": True
                }
            }
        },
        "notes": "Often used as a reducer in reduce_dimension to count observations per pixel."
    },
    "rename_labels": {
        "usage": "Rename labels of a dimension (e.g., rename band names)",
        "example": {
            "rename": {
                "process_id": "rename_labels",
                "arguments": {
                    "data": {"from_node": "result"},
                    "dimension": "bands",
                    "target": ["ndvi"],
                    "source": ["band_0"]
                }
            }
        },
        "notes": "Useful after band math to give meaningful names to result bands."
    },
    "add_dimension": {
        "usage": "Add a new dimension with a single label",
        "example": {
            "add_band_dim": {
                "process_id": "add_dimension",
                "arguments": {
                    "data": {"from_node": "index_result"},
                    "name": "bands",
                    "label": "ndvi",
                    "type": "bands"
                }
            }
        },
        "notes": "Use after computing a single-band result to add the bands dimension back."
    },
    "drop_dimension": {
        "usage": "Remove a dimension (must have exactly one label)",
        "example": {
            "drop": {
                "process_id": "drop_dimension",
                "arguments": {
                    "data": {"from_node": "single_time"},
                    "name": "time"
                }
            }
        },
        "notes": "Dimension must have exactly one label. Reduce first if it has multiple."
    },
    "load_geojson": {
        "usage": "Load a GeoJSON geometry for use in spatial operations",
        "example": {
            "geom": {
                "process_id": "load_geojson",
                "arguments": {
                    "data": {
                        "type": "FeatureCollection",
                        "features": [{
                            "type": "Feature",
                            "geometry": {"type": "Polygon", "coordinates": [[[76.0, 10.0], [77.0, 10.0], [77.0, 11.0], [76.0, 11.0], [76.0, 10.0]]]},
                            "properties": {}
                        }]
                    }
                }
            }
        },
        "notes": "Creates a vector datacube from GeoJSON. Use with aggregate_spatial or filter_spatial."
    },
    "dimension_labels": {
        "usage": "Get the labels of a dimension (e.g., band names, time values)",
        "example": {
            "labels": {
                "process_id": "dimension_labels",
                "arguments": {
                    "data": {"from_node": "load"},
                    "dimension": "bands"
                }
            }
        },
        "notes": "Returns array of labels. Useful for debugging to see what bands/times are available."
    },
}


def create_discovery_tools(config=None) -> Dict[str, Any]:
    """
    Create discovery tools dict for Claude SDK.

    Args:
        config: Optional configuration (unused, kept for interface consistency)

    Returns:
        Dict mapping tool names to async handler functions
    """

    async def _recommend_collection(args: Dict[str, Any]) -> Dict[str, Any]:
        """Recommend EO collections based on a research question."""
        try:
            research_question = args.get("research_question", "")
            if not research_question:
                return {
                    "content": [{"type": "text", "text": json.dumps({
                        "error": "research_question is required"
                    })}]
                }

            # Check for unsupported analyses FIRST
            unsupported_warnings = _check_unsupported(research_question)

            matched_cases = _match_use_cases(research_question)

            # If there are unsupported warnings, return them prominently
            if unsupported_warnings:
                result = {
                    "research_question": research_question,
                    "unsupported_warning": True,
                    "unsupported_analyses": unsupported_warnings,
                    "matched_use_cases": matched_cases,
                    "feasible": False if not matched_cases else True,
                    "message": (
                        "STOP — This analysis involves data types our platform cannot provide. "
                        "See unsupported_analyses for details on what sensors are needed and where to find the data. "
                        "Do NOT proceed with a proxy analysis unless the user explicitly requests it after full disclosure."
                    ),
                }

                # Still include any partial matches if some parts ARE supported
                if matched_cases:
                    result["note"] = (
                        "Some aspects of this query match supported use cases. "
                        "However, the core unsupported analysis MUST be disclosed first."
                    )
                    seen_collections: Dict[str, int] = {}
                    for case_key in matched_cases:
                        mapping = USE_CASE_MAPPING[case_key]
                        for cid in mapping["collections"]:
                            if cid not in seen_collections:
                                seen_collections[cid] = 0
                            seen_collections[cid] += 1
                    result["partial_matches"] = {
                        "supported_use_cases": matched_cases,
                        "collections": list(seen_collections.keys()),
                    }

                return {
                    "content": [{"type": "text", "text": json.dumps(result)}]
                }

            if not matched_cases:
                all_collections = list(COLLECTION_KNOWLEDGE.keys())
                return {
                    "content": [{"type": "text", "text": json.dumps({
                        "research_question": research_question,
                        "matched_use_cases": [],
                        "recommendation": "No specific use case matched. Here are all available collections for manual exploration.",
                        "available_collections": [
                            {
                                "id": cid,
                                "name": COLLECTION_KNOWLEDGE[cid]["name"],
                                "provider": COLLECTION_KNOWLEDGE[cid]["provider"],
                                "best_for": COLLECTION_KNOWLEDGE[cid]["best_for"],
                                "pixel_loading": _pixel_loading(cid)
                            }
                            for cid in all_collections
                        ]
                    })}]
                }

            seen_collections: Dict[str, int] = {}
            all_indices: List[str] = []
            tips: List[str] = []

            for case_key in matched_cases:
                mapping = USE_CASE_MAPPING[case_key]
                for cid in mapping["collections"]:
                    if cid not in seen_collections:
                        seen_collections[cid] = 0
                    seen_collections[cid] += 1
                for idx in mapping["indices"]:
                    if idx not in all_indices:
                        all_indices.append(idx)
                tips.append(f"[{case_key}] {mapping['tips']}")

            sorted_collections = sorted(
                seen_collections.keys(),
                key=lambda c: seen_collections[c],
                reverse=True
            )

            recommended = []
            for cid in sorted_collections:
                info = COLLECTION_KNOWLEDGE.get(cid, {})
                rec = {
                    "collection_id": cid,
                    "name": info.get("name", cid),
                    "provider": info.get("provider", "Unknown"),
                    "resolution": info.get("resolution", {}),
                    "revisit": info.get("revisit", "Unknown"),
                    "relevance_score": seen_collections[cid],
                    "best_for": info.get("best_for", []),
                    "pixel_loading": _pixel_loading(cid),
                }
                key_bands = {}
                bands_explained = info.get("bands_explained", {})
                for band_name, explanation in bands_explained.items():
                    key_bands[band_name] = explanation
                rec["key_bands"] = key_bands
                recommended.append(rec)

            result = {
                "research_question": research_question,
                "matched_use_cases": matched_cases,
                "recommended_collections": recommended,
                "suggested_indices": all_indices,
                "practical_tips": tips,
            }

            return {
                "content": [{"type": "text", "text": json.dumps(result)}]
            }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": json.dumps({
                    "error": f"Failed to recommend collection: {str(e)}"
                })}]
            }

    async def _explain_collection_bands(args: Dict[str, Any]) -> Dict[str, Any]:
        """Explain the bands and capabilities of a specific collection."""
        try:
            collection_id = args.get("collection_id", "")
            if not collection_id:
                return {
                    "content": [{"type": "text", "text": json.dumps({
                        "error": "collection_id is required"
                    })}]
                }

            info = COLLECTION_KNOWLEDGE.get(collection_id)
            if info is None:
                # Fallback: live STAC lookup via DatasetIntelligenceService
                try:
                    import os
                    from openeo_ai.validation.dataset_intelligence import DatasetIntelligenceService
                    stac_url = os.environ.get(
                        "STAC_API_URL", "https://earth-search.aws.element84.com/v1/"
                    )
                    svc = DatasetIntelligenceService(stac_url)
                    raw = svc.fetch_collection_sync(collection_id)
                    normalized = svc.normalize_collection(raw)
                    bands_detail = [
                        {"band": b.get("common_name") or b.get("name"), "description": b.get("description") or ""}
                        for b in normalized.get("bands", [])
                    ]
                    result = {
                        "collection_id": collection_id,
                        "name": normalized.get("title", collection_id),
                        "provider": "STAC catalog (live lookup)",
                        "bands": bands_detail,
                        "qa_bands": normalized.get("qa_bands", []),
                        "surface_reflectance": normalized.get("surface_reflectance"),
                        "native_gsd_meters": normalized.get("native_gsd"),
                        "source": "DatasetIntelligenceService (live STAC)",
                    }
                    return {
                        "content": [{"type": "text", "text": json.dumps(result)}]
                    }
                except Exception:
                    pass  # Fall through to static error

                available = list(COLLECTION_KNOWLEDGE.keys())
                return {
                    "content": [{"type": "text", "text": json.dumps({
                        "error": f"Unknown collection '{collection_id}'. Available collections: {', '.join(available)}"
                    })}]
                }

            bands_detail = []
            for band_name, explanation in info.get("bands_explained", {}).items():
                bands_detail.append({
                    "band": band_name,
                    "description": explanation,
                })

            resolution_detail = []
            for res, band_list in info.get("resolution", {}).items():
                resolution_detail.append({
                    "resolution": res,
                    "bands": band_list,
                })

            result = {
                "collection_id": collection_id,
                "name": info.get("name", collection_id),
                "provider": info.get("provider", "Unknown"),
                "coverage": info.get("coverage", "Unknown"),
                "start_date": info.get("start_date", "Unknown"),
                "revisit": info.get("revisit", "Unknown"),
                "resolution": resolution_detail,
                "bands": bands_detail,
                "common_indices": info.get("common_indices", []),
                "typical_use_cases": info.get("best_for", []),
            }

            return {
                "content": [{"type": "text", "text": json.dumps(result)}]
            }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": json.dumps({
                    "error": f"Failed to explain collection bands: {str(e)}"
                })}]
            }

    async def _list_openeo_processes(args: Dict[str, Any]) -> Dict[str, Any]:
        """List all available OpenEO processes from the runtime registry."""
        try:
            from openeo_app.execution.process_registry_singleton import ProcessRegistrySingleton

            registry = ProcessRegistrySingleton.get_registry()
            category_filter = args.get("category", None)

            processes = {}
            for process_id, process in registry.items():
                cat = _categorize_process(process_id)

                # Apply category filter if specified
                if category_filter and category_filter != "all" and cat != category_filter:
                    continue

                # Extract parameter names from implementation signature
                params = []
                try:
                    impl = process.implementation
                    sig = inspect.signature(impl)
                    params = [
                        p for p in sig.parameters
                        if p != "named_parameters"
                    ]
                except (ValueError, TypeError):
                    pass

                processes[process_id] = {
                    "parameters": params,
                    "category": cat,
                }

            result = {
                "total_count": len(registry),
                "filtered_count": len(processes),
                "categories": sorted(set(
                    v["category"] for v in processes.values()
                )),
                "processes": processes,
            }

            return {
                "content": [{"type": "text", "text": json.dumps(result)}]
            }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": json.dumps({
                    "error": f"Failed to list processes: {str(e)}"
                })}]
            }

    async def _get_process_spec(args: Dict[str, Any]) -> Dict[str, Any]:
        """Get detailed specification for a specific OpenEO process."""
        try:
            from openeo_app.execution.process_registry_singleton import ProcessRegistrySingleton

            process_id = args.get("process_id", "")
            if not process_id:
                return {
                    "content": [{"type": "text", "text": json.dumps({
                        "error": "process_id is required"
                    })}]
                }

            registry = ProcessRegistrySingleton.get_registry()

            if process_id not in registry:
                # Suggest similar process names
                available = sorted(registry.keys())
                suggestions = [p for p in available if process_id in p or p in process_id]
                return {
                    "content": [{"type": "text", "text": json.dumps({
                        "error": f"Process '{process_id}' not found",
                        "suggestions": suggestions[:10],
                        "total_available": len(available),
                    })}]
                }

            process = registry[process_id]
            impl = process.implementation

            # Extract parameters from signature
            params = {}
            try:
                sig = inspect.signature(impl)
                for name, param in sig.parameters.items():
                    if name == "named_parameters":
                        continue
                    params[name] = {
                        "required": param.default == inspect.Parameter.empty,
                        "default": (
                            str(param.default)
                            if param.default != inspect.Parameter.empty
                            else None
                        ),
                    }
            except (ValueError, TypeError):
                pass

            # Get usage hints
            hints = PROCESS_USAGE_HINTS.get(process_id, {})

            # Auto-generate example graph node if no manual example exists
            example_node = hints.get("example", None)
            if not example_node and params:
                auto_args = {}
                for pname, pinfo in params.items():
                    if pname in ("kwargs",):
                        continue
                    if pname == "data":
                        auto_args[pname] = {"from_node": "PREVIOUS_NODE"}
                    elif pname in ("x", "cube1"):
                        auto_args[pname] = {"from_node": "PREVIOUS_NODE"}
                    elif pname in ("y", "cube2", "mask"):
                        auto_args[pname] = {"from_node": "OTHER_NODE"}
                    elif pname == "dimension":
                        auto_args[pname] = "time"
                    elif pname == "label":
                        auto_args[pname] = "band_name"
                    elif pname == "bands":
                        auto_args[pname] = ["red", "nir"]
                    elif pname == "format":
                        auto_args[pname] = "GTiff"
                    elif pinfo.get("default") and pinfo["default"] != "None":
                        auto_args[pname] = pinfo["default"]
                    else:
                        auto_args[pname] = f"<{pname}>"
                example_node = {
                    "process_id": process_id,
                    "arguments": auto_args,
                }

            result = {
                "process_id": process_id,
                "parameters": params,
                "category": _categorize_process(process_id),
                "usage_hint": hints.get("usage", ""),
                "example_node": example_node,
                "notes": hints.get("notes", ""),
            }

            return {
                "content": [{"type": "text", "text": json.dumps(result)}]
            }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": json.dumps({
                    "error": f"Failed to get process spec: {str(e)}"
                })}]
            }

    return {
        "recommend_collection": _recommend_collection,
        "explain_collection_bands": _explain_collection_bands,
        "openeo_list_processes": _list_openeo_processes,
        "openeo_get_process_spec": _get_process_spec,
    }


DISCOVERY_TOOL_DEFINITIONS = [
    {
        "name": "recommend_collection",
        "description": "Recommend the best Earth Observation data collections for a research question. Analyzes the question to identify relevant use cases (vegetation, fire, flood, urban, etc.) and returns ranked collection recommendations with suggested spectral indices and practical tips.",
        "input_schema": {
            "type": "object",
            "properties": {
                "research_question": {
                    "type": "string",
                    "description": "The research question or analysis goal (e.g., 'I want to monitor deforestation in the Amazon over the last 10 years', 'How can I detect flood extent after heavy rainfall?')"
                }
            },
            "required": ["research_question"]
        }
    },
    {
        "name": "explain_collection_bands",
        "description": "Get a detailed explanation of all bands in an Earth Observation collection, including wavelength, what each band measures, common uses, available spectral indices, and typical use cases. Supports: sentinel-2-l2a, sentinel-2-l1c, landsat-c2-l2, cop-dem-glo-30, cop-dem-glo-90.",
        "input_schema": {
            "type": "object",
            "properties": {
                "collection_id": {
                    "type": "string",
                    "description": "The collection identifier (e.g., 'sentinel-2-l2a', 'landsat-c2-l2', 'cop-dem-glo-30')"
                }
            },
            "required": ["collection_id"]
        }
    },
    {
        "name": "openeo_list_processes",
        "description": "List all available OpenEO processes from the runtime backend registry. Returns process names, parameters, and categories. Use this to discover what processes are actually available before building process graphs. Optionally filter by category: math, array, comparison, logic, datacube, filter, reduce, apply, aggregate, load, mask, merge, resample, indices, geometry, date, text, output, debug.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional category filter. Use 'all' or omit to list everything. Categories: math, array, comparison, logic, datacube, filter, reduce, apply, aggregate, load, mask, merge, resample, indices, geometry, date, text, output, debug."
                }
            },
            "required": []
        }
    },
    {
        "name": "openeo_get_process_spec",
        "description": "Get detailed specification for a specific OpenEO process including parameters (with required/optional), category, usage hints, example process graph JSON, and known limitations. Use this when you need to understand how to use a specific process correctly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "process_id": {
                    "type": "string",
                    "description": "The process identifier (e.g., 'reduce_dimension', 'array_element', 'aggregate_temporal_period')"
                }
            },
            "required": ["process_id"]
        }
    }
]
