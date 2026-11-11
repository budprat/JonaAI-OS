/**
 * Seed saved results into Convex from exported PostgreSQL data.
 * Run: node scripts/seed-results.mjs
 */
import { ConvexHttpClient } from "convex/browser";
import { api } from "../convex/_generated/api.js";
import { readFileSync } from "fs";

const CONVEX_URL = "https://quixotic-hare-743.eu-west-1.convex.cloud";
const client = new ConvexHttpClient(CONVEX_URL);

const results = JSON.parse(readFileSync("/tmp/saved_results_seed.json", "utf8"));

async function seed() {
  console.log(`Seeding ${results.length} saved results into Convex...\n`);

  let ok = 0;
  let skip = 0;
  let fail = 0;

  for (const r of results) {
    try {
      const createdAt = r.createdAt
        ? new Date(r.createdAt).getTime()
        : Date.now();

      await client.mutation(api.seed.seedResult, {
        saveId: r.saveId,
        title: r.title,
        resultPath: r.resultPath,
        originalPath: r.originalPath || undefined,
        bounds: r.bounds || undefined,
        colormap: r.colormap,
        vmin: r.vmin ?? undefined,
        vmax: r.vmax ?? undefined,
        sizeBytes: r.sizeBytes,
        sourceQuery: r.sourceQuery || undefined,
        jobId: r.jobId || undefined,
        statistics: r.statistics || undefined,
        validation: r.validation || undefined,
        projectExternalId: r.projectExternalId || undefined,
        userId: "admin",
        createdAt,
      });
      ok++;
      if (ok % 10 === 0) console.log(`  ... ${ok}/${results.length}`);
    } catch (e) {
      if (e.message?.includes("already exists")) {
        skip++;
      } else {
        fail++;
        console.log(`  ✗ ${r.saveId}: ${e.message?.slice(0, 80)}`);
      }
    }
  }

  console.log(`\n✓ Seeded: ${ok} | Skipped: ${skip} | Failed: ${fail}`);
  console.log("Check: https://dashboard.convex.dev/d/quixotic-hare-743");
}

seed().catch(console.error);
