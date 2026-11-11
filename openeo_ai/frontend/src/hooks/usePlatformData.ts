/**
 * Platform data hooks backed by the FastAPI REST API and Supabase JWT auth.
 *
 * These hooks cover projects, saved analyses, workflow templates, and the
 * persistent Earth Intelligence entities. Older Convex-era return fields and
 * aliases remain for compatibility, but the implementation is REST/Supabase.
 */
import { useCallback, useEffect, useState, createContext } from "react";
import type {
  EarthAsset,
  EarthBaseline,
  EarthObservation,
  EarthSignal,
  EarthWatch,
  EvidencePackage,
  CatalogItem,
  Delivery,
  ObservationEvent,
  SavedJob,
} from "@/types";
import { useAuth } from "@/components/auth/useAuth";

/**
 * Kept as a backward-compatibility stub so any module still importing
 * ConvexReadyContext (tests, older code paths) keeps compiling.
 * The provider in main.tsx is gone; this context always returns false.
 */
export const ConvexReadyContext = createContext(false);

// ── Shared empty returns (stable references for first render) ──

const EMPTY_PROJECTS = {
  projects: [] as Record<string, unknown>[],
  unassignedFiles: [] as Record<string, unknown>[],
  isLoading: true,
  error: null as string | null,
  isConvex: false as const,
  refetch: () => {},
};

const EMPTY_RESULTS = {
  results: [] as SavedJob[],
  rawResults: [] as Record<string, unknown>[],
  isLoading: true,
  error: null as string | null,
  isConvex: false as const,
  refetch: () => {},
};

const EMPTY_TEMPLATES = {
  templates: [] as Record<string, unknown>[],
  isLoading: true,
  error: null as string | null,
  isConvex: false as const,
  refetch: () => {},
};

// ── Internal: shared fetch hook ──

function useAuthedFetch<T>(
  url: string | null,
): { data: T | null; isLoading: boolean; error: string | null; refetch: () => void } {
  const { accessToken } = useAuth();
  const [data, setData] = useState<T | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(Boolean(url));
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    if (!url || !accessToken) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = (await res.json()) as T;
      setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fetch failed");
    } finally {
      setIsLoading(false);
    }
  }, [url, accessToken]);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  return { data, isLoading, error, refetch };
}

// ── Projects ──

interface ProjectsResponse {
  projects: Record<string, unknown>[];
  unassigned_files?: Record<string, unknown>[];
}

export function useProjects() {
  const { data, isLoading, error, refetch } = useAuthedFetch<ProjectsResponse>("/api/projects");
  if (!data) return { ...EMPTY_PROJECTS, isLoading, error, refetch };
  return {
    projects: data.projects ?? [],
    unassignedFiles: data.unassigned_files ?? [],
    isLoading,
    error,
    isConvex: false as const,
    refetch,
  };
}

// ── Saved Results ──

interface SavedJobsResponse {
  jobs: Array<{
    save_id: string;
    title: string;
    result_path: string;
    original_path?: string;
    bounds?: number[] | number[][];
    colormap?: string;
    vmin?: number | null;
    vmax?: number | null;
    size_bytes?: number;
    source_query?: string;
    job_id?: string;
    project_id?: string;
    created_at: string;
  }>;
  count: number;
}

export function useSavedResults(limit = 50) {
  const url = `/saved-jobs?limit=${limit}`;
  const { data, isLoading, error, refetch } = useAuthedFetch<SavedJobsResponse>(url);
  if (!data) return { ...EMPTY_RESULTS, isLoading, error, refetch };
  const mapped: SavedJob[] = (data.jobs ?? []).map((r) => ({
    save_id: r.save_id,
    title: r.title,
    result_path: r.result_path,
    original_path: r.original_path,
    bounds: r.bounds ? (Array.isArray(r.bounds[0]) ? (r.bounds as number[][]) : [r.bounds as number[]]) : undefined,
    colormap: r.colormap ?? "viridis",
    vmin: r.vmin ?? undefined,
    vmax: r.vmax ?? undefined,
    size_bytes: r.size_bytes ?? 0,
    source_query: r.source_query,
    job_id: r.job_id,
    project_id: r.project_id,
    created_at: r.created_at,
  }));
  return {
    results: mapped,
    rawResults: (data.jobs ?? []) as unknown as Record<string, unknown>[],
    isLoading,
    error,
    isConvex: false as const,
    refetch,
  };
}

// ── Workflow Templates ──

export function useTemplates(limit = 50) {
  const url = `/api/batch/templates?limit=${limit}`;
  const { data, isLoading, error, refetch } = useAuthedFetch<Record<string, unknown>[]>(url);
  if (!data) return { ...EMPTY_TEMPLATES, isLoading, error, refetch };
  return {
    templates: data as Record<string, unknown>[],
    isLoading,
    error,
    isConvex: false as const,
    refetch,
  };
}

export const useConvexProjects = useProjects;
export const useConvexSavedResults = useSavedResults;
export const useConvexTemplates = useTemplates;

// ── Persistent Earth Intelligence ──

interface AssetsResponse {
  assets: EarthAsset[];
  count: number;
}

interface WatchesResponse {
  watches: EarthWatch[];
  count: number;
}

interface ObservationsResponse {
  observations: EarthObservation[];
  count: number;
}

interface EvidenceResponse {
  evidence: EvidencePackage[];
  count: number;
}

interface CatalogItemsResponse {
  catalog_items: CatalogItem[];
  count: number;
}

interface BaselinesResponse {
  baselines: EarthBaseline[];
  count: number;
}

interface SignalsResponse {
  signals: EarthSignal[];
  count: number;
}

interface DeliveriesResponse {
  deliveries: Delivery[];
  count: number;
}

interface ObservationEventsResponse {
  events: ObservationEvent[];
  count: number;
}

export function useEarthAssets(options?: { projectId?: string; includeArchived?: boolean; limit?: number }) {
  const params = new URLSearchParams();
  params.set("limit", String(options?.limit ?? 100));
  if (options?.projectId) params.set("project_id", options.projectId);
  if (options?.includeArchived) params.set("include_archived", "true");
  const { data, isLoading, error, refetch } = useAuthedFetch<AssetsResponse>(
    `/api/assets?${params.toString()}`,
  );
  return {
    assets: data?.assets ?? [],
    count: data?.count ?? 0,
    isLoading,
    error,
    refetch,
  };
}

export function useEarthWatches(options?: { assetId?: string; status?: string; limit?: number }) {
  const params = new URLSearchParams();
  params.set("limit", String(options?.limit ?? 100));
  if (options?.assetId) params.set("asset_id", options.assetId);
  if (options?.status) params.set("status", options.status);
  const { data, isLoading, error, refetch } = useAuthedFetch<WatchesResponse>(
    `/api/watches?${params.toString()}`,
  );
  return {
    watches: data?.watches ?? [],
    count: data?.count ?? 0,
    isLoading,
    error,
    refetch,
  };
}

export function useEarthObservations(options?: { assetId?: string; watchId?: string; status?: string; limit?: number }) {
  const params = new URLSearchParams();
  params.set("limit", String(options?.limit ?? 100));
  if (options?.assetId) params.set("asset_id", options.assetId);
  if (options?.watchId) params.set("watch_id", options.watchId);
  if (options?.status) params.set("status", options.status);
  const { data, isLoading, error, refetch } = useAuthedFetch<ObservationsResponse>(
    `/api/observations?${params.toString()}`,
  );
  return {
    observations: data?.observations ?? [],
    count: data?.count ?? 0,
    isLoading,
    error,
    refetch,
  };
}

export function useEvidencePackages(options?: { observationId?: string; savedResultSaveId?: string; limit?: number }) {
  const params = new URLSearchParams();
  params.set("limit", String(options?.limit ?? 100));
  if (options?.observationId) params.set("observation_id", options.observationId);
  if (options?.savedResultSaveId) params.set("saved_result_save_id", options.savedResultSaveId);
  const { data, isLoading, error, refetch } = useAuthedFetch<EvidenceResponse>(
    `/api/evidence?${params.toString()}`,
  );
  return {
    evidence: data?.evidence ?? [],
    count: data?.count ?? 0,
    isLoading,
    error,
    refetch,
  };
}

export function useCatalogItems(options?: { provider?: string; collectionId?: string; limit?: number }) {
  const params = new URLSearchParams();
  params.set("limit", String(options?.limit ?? 100));
  if (options?.provider) params.set("provider", options.provider);
  if (options?.collectionId) params.set("collection_id", options.collectionId);
  const { data, isLoading, error, refetch } = useAuthedFetch<CatalogItemsResponse>(
    `/api/catalog-items?${params.toString()}`,
  );
  return {
    catalogItems: data?.catalog_items ?? [],
    count: data?.count ?? 0,
    isLoading,
    error,
    refetch,
  };
}

export function useEarthBaselines(options?: { assetId?: string; watchId?: string; metric?: string; limit?: number }) {
  const params = new URLSearchParams();
  params.set("limit", String(options?.limit ?? 100));
  if (options?.assetId) params.set("asset_id", options.assetId);
  if (options?.watchId) params.set("watch_id", options.watchId);
  if (options?.metric) params.set("metric", options.metric);
  const { data, isLoading, error, refetch } = useAuthedFetch<BaselinesResponse>(
    `/api/baselines?${params.toString()}`,
  );
  return {
    baselines: data?.baselines ?? [],
    count: data?.count ?? 0,
    isLoading,
    error,
    refetch,
  };
}

export function useEarthSignals(options?: { assetId?: string; watchId?: string; status?: string; limit?: number }) {
  const params = new URLSearchParams();
  params.set("limit", String(options?.limit ?? 100));
  if (options?.assetId) params.set("asset_id", options.assetId);
  if (options?.watchId) params.set("watch_id", options.watchId);
  if (options?.status) params.set("status", options.status);
  const { data, isLoading, error, refetch } = useAuthedFetch<SignalsResponse>(
    `/api/signals?${params.toString()}`,
  );
  return {
    signals: data?.signals ?? [],
    count: data?.count ?? 0,
    isLoading,
    error,
    refetch,
  };
}

export function useDeliveries(options?: { signalId?: string; status?: string; limit?: number }) {
  const params = new URLSearchParams();
  params.set("limit", String(options?.limit ?? 100));
  if (options?.signalId) params.set("signal_id", options.signalId);
  if (options?.status) params.set("status", options.status);
  const { data, isLoading, error, refetch } = useAuthedFetch<DeliveriesResponse>(
    `/api/deliveries?${params.toString()}`,
  );
  return {
    deliveries: data?.deliveries ?? [],
    count: data?.count ?? 0,
    isLoading,
    error,
    refetch,
  };
}

export function useObservationEvents(options?: { observationId?: string; limit?: number }) {
  const params = new URLSearchParams();
  params.set("limit", String(options?.limit ?? 100));
  if (options?.observationId) params.set("observation_id", options.observationId);
  const { data, isLoading, error, refetch } = useAuthedFetch<ObservationEventsResponse>(
    `/api/observation-events?${params.toString()}`,
  );
  return {
    events: data?.events ?? [],
    count: data?.count ?? 0,
    isLoading,
    error,
    refetch,
  };
}

export function useEarthIntelligenceActions() {
  const { accessToken } = useAuth();

  const request = useCallback(
    async <T,>(url: string, options: RequestInit = {}): Promise<T> => {
      if (!accessToken) throw new Error("Authentication required");
      const extraHeaders =
        options.headers instanceof Headers
          ? Object.fromEntries(options.headers.entries())
          : Array.isArray(options.headers)
            ? Object.fromEntries(options.headers)
            : options.headers ?? {};
      const res = await fetch(url, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
          ...extraHeaders,
        },
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
      }
      return (await res.json()) as T;
    },
    [accessToken],
  );

  return {
    createAsset: (body: Partial<EarthAsset>) =>
      request<EarthAsset>("/api/assets", { method: "POST", body: JSON.stringify(body) }),
    updateAsset: (assetId: string, body: Partial<EarthAsset> & { archived?: boolean }) =>
      request<EarthAsset>(`/api/assets/${assetId}`, { method: "PATCH", body: JSON.stringify(body) }),
    discoverAssetCatalog: (assetId: string, body: Record<string, unknown> = {}) =>
      request<{ catalog_items: CatalogItem[]; observations: EarthObservation[]; count: number; queued_count: number }>(
        `/api/assets/${assetId}/catalog/discover`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    createWatch: (body: Partial<EarthWatch> & { asset_id: string; name: string }) =>
      request<EarthWatch>("/api/watches", { method: "POST", body: JSON.stringify(body) }),
    updateWatch: (watchId: string, body: Partial<EarthWatch>) =>
      request<EarthWatch>(`/api/watches/${watchId}`, { method: "PATCH", body: JSON.stringify(body) }),
    runWatch: (watchId: string, body: Record<string, unknown> = {}) =>
      request<{ observation: EarthObservation; queue_status: string; message: string }>(
        `/api/watches/${watchId}/run`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    enqueueDueWatches: (limit = 25) =>
      request<{ observations: EarthObservation[]; count: number }>(
        `/api/watches/due/enqueue?limit=${limit}`,
        { method: "POST" },
      ),
    executeObservation: (observationId: string) =>
      request<{ observation: EarthObservation; queue_status: string; message: string }>(
        `/api/observations/${observationId}/execute`,
        { method: "POST" },
      ),
    tickWorker: (maxObservations = 1) =>
      request<{ worker_id: string; queued_due: EarthObservation[]; processed: Record<string, unknown>[]; at: string }>(
        `/api/intelligence/worker/tick?max_observations=${maxObservations}`,
        { method: "POST" },
      ),
    createEvidence: (body: Partial<EvidencePackage>) =>
      request<EvidencePackage>("/api/evidence", { method: "POST", body: JSON.stringify(body) }),
    createSignal: (body: Partial<EarthSignal>) =>
      request<EarthSignal>("/api/signals", { method: "POST", body: JSON.stringify(body) }),
    updateSignal: (signalId: string, body: Partial<EarthSignal> & { acknowledged?: boolean; resolved?: boolean }) =>
      request<EarthSignal>(`/api/signals/${signalId}`, { method: "PATCH", body: JSON.stringify(body) }),
    createDelivery: (body: Partial<Delivery> & { signal_id: string; channel: string; target: string }) =>
      request<Delivery>("/api/deliveries", { method: "POST", body: JSON.stringify(body) }),
    updateDelivery: (deliveryId: string, body: Partial<Delivery>) =>
      request<Delivery>(`/api/deliveries/${deliveryId}`, { method: "PATCH", body: JSON.stringify(body) }),
  };
}

// ── REST Fallback helper (kept for any existing callers) ──

export function useRestFetch<T>(
  url: string,
  authToken: string | null,
  options?: { skip?: boolean },
): { data: T | null; isLoading: boolean; error: string | null; refetch: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [isLoading, setIsLoading] = useState(!options?.skip);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    if (!authToken || options?.skip) return;
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fetch failed");
    } finally {
      setIsLoading(false);
    }
  }, [url, authToken, options?.skip]);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  return { data, isLoading, error, refetch };
}
