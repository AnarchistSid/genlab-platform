import { useMemo } from "react";
import { useCrossNicheOverview } from "@/hooks/use-cross-niche-overview";
import { useChannelHealth } from "@/hooks/use-channel-health";
import { AlertBanner } from "@/components/shared/AlertBanner";
import { PageHeader } from "@/components/shared/page-header";
import { ErrorState } from "@/components/shared/error-state";
import { LoadingSkeleton } from "@/components/shared/loading-skeleton";
import { PublishingAlertBanner } from "./AlertBanner";
import { CriticalAlertsBanner } from "./CriticalAlertsBanner";
import { ScopedAccessBanner } from "./ScopedAccessBanner";
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
import { SponsorshipReadinessCard } from "./SponsorshipReadinessCard";
import { StrategistReportCard } from "./StrategistReportCard";
import { TrendAnticipationCard } from "./TrendAnticipationCard";
import { TrendAnticipationAccuracyCard } from "./TrendAnticipationAccuracyCard";
import { BayesianGateStateCard } from "./BayesianGateStateCard";
import { ConformalRouterStateCard } from "./ConformalRouterStateCard";
import { CrossNichePriorsCard } from "./CrossNichePriorsCard";
import { DriftSignalCard } from "./DriftSignalCard";
import { EnsembleVotesCard } from "./EnsembleVotesCard";
import { FlagStateCard } from "./FlagStateCard";
import { TopCreatorPriorsCard } from "./TopCreatorPriorsCard";
import { CounterfactualReplayCard } from "./CounterfactualReplayCard";
import { AutoExperimentsCard } from "./AutoExperimentsCard";
import { ProductBanditCard } from "./ProductBanditCard";
import { TransformationBanditCard } from "./TransformationBanditCard";
import { BanditHourHeatmap } from "./BanditHourHeatmap";
import { NichePauseBanner } from "./NichePauseBanner";
import { NichePauseCard } from "./NichePauseCard";
import { AttributionHealthCard } from "./AttributionHealthCard";
import { ComplianceStatsCard } from "./ComplianceStatsCard";
import { PerPlatformHealthCard } from "./PerPlatformHealthCard";
import { BanditPlatformDivergenceCard } from "./BanditPlatformDivergenceCard";
import { SourceDiscoveryCard } from "./SourceDiscoveryCard";
import { SourcePerformanceCard } from "./SourcePerformanceCard";

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

    // PR #510 (2026-06-24): prefer the structured operator-review
    // count over the historical conflated ``total_pending_review``.
    // The old count lumped DRAFTED (render-retry, NOT an operator
    // action) with VISUAL_READY-awaiting-click — when the dashboard
    // banner says "X posts waiting for review" but X of those are
    // really render-retry, the operator clicks through, sees the
    // unactionable DRAFTED rows, and loses trust in the badge.
    // Falls back to the legacy field on older API responses.
    const operatorReviewCount =
      data.global.total_operator_review ?? data.global.total_pending_review;
    if (operatorReviewCount > 5) {
      const oldestHours = data.global.oldest_operator_review_age_hours;
      // Annotate the message with oldest-age when >24h so the operator
      // sees the urgency signal inline. Younger queues just show count.
      const ageSuffix =
        oldestHours != null && oldestHours >= 24
          ? ` (oldest: ${Math.round(oldestHours / 24)}d)`
          : "";
      return {
        message: `${operatorReviewCount} awaiting your review${ageSuffix}`,
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

      {/* SR-F scoped-access banner (PR #554, consumer for PR #553's
          server-side carve-out). Renders ABOVE infrastructure +
          publishing alerts so the operator's first orienting signal
          is "this is the data I'm scoped to" — totals + sparklines
          differ from the unrestricted admin view, and that's
          expected. Hidden for admin sessions (back-compat). */}
      <ScopedAccessBanner />

      {/* Critical infrastructure alerts banner — reads unresolved
          CRITICAL rows from pipeline_alerts (warp_down,
          download_failure, qc_collapse, etc). Rendered ABOVE the
          publishing banner so infrastructure/operator-action signals
          land before blueprint-derived signals. */}
      <CriticalAlertsBanner />

      {/* Publishing alert banner — shows unresolved publish failures/warnings */}
      <PublishingAlertBanner />

      {/* Niche pause banner (H3, 2026-07-08 audit) — top-surface
          reminder when any niche is paused so operator doesn't lose
          30 minutes debugging "why isn't gaming publishing?" before
          finding NichePauseCard below the fold. Renders NOTHING
          when no niches are paused. */}
      <NichePauseBanner />

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
            {/* PR T (2026-06-23): operator-leverage opener. Per-niche
                sponsorship readiness tier (eligible_now / within_2_months
                / within_6_months / tracking) derived from the existing
                monetisationprogress data. Sits next to MonetisationCompact
                because they're conceptually adjacent: progress shows the
                raw numbers, readiness shows the actionable inference. */}
            <SponsorshipReadinessCard />
            {/* PR BB (2026-06-23): operator's feedback loop for PR #487's
                optimal-time bandit foundation. Per-niche 24-hour strips
                colored by Beta posterior mean. Operator sees when each
                niche crosses 30 observations (readiness threshold for
                flipping GENLAB_OPTIMAL_TIME_BANDIT=1) — same pattern as
                AutoApprovalCalibrationCard. */}
            <BanditHourHeatmap />
            {/* PR Strategist-2b (2026-07-01): weekly LLM Strategist reports
                surface. Completes the Strategist trilogy dashboard wire —
                Strategist-1 (foundation), Strategist-1b (persister),
                Strategist-2 (server-side wire), Strategist-3 (integration)
                had shipped without a React visualization; this card is
                that. Placed next to BanditHourHeatmap because both are
                weekly / long-cadence learning surfaces — the operator
                reads them together to reason about "what should we
                change this week?" */}
            <StrategistReportCard />
            {/* Intervention 5 Session 3 (2026-07-01): Trend Anticipation
                daily rankings surface. Reads the artifact written by
                the 03:30 UTC systemd runner. Sits next to
                StrategistReportCard because both are periodic
                LLM/analytic surfaces the operator reads at the same
                cadence — Strategist for the weekly meta-strategy,
                TrendAnticipation for the daily topic-selection input. */}
            <TrendAnticipationCard />
            {/* Session 3b (2026-07-01): Anticipation accuracy — weekly
                Spearman validation that answers "is the anticipation
                score ACTUALLY predicting peaks?". Companion to the
                daily rankings card. Placed next to it so the operator
                reads them together: forward-looking ranking + backward-
                looking validation. Ready/learning/not-predictive badge
                is the operator's go/wait/rollback signal for flipping
                pipeline steering on. */}
            <TrendAnticipationAccuracyCard />
            {/* Intervention 2 observability (2026-07-01): cross-niche
                Bayes transferred priors. Weekly refit shows per-style
                (α, β) alongside (μ, σ², N) empirical fit. Operator
                validates the priors look reasonable before flipping
                GENLAB_CROSS_NICHE_TRANSFER_ENABLED — the "active"
                vs "observation only" badge tells them which state
                they're in without needing to check .env. */}
            <CrossNichePriorsCard />
            {/* B.2 + B.3 observability (2026-07-08): A+B intelligence
                stack — top-creator upload watchlist (A.2) + weekly
                Spearman correlations (B.2). Per-niche row shows top
                correlated feature + count of fresh (<24h) uploads
                on watchlist. Two independent flag badges (B.2 + A.2)
                so operator can see which artifact source is
                observation-only vs active without checking .env. */}
            <TopCreatorPriorsCard />
            {/* L11 observability (2026-07-08 audit): all 36
                catalogued GENLAB_* env flags in one card, grouped
                by capability. Primary operator value: catches
                mis-set values (e.g. "true" when reader wants "1")
                that silently no-op — the class-of-bug that
                motivated the entire flag-audit epic. */}
            <FlagStateCard />
            {/* L4 observability (2026-07-08 audit): Bayesian LR
                gate posterior state. Shows per-niche mean vector
                + training size so the operator can eyeball
                whether the fit looks reasonable before flipping
                GENLAB_BAYESIAN_GATE_ENABLED. Covariance matrix
                deliberately excluded — see endpoint pin tests. */}
            <BayesianGateStateCard />
            {/* L4 observability (2026-07-08 audit): conformal
                router q_hat + calibration state per niche. `ready`
                badge derives from sample_count ≥ min_niche_samples
                (default 50) — under threshold the router falls
                back to routing to operator regardless of q_hat. */}
            <ConformalRouterStateCard />
            {/* L5 observability (2026-07-08 audit): per-component
                ensemble vote summary. Shows which components voted
                vs abstained and how their scores agreed with the
                final recommendation. Enables post-hoc analysis of
                inter-component disagreement. */}
            <EnsembleVotesCard />
            {/* L7 observability (2026-07-08 audit): bandit-arm
                drift detection. Regressions (negative z_score) at
                the top — most concerning arms first. `is_regression`
                partial index on the DB side keeps this hot query
                cheap even with hundreds of signals per detector run. */}
            <DriftSignalCard />
            {/* Intervention 7 observability (2026-07-01): monthly
                counterfactual replay. Shows per-niche IPS + DR reward
                estimates so the operator can see which arms would
                have earned the most reward if picked more often —
                complements the daily bandit_arms view with a
                counterfactual overlay. */}
            <CounterfactualReplayCard />
            {/* #9 lifecycle observability (2026-07-23): auto-experiment
                verdicts + queue depth. Sibling to the strategist card —
                strategist proposes causal hypotheses; parser queues them;
                lifecycle measures them; THIS card is where the operator
                sees whether they held up. Active-vs-observation-only badge
                matches the other lifecycle cards. */}
            <AutoExperimentsCard />
            {/* PR #Layer5 (2026-07-11): post-Markanimation attribution
                observability. Shows per-niche attribution_present_pct
                over a rolling 24h window. Server-computed status
                thresholds drive per-row colouring so the operator can
                spot pipeline gaps at a glance. Complements the ~350
                retroactive credit ops + fb_survival_check detection
                fix shipped the same day. */}
            <AttributionHealthCard />
            {/* PR 14 (2026-07-05): intelligent transformation sprint
                observability. Shows top arm per (niche × dimension)
                so operator eyeballs whether the bandit is learning
                consistent preferences BEFORE flipping
                GENLAB_INTELLIGENT_TRANSFORM_ENABLED. Same
                observation-only vs active badge pattern as the
                other intervention cards. */}
            <TransformationBanditCard />
            {/* L3 PR 9 (2026-07-07): Monetization Layer 3 sprint
                observability. Shows top-5 product arms per niche by
                Beta posterior mean so operator sees which affiliate
                products are earning clicks. Same active-vs-observation-
                only badge as TransformationBanditCard; flips to active
                when GENLAB_PRODUCT_SELECTOR_ENABLED=true (L3 PR 5's
                wire ships observation-only until PR 12 flips
                enforcement per-niche after divergence-log review). */}
            <ProductBanditCard />
            {/* PR after #580 (2026-06-25): operator emergency-stop
                surface. Wraps PR #577 endpoints + the niche_pause
                primitive (PR #575) + the auto-pause wire (PR #579).
                Per-niche Pause/Resume buttons close the operator-
                leverage loop — what previously took a DB row or a
                publishing.yaml edit + redeploy is now one click. */}
            <NichePauseCard />
            {/* PR after #581 (2026-06-25): Phase A compliance-moat
                visibility. Aggregates compliance_events rows over a
                7d window into per-niche warn/block counts + the top-
                firing event_type. Sibling to NichePauseCard — both
                surface the same backend signals; pause is the action,
                stats are the diagnosis. */}
            <ComplianceStatsCard />
            {/* PR B (2026-06-27): per-niche per-platform health matrix.
                The existing publishing-health card averages across all
                niches × platforms, hiding silent regressions like the
                ai_creators IG 0/3 over 7 days that today's investigation
                surfaced (error 2207077). This card renders one row per
                niche, one column per platform; each cell is a 14d success-
                rate colored red/yellow/green so an unhealthy combo is
                visible at-a-glance instead of buried in a global average. */}
            <PerPlatformHealthCard />
            {/* PR AG (2026-06-29): per-(niche, base_arm) per-platform
                bandit divergence surface for PR #641. After ~2 weeks of
                metric_collector reward updates accumulating both base +
                per-platform variants, this card shows whether the per-
                platform split is producing meaningful signal (IG↔YT delta
                ≥5%) vs collapsing back to the base arm. Sits next to
                PerPlatformHealthCard because both answer per-platform
                operator questions — health is "does it publish?",
                divergence is "does it work differently?". */}
            <BanditPlatformDivergenceCard />
            {/* PR after #586 (2026-06-25): source-discovery proposer.
                Ranks SOURCE YouTube channels we pulled clips from
                by per-clip avg engagement so operators decide which
                under-tracked channels deserve config/sources.yaml
                slots. Per-niche selector (10 rows × 1 niche at a
                time, vs the 1-row × 5-niche layout of the sibling
                cards) because the proposer's output IS itself a
                ranked list. Zero-state for days until source data
                accumulates from new trending fetches. */}
            <SourceDiscoveryCard />
            {/* Phase 2 L1 (2026-06-30): operator's window into the
                source-performance bandit arms shipped in PR #571 +
                activated in PR #571b. Sibling to SourceDiscoveryCard
                but answers a different question: SourceDiscoveryCard
                says "which channels deserve to be added"; this one
                says "which existing sources is the bandit favouring?"
                Per-niche selector + top 10 by Beta posterior mean +
                95% CI bands. Trusted (n_plays ≥ 10) / Insufficient
                data split prevents acting on single-play noise. */}
            <SourcePerformanceCard />
          </div>
        </div>
      )}
    </div>
  );
}
