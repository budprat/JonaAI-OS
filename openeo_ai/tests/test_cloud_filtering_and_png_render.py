import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import xarray as xr
from PIL import Image

from openeo_app.processes.load_collection import _build_stac_query, _sort_stac_items_by_cloud_cover
from openeo_app.storage.results import ResultStorage


class CloudFilteringAndPngRenderTests(unittest.TestCase):
    def test_build_stac_query_supports_nested_property_operators(self):
        query = _build_stac_query({"eo:cloud_cover": {"lte": 20}, "platform": "sentinel-2a"})
        self.assertEqual(query, {"eo:cloud_cover": {"lte": 20}, "platform": {"eq": "sentinel-2a"}})

    def test_sort_stac_items_by_cloud_cover_prefers_lowest_cloud_first(self):
        items = [
            SimpleNamespace(properties={"eo:cloud_cover": 75.0}, id="cloudy"),
            SimpleNamespace(properties={"eo:cloud_cover": 14.0}, id="clearer"),
            SimpleNamespace(properties={"eo:cloud_cover": 30.0}, id="mid"),
        ]
        sorted_items = _sort_stac_items_by_cloud_cover(items)
        self.assertEqual([item.id for item in sorted_items], ["clearer", "mid", "cloudy"])

    def test_result_storage_png_stretches_rgb_channels_independently(self):
        data = xr.DataArray(
            np.array(
                [
                    [[0.0, 1.0], [2.0, 100.0]],
                    [[0.0, 10.0], [20.0, 30.0]],
                    [[5.0, 5.0], [5.0, 8.0]],
                ],
                dtype=np.float32,
            ),
            dims=("bands", "y", "x"),
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = ResultStorage(base_path=tmp_dir)
            path = storage.save_result(uuid.uuid4(), data, format="PNG")
            image = Image.open(path).convert("RGB")
            arr = np.asarray(image)

        self.assertEqual(arr.shape, (2, 2, 3))
        self.assertFalse(np.array_equal(arr[:, :, 0], arr[:, :, 1]))
        self.assertFalse(np.array_equal(arr[:, :, 1], arr[:, :, 2]))

    def test_result_storage_png_reduces_singleton_time_dimension(self):
        data = xr.DataArray(
            np.array(
                [
                    [[[0.0, 1.0], [2.0, 100.0]]],
                    [[[0.0, 10.0], [20.0, 30.0]]],
                    [[[5.0, 5.0], [5.0, 8.0]]],
                ],
                dtype=np.float32,
            ),
            dims=("bands", "time", "y", "x"),
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = ResultStorage(base_path=tmp_dir)
            path = storage.save_result(uuid.uuid4(), data, format="PNG")
            image = Image.open(path).convert("RGB")
            arr = np.asarray(image)

        self.assertEqual(arr.shape, (2, 2, 3))


if __name__ == "__main__":
    unittest.main()
