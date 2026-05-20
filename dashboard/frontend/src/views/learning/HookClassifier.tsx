import { useMemo } from "react";
import {
  useHookClassifierStatus,
  useLearningStatus,
} from "@/hooks/use-learning";
import { getNicheInfo } from "@/niches/registry";
import { ProgressBar } from "@/components/shared/progress-bar";
import { SectionHeader } from "@/components/shared/section-header";
import { LoadingSkeleton } from "@/components/shared/loading-skeleton";

// ── Constants ──────────────────────────────────────────────

const FEATURES = [
  { key: "word_count",        label: "Word Count",         desc: "Total words in the hook" },
  { key: "has_question",      label: "Has Question",       desc: "Ends with a question mark" },
  { key: "has_number",        label: "Has Number",         desc: "Contains a numeric value" },
  { key: "emoji_count",       label: "Emoji Count",        desc: "Number of emojis present" },
  { key: "has_superlative",   label: "Has Superlative",    desc: "Uses best/worst/most/least" },
  { key: "starts_with_you",   label: "Starts With You",    desc: "Addresses the viewer directly" },
  { key: "avg_word_length",   label: "Avg Word Length",    desc: "Average characters per word" },
  { key: "unique_word_ratio", label: "Unique Word Ratio",  desc: "Vocabulary diversity score" },
];

// 5 niches × 1 reel/day = 5 posts/day. Earlier value (15) made the
// "training data ready in ~N days" estimate off by 3×.
const DAILY_POST_RATE = 5;

// ── Sub-components ─────────────────────────────────────────

function ProgressSection({
  progress,
  threshold,
}: {
  progress: number;
  threshold: number;
}) {
  const pct = threshold > 0 ? Math.min(100, (progress / threshold) * 100) : 0;
  const daysRemaining = DAILY_POST_RATE > 0
    ? Math.ceil(Math.max(0, threshold - progress) / DAILY_POST_RATE)
    : null;

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4">
      <div className="flex justify-between items-end mb-4">
        <div>
          <SectionHeader title="Training Data Collection" />
          <div className="text-2xl font-bold text-text-primary tabular-nums">
            {progress.toLocaleString()}
            <span className="text-base font-normal text-text-muted ml-1.5">
              / {threshold.toLocaleString()} training examples collected
            </span>
          </div>
        </div>
        <div
          className="text-xl font-bold tabular-nums"
          style={{ color: pct >= 100 ? "var(--color-green)" : "var(--color-purple)" }}
        >
          {pct.toFixed(0)}%
        </div>
      </div>

      <ProgressBar value={pct} color="var(--color-purple)" height={6} animated />

      {/* Timeline estimate */}
      <div className="mt-3 flex items-center gap-1.5 text-xs text-text-muted">
        <span
          className="w-1.5 h-1.5 rounded-full shrink-0"
          style={{ background: "var(--color-purple)" }}
        />
        {pct >= 100 ? (
          <span style={{ color: "var(--color-green)" }}>
            Training data threshold reached — classifier ready to train
          </span>
        ) : daysRemaining != null ? (
          <span>
            At {DAILY_POST_RATE} posts/day, training data ready in{" "}
            <strong className="text-text-secondary">
              ~{daysRemaining} {daysRemaining === 1 ? "day" : "days"}
            </strong>
          </span>
        ) : (
          <span>Collecting training examples from published posts</span>
        )}
      </div>
    </div>
  );
}

function FeatureList() {
  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4">
      <SectionHeader title="Classifier Features" />
      <div className="grid grid-cols-2 gap-2">
        {FEATURES.map((f, idx) => (
          <div
            key={f.key}
            className={`flex items-start gap-2.5 p-2 rounded-sm border border-border-subtle ${
              idx % 2 === 0 ? "bg-bg-elevated" : "bg-bg-elevated/60"
            }`}
          >
            <div
              className="w-1.5 h-1.5 rounded-full shrink-0 mt-[3px]"
              style={{ background: "var(--color-purple)" }}
            />
            <div>
              <div className="text-sm text-text-secondary font-medium mb-0.5">
                {f.label}
              </div>
              <div className="text-xs text-text-ghost">
                {f.desc}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function TrainingStatus({
  status,
  threshold,
}: {
  status: Record<string, { trained: boolean; n_examples?: number; pos_rate?: number }>;
  threshold: number;
}) {
  // Surface per-niche training state from /hook-classifier-status.
  // Previously the tab only watched "rewards collected" and silently
  // implied the classifier was trained once that count went up — but
  // training is a separate weekly cron that only runs when a niche
  // crosses the example threshold, AND only succeeds when the bridge
  // from PF -> hook_text works.
  const niches = Object.keys(status);
  if (niches.length === 0) return null;

  const trainedCount = niches.filter((n) => status[n].trained).length;
  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4">
      <SectionHeader title="Trained Classifiers" />
      <div className="text-xs text-text-muted mb-3">
        {trainedCount} of {niches.length} niche classifier{niches.length === 1 ? "" : "s"} trained.
        Weekly trainer fires Sunday 10:30 UTC and only persists a model
        when a niche has ≥ {threshold} labelled examples.
      </div>
      <div className="grid grid-cols-2 gap-2">
        {niches.map((n) => {
          const s = status[n];
          const info = getNicheInfo(n);
          return (
            <div
              key={n}
              className="flex items-center gap-2.5 p-2 rounded-sm border border-border-subtle bg-bg-elevated"
            >
              <span
                className="w-1.5 h-1.5 rounded-full shrink-0"
                style={{ background: info.hex }}
              />
              <span className="text-sm text-text-secondary flex-1 truncate">
                {info.label}
              </span>
              {s.trained ? (
                <span
                  className="text-xs font-semibold"
                  style={{ color: "var(--color-green)" }}
                >
                  Trained · {s.n_examples ?? 0}
                </span>
              ) : (
                <span className="text-xs text-text-ghost">Not trained</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Main export ────────────────────────────────────────────

export function HookClassifier() {
  const { data: resp, isLoading } = useLearningStatus();
  const { data: trainStatus, isLoading: trainLoading } =
    useHookClassifierStatus();

  // Fallback threshold matches MIN_EXAMPLES in
  // genlab_core.learning.hook_classifier (Sprint 68 lowered to 50).
  const { progress, threshold } = useMemo(() => {
    if (!resp) return { progress: 0, threshold: 50 };
    return {
      progress: resp.hook_classifier_progress,
      threshold: resp.hook_classifier_threshold,
    };
  }, [resp]);

  if (isLoading || trainLoading)
    return <LoadingSkeleton variant="card-list" rows={3} />;

  return (
    <div className="flex flex-col gap-4">
      <ProgressSection progress={progress} threshold={threshold} />
      {trainStatus ? (
        <TrainingStatus status={trainStatus} threshold={threshold} />
      ) : null}
      <FeatureList />
    </div>
  );
}
