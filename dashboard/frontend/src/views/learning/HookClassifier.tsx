import { useMemo } from "react";
import { useLearningStatus } from "@/hooks/use-learning";
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

const DAILY_POST_RATE = 15; // posts per day estimate

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

function ClassifierEmptyState({ progress }: { progress: number }) {
  if (progress > 0) return null;
  return (
    <div className="bg-bg-surface border border-border rounded-lg p-3.5 flex items-center gap-3"
      style={{
        background: "rgba(139,92,246,0.06)",
        borderColor: "rgba(139,92,246,0.18)",
      }}
    >
      <div
        className="w-8 h-8 rounded-sm flex items-center justify-center shrink-0 text-base"
        style={{ background: "rgba(139,92,246,0.12)" }}
      >
        🧠
      </div>
      <div>
        <div className="text-sm font-medium mb-0.5" style={{ color: "var(--color-purple)" }}>
          Hook Classifier Not Yet Trained
        </div>
        <div className="text-xs text-text-muted">
          The hook classifier will predict which hooks perform best once trained. It learns from published posts and their engagement outcomes.
        </div>
      </div>
    </div>
  );
}

// ── Main export ────────────────────────────────────────────

export function HookClassifier() {
  const { data: resp, isLoading } = useLearningStatus();

  const { progress, threshold } = useMemo(() => {
    if (!resp) return { progress: 0, threshold: 200 };
    return {
      progress: resp.hook_classifier_progress,
      threshold: resp.hook_classifier_threshold,
    };
  }, [resp]);

  if (isLoading) return <LoadingSkeleton variant="card-list" rows={2} />;

  return (
    <div className="flex flex-col gap-4">
      <ProgressSection progress={progress} threshold={threshold} />
      <ClassifierEmptyState progress={progress} />
      <FeatureList />
    </div>
  );
}
