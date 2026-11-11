/**
 * Seed Convex DB with existing JonaAI data from PostgreSQL + SQLite.
 * Run: node scripts/seed-convex.mjs
 */
import { ConvexHttpClient } from "convex/browser";
import { api } from "../convex/_generated/api.js";

const CONVEX_URL = "https://quixotic-hare-743.eu-west-1.convex.cloud";
const client = new ConvexHttpClient(CONVEX_URL);

// ── Data from JonaAI backend (exported via Python script below) ──
const PROJECTS = [
  { externalId: "proj-nazca", name: "Nazca Geoglyphs Archaeological Survey", description: "Multi-spectral analysis of Nazca Lines using NDVI, NDBI, NDWI, and BSI." },
  { externalId: "proj-caral", name: "Caral Archaeological Site Analysis", description: "Remote sensing analysis of the oldest known civilization in the Americas." },
  { externalId: "proj-flood", name: "Bangladesh Flood Monitoring 2024", description: "Real-time flood extent mapping using MNDWI during August 2024 monsoon." },
  { externalId: "proj-rice", name: "Thanjavur Rice Paddy Health", description: "Multi-temporal vegetation health monitoring of rice paddies in Tamil Nadu." },
  { externalId: "proj-houston", name: "Houston Urban Heat & Industry", description: "Urban built-up index analysis detecting industrial zones and heat islands." },
  { externalId: "proj-general", name: "General Earth Observation", description: "Miscellaneous satellite analyses including vegetation, water, and land cover." },
];

const TEMPLATES = [
  { name: "NDVI Vegetation Health Analysis", description: "Compute NDVI from Sentinel-2 to assess vegetation health.", processGraph: {}, parameters: [{ name: "spatial_extent", type: "spatial_extent", description: "Bounding box", required: true }, { name: "temporal_extent", type: "temporal_extent", description: "Date range", required: true }], tags: ["vegetation", "ndvi", "health", "sentinel-2"], sourceQuery: "Analyze vegetation health using NDVI", collectionId: "sentinel-2-l2a", indexType: "ndvi", useCount: 23 },
  { name: "NDWI Water Body Detection", description: "Detect water bodies using the Normalized Difference Water Index.", processGraph: {}, parameters: [{ name: "spatial_extent", type: "spatial_extent", description: "Bounding box", required: true }, { name: "temporal_extent", type: "temporal_extent", description: "Date range", required: true }], tags: ["water", "ndwi", "flood", "moisture"], sourceQuery: "Detect water bodies using NDWI", collectionId: "sentinel-2-l2a", indexType: "ndwi", useCount: 15 },
  { name: "NDBI Urban Built-up Detection", description: "Map urban and built-up areas using NDBI.", processGraph: {}, parameters: [{ name: "spatial_extent", type: "spatial_extent", description: "Bounding box", required: true }], tags: ["urban", "ndbi", "built-up", "archaeological"], sourceQuery: "Detect built-up areas using NDBI", collectionId: "sentinel-2-l2a", indexType: "ndbi", useCount: 11 },
  { name: "Flood Extent Mapping (MNDWI)", description: "Map flood extent using Modified NDWI for disaster response.", processGraph: {}, parameters: [{ name: "spatial_extent", type: "spatial_extent", description: "Flood region", required: true }], tags: ["flood", "disaster", "mndwi", "emergency"], sourceQuery: "Map flood extent using MNDWI", collectionId: "sentinel-2-l2a", indexType: "mndwi", useCount: 8 },
];

async function seed() {
  console.log("Seeding Convex DB...\n");

  // Seed projects
  for (const p of PROJECTS) {
    try {
      await client.mutation(api.seed.seedProject, { ...p, userId: "admin" });
      console.log(`✓ Project: ${p.name}`);
    } catch (e) {
      console.log(`✗ Project ${p.name}: ${e.message}`);
    }
  }

  // Seed templates
  for (const t of TEMPLATES) {
    try {
      await client.mutation(api.seed.seedTemplate, { ...t, userId: "admin" });
      console.log(`✓ Template: ${t.name}`);
    } catch (e) {
      console.log(`✗ Template ${t.name}: ${e.message}`);
    }
  }

  console.log("\nDone! Check https://dashboard.convex.dev/d/quixotic-hare-743");
}

seed().catch(console.error);
