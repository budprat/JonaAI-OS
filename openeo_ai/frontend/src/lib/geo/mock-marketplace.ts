export interface MarketplaceTemplate {
  id: string;
  name: string;
  description: string;
  longDescription: string;
  author: string;
  category: "agriculture" | "urban" | "forestry" | "climate";
  rating: number;
  reviewCount: number;
  usageCount: number;
  tags: string[];
  sampleOutputs: string[];
  createdAt: string;
}

export const marketplaceTemplates: MarketplaceTemplate[] = [
  {
    id: "mp1",
    name: "Crop Stress Early Warning System",
    description: "Multi-sensor fusion pipeline detecting vegetation stress 2-3 weeks before visible symptoms.",
    longDescription: "This template combines Sentinel-2 NDVI, Landsat thermal, and soil moisture indices to identify crop stress before it becomes visually apparent. The pipeline normalizes data across sensors, applies machine learning classifiers trained on 50,000+ ground-truth samples, and outputs zone-level risk scores with actionable recommendations.",
    author: "PrecisionAg Labs",
    category: "agriculture",
    rating: 4.8,
    reviewCount: 124,
    usageCount: 2340,
    tags: ["ndvi", "thermal", "multi-sensor", "early-warning"],
    sampleOutputs: ["Zone risk heatmap", "Stress timeline chart", "Executive summary PDF"],
    createdAt: "2025-11-15",
  },
  {
    id: "mp2",
    name: "Urban Expansion Tracker",
    description: "Monthly change detection for urban sprawl using SAR and optical imagery.",
    longDescription: "Automatically detects and quantifies urban expansion by combining Sentinel-1 SAR data with optical imagery. Produces classified maps showing new construction, road development, and green space conversion with accuracy above 90% in diverse climatic regions.",
    author: "CityScope Analytics",
    category: "urban",
    rating: 4.5,
    reviewCount: 67,
    usageCount: 890,
    tags: ["sar", "change-detection", "urban-planning"],
    sampleOutputs: ["Expansion heatmap", "Area statistics CSV", "Change polygon GeoJSON"],
    createdAt: "2025-09-22",
  },
  {
    id: "mp3",
    name: "Forest Canopy Density Model",
    description: "High-resolution canopy density estimation using LiDAR-calibrated spectral indices.",
    longDescription: "Estimates forest canopy density at 10m resolution by combining Sentinel-2 spectral indices with LiDAR-calibrated regression models. Validated against 15,000 field measurements across tropical, temperate, and boreal forests.",
    author: "GreenSight AI",
    category: "forestry",
    rating: 4.9,
    reviewCount: 89,
    usageCount: 1560,
    tags: ["lidar", "canopy", "density", "biomass"],
    sampleOutputs: ["Density raster", "Biomass estimation", "Carbon stock report"],
    createdAt: "2025-08-10",
  },
  {
    id: "mp4",
    name: "Drought Severity Index Calculator",
    description: "Composite drought index combining satellite and weather station data.",
    longDescription: "Computes a standardized drought severity index by fusing MODIS vegetation data, precipitation anomalies, soil moisture, and surface temperature. Outputs weekly severity maps with historical comparison and forecast projections.",
    author: "ClimateWatch Pro",
    category: "climate",
    rating: 4.6,
    reviewCount: 156,
    usageCount: 3100,
    tags: ["drought", "composite-index", "forecasting"],
    sampleOutputs: ["Severity map", "Trend analysis", "Stakeholder alert"],
    createdAt: "2025-07-04",
  },
  {
    id: "mp5",
    name: "Precision Irrigation Optimizer",
    description: "Variable-rate irrigation recommendations based on real-time ET and soil maps.",
    longDescription: "Uses evapotranspiration models, soil type maps, and real-time satellite imagery to generate variable-rate irrigation prescriptions. Typically reduces water usage by 15-25% while maintaining or improving crop yields.",
    author: "AquaSense",
    category: "agriculture",
    rating: 4.7,
    reviewCount: 201,
    usageCount: 4200,
    tags: ["irrigation", "water-management", "prescription-maps"],
    sampleOutputs: ["Irrigation zones map", "Water budget", "Equipment prescription file"],
    createdAt: "2025-06-18",
  },
  {
    id: "mp6",
    name: "Wildfire Risk Assessment",
    description: "Multi-factor wildfire risk scoring using vegetation dryness, topography, and weather.",
    longDescription: "Integrates live fuel moisture content, dead fuel moisture, topographic exposure, wind patterns, and historical fire data to produce daily wildfire risk maps. Includes evacuation corridor analysis and resource staging recommendations.",
    author: "FireWatch Analytics",
    category: "forestry",
    rating: 4.4,
    reviewCount: 45,
    usageCount: 670,
    tags: ["wildfire", "risk", "fuel-moisture", "emergency"],
    sampleOutputs: ["Risk zone map", "Resource staging plan", "Daily risk bulletin"],
    createdAt: "2026-01-12",
  },
];
