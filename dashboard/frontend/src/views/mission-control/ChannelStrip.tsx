import { useMemo } from "react";
import { motion } from "framer-motion";
import { useCrossNicheOverview } from "@/hooks/use-cross-niche-overview";
import { MiniSparkline } from "@/components/charts/MiniSparkline";
import { formatCompact } from "@/lib/format";
import { getNicheInfo } from "@/niches/registry";

interface ChannelDef {
  id: string;
  name: string;
  accent: string;
}

const CHANNELS: ChannelDef[] = [
  "ai_creators", "gaming", "sports", "movies", "anime",
].map((id) => {
  const info = getNicheInfo(id);
  return { id, name: info.label, accent: info.hex };
});

function ChannelTile({ channel, publishedToday, sparkData, index }: {
  channel: ChannelDef;
  publishedToday: number;
  sparkData: number[];
  index: number;
}) {
  const totalReach = useMemo(() => sparkData.reduce((s, v) => s + v, 0), [sparkData]);

  return (
    <motion.div
      className="bg-bg-surface border border-border rounded-lg overflow-hidden flex"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.06, duration: 0.35 }}
    >
      <div
        className="w-[3px] shrink-0"
        style={{ backgroundColor: channel.accent }}
      />
      <div className="flex-1 p-3 flex flex-col gap-0.5">
        <span className="text-sm font-semibold text-text-primary">{channel.name}</span>
        <span className="text-xs text-text-muted">
          {totalReach > 0 ? `${formatCompact(totalReach)} reach` : `${publishedToday} published`}
        </span>
        <span className="text-2xl font-bold text-text-primary tabular-nums mt-1">{publishedToday}</span>
        <span className="text-[10px] text-text-muted uppercase tracking-wide">today</span>
        <div className="mt-1">
          <MiniSparkline data={sparkData} color={channel.accent} width={80} height={20} />
        </div>
      </div>
    </motion.div>
  );
}

export function ChannelStrip() {
  const { data } = useCrossNicheOverview();

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
      {CHANNELS.map((ch, i) => {
        const nicheData = data?.niches.find((n) => n.id === ch.id);
        const dailyReach = data?.niche_daily_reach?.[ch.id] ?? [];
        const sparkData = dailyReach.map((d) => d.reach);
        return (
          <ChannelTile
            key={ch.id}
            channel={ch}
            publishedToday={nicheData?.published_today ?? 0}
            sparkData={sparkData}
            index={i}
          />
        );
      })}
    </div>
  );
}
