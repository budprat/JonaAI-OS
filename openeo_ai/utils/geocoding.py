# ABOUTME: Geocoding utility for resolving place names to bounding boxes.
# Uses Nominatim (OpenStreetMap) for free geocoding without API keys.

"""
Geocoding utilities for OpenEO AI Assistant.

Resolves natural language location references to geographic bounding boxes
suitable for Earth Observation data queries.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
from functools import lru_cache

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

logger = logging.getLogger(__name__)

# Default buffer in degrees for point locations
DEFAULT_BUFFER_DEG = 0.1  # ~11km at equator

# Well-known regions with predefined bounding boxes
KNOWN_REGIONS = {
    # Indian States
    "kerala": {"west": 74.85, "south": 8.28, "east": 77.42, "north": 12.79},
    "maharashtra": {"west": 72.60, "south": 15.60, "east": 80.90, "north": 22.10},
    "rajasthan": {"west": 69.30, "south": 23.30, "east": 78.27, "north": 30.19},
    "punjab": {"west": 73.87, "south": 29.53, "east": 76.94, "north": 32.51},
    "tamil nadu": {"west": 76.23, "south": 8.07, "east": 80.35, "north": 13.57},
    "karnataka": {"west": 74.05, "south": 11.59, "east": 78.59, "north": 18.45},

    # Major Cities (with reasonable analysis extent)
    "delhi": {"west": 76.84, "south": 28.40, "east": 77.35, "north": 28.88},
    "mumbai": {"west": 72.77, "south": 18.89, "east": 72.98, "north": 19.27},
    "bangalore": {"west": 77.46, "south": 12.83, "east": 77.78, "north": 13.14},
    "chennai": {"west": 80.17, "south": 12.90, "east": 80.32, "north": 13.23},
    "kolkata": {"west": 88.25, "south": 22.45, "east": 88.50, "north": 22.65},
    "hyderabad": {"west": 78.27, "south": 17.28, "east": 78.60, "north": 17.55},

    # Global Regions
    "amazon rainforest": {"west": -73.0, "south": -10.0, "east": -50.0, "north": 5.0},
    "amazon": {"west": -73.0, "south": -10.0, "east": -50.0, "north": 5.0},
    "sahara": {"west": -17.0, "south": 15.0, "east": 35.0, "north": 35.0},
    "alps": {"west": 5.5, "south": 44.0, "east": 16.5, "north": 48.5},
    "himalayas": {"west": 73.0, "south": 26.0, "east": 95.0, "north": 36.0},

    # Countries (small/medium sized for practical analysis)
    "netherlands": {"west": 3.31, "south": 50.75, "east": 7.21, "north": 53.47},
    "belgium": {"west": 2.54, "south": 49.50, "east": 6.41, "north": 51.50},
    "switzerland": {"west": 5.96, "south": 45.82, "east": 10.49, "north": 47.81},
    "austria": {"west": 9.53, "south": 46.37, "east": 17.16, "north": 49.02},
    "singapore": {"west": 103.60, "south": 1.17, "east": 104.09, "north": 1.47},

    # US States & Regions
    "california": {"west": -124.48, "south": 32.53, "east": -114.13, "north": 42.01},
    "northern california": {"west": -124.41, "south": 38.0, "east": -119.99, "north": 42.01},
    "southern california": {"west": -121.0, "south": 32.53, "east": -114.13, "north": 35.8},
    "central california": {"west": -122.5, "south": 34.5, "east": -118.0, "north": 38.0},
    "texas": {"west": -106.65, "south": 25.84, "east": -93.51, "north": 36.50},
    "florida": {"west": -87.63, "south": 24.52, "east": -80.03, "north": 31.00},
    "new york": {"west": -79.76, "south": 40.50, "east": -71.86, "north": 45.01},
    "oregon": {"west": -124.57, "south": 41.99, "east": -116.46, "north": 46.29},
    "washington": {"west": -124.85, "south": 45.54, "east": -116.92, "north": 49.00},
    "colorado": {"west": -109.06, "south": 36.99, "east": -102.04, "north": 41.00},
    "arizona": {"west": -114.82, "south": 31.33, "east": -109.04, "north": 37.00},
    "alaska": {"west": -179.15, "south": 51.21, "east": -129.98, "north": 71.39},
    "hawaii": {"west": -160.24, "south": 18.91, "east": -154.81, "north": 22.24},

    # European countries
    "germany": {"west": 5.87, "south": 47.27, "east": 15.04, "north": 55.10},
    "france": {"west": -5.14, "south": 41.33, "east": 9.56, "north": 51.12},
    "spain": {"west": -9.30, "south": 36.00, "east": 3.32, "north": 43.79},
    "italy": {"west": 6.63, "south": 36.62, "east": 18.52, "north": 47.09},
    "united kingdom": {"west": -8.18, "south": 49.96, "east": 1.77, "north": 58.64},
    "uk": {"west": -8.18, "south": 49.96, "east": 1.77, "north": 58.64},
    "portugal": {"west": -9.50, "south": 36.96, "east": -6.19, "north": 42.15},
    "greece": {"west": 19.37, "south": 34.80, "east": 29.65, "north": 41.75},
    "poland": {"west": 14.12, "south": 49.00, "east": 24.15, "north": 54.84},
    "sweden": {"west": 11.11, "south": 55.34, "east": 24.17, "north": 69.06},
    "norway": {"west": 4.65, "south": 57.96, "east": 31.08, "north": 71.19},

    # Asian countries
    "japan": {"west": 129.56, "south": 31.03, "east": 145.82, "north": 45.55},
    "south korea": {"west": 125.07, "south": 33.11, "east": 131.87, "north": 38.61},
    "china": {"west": 73.50, "south": 18.16, "east": 134.77, "north": 53.56},
    "indonesia": {"west": 95.01, "south": -11.01, "east": 141.02, "north": 6.08},
    "thailand": {"west": 97.34, "south": 5.61, "east": 105.64, "north": 20.46},
    "vietnam": {"west": 102.14, "south": 8.18, "east": 109.46, "north": 23.39},
    "australia": {"west": 113.34, "south": -43.64, "east": 153.64, "north": -10.69},
    "new zealand": {"west": 166.43, "south": -47.29, "east": 178.58, "north": -34.39},

    # African countries
    "nigeria": {"west": 2.69, "south": 4.27, "east": 14.68, "north": 13.89},
    "south africa": {"west": 16.46, "south": -34.84, "east": 32.89, "north": -22.13},
    "kenya": {"west": 33.91, "south": -4.68, "east": 41.91, "north": 5.02},
    "ethiopia": {"west": 32.99, "south": 3.40, "east": 47.99, "north": 14.89},
    "egypt": {"west": 24.70, "south": 22.00, "east": 36.87, "north": 31.67},
    "morocco": {"west": -13.17, "south": 27.67, "east": -1.01, "north": 35.92},

    # South America
    "brazil": {"west": -73.98, "south": -33.75, "east": -34.79, "north": 5.27},
    "argentina": {"west": -73.56, "south": -55.06, "east": -53.64, "north": -21.78},
    "chile": {"west": -75.64, "south": -55.98, "east": -66.96, "north": -17.51},
    "colombia": {"west": -79.00, "south": -4.23, "east": -66.87, "north": 12.44},
    "peru": {"west": -81.33, "south": -18.35, "east": -68.65, "north": -0.04},
}


@dataclass
class LocationResult:
    """Result of location resolution."""

    success: bool
    bbox: Optional[Dict[str, float]] = None  # west, south, east, north
    name: str = ""
    display_name: str = ""
    confidence: float = 0.0
    source: str = ""  # "known", "nominatim", "coordinates"
    error: Optional[str] = None
    alternatives: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "success": self.success,
            "name": self.name,
            "display_name": self.display_name,
            "confidence": self.confidence,
            "source": self.source,
        }
        if self.bbox:
            result["bbox"] = self.bbox
            result["spatial_extent"] = self.bbox  # OpenEO format alias
        if self.error:
            result["error"] = self.error
        if self.alternatives:
            result["alternatives"] = self.alternatives
        return result


class GeocodingService:
    """Service for resolving location names to bounding boxes."""

    def __init__(self, user_agent: str = "openeo-ai-assistant"):
        """
        Initialize geocoding service.

        Args:
            user_agent: User agent string for Nominatim (required)
        """
        self.geocoder = Nominatim(user_agent=user_agent, timeout=10)
        self.known_regions = KNOWN_REGIONS

    def resolve(
        self,
        location: str,
        buffer_deg: float = DEFAULT_BUFFER_DEG
    ) -> LocationResult:
        """
        Resolve a location string to a bounding box.

        Args:
            location: Location name, coordinates, or description
            buffer_deg: Buffer to add around point locations (degrees)

        Returns:
            LocationResult with bounding box or error
        """
        location = location.strip()

        # Try parsing as coordinates first
        coords = self._parse_coordinates(location)
        if coords:
            lat, lon = coords
            return LocationResult(
                success=True,
                bbox={
                    "west": lon - buffer_deg,
                    "south": lat - buffer_deg,
                    "east": lon + buffer_deg,
                    "north": lat + buffer_deg,
                },
                name=f"Point ({lat:.4f}, {lon:.4f})",
                display_name=f"Coordinates: {lat:.4f}°N, {lon:.4f}°E",
                confidence=1.0,
                source="coordinates",
            )

        # Check known regions - exact match
        location_lower = location.lower().strip()
        if location_lower in self.known_regions:
            bbox = self.known_regions[location_lower]
            return LocationResult(
                success=True,
                bbox=bbox,
                name=location.title(),
                display_name=f"{location.title()} (predefined region)",
                confidence=1.0,
                source="known",
            )

        # Check known regions - partial match (e.g., "Florida, USA" matches "florida")
        for region_name, bbox in self.known_regions.items():
            if region_name in location_lower or location_lower in region_name:
                return LocationResult(
                    success=True,
                    bbox=bbox,
                    name=region_name.title(),
                    display_name=f"{region_name.title()} (predefined region, partial match)",
                    confidence=0.9,
                    source="known",
                )

        # Try Nominatim geocoding
        return self._geocode_nominatim(location, buffer_deg)

    def _parse_coordinates(self, text: str) -> Optional[Tuple[float, float]]:
        """
        Parse coordinate string to (lat, lon) tuple.

        Supports formats:
        - "28.6139, 77.2090"
        - "28.6139°N, 77.2090°E"
        - "lat: 28.6139, lon: 77.2090"
        """
        import re

        # Remove common prefixes
        text = re.sub(r'(lat|latitude|lon|longitude|lng)[\s:=]*', '', text, flags=re.I)

        # Try to find two numbers
        numbers = re.findall(r'[-+]?\d+\.?\d*', text)
        if len(numbers) >= 2:
            try:
                lat = float(numbers[0])
                lon = float(numbers[1])

                # Validate ranges
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return (lat, lon)
                # Try swapping if first looks like longitude
                if -180 <= lat <= 180 and -90 <= lon <= 90:
                    return (lon, lat)
            except ValueError:
                pass

        return None

    def _pick_best_result(self, results, query: str):
        """Pick the best geocoding result, preferring larger geographic features."""
        # Feature type scoring - prefer administrative regions over streets
        TYPE_SCORES = {
            'country': 10,
            'state': 9,
            'region': 8,
            'county': 7,
            'city': 6,
            'town': 5,
            'village': 4,
            'suburb': 3,
            'neighbourhood': 2,
            'road': 1,
            'building': 0,
        }

        best_score = -1
        best_result = results[0]  # default to first

        for r in results:
            raw = r.raw
            importance = raw.get('importance', 0.5)
            osm_type = raw.get('type', '')
            category = raw.get('class', '')

            # Base score from importance
            score = importance * 5

            # Bonus for administrative/geographic features
            type_bonus = TYPE_SCORES.get(osm_type, 1)
            score += type_bonus

            # Bonus for boundary class (administrative regions)
            if category == 'boundary':
                score += 3
            elif category == 'place':
                score += 2

            if score > best_score:
                best_score = score
                best_result = r

        return best_result

    def _geocode_nominatim(
        self,
        location: str,
        buffer_deg: float
    ) -> LocationResult:
        """Geocode using Nominatim service."""
        try:
            # Get multiple results and pick the best one
            results = self.geocoder.geocode(
                location,
                exactly_one=False,
                limit=5,
                addressdetails=True,
                language="en",
            )

            if not results:
                # Try to find alternatives
                alternatives = self._find_alternatives(location)
                return LocationResult(
                    success=False,
                    error=f"Could not find location: '{location}'",
                    alternatives=alternatives,
                )

            # Score results - prefer larger geographic features
            result = self._pick_best_result(results, location)

            # Extract bounding box if available
            raw = result.raw
            if 'boundingbox' in raw:
                bb = raw['boundingbox']
                bbox = {
                    "south": float(bb[0]),
                    "north": float(bb[1]),
                    "west": float(bb[2]),
                    "east": float(bb[3]),
                }
            else:
                # Create bbox from point + buffer
                bbox = {
                    "west": result.longitude - buffer_deg,
                    "south": result.latitude - buffer_deg,
                    "east": result.longitude + buffer_deg,
                    "north": result.latitude + buffer_deg,
                }

            # Calculate confidence based on importance
            importance = raw.get('importance', 0.5)
            confidence = min(importance * 1.2, 1.0)

            return LocationResult(
                success=True,
                bbox=bbox,
                name=raw.get('name', location),
                display_name=result.address,
                confidence=confidence,
                source="nominatim",
            )

        except GeocoderTimedOut:
            return LocationResult(
                success=False,
                error="Geocoding service timed out. Please try again.",
            )
        except GeocoderServiceError as e:
            return LocationResult(
                success=False,
                error=f"Geocoding service error: {str(e)}",
            )
        except Exception as e:
            logger.exception(f"Unexpected geocoding error for '{location}'")
            return LocationResult(
                success=False,
                error=f"Unexpected error: {str(e)}",
            )

    def _find_alternatives(self, location: str) -> List[str]:
        """Find alternative location suggestions."""
        location_lower = location.lower()
        alternatives = []

        # Check for partial matches in known regions
        for name in self.known_regions:
            if location_lower in name or name in location_lower:
                alternatives.append(name.title())

        # Try geocoding with different queries
        try:
            results = self.geocoder.geocode(
                location,
                exactly_one=False,
                limit=5,
                language="en",
            )
            if results:
                for r in results[:3]:
                    if r.address not in alternatives:
                        alternatives.append(r.address)
        except Exception:
            pass

        return alternatives[:5] if alternatives else None


# A typical index job with a cloud mask: three bands on up to five scene dates.
_TYPICAL_BANDS = 3
_TYPICAL_DATES = 5


def load_check(bbox: Dict[str, float], num_bands: int, num_timesteps: int) -> Dict[str, Any]:
    """Compare what a job would load with this service's limit.

    File size is not the constraint here: a job loads every band and date before
    reducing them, so its memory use can be many times the size of the output file.
    """
    import math

    from openeo_app.core.extent import load_resolution
    from openeo_app.core.limits import load_element_limit

    step_x, step_y = load_resolution(bbox["south"], bbox["north"])
    pixels = abs(bbox["east"] - bbox["west"]) / step_x * abs(bbox["north"] - bbox["south"]) / step_y
    elements = int(pixels * max(num_bands, _TYPICAL_BANDS) * max(num_timesteps, _TYPICAL_DATES))
    limit = load_element_limit()
    check: Dict[str, Any] = {"typical_job_elements": elements, "limit_elements": limit, "fits": elements <= limit}
    if elements > limit:
        scale = math.sqrt(elements / limit)
        check["note"] = (
            f"A typical job on this area ({_TYPICAL_BANDS} bands, {_TYPICAL_DATES} dates) would load "
            f"{elements:,} elements, over this service's limit of {limit:,}, and be "
            + (f"loaded at about {round(11 * scale)} m per pixel." if scale <= 4 else "rejected.")
            + " Use a smaller area or fewer bands and dates to avoid that."
        )
    return check


def constrain_bbox_to_target_size(
    bbox: Dict[str, float],
    target_mb: float = 400,
    num_bands: int = 1,
    num_timesteps: int = 1,
    resolution_deg: float = 0.0001,  # ~10m at equator (Sentinel-2)
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """
    Constrain a bounding box so the estimated output GeoTIFF is ~target_mb.

    Calculation:
      pixels = (width_deg / resolution_deg) * (height_deg / resolution_deg)
      file_bytes = pixels * num_bands * num_timesteps * 4 (float32)

    If the estimated size exceeds target_mb * 1.1, the bbox is shrunk
    proportionally around its center. Returns (constrained_bbox, info_dict).
    """
    import math

    width_deg = bbox["east"] - bbox["west"]
    height_deg = bbox["north"] - bbox["south"]

    width_px = width_deg / resolution_deg
    height_px = height_deg / resolution_deg
    current_pixels = width_px * height_px
    bytes_per_pixel = num_bands * num_timesteps * 8  # float64 (OpenEO outputs float64)
    current_size_bytes = current_pixels * bytes_per_pixel
    current_size_mb = current_size_bytes / (1024 * 1024)

    target_bytes = target_mb * 1024 * 1024

    info: Dict[str, Any] = {
        "estimated_size_mb": round(current_size_mb, 1),
        "target_size_mb": target_mb,
        "num_bands": num_bands,
        "num_timesteps": num_timesteps,
        "constrained": False,
        "load_check": load_check(bbox, num_bands, num_timesteps),
    }

    # Within 10% tolerance — no change needed
    if current_size_bytes <= target_bytes * 1.1:
        return bbox, info

    # Shrink proportionally around center
    target_pixels = target_bytes / bytes_per_pixel
    scale = math.sqrt(target_pixels / current_pixels)

    center_lon = (bbox["west"] + bbox["east"]) / 2
    center_lat = (bbox["south"] + bbox["north"]) / 2

    new_width = width_deg * scale
    new_height = height_deg * scale

    constrained_bbox = {
        "west": round(center_lon - new_width / 2, 4),
        "south": round(center_lat - new_height / 2, 4),
        "east": round(center_lon + new_width / 2, 4),
        "north": round(center_lat + new_height / 2, 4),
    }

    info["constrained"] = True
    info["original_bbox"] = bbox
    info["scale_factor"] = round(scale, 3)
    info["constrained_size_mb"] = round(target_mb, 1)
    info["original_size_mb"] = round(current_size_mb, 1)

    logger.info(
        "Constrained bbox from %.1f MB to ~%.0f MB (scale=%.3f) for %s",
        current_size_mb, target_mb, scale,
        f"{new_width:.2f}° x {new_height:.2f}°",
    )

    return constrained_bbox, info


# Module-level instance for convenience
_service: Optional[GeocodingService] = None


def get_service() -> GeocodingService:
    """Get or create the geocoding service singleton."""
    global _service
    if _service is None:
        _service = GeocodingService()
    return _service


def resolve_location(
    location: str,
    buffer_deg: float = DEFAULT_BUFFER_DEG
) -> LocationResult:
    """
    Resolve a location string to a bounding box.

    This is the main entry point for location resolution.

    Args:
        location: Location name, coordinates, or description
        buffer_deg: Buffer to add around point locations (degrees)

    Returns:
        LocationResult with bounding box or error

    Examples:
        >>> result = resolve_location("Mumbai")
        >>> result.bbox
        {'west': 72.77, 'south': 18.89, 'east': 72.98, 'north': 19.27}

        >>> result = resolve_location("28.6139, 77.2090")
        >>> result.source
        'coordinates'
    """
    return get_service().resolve(location, buffer_deg)


# Convenience function for tools
async def resolve_location_async(
    location: str,
    buffer_deg: float = DEFAULT_BUFFER_DEG
) -> Dict[str, Any]:
    """
    Async wrapper for location resolution (for tool use).

    Returns dict suitable for JSON response.
    """
    result = resolve_location(location, buffer_deg)
    return result.to_dict()
