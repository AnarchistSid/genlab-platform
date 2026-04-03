import { useState, useEffect, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Globe,
  Clock,
  BarChart3,
  LayoutTemplate,
  BellRing,
  Server,
  ExternalLink,
  CheckCircle2,
  XCircle,
  Save,
  Volume2,
} from "lucide-react";
import { isSoundEnabled, toggleSound } from "@/lib/sounds";
import { config, health, notifications as notificationsApi } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { NotificationPreferences } from "@/api/types";
import { useNicheStore } from "@/stores/niche-store";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { DataTable, type Column } from "@/components/shared/data-table";
import { EmptyState } from "@/components/shared/empty-state";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

interface SourceRow {
  name: string;
  url: string;
  type: string;
  status: string;
}

interface TemplateRow {
  id: string;
  name: string;
  type: string;
  best_for?: string[];
  structure?: string[];
  default_cta?: string;
  notes?: string;
  [key: string]: unknown;
}

/* ------------------------------------------------------------------ */
/* Shared helpers                                                      */
/* ------------------------------------------------------------------ */

const NOTIFICATION_TYPES = [
  { key: "pipeline_complete", label: "Pipeline Complete" },
  { key: "pipeline_error", label: "Pipeline Error" },
  { key: "publish_success", label: "Publish Success" },
  { key: "publish_failure", label: "Publish Failure" },
  { key: "review_needed", label: "Review Needed" },
] as const;

const LOCAL_PREFS_KEY = "bb-notification-prefs";

function loadLocalPrefs(): NotificationPreferences {
  try {
    const raw = localStorage.getItem(LOCAL_PREFS_KEY);
    if (raw) return JSON.parse(raw) as NotificationPreferences;
  } catch {
    /* ignore */
  }
  return {
    slack_webhook_url: "",
    email_digest: "never",
    enabled_types: NOTIFICATION_TYPES.map((t) => t.key),
  };
}

function saveLocalPrefs(prefs: NotificationPreferences) {
  localStorage.setItem(LOCAL_PREFS_KEY, JSON.stringify(prefs));
}

/* ------------------------------------------------------------------ */
/* Tab: Sources                                                        */
/* ------------------------------------------------------------------ */

const sourceColumns: Column<SourceRow>[] = [
  { key: "name", header: "Name", sortable: true },
  {
    key: "url",
    header: "URL",
    render: (row) => (
      <span className="flex items-center gap-1.5 text-text-secondary truncate max-w-[300px]">
        {row.url}
        <ExternalLink className="size-3 shrink-0 opacity-40" />
      </span>
    ),
    sortable: false,
  },
  {
    key: "type",
    header: "Type",
    render: (row) => (
      <Badge variant="outline" className="capitalize text-xs">
        {row.type}
      </Badge>
    ),
    sortable: true,
  },
  {
    key: "status",
    header: "Status",
    render: (row) => {
      const active =
        row.status === "active" || row.status === "enabled" || !row.status;
      return (
        <Badge
          variant={active ? "default" : "secondary"}
          className={cn("text-xs", active && "bg-emerald-600")}
        >
          {row.status || "active"}
        </Badge>
      );
    },
    sortable: true,
  },
];

function SourcesTab({ nicheId }: { nicheId?: string }) {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.config.sources(nicheId),
    queryFn: () => config.sources(nicheId ? { niche_id: nicheId } : undefined),
  });

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  // Normalize: the API returns { data: ... } where data could be an array or object with sources key
  let sources: SourceRow[] = [];
  const raw = data?.data;
  const rawList: Array<Record<string, unknown>> = [];
  if (Array.isArray(raw)) {
    rawList.push(...(raw as Array<Record<string, unknown>>));
  } else if (raw && typeof raw === "object") {
    const obj = raw as Record<string, unknown>;
    if (Array.isArray(obj.sources)) {
      rawList.push(...(obj.sources as Array<Record<string, unknown>>));
    }
  }
  // Map API fields (source_id, enabled) to SourceRow (name, status)
  sources = rawList.map((s) => ({
    name: (s.source_id as string) ?? (s.name as string) ?? "",
    url: (s.url as string) ?? "",
    type: (s.type as string) ?? "",
    status: s.enabled === false ? "disabled" : "active",
  }));

  if (sources.length === 0) {
    return (
      <EmptyState
        icon={Globe}
        title="No sources configured"
        description="Add sources in config/sources.yaml"
      />
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Configured Sources</CardTitle>
      </CardHeader>
      <CardContent>
        <DataTable columns={sourceColumns} data={sources} sortable />
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Tab: Schedule                                                       */
/* ------------------------------------------------------------------ */

function ScheduleTab({ nicheId }: { nicheId?: string }) {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.config.scheduleSlots(nicheId),
    queryFn: () => config.scheduleSlots(nicheId ? { niche_id: nicheId } : undefined),
  });

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-48" />
        <div className="grid grid-cols-2 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      </div>
    );
  }

  const slots = data?.data?.slots ?? [];
  const timezone = data?.data?.timezone ?? "UTC";

  if (slots.length === 0) {
    return (
      <EmptyState
        icon={Clock}
        title="No schedule slots"
        description="Configure publishing time slots in config/publishing.yaml"
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm text-text-secondary">
        <Clock className="size-4" />
        <span>
          Timezone: <span className="font-medium text-text-primary">{timezone}</span>
        </span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {slots.map((slot, i) => (
          <Card key={i}>
            <CardContent className="flex flex-col items-center justify-center py-8">
              <span className="text-3xl font-bold text-text-primary tracking-tight">
                {slot}
              </span>
              <span className="text-xs text-text-muted mt-2">
                Slot {i + 1}
              </span>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Tab: Scoring                                                        */
/* ------------------------------------------------------------------ */

function ScoringTab({ nicheId }: { nicheId?: string }) {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.config.scoring(nicheId),
    queryFn: () => config.scoring(nicheId ? { niche_id: nicheId } : undefined),
  });

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-48" />
      </div>
    );
  }

  const raw = data?.data;
  let entries: Array<[string, unknown]> = [];

  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    entries = Object.entries(raw as Record<string, unknown>);
  }

  if (entries.length === 0) {
    return (
      <EmptyState
        icon={BarChart3}
        title="No scoring weights"
        description="Configure weights in config/scoring_weights.yaml"
      />
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Scoring Weights</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {entries.map(([key, value]) => {
            // Recurse one level for nested objects
            if (value && typeof value === "object" && !Array.isArray(value)) {
              const nested = Object.entries(value as Record<string, unknown>);
              return (
                <div key={key} className="space-y-2">
                  <h4 className="text-sm font-medium text-text-primary capitalize mt-3">
                    {key.replace(/_/g, " ")}
                  </h4>
                  {nested.map(([nk, nv]) => {
                    // Handle deeply nested objects (e.g. virality_fit.hook_formulas)
                    if (nv && typeof nv === "object" && !Array.isArray(nv)) {
                      const deep = Object.entries(nv as Record<string, unknown>);
                      return (
                        <div key={nk} className="space-y-1 ml-2">
                          <h5 className="text-xs font-medium text-text-muted capitalize mt-2">
                            {nk.replace(/_/g, " ")}
                          </h5>
                          {deep.map(([dk, dv]) => (
                            <div
                              key={dk}
                              className="flex items-center justify-between py-1.5 px-3 rounded-md bg-bg-surface"
                            >
                              <span className="text-xs text-text-secondary capitalize">
                                {dk.replace(/_/g, " ")}
                              </span>
                              <span className="text-xs font-mono text-text-primary">
                                {typeof dv === "number" ? dv.toFixed(2) : String(dv)}
                              </span>
                            </div>
                          ))}
                        </div>
                      );
                    }
                    return (
                      <div
                        key={nk}
                        className="flex items-center justify-between py-2 px-3 rounded-md bg-bg-surface"
                      >
                        <span className="text-sm text-text-secondary capitalize">
                          {nk.replace(/_/g, " ")}
                        </span>
                        <span className="text-sm font-mono text-text-primary">
                          {typeof nv === "number" ? nv.toFixed(2) : String(nv)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              );
            }

            return (
              <div
                key={key}
                className="flex items-center justify-between py-2 px-3 rounded-md bg-bg-surface"
              >
                <span className="text-sm text-text-secondary capitalize">
                  {key.replace(/_/g, " ")}
                </span>
                <span className="text-sm font-mono text-text-primary">
                  {typeof value === "number" ? value.toFixed(2) : String(value)}
                </span>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Tab: Templates                                                      */
/* ------------------------------------------------------------------ */

const templateColumns: Column<TemplateRow>[] = [
  { key: "id", header: "Template ID", sortable: true },
  {
    key: "name",
    header: "Name",
    render: (row) => (
      <span className="font-medium text-sm">{row.name}</span>
    ),
    sortable: true,
  },
  {
    key: "type",
    header: "Format",
    render: (row) => (
      <Badge variant="outline" className="capitalize text-xs">
        {row.type}
      </Badge>
    ),
    sortable: true,
  },
  {
    key: "best_for",
    header: "Best For",
    render: (row) => (
      <div className="flex flex-wrap gap-1">
        {(row.best_for ?? []).slice(0, 3).map((tag) => (
          <Badge key={tag} variant="secondary" className="text-xs capitalize">
            {tag.replace(/_/g, " ")}
          </Badge>
        ))}
        {(row.best_for ?? []).length > 3 && (
          <span className="text-xs text-muted-foreground">+{(row.best_for ?? []).length - 3}</span>
        )}
      </div>
    ),
  },
  {
    key: "structure",
    header: "Slides",
    render: (row) => (
      <span className="font-mono text-sm">{row.structure?.length ?? 0}</span>
    ),
    sortable: true,
  },
];

function TemplatesTab({ nicheId }: { nicheId?: string }) {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.config.templates(nicheId),
    queryFn: () => config.templates(nicheId ? { niche_id: nicheId } : undefined),
  });

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  const templates = (data?.data ?? []) as TemplateRow[];

  if (templates.length === 0) {
    return (
      <EmptyState
        icon={LayoutTemplate}
        title="No templates configured"
        description="Configure templates in config/templates.yaml"
      />
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Reel Templates ({templates.length})</CardTitle>
      </CardHeader>
      <CardContent>
        <DataTable columns={templateColumns} data={templates} sortable />
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Tab: Notifications                                                  */
/* ------------------------------------------------------------------ */

function NotificationsTab() {
  const [prefs, setPrefs] = useState<NotificationPreferences>(loadLocalPrefs);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  // Try to load server prefs on mount
  useEffect(() => {
    notificationsApi
      .getPreferences()
      .then((resp) => {
        if (resp.data) {
          setPrefs(resp.data);
          saveLocalPrefs(resp.data);
        }
      })
      .catch(() => {
        // Backend may not exist yet -- fall back to localStorage
      });
  }, []);

  const handleToggleType = useCallback(
    (typeKey: string) => {
      setPrefs((prev) => {
        const types = prev.enabled_types.includes(typeKey)
          ? prev.enabled_types.filter((t) => t !== typeKey)
          : [...prev.enabled_types, typeKey];
        return { ...prev, enabled_types: types };
      });
      setSaved(false);
    },
    [],
  );

  const handleSave = useCallback(async () => {
    setSaving(true);
    setSaved(false);
    saveLocalPrefs(prefs);
    try {
      await notificationsApi.savePreferences(prefs);
    } catch {
      // Backend may not exist -- preferences still saved locally
    }
    setSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }, [prefs]);

  return (
    <div className="space-y-6">
      {/* Slack */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Slack Integration</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <label className="text-sm text-text-secondary">
            Webhook URL
          </label>
          <div className="flex gap-2">
            <Input
              type="url"
              placeholder="https://hooks.slack.com/services/..."
              value={prefs.slack_webhook_url}
              onChange={(e) => {
                setPrefs((prev) => ({
                  ...prev,
                  slack_webhook_url: e.target.value,
                }));
                setSaved(false);
              }}
              className="flex-1"
            />
          </div>
        </CardContent>
      </Card>

      {/* Email Digest */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Email Digest</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <label className="text-sm text-text-secondary">
            Digest Frequency
          </label>
          <Select
            value={prefs.email_digest}
            onValueChange={(val) => {
              setPrefs((prev) => ({
                ...prev,
                email_digest: val as NotificationPreferences["email_digest"],
              }));
              setSaved(false);
            }}
          >
            <SelectTrigger className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="daily">Daily</SelectItem>
              <SelectItem value="weekly">Weekly</SelectItem>
              <SelectItem value="never">Never</SelectItem>
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      {/* Notification Types */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Notification Types</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {NOTIFICATION_TYPES.map((nt) => {
              const checked = prefs.enabled_types.includes(nt.key);
              return (
                <label
                  key={nt.key}
                  className="flex items-center gap-3 py-2 px-3 rounded-md hover:bg-bg-surface transition-colors cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => handleToggleType(nt.key)}
                    className="size-4 rounded border-border accent-accent"
                  />
                  <span className="text-sm text-text-primary">{nt.label}</span>
                </label>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Save */}
      <div className="flex items-center gap-3">
        <Button onClick={() => void handleSave()} disabled={saving}>
          <Save className="size-4" />
          {saving ? "Saving..." : "Save Preferences"}
        </Button>
        {saved && (
          <span className="text-sm text-emerald-500 flex items-center gap-1">
            <CheckCircle2 className="size-4" />
            Saved
          </span>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Tab: Appearance                                                     */
/* ------------------------------------------------------------------ */

function AppearanceTab() {
  const [soundEnabled, setSoundEnabled] = useState(isSoundEnabled);
  const [density, setDensity] = useState(() => localStorage.getItem("genlab-density") ?? "default");
  const [theme, setTheme] = useState(() => localStorage.getItem("genlab-theme") ?? "dark");

  const applyTheme = useCallback((t: string) => {
    setTheme(t);
    localStorage.setItem("genlab-theme", t);
    document.documentElement.setAttribute("data-theme", t);
    if (t === "light") {
      document.documentElement.classList.add("light");
    } else {
      document.documentElement.classList.remove("light");
      document.documentElement.removeAttribute("data-theme");
    }
  }, []);

  const handleToggleSound = useCallback(() => {
    const next = toggleSound();
    setSoundEnabled(next);
  }, []);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            Theme
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-text-muted mb-3">
            The dashboard currently uses a dark theme optimized for low-light environments.
            Light mode is planned for a future release.
          </p>
          <div className="flex gap-3">
            <button
              onClick={() => applyTheme("dark")}
              className={`flex-1 p-3 rounded-lg border-2 text-center cursor-pointer transition-colors ${
                theme === "dark"
                  ? "border-[var(--niche-current)] bg-bg-surface"
                  : "border-border bg-bg-elevated hover:bg-bg-raised"
              }`}
            >
              <div className="text-sm font-medium text-text-primary">Dark</div>
              <div className="text-xs text-text-muted">{theme === "dark" ? "Active" : ""}</div>
            </button>
            <button
              onClick={() => applyTheme("light")}
              className={`flex-1 p-3 rounded-lg border-2 text-center cursor-pointer transition-colors ${
                theme === "light"
                  ? "border-[var(--niche-current)] bg-bg-surface"
                  : "border-border bg-bg-elevated hover:bg-bg-raised"
              }`}
            >
              <div className="text-sm font-medium text-text-primary">Light</div>
              <div className="text-xs text-text-muted">{theme === "light" ? "Active" : ""}</div>
            </button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            Density
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2">
            {(["compact", "default", "comfortable"] as const).map((d) => (
              <button
                key={d}
                onClick={() => { setDensity(d); localStorage.setItem("genlab-density", d); }}
                className={`px-3 py-1.5 rounded-md text-xs font-medium capitalize border transition-colors cursor-pointer ${
                  density === d
                    ? "border-[var(--niche-current)] text-text-primary bg-bg-surface"
                    : "border-border text-text-muted bg-transparent hover:bg-bg-elevated"
                }`}
              >
                {d}
              </button>
            ))}
          </div>
          <p className="text-xs text-text-muted mt-2">
            Controls spacing in tables and lists. Currently visual only — full density support coming soon.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Volume2 className="size-4" />
            UI Sounds
          </CardTitle>
        </CardHeader>
        <CardContent>
          <label className="flex items-center gap-3 py-2 px-3 rounded-md hover:bg-bg-surface transition-colors cursor-pointer w-fit">
            <input
              type="checkbox"
              checked={soundEnabled}
              onChange={handleToggleSound}
              className="size-4 rounded border-border accent-accent"
            />
            <span className="text-sm text-text-primary">Enable UI sounds</span>
          </label>
          <p className="text-xs text-text-muted mt-2 px-3">
            Plays subtle tones on approve, reject, publish, and notification events.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Tab: System                                                         */
/* ------------------------------------------------------------------ */

function SystemTab() {
  const { data, isLoading, isError } = useQuery({
    queryKey: queryKeys.health(),
    queryFn: health,
    refetchInterval: 30_000,
  });

  return (
    <div className="space-y-4">
      {/* Health */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Health Check</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-8 w-32" />
          ) : (
            <div className="flex items-center gap-3">
              {isError ? (
                <>
                  <XCircle className="size-5 text-red-500" />
                  <span className="text-sm text-red-400">
                    Server unreachable
                  </span>
                </>
              ) : (
                <>
                  <CheckCircle2 className="size-5 text-emerald-500" />
                  <span className="text-sm text-emerald-400">
                    {(data?.status as string) ?? "Healthy"}
                  </span>
                </>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Info Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Card>
          <CardContent className="py-5">
            <p className="text-xs text-text-muted uppercase tracking-wider mb-1">
              Server Uptime
            </p>
            {isLoading ? (
              <Skeleton className="h-6 w-24" />
            ) : (
              <p className="text-lg font-semibold text-text-primary">
                {data?.uptime_seconds != null
                  ? `${Math.round(Number(data.uptime_seconds) / 3600)}h`
                  : "--"}
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="py-5">
            <p className="text-xs text-text-muted uppercase tracking-wider mb-1">
              Dashboard Version
            </p>
            <p className="text-lg font-semibold text-text-primary">
              {(data?.version as string) ?? "0.0.0"}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="py-5">
            <p className="text-xs text-text-muted uppercase tracking-wider mb-1">
              Environment
            </p>
            <p className="text-lg font-semibold text-text-primary">
              {(data?.environment as string) ?? import.meta.env.MODE}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="py-5">
            <p className="text-xs text-text-muted uppercase tracking-wider mb-1">
              API Endpoint
            </p>
            <p className="text-sm font-mono text-text-secondary truncate">
              {window.location.origin}/api/v1
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Main Settings View                                                  */
/* ------------------------------------------------------------------ */

const TAB_ITEMS = [
  { value: "sources", label: "Sources", icon: Globe },
  { value: "schedule", label: "Schedule", icon: Clock },
  { value: "scoring", label: "Scoring", icon: BarChart3 },
  { value: "templates", label: "Templates", icon: LayoutTemplate },
  { value: "notifications", label: "Notifications", icon: BellRing },
  { value: "appearance", label: "Appearance", icon: Volume2 },
  { value: "system", label: "System", icon: Server },
] as const;

export default function SettingsView() {
  const { selectedNicheId } = useNicheStore();
  // Config endpoints are per-niche; omit niche_id when "all" so backend picks a sensible default
  const nicheId = selectedNicheId && selectedNicheId !== "all" ? selectedNicheId : undefined;

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight mb-6">Settings</h1>

      <Tabs defaultValue="sources">
        <TabsList className="mb-6">
          {TAB_ITEMS.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>
              <tab.icon className="size-4" />
              <span className="hidden sm:inline">{tab.label}</span>
            </TabsTrigger>
          ))}
        </TabsList>

        <Separator className="mb-6" />

        <TabsContent value="sources">
          <SourcesTab nicheId={nicheId} />
        </TabsContent>
        <TabsContent value="schedule">
          <ScheduleTab nicheId={nicheId} />
        </TabsContent>
        <TabsContent value="scoring">
          <ScoringTab nicheId={nicheId} />
        </TabsContent>
        <TabsContent value="templates">
          <TemplatesTab nicheId={nicheId} />
        </TabsContent>
        <TabsContent value="notifications">
          <NotificationsTab />
        </TabsContent>
        <TabsContent value="appearance">
          <AppearanceTab />
        </TabsContent>
        <TabsContent value="system">
          <SystemTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
