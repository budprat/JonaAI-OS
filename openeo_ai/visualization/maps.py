# ABOUTME: MCP-UI compatible map visualization components.
# Renders rasters on interactive maps with colormaps, legends, and comparison sliders.

"""
Map visualization components for OpenEO AI Assistant.

Provides MCP-UI compatible map components for displaying:
- Raster data on interactive maps
- NDVI and other index visualizations
- Before/after comparison sliders
"""

import base64
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# Colormap definitions
COLORMAPS = {
    "viridis": [
        (68, 1, 84), (72, 35, 116), (64, 67, 135), (52, 94, 141),
        (41, 120, 142), (32, 144, 140), (34, 167, 132), (68, 190, 112),
        (121, 209, 81), (189, 222, 38), (253, 231, 37)
    ],
    "plasma": [
        (13, 8, 135), (75, 3, 161), (125, 3, 168), (168, 34, 150),
        (203, 70, 121), (229, 107, 93), (248, 148, 65), (253, 195, 40),
        (240, 249, 33)
    ],
    "inferno": [
        (0, 0, 4), (22, 11, 57), (66, 10, 104), (106, 23, 110),
        (147, 38, 103), (188, 55, 84), (221, 81, 58), (243, 118, 27),
        (252, 165, 10), (246, 215, 70), (252, 255, 164)
    ],
    "ndvi": [
        (165, 0, 38), (215, 48, 39), (244, 109, 67), (253, 174, 97),
        (254, 224, 139), (255, 255, 191), (217, 239, 139), (166, 217, 106),
        (102, 189, 99), (26, 152, 80), (0, 104, 55)
    ],
    "terrain": [
        (0, 97, 71), (16, 122, 47), (232, 215, 125), (161, 67, 0),
        (130, 30, 30), (161, 161, 161), (206, 206, 206), (255, 255, 255)
    ],
    "coolwarm": [
        (59, 76, 192), (103, 136, 238), (154, 187, 255), (201, 215, 240),
        (237, 209, 194), (247, 168, 137), (226, 105, 82), (180, 4, 38)
    ],
    "grayscale": [
        (0, 0, 0), (51, 51, 51), (102, 102, 102), (153, 153, 153),
        (204, 204, 204), (255, 255, 255)
    ],
    "magma": [
        (0, 0, 4), (24, 15, 61), (68, 15, 118), (114, 31, 129),
        (158, 47, 127), (205, 64, 113), (241, 96, 93), (253, 150, 104),
        (254, 202, 141), (252, 253, 191)
    ],
    "cividis": [
        (0, 32, 77), (0, 65, 108), (52, 94, 112), (101, 114, 107),
        (142, 133, 96), (181, 155, 74), (219, 180, 44), (253, 210, 37)
    ],
    "rdylgn": [
        (165, 0, 38), (215, 48, 39), (244, 109, 67), (253, 174, 97),
        (254, 224, 139), (255, 255, 191), (217, 239, 139), (166, 217, 106),
        (102, 189, 99), (26, 152, 80), (0, 104, 55)
    ],
    "rdylbu": [
        (165, 0, 38), (215, 48, 39), (244, 109, 67), (253, 174, 97),
        (254, 224, 144), (255, 255, 191), (224, 243, 248), (171, 217, 233),
        (116, 173, 209), (69, 117, 180), (49, 54, 149)
    ],
    "spectral": [
        (158, 1, 66), (213, 62, 79), (244, 109, 67), (253, 174, 97),
        (254, 224, 139), (255, 255, 191), (230, 245, 152), (171, 221, 164),
        (102, 194, 165), (50, 136, 189), (94, 79, 162)
    ],
    "rdbu": [
        (103, 0, 31), (178, 24, 43), (214, 96, 77), (244, 165, 130),
        (253, 219, 199), (247, 247, 247), (209, 229, 240), (146, 197, 222),
        (67, 147, 195), (33, 102, 172), (5, 48, 97)
    ],
    "ylgnbu": [
        (255, 255, 217), (237, 248, 177), (199, 233, 180), (127, 205, 187),
        (65, 182, 196), (29, 145, 192), (34, 94, 168), (37, 52, 148),
        (8, 29, 88)
    ],
    "hot": [
        (10, 0, 0), (125, 0, 0), (255, 0, 0), (255, 85, 0),
        (255, 170, 0), (255, 255, 0), (255, 255, 85), (255, 255, 170),
        (255, 255, 255)
    ],
    "ylorrd": [
        (255, 255, 204), (255, 237, 160), (254, 217, 118), (254, 178, 76),
        (253, 141, 60), (252, 78, 42), (227, 26, 28), (189, 0, 38),
        (128, 0, 38)
    ],
    "ylorbr": [
        (255, 255, 229), (255, 247, 188), (254, 227, 145), (254, 196, 79),
        (254, 153, 41), (236, 112, 20), (204, 76, 2), (153, 52, 4),
        (102, 37, 6)
    ],
    "set2": [
        (102, 194, 165), (252, 141, 98), (141, 160, 203), (231, 138, 195),
        (166, 216, 84), (255, 217, 47), (229, 196, 148), (179, 179, 179)
    ],
}


class MapComponent:
    """
    Create MCP-UI compatible map visualizations.

    Generates interactive map components with raster overlays,
    legends, and comparison tools.
    """

    def __init__(self, default_colormap: str = "viridis"):
        """
        Initialize map component.

        Args:
            default_colormap: Default colormap for raster display
        """
        self.default_colormap = default_colormap

    async def create_raster_map(
        self,
        geotiff_path: str,
        title: str = "Result",
        colormap: str = "viridis",
        vmin: Optional[float] = None,
        vmax: Optional[float] = None,
        opacity: float = 0.5
    ) -> Dict[str, Any]:
        """
        Create a map component with raster overlay.

        Args:
            geotiff_path: Path to GeoTIFF file
            title: Map title
            colormap: Colormap name
            vmin: Minimum value for colormap
            vmax: Maximum value for colormap
            opacity: Raster opacity (0-1)

        Returns:
            MCP-UI component specification
        """
        logger.info(f"Creating raster map for {geotiff_path}")

        # Load raster data
        data, metadata = self._load_raster(geotiff_path)
        nodata = metadata.get("nodata")

        # Calculate bounds
        bounds = self._get_bounds(metadata)
        center = self._get_center(bounds)

        # Handle multi-band RGB differently from single-band colormapped
        if metadata.get("rgb"):
            # Multi-band: compose RGBA directly from RGB bands (3, H, W)
            colored_image = self._compose_rgb(data, nodata=nodata)
            # Reason: vmin/vmax and colormap are meaningless for RGB data
            valid_data = self._get_valid_data(data, nodata)
            vmin = vmin if vmin is not None else float(np.nanmin(valid_data)) if valid_data.size > 0 else 0.0
            vmax = vmax if vmax is not None else float(np.nanmax(valid_data)) if valid_data.size > 0 else 1.0
        else:
            # Single-band: apply colormap
            valid_data = self._get_valid_data(data, nodata)
            vmin = vmin if vmin is not None else float(np.nanmin(valid_data)) if valid_data.size > 0 else 0.0
            vmax = vmax if vmax is not None else float(np.nanmax(valid_data)) if valid_data.size > 0 else 1.0
            colored_image = self._apply_colormap(data, colormap, vmin, vmax, nodata=nodata)

        # Encode as base64
        image_data = self._encode_image(colored_image)

        # Generate legend
        legend = self._create_legend(colormap, vmin, vmax)

        return {
            "type": "map",
            "spec": {
                "title": title,
                "center": center,
                "zoom": 12,
                "layers": [
                    {
                        "type": "raster",
                        "bounds": bounds,
                        "url": f"data:image/png;base64,{image_data}",
                        "source": geotiff_path,
                        "opacity": opacity,
                        "colormap": colormap,
                        "vmin": vmin,
                        "vmax": vmax
                    }
                ],
                "colorbar": {
                    "min": vmin,
                    "max": vmax,
                    "colormap": colormap
                },
                "controls": ["zoom", "scale", "fullscreen"]
            }
        }

    async def create_ndvi_map(
        self,
        geotiff_path: str,
        title: str = "NDVI"
    ) -> Dict[str, Any]:
        """
        Create an NDVI visualization map.

        Args:
            geotiff_path: Path to NDVI GeoTIFF
            title: Map title

        Returns:
            MCP-UI component specification
        """
        logger.info(f"Creating NDVI map for {geotiff_path}")

        # Load raster data
        data, metadata = self._load_raster(geotiff_path)
        nodata = metadata.get("nodata")

        # Calculate bounds
        bounds = self._get_bounds(metadata)
        center = self._get_center(bounds)

        # NDVI specific settings
        vmin = -1.0
        vmax = 1.0
        colormap = "ndvi"

        # Generate colored image (nodata pixels → transparent)
        colored_image = self._apply_colormap(data, "ndvi", vmin, vmax, nodata=nodata)
        image_data = self._encode_image(colored_image)

        return {
            "type": "map",
            "spec": {
                "title": title,
                "center": center,
                "zoom": 12,
                "layers": [
                    {
                        "type": "raster",
                        "bounds": bounds,
                        "url": f"data:image/png;base64,{image_data}",
                        "source": geotiff_path,
                        "opacity": 0.5,
                        "colormap": colormap,
                        "vmin": vmin,
                        "vmax": vmax
                    }
                ],
                "colorbar": {
                    "min": vmin,
                    "max": vmax,
                    "colormap": colormap
                }
            }
        }

    async def create_comparison_slider(
        self,
        before_path: str,
        after_path: str,
        title: str = "Before / After",
        colormap: str = "viridis"
    ) -> Dict[str, Any]:
        """
        Create a before/after comparison slider.

        Args:
            before_path: Path to before image
            after_path: Path to after image
            title: Comparison title
            colormap: Colormap for both images

        Returns:
            MCP-UI component specification
        """
        logger.info(f"Creating comparison: {before_path} vs {after_path}")

        # Load both images
        before_data, before_meta = self._load_raster(before_path)
        after_data, after_meta = self._load_raster(after_path)
        before_nodata = before_meta.get("nodata")
        after_nodata = after_meta.get("nodata")

        # Calculate common bounds
        bounds = self._get_bounds(before_meta)
        center = self._get_center(bounds)

        # Calculate common value range (excluding nodata)
        before_valid = self._get_valid_data(before_data, before_nodata)
        after_valid = self._get_valid_data(after_data, after_nodata)
        vmin = float(min(
            np.nanmin(before_valid) if before_valid.size > 0 else 0,
            np.nanmin(after_valid) if after_valid.size > 0 else 0,
        ))
        vmax = float(max(
            np.nanmax(before_valid) if before_valid.size > 0 else 1,
            np.nanmax(after_valid) if after_valid.size > 0 else 1,
        ))

        # Generate colored images (nodata → transparent)
        before_colored = self._apply_colormap(before_data, colormap, vmin, vmax, nodata=before_nodata)
        after_colored = self._apply_colormap(after_data, colormap, vmin, vmax, nodata=after_nodata)

        # Encode as base64
        before_b64 = self._encode_image(before_colored)
        after_b64 = self._encode_image(after_colored)

        return {
            "type": "comparison_slider",
            "spec": {
                "title": title,
                "center": center,
                "zoom": 12,
                "before": {
                    "label": "Before",
                    "bounds": bounds,
                    "source": before_path,
                    "url": f"data:image/png;base64,{before_b64}"
                },
                "after": {
                    "label": "After",
                    "bounds": bounds,
                    "source": after_path,
                    "url": f"data:image/png;base64,{after_b64}"
                },
                "colorbar": {
                    "min": vmin,
                    "max": vmax,
                    "colormap": colormap
                },
                "initial_position": 50
            }
        }

    async def create_multi_layer_map(
        self,
        layers: List[Dict[str, Any]],
        title: str = "Multi-Layer View",
        center: Optional[List[float]] = None,
        zoom: int = 10
    ) -> Dict[str, Any]:
        """
        Create a map with multiple toggleable layers.

        Args:
            layers: List of layer specifications
            title: Map title
            center: Map center [lat, lon]
            zoom: Initial zoom level

        Returns:
            MCP-UI component specification
        """
        processed_layers = []
        all_bounds = []

        for layer_spec in layers:
            if layer_spec.get("type") == "raster" and "path" in layer_spec:
                data, metadata = self._load_raster(layer_spec["path"])
                nodata = metadata.get("nodata")
                bounds = self._get_bounds(metadata)
                all_bounds.append(bounds)

                colormap = layer_spec.get("colormap", self.default_colormap)
                valid_data = self._get_valid_data(data, nodata)
                vmin = layer_spec.get("vmin", float(np.nanmin(valid_data)) if valid_data.size > 0 else 0.0)
                vmax = layer_spec.get("vmax", float(np.nanmax(valid_data)) if valid_data.size > 0 else 1.0)

                colored = self._apply_colormap(data, colormap, vmin, vmax, nodata=nodata)
                image_data = self._encode_image(colored)

                processed_layers.append({
                    "type": "image",
                    "name": layer_spec.get("name", "Layer"),
                    "bounds": bounds,
                    "url": f"data:image/png;base64,{image_data}",
                    "opacity": layer_spec.get("opacity", 0.5),
                    "visible": layer_spec.get("visible", True)
                })
            else:
                processed_layers.append(layer_spec)

        # Calculate center from bounds if not provided
        if center is None and all_bounds:
            center = self._get_center(all_bounds[0])

        return {
            "type": "map",
            "spec": {
                "title": title,
                "center": center or [0, 0],
                "zoom": zoom,
                "layers": processed_layers,
                "layerControl": True,
                "controls": ["zoom", "scale", "fullscreen", "layers"]
            }
        }

    def _load_raster(self, path: str) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Load raster data from GeoTIFF."""
        try:
            import rasterio

            with rasterio.open(path) as src:
                if src.count >= 3:
                    # Multi-band: read as RGB (bands 1-3)
                    data = src.read([1, 2, 3])  # shape: (3, H, W)
                    metadata = {
                        "crs": str(src.crs),
                        "transform": list(src.transform),
                        "bounds": list(src.bounds),
                        "width": src.width,
                        "height": src.height,
                        "nodata": src.nodata,
                        "rgb": True,
                        "band_count": src.count,
                    }
                    logger.info(f"Multi-band raster ({src.count} bands): rendering as RGB")
                else:
                    data = src.read(1)  # Single band
                    metadata = {
                        "crs": str(src.crs),
                        "transform": list(src.transform),
                        "bounds": list(src.bounds),
                        "width": src.width,
                        "height": src.height,
                        "nodata": src.nodata,
                    }

            return data, metadata

        except ImportError:
            logger.warning("rasterio not available, using fallback")
            return self._load_raster_fallback(path)

    def _load_raster_fallback(self, path: str) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Fallback raster loader using PIL."""
        from PIL import Image

        img = Image.open(path)
        data = np.array(img)

        if data.ndim == 3:
            data = data[:, :, 0]  # Use first channel

        # Assume WGS84 and small region if no georef
        metadata = {
            "bounds": [0, 0, 1, 1],  # Default bounds
            "width": data.shape[1],
            "height": data.shape[0]
        }

        return data, metadata

    def _get_bounds(self, metadata: Dict[str, Any]) -> List[List[float]]:
        """Extract bounds as [[south, west], [north, east]]."""
        if "bounds" in metadata:
            b = metadata["bounds"]
            return [[b[1], b[0]], [b[3], b[2]]]  # [minlat, minlon], [maxlat, maxlon]
        return [[0, 0], [1, 1]]

    def _get_center(self, bounds: List[List[float]]) -> List[float]:
        """Calculate center from bounds."""
        lat = (bounds[0][0] + bounds[1][0]) / 2
        lon = (bounds[0][1] + bounds[1][1]) / 2
        return [lat, lon]

    def _get_valid_data(self, data: np.ndarray, nodata: Optional[float] = None) -> np.ndarray:
        """Return a 1-D array of valid (non-nodata, non-NaN) pixels."""
        if np.issubdtype(data.dtype, np.floating):
            mask = np.isnan(data)
        else:
            mask = np.zeros(data.shape, dtype=bool)
        if nodata is not None:
            mask |= np.isclose(data.astype(float), float(nodata))
        return data[~mask]

    def _compose_rgb(
        self,
        data: np.ndarray,
        nodata: Optional[float] = None,
    ) -> np.ndarray:
        """Compose RGBA image from multi-band RGB data (3, H, W) → (H, W, 4)."""
        # Reason: RGB rasters (true-color composites) should display directly,
        # not be passed through a single-band colormap.
        bands, h, w = data.shape

        # Build nodata/NaN mask across all bands
        if np.issubdtype(data.dtype, np.floating):
            mask = np.any(np.isnan(data), axis=0)  # (H, W)
        else:
            mask = np.zeros((h, w), dtype=bool)
        if nodata is not None:
            mask |= np.any(np.isclose(data.astype(float), float(nodata)), axis=0)

        # Normalize each band to 0-255 uint8
        result = np.zeros((h, w, 4), dtype=np.uint8)
        for c in range(3):
            band = data[c].astype(float)
            bmin = float(np.nanmin(band[~mask])) if np.any(~mask) else 0.0
            bmax = float(np.nanmax(band[~mask])) if np.any(~mask) else 1.0
            if bmax - bmin < 1e-10:
                bmax = bmin + 1.0
            normalized = np.clip(np.nan_to_num((band - bmin) / (bmax - bmin), nan=0.0), 0, 1)
            result[:, :, c] = (normalized * 255).astype(np.uint8)

        # Alpha: opaque for valid pixels, transparent for nodata
        result[:, :, 3] = 255
        result[mask, 3] = 0

        logger.info(f"Composed RGB image: {w}x{h}, {np.sum(~mask)} valid pixels")
        return result

    def _apply_colormap(
        self,
        data: np.ndarray,
        colormap: str,
        vmin: float,
        vmax: float,
        nodata: Optional[float] = None,
    ) -> np.ndarray:
        """Apply colormap to data array. Nodata/NaN pixels become transparent."""
        # Case-insensitive colormap lookup: normalize to lowercase, strip separators
        colormap_key = colormap.lower().replace("_", "").replace("-", "")
        colors = COLORMAPS.get(colormap_key, COLORMAPS.get(colormap, COLORMAPS["viridis"]))

        # Normalize data to 0-1
        normalized = np.clip((data - vmin) / (vmax - vmin + 1e-10), 0, 1)

        # Build transparency mask: NaN (float arrays) + nodata sentinel
        if np.issubdtype(data.dtype, np.floating):
            mask = np.isnan(data)
        else:
            mask = np.zeros(data.shape, dtype=bool)
        if nodata is not None:
            mask |= np.isclose(data.astype(float), float(nodata))
        normalized[mask] = 0

        # Interpolate colors
        n_colors = len(colors)
        indices = normalized * (n_colors - 1)
        lower_idx = np.floor(indices).astype(int)
        upper_idx = np.ceil(indices).astype(int)
        frac = indices - lower_idx

        # Clamp indices
        lower_idx = np.clip(lower_idx, 0, n_colors - 1)
        upper_idx = np.clip(upper_idx, 0, n_colors - 1)

        # Interpolate RGB
        result = np.zeros((*data.shape, 4), dtype=np.uint8)

        for c in range(3):
            lower_colors = np.array([colors[i][c] for i in range(n_colors)])
            upper_colors = np.array([colors[i][c] for i in range(n_colors)])

            lower_vals = lower_colors[lower_idx]
            upper_vals = upper_colors[upper_idx]

            result[:, :, c] = (lower_vals * (1 - frac) + upper_vals * frac).astype(np.uint8)

        # Alpha channel: opaque for valid data, transparent for nodata/NaN
        result[:, :, 3] = 255
        result[mask, 3] = 0

        return result

    def _encode_image(self, rgba_array: np.ndarray) -> str:
        """Encode RGBA array as base64 PNG."""
        from PIL import Image
        import io

        img = Image.fromarray(rgba_array, mode="RGBA")

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)

        return base64.b64encode(buffer.read()).decode("utf-8")

    def _create_legend(
        self,
        colormap: str,
        vmin: float,
        vmax: float
    ) -> Dict[str, Any]:
        """Create legend specification."""
        colors = COLORMAPS.get(colormap, COLORMAPS["viridis"])

        return {
            "type": "gradient",
            "title": "Value",
            "colors": [f"rgb({c[0]},{c[1]},{c[2]})" for c in colors],
            "labels": [
                f"{vmin:.2f}",
                f"{(vmin + vmax) / 2:.2f}",
                f"{vmax:.2f}"
            ]
        }
