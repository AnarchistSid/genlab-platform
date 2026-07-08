/**
 * SourcesEditor — M-19 add/remove UI for niche sources.yaml.
 *
 * Pairs with SourceQualityCard (read-only health) to give operators a
 * write path: SourceQualityCard says "channel X has 0% claim rate",
 * SourcesEditor lets them prune it without touching the file system.
 *
 * Per-niche scoping:
 *   - One niche selector at the top — keeps URLs scoped, prevents
 *     accidentally adding a Crunchyroll feed to the gaming niche
 *   - Lists currently-active youtube_channels for that niche
 *   - Form to add (URL + display name); delete button per row
 *
 * Backend contract:
 *   - GET /api/v1/config/sources/youtube-channels?niche_id=X
 *   - POST /api/v1/config/sources/youtube-channels?niche_id=X {url, name}
 *   - DELETE /api/v1/config/sources/youtube-channels?niche_id=X&url=...
 *   - All writes go through ruamel round-trip + fcntl.flock + URL
 *     validator (channel-flavoured paths only) — see config_routes.py
 *
 * Validation feedback:
 *   - 4xx errors surface via the form error region (URL rejected,
 *     name too long, etc.)
 *   - 409 (URL already present) surfaces clearly so operator knows
 *     the dup wasn't silently swallowed
 *
 * Accessibility:
 *   - <form> with proper labels + aria-describedby for error region
 *   - Each delete button has aria-label "Remove <name>" for SR users
 *   - Tab order: niche → URL → name → submit → first row's delete
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  sources,
  type YoutubeChannelEntry,
} from "@/api/client";
import { humanizeApiError } from "@/lib/humanize-api-error";
import { NICHE_IDS, getNicheInfo, type NicheId } from "@/niches/registry";

const QK = (nicheId: NicheId) => ["config", "youtube-channels", nicheId];

export function SourcesEditor() {
  const qc = useQueryClient();
  const [nicheId, setNicheId] = useState<NicheId>(NICHE_IDS[0]);
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const channelsQuery = useQuery({
    queryKey: QK(nicheId),
    queryFn: () => sources.listYoutubeChannels(nicheId),
    staleTime: 30_000,
  });

  const addMutation = useMutation({
    mutationFn: (entry: YoutubeChannelEntry) =>
      sources.addYoutubeChannel(nicheId, entry),
    onSuccess: () => {
      setUrl("");
      setName("");
      setFormError(null);
      void qc.invalidateQueries({ queryKey: QK(nicheId) });
    },
    onError: (err) => setFormError(humanizeApiError(err).message),
  });

  const removeMutation = useMutation({
    mutationFn: (channelUrl: string) =>
      sources.removeYoutubeChannel(nicheId, channelUrl),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QK(nicheId) });
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
    addMutation.mutate({ url: trimmedUrl, name: trimmedName });
  };

  const channels = channelsQuery.data?.youtube_channels ?? [];
  const nicheInfo = getNicheInfo(nicheId);

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Edit YouTube channel sources</h3>
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
          onChange={(e) => {
            setNicheId(e.target.value as NicheId);
            setFormError(null);
          }}
          className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
        >
          {NICHE_IDS.map((id) => (
            <option key={id} value={id}>
              {getNicheInfo(id).label}
            </option>
          ))}
        </select>
      </label>

      <form onSubmit={handleSubmit} className="mb-4 space-y-2">
        <label className="block text-xs">
          <span className="mb-1 block text-muted-foreground">
            Channel URL (must include /channel/, /@, /c/, /user/, or /feeds/videos.xml)
          </span>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://www.youtube.com/@example"
            className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            aria-describedby={formError ? "sources-form-error" : undefined}
          />
        </label>
        <label className="block text-xs">
          <span className="mb-1 block text-muted-foreground">Display name (≤80 chars)</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={80}
            placeholder="Example Channel"
            className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          />
        </label>
        <button
          type="submit"
          disabled={addMutation.isPending}
          className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
        >
          {addMutation.isPending ? "Adding…" : "Add channel"}
        </button>
        {formError && (
          <p
            id="sources-form-error"
            role="alert"
            className="text-xs text-destructive"
          >
            {formError}
          </p>
        )}
      </form>

      <div>
        <div className="mb-2 text-xs text-muted-foreground">
          Current channels ({channels.length})
        </div>
        {channelsQuery.isLoading && (
          <p className="text-xs text-muted-foreground">Loading…</p>
        )}
        {channelsQuery.isError && (
          <p className="text-xs text-destructive">Failed to load channels</p>
        )}
        {!channelsQuery.isLoading && channels.length === 0 && (
          <p className="text-xs text-muted-foreground">No channels configured.</p>
        )}
        {channels.length > 0 && (
          <ul className="space-y-1">
            {channels.map((c) => (
              <li
                key={c.url}
                className="flex items-center justify-between gap-2 rounded-md border border-border bg-background px-2 py-1.5 text-xs"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{c.name}</div>
                  <div className="truncate text-muted-foreground">{c.url}</div>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    if (window.confirm(`Remove "${c.name}" from ${nicheInfo.label}?`)) {
                      removeMutation.mutate(c.url);
                    }
                  }}
                  disabled={removeMutation.isPending}
                  aria-label={`Remove ${c.name}`}
                  className="rounded-md border border-destructive/40 px-2 py-1 text-xs text-destructive hover:bg-destructive/10 disabled:opacity-50"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
