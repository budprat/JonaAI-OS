# open-water surface area (NDWI-derived) over Shasta Lake, Shasta County, California, United States fell -1 ha across the ~1-month window (Mann-Kendall p=0.631, not statistically significant) — a within-season change, likely seasonal hydrology (refill/drawdown), not a multi-year trend.

## Decision context
Monitor reservoir water level and extent changes for water resource management and drought assessment

## Evidence
- Target window: 12 clear scenes used of 12 discovered.
- Median open-water surface area = 8 ha (mean 8 ha, scene-to-scene σ 2 ha).
- Mean valid-pixel fraction after cloud masking: 99%.

## Trend & change
- Net change: -1 ha over the ~1-month window (annualised per-year rate withheld — a within-season signal, likely seasonal hydrology).
- Mann-Kendall: S=-8, Z=-0.48, p=0.631.

## Confidence: Medium
- 12 valid scenes — good temporal sampling.
- Change/trend is NOT statistically significant — treat direction as tentative.

## Caveats & alternative explanations
- Assumption: Cloud threshold not stated → defaulted to 20% max cloud cover.
- Trend window is 0.08 yr (<1.5 yr): the change reflects WITHIN-SEASON hydrology (refill/drawdown), not a multi-year trend. Interpret the net change across the window and compare to the same season last year before attributing drought or loss.
- Surface area is derived from NDWI>0 pixel counts on a coarse overview grid — shoreline mixed pixels limit precision; treat changes smaller than a few grid cells as noise and corroborate with gauge data.
- AOI reduced at a 128px overview — a spatial-median signal, not a per-field map.

## Recommended actions
- Monitor Shasta Lake, Shasta County, California, United States on the next clear acquisition; current signal does not cross an action threshold. Ground-truth any suspected change before intervening.

## Data lineage
- **sensor / product:** Sentinel-2 L2A (surface reflectance), Collection sentinel-2-l2a
- **source:** Element84 Earth Search v1 (earth-search.aws.element84.com), public COGs
- **AOI:** Shasta Lake, Shasta County, California, United States | bbox [-122.411328, 40.650921, -122.336985, 40.709755]
- **target period:** 2025-05-01 … 2025-05-31
- **cloud filter:** eo:cloud_cover < 20%
- **cloud mask:** Scene Classification Layer (SCL), keep {4,5,6,7}
- **reflectance:** per-item raster:bands scale/offset; earthsearch:boa_offset_applied items are pre-corrected → effective offset 0 (reflectance = DN/10000)
- **index formula:** (Green−NIR)/(Green+NIR); water = NDWI > 0; area = water-pixel fraction × AOI bbox area (ha)
- **target scene ids:** ['S2C_10TEL_20250521_0_L2A', 'S2A_10TEL_20250530_0_L2A', 'S2C_10TEL_20250528_1_L2A', 'S2C_10TEL_20250518_0_L2A', 'S2C_10TEL_20250508_0_L2A', 'S2B_10TEL_20250506_0_L2A', 'S2C_10TEL_20250501_0_L2A', 'S2A_10TEL_20250510_0_L2A', 'S2C_10TEL_20250531_0_L2A', 'S2B_10TEL_20250516_0_L2A', 'S2B_10TEL_20250523_0_L2A', 'S2B_10TEL_20250526_0_L2A']
- **plan_hash:** 70632aae3cf2a612
- **pipeline_integrity:** 100% of steps passed validators
- **run date:** 2026-07-12

