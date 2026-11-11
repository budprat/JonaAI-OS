# vegetation vigour (NDVI) over Kings County, California, United States fell -0.085 vs the same season last year (median 0.293 → 0.208; significant, p=0.000).

## Decision context
(not specified)

## Evidence
- Target window: 10 clear scenes used of 12 discovered.
- AOI median NDVI = 0.208 (mean 0.210, scene-to-scene σ 0.011).
- Baseline window: 8 clear scenes, median 0.293.
- Mean valid-pixel fraction after cloud masking: 89%.

## Trend & change
- Δ median: -0.085 (0.293 → 0.208).
- Welch t-test on per-scene medians: t=-8.98, p=0.000.

## Confidence: High
- 10 valid scenes — good temporal sampling.
- Change/trend is statistically significant (p<0.05).

## Caveats & alternative explanations
- Assumption: Cloud threshold not stated → defaulted to 20% max cloud cover.
- Assumption: Anomaly baseline not stated → using the same calendar window one year earlier (2024-07-01 … 2024-07-31).
- Assumption: AOI 'Kings County, California, United States' spans a large area → analysed a centred 0.30°×0.30° (~33 km) window to keep pixels tile-consistent.
- 2/12 scenes rejected (valid fraction < 0.5).
- 4/12 scenes rejected (valid fraction < 0.5).
- NDVI saturates above ~0.8 in dense canopy; use EVI where vigour is very high.
- AOI reduced at a 128px overview — a spatial-median signal, not a per-field map.

## Recommended actions
- Continue routine monitoring of Kings County, California, United States; Δ-0.09 is within normal variation.

## Data lineage
- **sensor / product:** Sentinel-2 L2A (surface reflectance), Collection sentinel-2-l2a
- **source:** Element84 Earth Search v1 (earth-search.aws.element84.com), public COGs
- **AOI:** Kings County, California, United States | bbox [-120.04474415, 35.98873095, -119.74474414999999, 36.288730949999994]
- **target period:** 2025-07-01 … 2025-07-31
- **cloud filter:** eo:cloud_cover < 20%
- **cloud mask:** Scene Classification Layer (SCL), keep {4,5,6,7}
- **reflectance:** per-item raster:bands scale/offset; earthsearch:boa_offset_applied items are pre-corrected → effective offset 0 (reflectance = DN/10000)
- **index formula:** (NIR−Red)/(NIR+Red)
- **target scene ids:** ['S2B_10SGF_20250709_0_L2A', 'S2B_10SGF_20250719_0_L2A', 'S2A_10SGF_20250706_0_L2A', 'S2A_11SKA_20250706_0_L2A', 'S2C_11SKA_20250714_0_L2A', 'S2B_11SKA_20250709_0_L2A', 'S2B_11SKA_20250719_0_L2A', 'S2C_10SGF_20250714_0_L2A', 'S2C_10SGF_20250704_0_L2A', 'S2C_11SKA_20250704_0_L2A', 'S2B_10SGE_20250709_0_L2A', 'S2C_10SGE_20250714_1_L2A']
- **plan_hash:** a0f7034b1d8c4060
- **pipeline_integrity:** 100% of steps passed validators
- **run date:** 2026-07-12
- **baseline period:** 2024-07-01 … 2024-07-31
- **baseline scene ids:** ['S2B_11SKA_20240704_0_L2A', 'S2A_10SGF_20240709_0_L2A', 'S2B_10SGF_20240704_0_L2A', 'S2A_10SGF_20240729_0_L2A', 'S2A_11SKA_20240709_0_L2A', 'S2A_11SKA_20240719_0_L2A', 'S2A_11SKA_20240729_0_L2A', 'S2A_10SGF_20240719_0_L2A', 'S2A_11SKV_20240719_0_L2A', 'S2A_11SKV_20240709_0_L2A', 'S2B_10SGE_20240704_0_L2A', 'S2B_11SKV_20240704_0_L2A']

