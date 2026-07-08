/**
 * AdditionalSourcesEditor — M-20 + M-21 edit UI for extra RSS feeds
 * and reddit subreddits on a per-niche sources.yaml.
 *
 * Companion to SourcesEditor (M-19, YouTube channels). Splits the
 * non-YouTube source types into a tabbed card so operators have one
 * place per niche to add/prune content sources:
 *
 *   - YouTube channels        → SourcesEditor (M-19, PR #315)
 *   - Extra RSS feeds         → this component (M-20)
 *   - Reddit subreddits       → this component (M-21)
 *
 * Why two separate cards (not a single mega-tab):
 *   - YouTube channels are the highest-volume edit surface (operator
 *     prunes them most often based on SourceQualityCard data) — they
 *     deserve a dedicated card without tab-switching latency
 *   - RSS + Reddit are bursty: operator adds 1-2 at a time when a new
 *     trade-press blog or subreddit becomes relevant. Tabs are fine.
 *
 * Backend contract (mounted under /api/v1/config/sources/):
 *   - GET/POST/DELETE /rss-feeds?niche_id=X
 *   - GET/POST/DELETE /subreddits?niche_id=X
 *   - All writes go through ruamel round-trip + fcntl.flock — see
 *     config_routes.py:add_rss_feed / config_routes.py:add_subreddit
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  sources,
  type RssFeedEntry,
} from "@/api/client";
import { humanizeApiError } from "@/lib/humanize-api-error";
import { NICHE_IDS, getNicheInfo, type NicheId } from "@/niches/registry";

const RSS_QK = (nicheId: NicheId) => ["config", "rss-feeds", nicheId];
const SUBS_QK = (nicheId: NicheId) => ["config", "subreddits", nicheId];

type Tab = "rss" | "reddit";

export function AdditionalSourcesEditor() {
  const [nicheId, setNicheId] = useState<NicheId>(NICHE_IDS[0]);
  const [tab, setTab] = useState<Tab>("rss");
  const nicheInfo = getNicheInfo(nicheId);

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Edit RSS feeds &amp; subreddits</h3>
        <span
          className="rounded-full px-2 py-0.5 text-xs"
          style={{ backgroundColor: `${nicheInfo.hex}20`, color: nicheInfo.hex }}
        >
          {nicheInfo.label}
        </span>
      </div>

      <label className="mb-3 block text-xs">
        <span className="mb-1 block text-muted-foreground">Niche</span>
        <select
          value={nicheId}
          onChange={(e) => setNicheId(e.target.value as NicheId)}
          className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
        >
          {NICHE_IDS.map((id) => (
            <option key={id} value={id}>
              {getNicheInfo(id).label}
            </option>
          ))}
        </select>
      </label>

      {/* Tab strip — kept compact so the card stays single-row */}
      <div role="tablist" className="mb-3 flex gap-1 border-b border-border">
        <TabButton active={tab === "rss"} onClick={() => setTab("rss")}>
          RSS feeds
        </TabButton>
        <TabButton active={tab === "reddit"} onClick={() => setTab("reddit")}>
          Subreddits
        </TabButton>
      </div>

      {tab === "rss" && <RssEditor nicheId={nicheId} />}
      {tab === "reddit" && <SubredditsEditor nicheId={nicheId} />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={
        "rounded-t-md border-b-2 px-3 py-1.5 text-xs font-medium transition-colors " +
        (active
          ? "border-primary text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground")
      }
    >
      {children}
    </button>
  );
}

// ── RSS feeds (M-20) ─────────────────────────────────────────

function RssEditor({ nicheId }: { nicheId: NicheId }) {
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [maxAge, setMaxAge] = useState<string>("");  // string for the input; converted on submit
  const [formError, setFormError] = useState<string | null>(null);

  const feedsQuery = useQuery({
    queryKey: RSS_QK(nicheId),
    queryFn: () => sources.listRssFeeds(nicheId),
    staleTime: 30_000,
  });

  const addMutation = useMutation({
    mutationFn: (entry: Omit<RssFeedEntry, "max_age_hours"> & { max_age_hours?: number }) =>
      sources.addRssFeed(nicheId, entry),
    onSuccess: () => {
      setUrl("");
      setName("");
      setMaxAge("");
      setFormError(null);
      void qc.invalidateQueries({ queryKey: RSS_QK(nicheId) });
    },
    onError: (err) => setFormError(humanizeApiError(err).message),
  });

  const removeMutation = useMutation({
    mutationFn: (feedUrl: string) => sources.removeRssFeed(nicheId, feedUrl),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: RSS_QK(nicheId) });
    },
    onError: (err) => setFormError(humanizeApiError(err).message),
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    const trimmedUrl = url.trim();
    const trimmedName = name.trim();
    if (!trimmedUrl || !trimmedName) {
      setFormError("URL and name are both required");
      return;
    }
    const entry: Parameters<typeof sources.addRssFeed>[1] = {
      url: trimmedUrl,
      name: trimmedName,
    };
    if (maxAge.trim()) {
      const parsed = parseInt(maxAge.trim(), 10);
      if (isNaN(parsed) || parsed <= 0) {
        setFormError("max_age_hours must be a positive integer");
        return;
      }
      entry.max_age_hours = parsed;
    }
    addMutation.mutate(entry);
  };

  const feeds = feedsQuery.data?.rss_feeds_extra ?? [];

  return (
    <>
      <form onSubmit={handleSubmit} className="mb-4 space-y-2">
        <label className="block text-xs">
          <span className="mb-1 block text-muted-foreground">
            Feed URL (must be https://, must have a path)
          </span>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/feed.xml"
            className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            aria-describedby={formError ? "rss-form-error" : undefined}
          />
        </label>
        <label className="block text-xs">
          <span className="mb-1 block text-muted-foreground">Display name (≤100 chars)</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={100}
            placeholder="Example Feed"
            className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          />
        </label>
        <label className="block text-xs">
          <span className="mb-1 block text-muted-foreground">
            Max age hours (default 168 = 7 days; cap 720 = 30 days)
          </span>
          <input
            type="number"
            value={maxAge}
            onChange={(e) => setMaxAge(e.target.value)}
            min={1}
            max={720}
            placeholder="168"
            className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          />
        </label>
        <button
          type="submit"
          disabled={addMutation.isPending}
          className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
        >
          {addMutation.isPending ? "Adding…" : "Add feed"}
        </button>
        {formError && (
          <p id="rss-form-error" role="alert" className="text-xs text-destructive">
            {formError}
          </p>
        )}
      </form>

      <div>
        <div className="mb-2 text-xs text-muted-foreground">
          Current feeds ({feeds.length})
        </div>
        {feedsQuery.isLoading && (
          <p className="text-xs text-muted-foreground">Loading…</p>
        )}
        {feedsQuery.isError && (
          <p className="text-xs text-destructive">Failed to load feeds</p>
        )}
        {!feedsQuery.isLoading && feeds.length === 0 && (
          <p className="text-xs text-muted-foreground">No extra feeds configured.</p>
        )}
        {feeds.length > 0 && (
          <ul className="space-y-1">
            {feeds.map((f) => (
              <li
                key={f.url}
                className="flex items-center justify-between gap-2 rounded-md border border-border bg-background px-2 py-1.5 text-xs"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{f.name}</div>
                  <div className="truncate text-muted-foreground">
                    {f.url} · {f.max_age_hours}h
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    if (window.confirm(`Remove "${f.name}"?`)) {
                      removeMutation.mutate(f.url);
                    }
                  }}
                  disabled={removeMutation.isPending}
                  aria-label={`Remove ${f.name}`}
                  className="rounded-md border border-destructive/40 px-2 py-1 text-xs text-destructive hover:bg-destructive/10 disabled:opacity-50"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

// ── Subreddits (M-21) ────────────────────────────────────────

function SubredditsEditor({ nicheId }: { nicheId: NicheId }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const subsQuery = useQuery({
    queryKey: SUBS_QK(nicheId),
    queryFn: () => sources.listSubreddits(nicheId),
    staleTime: 30_000,
  });

  const addMutation = useMutation({
    mutationFn: (subName: string) => sources.addSubreddit(nicheId, subName),
    onSuccess: () => {
      setName("");
      setFormError(null);
      void qc.invalidateQueries({ queryKey: SUBS_QK(nicheId) });
    },
    onError: (err) => setFormError(humanizeApiError(err).message),
  });

  const removeMutation = useMutation({
    mutationFn: (subName: string) => sources.removeSubreddit(nicheId, subName),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SUBS_QK(nicheId) });
    },
    onError: (err) => setFormError(humanizeApiError(err).message),
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    const trimmedName = name.trim();
    if (!trimmedName) {
      setFormError("Subreddit name is required");
      return;
    }
    addMutation.mutate(trimmedName);
  };

  const subs = subsQuery.data?.subreddits ?? [];

  return (
    <>
      <form onSubmit={handleSubmit} className="mb-4 space-y-2">
        <label className="block text-xs">
          <span className="mb-1 block text-muted-foreground">
            Subreddit name (3-21 chars, alphanumeric + underscore; ``r/`` prefix optional)
          </span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="AnimeSakuga"
            maxLength={24}  // allow ``r/`` prefix without blocking input
            className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            aria-describedby={formError ? "subs-form-error" : undefined}
          />
        </label>
        <button
          type="submit"
          disabled={addMutation.isPending}
          className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
        >
          {addMutation.isPending ? "Adding…" : "Add subreddit"}
        </button>
        {formError && (
          <p id="subs-form-error" role="alert" className="text-xs text-destructive">
            {formError}
          </p>
        )}
      </form>

      <div>
        <div className="mb-2 text-xs text-muted-foreground">
          Current subreddits ({subs.length})
        </div>
        {subsQuery.isLoading && (
          <p className="text-xs text-muted-foreground">Loading…</p>
        )}
        {subsQuery.isError && (
          <p className="text-xs text-destructive">Failed to load subreddits</p>
        )}
        {!subsQuery.isLoading && subs.length === 0 && (
          <p className="text-xs text-muted-foreground">No subreddits configured.</p>
        )}
        {subs.length > 0 && (
          <ul className="space-y-1">
            {subs.map((s) => (
              <li
                key={s.name}
                className="flex items-center justify-between gap-2 rounded-md border border-border bg-background px-2 py-1.5 text-xs"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">r/{s.name}</div>
                  {(s.min_score || s.time_filter || s.content_type) && (
                    <div className="truncate text-muted-foreground">
                      {[
                        s.min_score ? `min_score: ${s.min_score}` : null,
                        s.time_filter ? `time: ${s.time_filter}` : null,
                        s.content_type ? `type: ${s.content_type}` : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => {
                    if (window.confirm(`Remove r/${s.name}?`)) {
                      removeMutation.mutate(s.name);
                    }
                  }}
                  disabled={removeMutation.isPending}
                  aria-label={`Remove r/${s.name}`}
                  className="rounded-md border border-destructive/40 px-2 py-1 text-xs text-destructive hover:bg-destructive/10 disabled:opacity-50"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
