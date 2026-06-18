import { useMemo } from "react";
import { useCrossNicheOverview } from "@/hooks/use-cross-niche-overview";
import { useChannelHealth } from "@/hooks/use-channel-health";
import { AlertBanner } from "@/components/shared/AlertBanner";
import { PageHeader } from "@/components/shared/page-header";
import { ErrorState } from "@/components/shared/error-state";
import { LoadingSkeleton } from "@/components/shared/loading-skeleton";
import { PublishingAlertBanner } from "./AlertBanner";
import { CriticalAlertsBanner } from "./CriticalAlertsBanner";
import { PublishingHealth } from "./PublishingHealth";
import { KpiHero } from "./KpiHero";
import { TopPostSpotlight } from "./TopPostSpotlight";
import { LearningLoopCard } from "./LearningLoopCard";
import { AutoApprovalCalibrationCard } from "./AutoApprovalCalibrationCard";
import { TrackRecordCard } from "./TrackRecordCard";
import { RolloutPctSlider } from "./RolloutPctSlider";
import { AutoApprovalKillSwitch } from "./AutoApprovalKillSwitch";
import { DailySloBadge } from "./DailySloBadge";
import { SourceQualityCard } from "./SourceQualityCard";
import { SourcesEditor } from "./SourcesEditor";
import { AdditionalSourcesEditor } from "./AdditionalSourcesEditor";
import { AiInsightCard } from "./AiInsightCard";
import { PublishTimeline } from "./PublishTimeline";
import { UpcomingQueue } from "./UpcomingQueue";
import { ChannelStrip } from "./ChannelStrip";
import { EngagementFeed } from "./EngagementFeed";
import { TrendRadar } from "./TrendRadar";
import { ContentQuality } from "./ContentQuality";
import { PipelineCountdowns } from "./PipelineCountdowns";
import { MonetisationCompact } from "./MonetisationCompact";

// ── Helpers ────────────────────────────────────────────────

function getGreeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

function formatDate(): string {
  const d = new Date();
  const day = d.toLocaleDateString("en-US", { weekday: "long" });
  const date = d.toLocaleDateString("en-US", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
  return `${day} · ${date}`;
}

function getSprintNumber(): number {
  // Sprint 66 completed as of 2026-03-17, weekly sprints
  const sprintEpoch = new Date("2026-03-17T00:00:00Z");
  const now = new Date();
  const weeksSince = Math.floor(
    (now.getTime() - sprintEpoch.getTime()) / (7 * 24 * 60 * 60 * 1000),
  );
  return 66 + Math.max(0, weeksSince);
}

// ── Alert Logic ────────────────────────────────────────────

function useAlertMessage(): {
  message: string;
  type: "error" | "warning" | "info";
  link?: string;
} | null {
  const { data } = useCrossNicheOverview();
  const { data: healthResp } = useChannelHealth();

  return useMemo(() => {
    if (!data) return null;

    // Check for pipeline errors
    const errorNiches = data.niches.filter((n) => n.pipeline_status === "error");
    if (errorNiches.length > 0) {
      const names = errorNiches.map((n) => n.display_name).join(", ");
      return { message: `Pipeline error on ${names}`, type: "error" as const };
    }

    // Check for platform health issues
    const healthData = healthResp?.data;
    if (healthData) {
      const degraded = Object.entries(healthData).filter(
        ([, status]) => status === "error" || status === "degraded",
      );
      if (degraded.length > 0) {
        const platforms = degraded.map(([p]) => p.replace("_", "/")).join(", ");
        return { message: `Platform health issue: ${platforms}`, type: "warning" as const };
      }
    }

    // Check pending review count
    if (data.global.total_pending_review > 5) {
      return {
        message: `${data.global.total_pending_review} posts waiting for review`,
        type: "info" as const,
        link: "/content",
      };
    }

    return null;
  }, [data, healthResp]);
}

// ── Main Component ─────────────────────────────────────────

export default function MissionControl() {
  const { data, isLoading, error, refetch } = useCrossNicheOverview();
  const alertInfo = useAlertMessage();
  const sprint = getSprintNumber();

  if (error && !data) {
    return (
      <div>
        <ErrorState
          title="Unable to load Mission Control"
          message="Could not connect to the API."
          onRetry={() => void refetch()}
        />
      </div>
    );
  }

  return (
    <div>
      {/* Greeting Header + global kill switch (D3.10) */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <PageHeader
          title={`${getGreeting()}.`}
          badge={<span className="text-xs font-medium text-text-muted px-2 py-0.5 rounded-full bg-bg-elevated">Sprint {sprint}</span>}
          subtitle={formatDate()}
        />
        <div className="flex items-center gap-3">
          <AutoApprovalKillSwitch />
        </div>
      </div>

      {/* Critical infrastructure alerts banner — reads unresolved
          CRITICAL rows from pipeline_alerts (warp_down,
          download_failure, qc_collapse, etc). Rendered ABOVE the
          publishing banner so infrastructure/operator-action signals
          land before blueprint-derived signals. */}
      <CriticalAlertsBanner />

      {/* Publishing alert banner — shows unresolved publish failures/warnings */}
      <PublishingAlertBanner />

      {/* Alert Banner (conditional) */}
      {alertInfo && (
        <AlertBanner
          message={alertInfo.message}
          type={alertInfo.type}
          link={alertInfo.link}
          dismissable
        />
      )}

      {/* Bento Grid */}
      {isLoading || !data ? (
        <LoadingSkeleton variant="bento" />
      ) : (
        <div className="mc-grid-v2">
          {/* Row 1: KPI Hero (full width) */}
          <div className="area-kpi">
            <KpiHero />
          </div>

          {/* Row 2: Top Post + Learning + AI Insight */}
          <div className="area-spotlight">
            <TopPostSpotlight />
          </div>
          <div className="area-learning">
            <LearningLoopCard />
          </div>
          <div className="area-insight">
            <AiInsightCard />
          </div>

          {/* Row 3: Publish Timeline + Upcoming Queue */}
          <div className="area-timeline">
            <PublishTimeline />
          </div>
          <div className="area-upcoming">
            <UpcomingQueue />
          </div>

          {/* Row 4: Channel Strip (full width, 5 columns) */}
          <div className="area-channels">
            <ChannelStrip />
          </div>

          {/* Row 5: Engagement Feed + Trend Radar + Content Quality */}
          <div className="area-engagement">
            <EngagementFeed />
          </div>
          <div className="area-trends">
            <TrendRadar />
          </div>
          <div className="area-quality flex flex-col gap-3">
            <ContentQuality />
            {/* Source-quality card (2026-06-13): surfaces per-source claim
                rate so operator can prune sources with 0% claim rate.
                The 2026-06-13 deep dive found 22 of 30 top sources fetch
                hundreds of items per 14d that NEVER convert to blueprints. */}
            <SourceQualityCard />
            {/* M-19 (2026-06-18): write-path companion to SourceQualityCard.
                Operator picks the worst offenders from the quality table
                above, then prunes them here without git-committing
                sources.yaml. */}
            <SourcesEditor />
            {/* M-20 + M-21 (2026-06-18): operator write-path for RSS
                feeds + reddit subreddits on the per-niche sources.yaml.
                Tabs because each individual surface is smaller than
                youtube_channels but operators still need both. */}
            <AdditionalSourcesEditor />
          </div>

          {/* Row 6: Pipeline Countdowns + Publishing Health + Monetisation */}
          <div className="area-countdowns">
            <PipelineCountdowns />
          </div>
          <div className="area-monetisation flex flex-col gap-3">
            <PublishingHealth />
            {/* AUTO #2 D1.4 (2026-06-15): daily SLO at-a-glance — answers
                "are we hitting the 1 reel/channel/day commitment?" without
                the operator scanning analytics. Sits above the calibration
                card because today's SLO is more urgent than this week's
                enforcement-readiness. */}
            <DailySloBadge />
            {/* AUTO #1c (2026-06-13): per-niche calibration progress
                toward AUTO #2 enforcement. Updates every 60s; surfaces
                when each niche crosses the ≥30-samples + ≥90%-agreement
                threshold so operator can flip enforcement on with
                evidence rather than gut. */}
            <AutoApprovalCalibrationCard />
            {/* W4.4: per-day trend so operator sees climb/flat/regress
                BEFORE the calibration card hits its 30-sample readiness
                threshold. Surfaces regressions earlier — at day 7 of
                a drifting gate, both cards visible side-by-side give
                the operator more signal than the snapshot alone. */}
            <TrackRecordCard />
            {/* AUTO #2 (W4.3, 2026-06-18): per-niche rollout_pct slider.
                Lets operator ramp graduated rollout (10% → 50% → 100%
                over a week) without a git commit per slide. Slider only
                writes rollout_pct; ``enabled`` flip stays git-commit
                only per the AUTO #2 runbook §4-5. */}
            <RolloutPctSlider />
            <MonetisationCompact />
          </div>
        </div>
      )}
    </div>
  );
}
