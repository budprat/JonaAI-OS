# burn index (NBR) over Butte County, California, United States fell -0.003 vs the preceding period (median 0.188 → 0.186; not significant, p=0.332).

## Decision context
(not specified)

## Evidence
- Target window: 5 clear scenes used of 12 discovered.
- AOI median NBR = 0.186 (mean 0.204, scene-to-scene σ 0.048).
- Baseline window: 5 clear scenes, median 0.188.
- Mean valid-pixel fraction after cloud masking: 59%.

## Trend & change
- Δ median: -0.003 (0.188 → 0.186).
- Welch t-test on per-scene medians: t=-1.05, p=0.332.

## Confidence: Low
- 5 valid scenes — moderate temporal sampling.
- Mean valid-pixel fraction 59% (<70%) — masking may bias the median.
- Change/trend is NOT statistically significant — treat direction as tentative.

## Caveats & alternative explanations
- Assumption: Cloud threshold not stated → defaulted to 20% max cloud cover.
- Assumption: Change baseline not stated → using the preceding equal-length window (2024-07-01 … 2024-07-31).
- Assumption: AOI 'Butte County, California, United States' spans a large area → analysed a centred 0.30°×0.30° (~33 km) window to keep pixels tile-consistent.
- 7/12 scenes rejected (valid fraction < 0.5).
- Seasonal/phenology confound: compare like phenophase before attributing change to stress.
- AOI reduced at a 128px overview — a spatial-median signal, not a per-field map.

## Recommended actions
- Monitor Butte County, California, United States on the next clear acquisition; current signal does not cross an action threshold. Ground-truth any suspected change before intervening.

## Data lineage
- **sensor / product:** Sentinel-2 L2A (surface reflectance), Collection sentinel-2-l2a
- **source:** Element84 Earth Search v1 (earth-search.aws.element84.com), public COGs
- **AOI:** Butte County, California, United States | bbox [-121.7229219, 39.57373805, -121.42292189999999, 39.87373805]
- **target period:** 2024-08-01 … 2024-08-31
- **cloud filter:** eo:cloud_cover < 20%
- **cloud mask:** Scene Classification Layer (SCL), keep {4,5,6,7}
- **reflectance:** per-item raster:bands scale/offset; earthsearch:boa_offset_applied items are pre-corrected → effective offset 0 (reflectance = DN/10000)
- **index formula:** (NIR−SWIR22)/(NIR+SWIR22)
- **target scene ids:** ['S2B_10SFJ_20240816_0_L2A', 'S2A_10SFJ_20240821_0_L2A', 'S2A_10SEJ_20240831_0_L2A', 'S2A_10SEJ_20240821_0_L2A', 'S2A_10SFJ_20240831_0_L2A', 'S2A_10TEK_20240821_0_L2A', 'S2B_10SFJ_20240806_0_L2A', 'S2B_10SEJ_20240816_0_L2A', 'S2B_10TEK_20240816_0_L2A', 'S2A_10TEK_20240801_0_L2A', 'S2B_10TFK_20240816_0_L2A', 'S2A_10TEK_20240831_0_L2A']
- **plan_hash:** 00d98b372fcde77b
- **pipeline_integrity:** 100% of steps passed validators
- **run date:** 2026-07-12
- **baseline period:** 2024-07-01 … 2024-07-31
- **baseline scene ids:** ['S2A_10TEK_20240712_0_L2A', 'S2A_10TFK_20240702_0_L2A', 'S2A_10SFJ_20240712_0_L2A', 'S2B_10SFJ_20240707_0_L2A', 'S2A_10SEJ_20240722_0_L2A', 'S2B_10TFK_20240707_0_L2A', 'S2B_10SEJ_20240717_0_L2A', 'S2B_10TEK_20240717_0_L2A', 'S2B_10TEK_20240707_0_L2A', 'S2B_10SFJ_20240717_0_L2A', 'S2A_10SEJ_20240712_0_L2A', 'S2B_10SEJ_20240707_0_L2A']

