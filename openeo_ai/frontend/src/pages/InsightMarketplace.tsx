import React, { useState } from "react";
import { Search, Download, Layers, Tag, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { useTemplates } from "@/hooks/usePlatformData";

interface WorkflowTemplate {
  id: string;
  name: string;
  description: string;
  process_graph: Record<string, unknown>;
  parameters: Array<{
    name: string;
    type: string;
    description: string;
    default_value: unknown;
    required: boolean;
  }>;
  tags: string[];
  source_query: string;
  collection_id: string;
  index_type: string;
  version: number;
  user_id: string;
  use_count: number;
  created_at: string;
  updated_at: string;
}

const InsightMarketplace: React.FC = () => {
  const [searchQuery, setSearchQuery] = useState("");
  const [detailTemplate, setDetailTemplate] = useState<WorkflowTemplate | null>(null);
  const [activeTag, setActiveTag] = useState<string | null>(null);

  const templatesHook = useTemplates(100);
  const templates = templatesHook.templates as unknown as WorkflowTemplate[];

  // Collect all unique tags
  const allTags = Array.from(new Set(templates.flatMap((t) => t.tags))).sort();

  // Filter
  const filtered = templates
    .filter((t) => !activeTag || t.tags.includes(activeTag))
    .filter(
      (t) =>
        !searchQuery ||
        t.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        t.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
        t.tags.some((tag) => tag.toLowerCase().includes(searchQuery.toLowerCase())) ||
        t.index_type.toLowerCase().includes(searchQuery.toLowerCase())
    )
    .sort((a, b) => b.use_count - a.use_count); // Most used first

  const fmtDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
    } catch {
      return iso;
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Search + Tags */}
      <div className="flex flex-shrink-0 items-center gap-4 border-b border-border px-6 py-3">
        <div className="flex max-w-md flex-1 items-center gap-2">
          <Search className="h-4 w-4 text-muted-foreground" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search templates by name, tag, or index type..."
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>
        <div className="flex flex-wrap gap-1">
          <button
            onClick={() => setActiveTag(null)}
            className={`rounded-md px-3 py-1 text-xs transition-colors ${
              !activeTag
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent"
            }`}
          >
            All
          </button>
          {allTags.slice(0, 8).map((tag) => (
            <button
              key={tag}
              onClick={() => setActiveTag(tag === activeTag ? null : tag)}
              className={`rounded-md px-3 py-1 text-xs capitalize transition-colors ${
                activeTag === tag
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent"
              }`}
            >
              {tag}
            </button>
          ))}
        </div>
      </div>

      {/* Grid */}
      <div className="flex-1 overflow-auto p-6">
        {templatesHook.isLoading ? (
          <div className="flex flex-col items-center py-16">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            <span className="mt-2 text-xs text-muted-foreground">Loading templates...</span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center py-16 text-center">
            <Layers className="mb-3 h-10 w-10 text-muted-foreground/30" />
            <p className="mb-1 text-sm font-medium text-muted-foreground">
              {templates.length === 0 ? "No templates available" : "No matching templates"}
            </p>
            <p className="max-w-sm text-xs text-muted-foreground">
              {templates.length === 0
                ? "Run analyses in the AI Chat workspace and save successful workflows as reusable templates. They will appear here for others to discover and use."
                : "Try adjusting your search query or filters."}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-4">
            {filtered.map((template) => (
              <div
                key={template.id}
                className="cursor-pointer rounded-md border border-border transition-colors hover:border-muted-foreground/30"
                onClick={() => setDetailTemplate(template)}
              >
                <div className="p-4">
                  <div className="mb-2 flex items-center gap-2">
                    {template.index_type && (
                      <span className="rounded-md bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] uppercase text-primary">
                        {template.index_type}
                      </span>
                    )}
                    {template.collection_id && (
                      <span className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[10px] uppercase text-muted-foreground">
                        {template.collection_id}
                      </span>
                    )}
                    <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                      v{template.version}
                    </span>
                  </div>
                  <h3 className="mb-1 text-sm font-semibold">{template.name}</h3>
                  <p className="mb-3 line-clamp-2 text-xs text-muted-foreground">
                    {template.description || template.source_query || "No description"}
                  </p>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1">
                      <Download className="h-3 w-3 text-muted-foreground" />
                      <span className="font-mono text-xs text-muted-foreground">
                        {template.use_count} runs
                      </span>
                    </div>
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {fmtDate(template.created_at)}
                    </span>
                  </div>
                  {template.tags.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1">
                      {template.tags.slice(0, 4).map((tag) => (
                        <span
                          key={tag}
                          className="rounded-md bg-accent px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Detail Dialog */}
      <Dialog open={!!detailTemplate} onOpenChange={(open) => !open && setDetailTemplate(null)}>
        <DialogContent className="max-w-lg">
          {detailTemplate && (
            <>
              <DialogHeader>
                <DialogTitle>{detailTemplate.name}</DialogTitle>
                <DialogDescription>
                  {detailTemplate.index_type &&
                    `${detailTemplate.index_type.toUpperCase()} analysis`}
                  {detailTemplate.collection_id && ` on ${detailTemplate.collection_id}`}
                  {!detailTemplate.index_type &&
                    !detailTemplate.collection_id &&
                    "Workflow template"}
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                <p className="text-sm">
                  {detailTemplate.description ||
                    detailTemplate.source_query ||
                    "No description available."}
                </p>

                {detailTemplate.source_query && detailTemplate.description && (
                  <div>
                    <span className="mb-1 block font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                      Original Query
                    </span>
                    <p className="rounded-md bg-muted/50 p-2 text-xs">
                      {detailTemplate.source_query}
                    </p>
                  </div>
                )}

                {detailTemplate.parameters.length > 0 && (
                  <div>
                    <span className="mb-2 block font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                      Parameters
                    </span>
                    <div className="space-y-1">
                      {detailTemplate.parameters.map((p) => (
                        <div key={p.name} className="flex items-center gap-2 text-sm">
                          <Tag className="h-3 w-3 text-muted-foreground" />
                          <span className="font-medium">{p.name}</span>
                          <span className="font-mono text-[10px] text-muted-foreground">
                            ({p.type})
                          </span>
                          {p.required && (
                            <span className="text-[10px] text-status-critical">required</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <div className="flex items-center justify-between pt-2">
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-xs text-muted-foreground">
                      {detailTemplate.use_count} runs
                    </span>
                    <span className="font-mono text-[10px] text-muted-foreground">
                      v{detailTemplate.version}
                    </span>
                  </div>
                  <Button
                    onClick={() => {
                      // Navigate to WorkflowBuilder with this template pre-selected
                      window.location.href = `/dashboard/workflows`;
                      setDetailTemplate(null);
                    }}
                  >
                    Use Template
                  </Button>
                </div>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default InsightMarketplace;
