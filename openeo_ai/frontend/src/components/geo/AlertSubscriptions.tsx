import React, { useState, useEffect, useCallback } from "react";
import { assets } from "@/lib/geo/mock-data";
import { X, Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";

export interface AlertSubscription {
  id: string;
  asset: string;
  assetName: string;
  metric: string;
  condition: string;
  threshold: number;
  channels: string[];
  active: boolean;
  created: string;
}

const STORAGE_KEY = "jonaai-alerts";

const condLabels: Record<string, string> = {
  below: "drops below",
  above: "rises above",
  change: "changes by",
};

const metricLabels: Record<string, string> = {
  health: "Health",
  ndvi: "NDVI",
  moisture: "Moisture",
  thermal: "Thermal",
};

function loadSubscriptions(): AlertSubscription[] {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveSubscriptions(subs: AlertSubscription[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(subs));
}

interface AlertSubscriptionsProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCountChange?: (count: number) => void;
  prefillCritical?: boolean;
}

export const AlertSubscriptions: React.FC<AlertSubscriptionsProps> = ({
  open,
  onOpenChange,
  onCountChange,
  prefillCritical,
}) => {
  const [subscriptions, setSubscriptions] =
    useState<AlertSubscription[]>(loadSubscriptions);
  const [editingId, setEditingId] = useState<string | null>(null);

  // Form state
  const [assetVal, setAssetVal] = useState("all");
  const [metric, setMetric] = useState("health");
  const [condition, setCondition] = useState("below");
  const [threshold, setThreshold] = useState("50");
  const [chEmail, setChEmail] = useState(true);
  const [chSms, setChSms] = useState(false);
  const [chSlack, setChSlack] = useState(false);

  const activeCount = subscriptions.filter((s) => s.active).length;

  useEffect(() => {
    onCountChange?.(activeCount);
  }, [activeCount, onCountChange]);

  // Prefill critical assets on open
  useEffect(() => {
    if (open && prefillCritical) {
      const critical = assets.find((a) => a.status === "critical");
      if (critical) setAssetVal(critical.id);
      setMetric("health");
      setCondition("below");
      setThreshold("50");
    }
  }, [open, prefillCritical]);

  const resetForm = useCallback(() => {
    setAssetVal("all");
    setMetric("health");
    setCondition("below");
    setThreshold("50");
    setChEmail(true);
    setChSms(false);
    setChSlack(false);
    setEditingId(null);
  }, []);

  const handleSubmit = () => {
    const thresholdNum = parseFloat(threshold);
    if (isNaN(thresholdNum)) {
      toast.error("Please enter a valid threshold.");
      return;
    }
    const channels: string[] = [];
    if (chEmail) channels.push("Email");
    if (chSms) channels.push("SMS");
    if (chSlack) channels.push("Slack");
    if (channels.length === 0) {
      toast.error("Select at least one notification channel.");
      return;
    }

    const assetName =
      assetVal === "all"
        ? "All Assets"
        : assets.find((a) => a.id === assetVal)?.name || "Unknown";

    let updated: AlertSubscription[];

    if (editingId) {
      updated = subscriptions.map((s) =>
        s.id === editingId
          ? {
              ...s,
              asset: assetVal,
              assetName,
              metric,
              condition,
              threshold: thresholdNum,
              channels,
            }
          : s,
      );
      toast.success(`Alert rule updated for ${assetName}.`);
    } else {
      const newSub: AlertSubscription = {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 5),
        asset: assetVal,
        assetName,
        metric,
        condition,
        threshold: thresholdNum,
        channels,
        active: true,
        created: new Date().toISOString(),
      };
      updated = [...subscriptions, newSub];
      toast.success(`Alert rule created for ${assetName}.`);
    }

    setSubscriptions(updated);
    saveSubscriptions(updated);
    resetForm();
  };

  const handleEdit = (id: string) => {
    const sub = subscriptions.find((s) => s.id === id);
    if (!sub) return;
    setEditingId(id);
    setAssetVal(sub.asset);
    setMetric(sub.metric);
    setCondition(sub.condition);
    setThreshold(sub.threshold.toString());
    setChEmail(sub.channels.includes("Email"));
    setChSms(sub.channels.includes("SMS"));
    setChSlack(sub.channels.includes("Slack"));
  };

  const handleToggle = (id: string) => {
    const updated = subscriptions.map((s) =>
      s.id === id ? { ...s, active: !s.active } : s,
    );
    setSubscriptions(updated);
    saveSubscriptions(updated);
  };

  const handleDelete = (id: string) => {
    const updated = subscriptions.filter((s) => s.id !== id);
    setSubscriptions(updated);
    saveSubscriptions(updated);
    toast.success("Alert subscription removed.");
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby="alerts-title"
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-foreground/50 backdrop-blur-[2px]"
        onClick={() => onOpenChange(false)}
      />

      {/* Modal */}
      <div className="relative bg-background border border-border w-full max-w-[600px] mx-4 max-h-[90vh] flex flex-col z-10">
        {/* Header */}
        <div className="flex justify-between items-center px-6 py-4 border-b border-border flex-shrink-0">
          <h2 id="alerts-title" className="text-xl font-semibold">
            Alert Subscriptions
          </h2>
          <button
            onClick={() => onOpenChange(false)}
            className="hover:bg-accent p-1.5 rounded-md transition-colors"
            aria-label="Close"
          >
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>

        {/* Body */}
        <div
          className="p-6 overflow-y-auto flex-1"
          style={{ overscrollBehavior: "contain" }}
        >
          {/* New Alert Rule Form */}
          <div className="bg-card border border-border p-4 mb-6">
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground font-medium">
              New Alert Rule
            </span>

            <div className="space-y-3 mt-3">
              {/* Asset Select */}
              <div>
                <label
                  htmlFor="alert-asset"
                  className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground block mb-1.5 font-medium"
                >
                  Assets
                </label>
                <select
                  id="alert-asset"
                  value={assetVal}
                  onChange={(e) => setAssetVal(e.target.value)}
                  className="w-full h-10 border border-border px-3 text-sm bg-background rounded-md focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer"
                >
                  <option value="all">All Assets</option>
                  {assets.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </div>

              {/* Metric */}
              <div>
                <label
                  htmlFor="alert-metric"
                  className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground block mb-1.5 font-medium"
                >
                  Metric
                </label>
                <select
                  id="alert-metric"
                  value={metric}
                  onChange={(e) => setMetric(e.target.value)}
                  className="w-full h-10 border border-border px-3 text-sm bg-background rounded-md focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer"
                >
                  <option value="health">Health Score</option>
                  <option value="ndvi">NDVI Index</option>
                  <option value="moisture">Soil Moisture</option>
                  <option value="thermal">Thermal Anomaly</option>
                </select>
              </div>

              {/* Condition + Threshold */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label
                    htmlFor="alert-condition"
                    className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground block mb-1.5 font-medium"
                  >
                    Condition
                  </label>
                  <select
                    id="alert-condition"
                    value={condition}
                    onChange={(e) => setCondition(e.target.value)}
                    className="w-full h-10 border border-border px-3 text-sm bg-background rounded-md focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer"
                  >
                    <option value="below">Drops below</option>
                    <option value="above">Rises above</option>
                    <option value="change">Changes by</option>
                  </select>
                </div>
                <div>
                  <label
                    htmlFor="alert-threshold"
                    className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground block mb-1.5 font-medium"
                  >
                    Threshold
                  </label>
                  <input
                    id="alert-threshold"
                    type="number"
                    value={threshold}
                    min={0}
                    max={100}
                    onChange={(e) => setThreshold(e.target.value)}
                    className="w-full h-10 border border-border px-3 font-mono text-sm bg-background rounded-md focus:outline-none focus:ring-1 focus:ring-ring"
                  />
                </div>
              </div>

              {/* Channels */}
              <div>
                <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground block mb-1.5 font-medium">
                  Notify Via
                </span>
                <div className="flex gap-4">
                  <label
                    htmlFor="alert-ch-email"
                    className="flex items-center gap-1.5 text-sm cursor-pointer"
                  >
                    <input
                      id="alert-ch-email"
                      type="checkbox"
                      checked={chEmail}
                      onChange={(e) => setChEmail(e.target.checked)}
                    />
                    Email
                  </label>
                  <label
                    htmlFor="alert-ch-sms"
                    className="flex items-center gap-1.5 text-sm cursor-pointer"
                  >
                    <input
                      id="alert-ch-sms"
                      type="checkbox"
                      checked={chSms}
                      onChange={(e) => setChSms(e.target.checked)}
                    />
                    SMS
                  </label>
                  <label
                    htmlFor="alert-ch-slack"
                    className="flex items-center gap-1.5 text-sm cursor-pointer"
                  >
                    <input
                      id="alert-ch-slack"
                      type="checkbox"
                      checked={chSlack}
                      onChange={(e) => setChSlack(e.target.checked)}
                    />
                    Slack
                  </label>
                </div>
              </div>
            </div>

            {/* Submit */}
            <div className="mt-4 flex gap-2">
              <button
                onClick={handleSubmit}
                className="flex-1 bg-primary text-primary-foreground font-medium text-sm h-10 rounded-md hover:bg-primary/90 transition-colors"
              >
                {editingId ? "Save Changes" : "Add Alert Rule"}
              </button>
              {editingId && (
                <button
                  onClick={resetForm}
                  className="border border-border text-sm text-muted-foreground px-4 h-10 rounded-md hover:text-foreground hover:border-primary transition-colors"
                >
                  Cancel
                </button>
              )}
            </div>
          </div>

          {/* Existing Subscriptions */}
          <div>
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground font-medium">
              Active Subscriptions
            </span>
            <div className="mt-3">
              {subscriptions.length === 0 ? (
                <div className="text-sm text-muted-foreground py-6 text-center">
                  No alert subscriptions configured.
                </div>
              ) : (
                subscriptions.map((s) => (
                  <div
                    key={s.id}
                    className="flex items-center gap-3 py-3 border-b border-border last:border-0"
                  >
                    <div
                      className={`toggle-switch ${s.active ? "active" : ""}`}
                      role="switch"
                      aria-checked={s.active}
                      aria-label={s.assetName}
                      tabIndex={0}
                      onClick={() => handleToggle(s.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          handleToggle(s.id);
                        }
                      }}
                    />
                    <div className="flex-1 min-w-0">
                      <div
                        className={`text-sm font-medium truncate ${
                          !s.active ? "text-muted-foreground line-through" : ""
                        }`}
                      >
                        {s.assetName}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {metricLabels[s.metric] || s.metric}{" "}
                        {condLabels[s.condition] || s.condition} {s.threshold}{" "}
                        &middot; {s.channels.join(", ")}
                      </div>
                    </div>
                    <button
                      onClick={() => handleEdit(s.id)}
                      className="text-muted-foreground hover:text-foreground transition-colors flex-shrink-0"
                      aria-label="Edit"
                    >
                      <Pencil className="w-4 h-4" aria-hidden="true" />
                    </button>
                    <button
                      onClick={() => handleDelete(s.id)}
                      className="text-muted-foreground hover:text-status-critical transition-colors flex-shrink-0"
                      aria-label="Delete"
                    >
                      <Trash2 className="w-4 h-4" aria-hidden="true" />
                    </button>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

/** Helper to get active alert count from localStorage without mounting the component */
export function getAlertCount(): number {
  try {
    const subs: AlertSubscription[] = JSON.parse(
      localStorage.getItem(STORAGE_KEY) || "[]",
    );
    return subs.filter((s) => s.active).length;
  } catch {
    return 0;
  }
}
