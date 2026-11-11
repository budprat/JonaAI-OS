/**
 * Backward-compatible Convex-era exports.
 *
 * New code should import from `usePlatformData`; these aliases exist so older
 * pages and tests keep compiling while the frontend migrates naming.
 */
export {
  ConvexReadyContext,
  useProjects as useConvexProjects,
  useSavedResults as useConvexSavedResults,
  useTemplates as useConvexTemplates,
  useEarthAssets,
  useEarthWatches,
  useEarthObservations,
  useEvidencePackages,
  useCatalogItems,
  useEarthBaselines,
  useEarthSignals,
  useDeliveries,
  useObservationEvents,
  useEarthIntelligenceActions,
  useRestFetch,
} from "./usePlatformData";
