import { useMemo } from "react";
import { motion } from "framer-motion";
import { useCrossNicheOverview } from "@/hooks/use-cross-niche-overview";
import { AnimatedKPICard } from "@/components/kpi/AnimatedKPICard";

export function KpiHero() {
  const { data, isLoading } = useCrossNicheOverview();

  const stats = useMemo(() => {
    if (!data) return null;

    const published = data.niches.reduce((s, n) => s + n.published_today, 0);
    const nicheCount = data.niches.length;

    // Health percentage — exclude "not_configured" platforms
    const configured = Object.entries(data.global.platform_health).filter(
      ([, s]) => s !== "not_configured",
    );
    const okCount = configured.filter(([, s]) => s === "ok").length;
    const healthPct = configured.length > 0 ? Math.round((okCount / configured.length) * 100) : 100;

    // Aggregate daily reach across all niches for sparkline
    const dailyMap = new Map<string, number>();
    if (data.niche_daily_reach) {
      for (const [, days] of Object.entries(data.niche_daily_reach)) {
        for (const d of days) {
          dailyMap.set(d.date, (dailyMap.get(d.date) ?? 0) + d.reach);
        }
      }
    }
    const reachSparkline = [...dailyMap.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-7)
      .map(([, v]) => ({ value: v }));

    // Simple previous period estimate (first half vs second half of sparkline)
    const half = Math.floor(reachSparkline.length / 2);
    const recentSum = reachSparkline.slice(half).reduce((s, d) => s + d.value, 0);
    const prevSum = reachSparkline.slice(0, half).reduce((s, d) => s + d.value, 0);

    return {
      reach: data.global.total_reach ?? null,
      reachPrev: prevSum > 0 && recentSum > 0 ? Math.round(prevSum * 2) : null,
      reachSparkline,
      likes: data.global.total_likes ?? null,
      published,
      nicheCount,
      healthPct,
    };
    // healthResp not read inside the closure — platform_health comes
    // from ``data.global``. Excluding satisfies exhaustive-deps.
  }, [data]);

  const cards = useMemo(
    () =>
      stats
        ? [
            {
              label: "TOTAL REACH",
              value: stats.reach,
              previousValue: stats.reachPrev,
              sparklineData: stats.reachSparkline,
              accentColor: "var(--color-blue)",
            },
            {
              label: "TOTAL LIKES",
              value: stats.likes,
              accentColor: "var(--color-emerald)",
            },
            {
              label: "PUBLISHED TODAY",
              value: stats.published,
              accentColor: "var(--color-green)",
            },
            {
              label: "SYSTEM HEALTH",
              value: stats.healthPct,
              format: "percent" as const,
              accentColor:
                stats.healthPct >= 90
                  ? "var(--color-green)"
                  : stats.healthPct >= 70
                    ? "var(--color-amber)"
                    : "var(--color-red)",
            },
          ]
        : [],
    [stats],
  );

  return (
    <motion.div
      className="kpi-hero-grid"
      initial="hidden"
      animate="visible"
      variants={{
        hidden: {},
        visible: { transition: { staggerChildren: 0.08 } },
      }}
    >
      {isLoading || !stats
        ? Array.from({ length: 4 }).map((_, i) => (
            <AnimatedKPICard key={i} label="" value={null} loading index={i} />
          ))
        : cards.map((card, i) => <AnimatedKPICard key={card.label} {...card} index={i} />)}
    </motion.div>
  );
}
