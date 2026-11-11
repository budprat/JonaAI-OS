import React, { useCallback, useMemo, useState } from "react";
import { CloudRain, Database, Loader2, RefreshCw, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useCatalogItems, useEarthAssets, useEarthIntelligenceActions } from "@/hooks/usePlatformData";
import { formatDateTime } from "@/lib/utils";
import type { CatalogItem, EarthAsset } from "@/types";

const COLLECTION_COLORS: Record<string, string> = {
  "sentinel-2-l2a": "#3b82f6",
  "sentinel-2-l1c": "#60a5fa",
  "landsat-c2-l2": "#22c55e",
  "sentinel-1-grd": "#f59e0b",
};

function colorForCollection(collectionId: string): string {
  return COLLECTION_COLORS[collectionId] || "#a855f7";
}

function itemCenter(item: CatalogItem): [number, number] | null {
  if (item.bbox && item.bbox.length >= 4) {
    return [(item.bbox[0]! + item.bbox[2]!) / 2, (item.bbox[1]! + item.bbox[3]!) / 2];
  }
  return null;
}

function assetCenter(asset: EarthAsset): [number, number] | null {
  if (asset.bbox && asset.bbox.length >= 4) {
    return [(asset.bbox[0]! + asset.bbox[2]!) / 2, (asset.bbox[1]! + asset.bbox[3]!) / 2];
  }
  return null;
}

function projectPoint(center: [number, number] | null, fallbackIndex: number): { left: string; top: string } {
  if (!center) {
    return { left: `${18 + (fallbackIndex % 6) * 12}%`, top: `${24 + (fallbackIndex % 5) * 11}%` };
  }
  const [lon, lat] = center;
  return {
    left: `${Math.max(5, Math.min(95, ((lon + 180) / 360) * 100))}%`,
    top: `${Math.max(5, Math.min(95, ((90 - lat) / 180) * 100))}%`,
  };
}

const SatelliteTracker: React.FC = () => {
  const assets = useEarthAssets({ limit: 50 });
  const catalog = useCatalogItems({ limit: 80 });
  const actions = useEarthIntelligenceActions();
  const [selectedCollection, setSelectedCollection] = useState<string | null>(null);
  const [selectedItem, setSelectedItem] = useState<CatalogItem | null>(null);
  const [discoveringAssetId, setDiscoveringAssetId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const collections = useMemo(
    () => Array.from(new Set(catalog.catalogItems.map((item) => item.collection_id))).sort(),
    [catalog.catalogItems],
  );

  const filteredItems = selectedCollection
    ? catalog.catalogItems.filter((item) => item.collection_id === selectedCollection)
    : catalog.catalogItems;

  const discoverForAsset = useCallback(
    async (asset: EarthAsset) => {
      setDiscoveringAssetId(asset.id);
      setError(null);
      try {
        await actions.discoverAssetCatalog(asset.id, {
          collections: ["sentinel-2-l2a"],
          temporal_policy: { lookback_days: 30 },
          quality_policy: { max_cloud_cover: 40 },
          limit: 12,
        });
        catalog.refetch();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Catalog discovery failed");
      } finally {
        setDiscoveringAssetId(null);
      }
    },
    [actions, catalog],
  );

  return (
    <div className="flex h-full overflow-hidden">
      <div className="relative flex-1 overflow-hidden bg-muted/10">
        <div
          className="absolute inset-0"
          style={{
            backgroundImage: `
              linear-gradient(to right, hsl(var(--border) / 0.5) 1px, transparent 1px),
              linear-gradient(to bottom, hsl(var(--border) / 0.5) 1px, transparent 1px)
            `,
            backgroundSize: "96px 96px",
          }}
        />

        {assets.assets.map((asset, index) => {
          const point = projectPoint(assetCenter(asset), index);
          return (
            <button
              key={asset.id}
              className="absolute -translate-x-1/2 -translate-y-1/2"
              style={point}
              onClick={() => void discoverForAsset(asset)}
              aria-label={`Discover acquisitions for ${asset.name}`}
            >
              <div className="h-4 w-4 rotate-45 border-2 border-foreground/40 bg-background" />
              <span className="absolute left-1/2 top-5 max-w-32 -translate-x-1/2 truncate whitespace-nowrap font-mono text-[9px] text-muted-foreground">
                {asset.name}
              </span>
            </button>
          );
        })}

        {filteredItems.map((item, index) => {
          const point = projectPoint(itemCenter(item), index);
          const color = colorForCollection(item.collection_id);
          return (
            <button
              key={item.id}
              className="absolute h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border border-background transition-transform hover:scale-125"
              style={{ ...point, backgroundColor: color, boxShadow: `0 0 8px ${color}` }}
              onClick={() => setSelectedItem(item)}
              aria-label={`Open acquisition ${item.stac_id}`}
            />
          );
        })}

        <div className="absolute bottom-3 left-3 border border-border bg-background/90 px-3 py-2">
          <span className="font-mono text-[10px] text-muted-foreground">
            {catalog.count} catalog acquisitions · {assets.count} assets ·{" "}
            {collections.length} collections
          </span>
        </div>
      </div>

      <div className="flex w-96 flex-shrink-0 flex-col overflow-hidden border-l border-border bg-card">
        <div className="border-b border-border px-4 py-3">
          <div className="mb-3 flex items-center justify-between">
            <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
              Catalog Acquisitions
            </span>
            <Button variant="ghost" size="sm" onClick={() => catalog.refetch()}>
              {catalog.isLoading ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="mr-1 h-3.5 w-3.5" />
              )}
              Refresh
            </Button>
          </div>
          <div className="flex flex-wrap gap-1">
            <button
              onClick={() => setSelectedCollection(null)}
              className={`rounded-md px-2 py-1 text-xs ${!selectedCollection ? "bg-primary text-primary-foreground" : "hover:bg-accent"}`}
            >
              All
            </button>
            {collections.map((collection) => (
              <button
                key={collection}
                onClick={() => setSelectedCollection(collection === selectedCollection ? null : collection)}
                className={`rounded-md px-2 py-1 font-mono text-[10px] ${
                  selectedCollection === collection ? "bg-primary text-primary-foreground" : "hover:bg-accent"
                }`}
              >
                {collection}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <div className="border-b border-destructive/30 bg-destructive/5 px-4 py-2 text-xs text-destructive">
            {error}
          </div>
        )}

        <div className="border-b border-border px-4 py-3">
          <span className="mb-2 block font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Assets
          </span>
          <div className="max-h-36 overflow-auto">
            {assets.assets.length === 0 ? (
              <div className="text-xs text-muted-foreground">Create assets to discover acquisitions.</div>
            ) : (
              assets.assets.slice(0, 8).map((asset) => (
                <button
                  key={asset.id}
                  onClick={() => void discoverForAsset(asset)}
                  className="flex w-full items-center justify-between border-b border-border py-2 text-left last:border-b-0"
                >
                  <span className="truncate text-sm">{asset.name}</span>
                  {discoveringAssetId === asset.id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                  ) : (
                    <Search className="h-3.5 w-3.5 text-muted-foreground" />
                  )}
                </button>
              ))
            )}
          </div>
        </div>

        <div className="flex-1 overflow-auto">
          {filteredItems.length === 0 ? (
            <div className="flex flex-col items-center px-4 py-12 text-center">
              <Database className="mb-2 h-7 w-7 text-muted-foreground/30" />
              <p className="text-xs text-muted-foreground">
                No catalog acquisitions stored yet. Run discovery for an asset.
              </p>
            </div>
          ) : (
            filteredItems.map((item) => (
              <button
                key={item.id}
                onClick={() => setSelectedItem(item)}
                className={`block w-full border-b border-border px-4 py-3 text-left transition-colors hover:bg-accent/30 ${
                  selectedItem?.id === item.id ? "bg-accent" : ""
                }`}
              >
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium">{item.stac_id}</span>
                  <span className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[10px] uppercase text-muted-foreground">
                    {item.collection_id}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {item.datetime ? formatDateTime(item.datetime) : "No datetime"}
                  </span>
                  <div className="flex items-center gap-1">
                    <CloudRain className="h-3 w-3 text-muted-foreground" />
                    <span
                      className={`font-mono text-[10px] ${
                        (item.cloud_cover ?? 0) > 40 ? "text-status-warn" : "text-status-good"
                      }`}
                    >
                      {item.cloud_cover == null ? "N/A" : `${Math.round(item.cloud_cover)}%`}
                    </span>
                  </div>
                </div>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
};

export default SatelliteTracker;
