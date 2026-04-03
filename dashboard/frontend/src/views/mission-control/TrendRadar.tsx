import { TrendingUp } from "lucide-react";
import { useTrends } from "@/hooks/use-trends";
import { getNicheInfo } from "@/niches/registry";

interface TrendItem {
  keyword: string;
  nicheId: string;
}

export function TrendRadar() {
  const { data, dataUpdatedAt } = useTrends();

  // Parse trends data — TrendData is Record<string, string[]>
  const { trends, hasRealData } = (() => {
    if (!data || typeof data !== "object") {
      return { trends: [] as TrendItem[], hasRealData: false };
    }

    const items: TrendItem[] = [];
    for (const [nicheId, keywords] of Object.entries(data)) {
      if (!Array.isArray(keywords)) continue;
      for (const kw of keywords) {
        if (!items.find((t) => t.keyword === kw)) {
          items.push({ keyword: kw, nicheId });
        }
      }
    }

    return { trends: items.slice(0, 4), hasRealData: items.length > 0 };
  })();

  // Cache TTL: 6h
  const cacheTtl = dataUpdatedAt
    ? Math.max(0, 6 * 60 * 60 * 1000 - (Date.now() - dataUpdatedAt))
    : null;
  const cacheMin = cacheTtl != null ? Math.floor(cacheTtl / 60000) : null;
  const cacheLabel = cacheMin != null
    ? cacheMin > 60
      ? `${Math.floor(cacheMin / 60)}h ${cacheMin % 60}m`
      : `${cacheMin}m`
    : "—";

  return (
    <div className="bento-card">
      <h3 className="card-title">
        <TrendingUp size={12} style={{ marginRight: 4, verticalAlign: "middle" }} />
        Trend Radar
      </h3>

      {!hasRealData ? (
        <p className="text-sm text-text-muted py-4 text-center">
          Google Trends data loading — cached every 6 hours
        </p>
      ) : (
        <>
          <div className="flex flex-col gap-2">
            {trends.map((trend, i) => (
              <div key={i} className="flex items-center gap-2">
                <span
                  className="size-2 rounded-full shrink-0"
                  style={{ backgroundColor: getNicheInfo(trend.nicheId).hex }}
                />
                <span className="text-sm text-text-primary flex-1 truncate">{trend.keyword}</span>
              </div>
            ))}
          </div>

          <div className="text-[10px] text-text-ghost mt-3 pt-2 border-t border-border-subtle">
            Cache TTL: {cacheLabel}
          </div>
        </>
      )}
    </div>
  );
}
