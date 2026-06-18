import { useState, useMemo, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { useQueryStates, parseAsString } from "nuqs";
import { Newspaper, ChevronLeft, ChevronRight, ExternalLink, Download } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { DataTable, type Column } from "@/components/shared/data-table";
import { EmptyState } from "@/components/shared/empty-state";
import { FilterBar } from "@/components/shared/filter-bar";
import { useStories } from "@/hooks/use-stories";
import { useNicheStore } from "@/stores/niche-store";
import { formatRelativeTime, getTodayISO } from "@/lib/format";
import { cn } from "@/lib/utils";
import { exportToCSV, type CSVColumn } from "@/lib/export";
import type { Story } from "@/api/types";
import { queryKeys } from "@/api/query-keys";

// Source filters from config API (all available platforms)
function useSourceFilters() {
  const { data } = useQuery<Array<{ value: string; label: string }>>({
    queryKey: queryKeys.config.sourceFilters(),
    queryFn: async () => {
      const resp = await fetch("/api/v1/config/source-filters");
      const body = await resp.json();
      // API returns {status, data: {data: [...]}} — unwrap the envelope
      const payload = body?.data ?? body;
      return Array.isArray(payload?.data) ? payload.data : Array.isArray(payload) ? payload : [];
    },
    staleTime: 5 * 60 * 1000,
  });
  return data ?? [];
}

function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength).trimEnd() + "...";
}

function ScoreIndicator({ score }: { score: number }) {
  // 0 -> red, 0.5 -> yellow, 1 -> green
  const hue = Math.round(score * 120); // 0 = red, 120 = green
  return (
    <span
      className="font-mono text-sm font-medium"
      style={{ color: `hsl(${hue}, 70%, 55%)` }}
    >
      {score.toFixed(2)}
    </span>
  );
}

function StoryDetail({ story }: { story: Story }) {
  return (
    <Card className="bg-bg-elevated border-border mt-2 mb-4">
      <CardContent className="pt-4 space-y-3">
        {story.summary && (
          <div>
            <p className="text-xs font-medium text-text-muted mb-1">Summary</p>
            <p className="text-sm text-text-secondary leading-relaxed">
              {story.summary}
            </p>
          </div>
        )}
        <Separator className="bg-border" />
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-sm">
          <div>
            <p className="text-xs font-medium text-text-muted mb-0.5">
              Story ID
            </p>
            <p className="text-text-secondary font-mono text-xs">
              {story.story_id ?? story.id}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium text-text-muted mb-0.5">
              Published
            </p>
            <p className="text-text-secondary">
              {story.published_date
                ? new Date(story.published_date).toLocaleDateString("en-US", {
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })
                : "Unknown"}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium text-text-muted mb-0.5">
              Source URL
            </p>
            {story.url ? (
              <a
                href={story.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent hover:underline inline-flex items-center gap-1 text-sm"
              >
                View source
                <ExternalLink className="size-3" />
              </a>
            ) : (
              <span className="text-text-muted">N/A</span>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function SkeletonTable() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 8 }).map((_, i) => (
        <Skeleton key={i} className="h-12 w-full" />
      ))}
    </div>
  );
}

export default function StoriesView() {
  const [page, setPage] = useState(1);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const { selectedNicheId } = useNicheStore();

  // Read filter params from nuqs (set by FilterBar)
  const [filterParams] = useQueryStates({
    source: parseAsString.withDefault(""),
    q: parseAsString.withDefault(""),
  });

  const params = useMemo(() => {
    const p: Record<string, string> = { page: String(page), per_page: "20" };
    if (filterParams.source) p.source = filterParams.source;
    if (filterParams.q) p.search = filterParams.q;
    p.niche_id = selectedNicheId ?? "all";
    return p;
  }, [page, filterParams.source, filterParams.q, selectedNicheId]);

  // Reset to page 1 when filters change.
  // React 19 idiom: "store previous prop, reset during render" rather than
  // the equivalent useEffect → setState, which trips
  // react-hooks/set-state-in-effect (cascading-render warning).
  // See: https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes
  const filtersKey = `${filterParams.source ?? ""}|${filterParams.q ?? ""}`;
  const [prevFiltersKey, setPrevFiltersKey] = useState(filtersKey);
  if (prevFiltersKey !== filtersKey) {
    setPrevFiltersKey(filtersKey);
    setPage(1);
  }

  const { data: response, isLoading, isError } = useStories(params);

  const stories = response?.data ?? [];
  const meta = response?.meta ?? { page: 1, per_page: 20, total: 0, total_pages: 1 };

  const sourceFilters = useSourceFilters();
  const filterDefinitions = useMemo(
    () => [{ key: "source", label: "Source", options: sourceFilters }],
    [sourceFilters],
  );

  const handleRowClick = useCallback(
    (row: Story) => {
      setExpandedId((prev) => (prev === row.id ? null : row.id));
    },
    []
  );

  const columns: Column<Story>[] = useMemo(
    () => [
      {
        key: "title",
        header: "Title",
        sortable: true,
        render: (row) => (
          <span className="text-text-primary font-medium hover:text-accent transition-colors">
            {truncate(row.title ?? "Untitled", 60)}
          </span>
        ),
      },
      {
        key: "source",
        header: "Source",
        sortable: true,
        className: "w-32",
        render: (row) =>
          row.source ? (
            <Badge
              variant="outline"
              className="bg-bg-elevated border-border text-text-secondary text-xs"
            >
              {row.source}
            </Badge>
          ) : (
            <span className="text-text-muted">--</span>
          ),
      },
      {
        key: "score",
        header: "Score",
        sortable: true,
        className: "w-20",
        render: (row) =>
          row.score != null ? (
            <ScoreIndicator score={row.score} />
          ) : (
            <span className="text-text-muted">--</span>
          ),
      },
      {
        key: "cluster_id",
        header: "Cluster",
        className: "w-24",
        render: (row) =>
          row.cluster_id ? (
            <span className="font-mono text-xs text-text-secondary">
              {row.cluster_id}
            </span>
          ) : (
            <span className="text-text-muted">&mdash;</span>
          ),
      },
      {
        key: "published_date",
        header: "Date",
        sortable: true,
        className: "w-28",
        render: (row) => (
          <span className="text-text-muted text-sm">
            {row.published_date
              ? formatRelativeTime(row.published_date)
              : "--"}
          </span>
        ),
      },
    ],
    []
  );

  if (isError) {
    return (
      <div>
        <h1 className="text-2xl font-semibold tracking-tight mb-6 text-text-primary">
          Stories Explorer
        </h1>
        <EmptyState
          icon={Newspaper}
          title="Failed to load stories"
          description="An error occurred while fetching stories. Please try again."
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
          Stories Explorer
        </h1>
        <Button
          variant="outline"
          size="sm"
          className="h-8 gap-1.5 text-xs"
          disabled={isLoading || stories.length === 0}
          onClick={() => {
            const cols: CSVColumn<Story>[] = [
              { key: "id", header: "ID" },
              { key: "title", header: "Title" },
              { key: "source", header: "Source" },
              { key: "score", header: "Score" },
              { key: "cluster_id", header: "Cluster" },
              { key: "published_date", header: "Published Date" },
              { key: "url", header: "URL" },
            ];
            exportToCSV(stories, cols, `stories-${getTodayISO()}.csv`);
          }}
        >
          <Download className="size-3.5" />
          Export CSV
        </Button>
      </div>

      {/* Filter bar */}
      <FilterBar
        filters={filterDefinitions}
        search
        searchPlaceholder="Search stories..."
      />

      {/* Content */}
      {isLoading ? (
        <SkeletonTable />
      ) : stories.length === 0 ? (
        <EmptyState
          icon={Newspaper}
          title="No stories found"
          description="No stories match your current filters. Try adjusting your search."
        />
      ) : (
        <>
          <DataTable
            columns={columns}
            data={stories}
            onRowClick={handleRowClick}
            sortable
          />

          {/* Expanded detail */}
          {expandedId && (
            <>
              {stories
                .filter((s) => s.id === expandedId)
                .map((s) => (
                  <StoryDetail key={s.id} story={s} />
                ))}
            </>
          )}

          {/* Pagination */}
          <div className="flex items-center justify-between pt-2">
            <p className="text-sm text-text-muted">
              Page {meta.page} of {meta.total_pages} ({meta.total} total)
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={meta.page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                className={cn(
                  "h-8 gap-1",
                  meta.page <= 1 && "opacity-50"
                )}
              >
                <ChevronLeft className="size-4" />
                Prev
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={meta.page >= meta.total_pages}
                onClick={() => setPage((p) => p + 1)}
                className={cn(
                  "h-8 gap-1",
                  meta.page >= meta.total_pages && "opacity-50"
                )}
              >
                Next
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
