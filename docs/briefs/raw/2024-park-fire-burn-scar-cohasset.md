# burn index (NBR) over Cohasset, Itasca County, Minnesota, United States rose +0.042 vs the preceding period (median 0.510 → 0.552; significance untested).

## Decision context
Track post-fire recovery and assess burn severity changes in the affected area

## Evidence
- Target window: 1 clear scenes used of 1 discovered.
- AOI median NBR = 0.552 (mean 0.552, scene-to-scene σ 0.000).
- Baseline window: 1 clear scenes, median 0.510.
- Mean valid-pixel fraction after cloud masking: 100%.

## Trend & change
- Δ median: +0.042 (0.510 → 0.552).

## Confidence: Low
- Only 1 valid scene(s) — very thin evidence.
- Change/trend is NOT statistically significant — treat direction as tentative.

## Caveats & alternative explanations
- Assumption: Cloud threshold not stated → defaulted to 20% max cloud cover.
- Assumption: Change baseline not stated → using the preceding equal-length window (2024-05-31 … 2024-06-30).
- Fewer than 2 scenes on one side — significance test skipped.
- Seasonal/phenology confound: compare like phenophase before attributing change to stress.
- AOI reduced at a 128px overview — a spatial-median signal, not a per-field map.

## Recommended actions
- Monitor Cohasset, Itasca County, Minnesota, United States on the next clear acquisition; current signal does not cross an action threshold. Ground-truth any suspected change before intervening.

## Data lineage
- **sensor / product:** Sentinel-2 L2A (surface reflectance), Collection sentinel-2-l2a
- **source:** Element84 Earth Search v1 (earth-search.aws.element84.com), public COGs
- **AOI:** Cohasset, Itasca County, Minnesota, United States | bbox [-93.7032675, 47.1951431, -93.5763424, 47.2822072]
- **target period:** 2024-07-01 … 2024-07-31
- **cloud filter:** eo:cloud_cover < 20%
- **cloud mask:** Scene Classification Layer (SCL), keep {4,5,6,7}
- **reflectance:** per-item raster:bands scale/offset; earthsearch:boa_offset_applied items are pre-corrected → effective offset 0 (reflectance = DN/10000)
- **index formula:** (NIR−SWIR22)/(NIR+SWIR22)
- **target scene ids:** ['S2B_15TVN_20240727_0_L2A']
- **plan_hash:** d5a778fd83f46db7
- **pipeline_integrity:** 100% of steps passed validators
- **run date:** 2026-07-12
- **baseline period:** 2024-05-31 … 2024-06-30
- **baseline scene ids:** ['S2A_15TVN_20240612_0_L2A']

