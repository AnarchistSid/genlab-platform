import { MessageSquareDashed } from "lucide-react";

// ── Types ────────────────────────────────────────────────────

export interface PendingReply {
  id: string;
  originalComment: string;
  generatedReply: string;
  confidence: number;
  platform: string;
  username: string;
}

interface Props {
  replies?: PendingReply[];
}

// ── Component ────────────────────────────────────────────────

export function ReplyQueue({ replies = [] }: Props) {
  // ── Empty state ──────────────────────────────────────────
  if (replies.length === 0) {
    return (
      <div className="bg-bg-surface border border-border rounded-lg p-4 flex flex-col items-center justify-center text-center gap-3 min-h-[200px]">
        <MessageSquareDashed size={32} className="text-text-ghost shrink-0" />
        <div>
          <p className="m-0 mb-1.5 text-base font-medium text-text-secondary">
            No pending replies
          </p>
          <p className="m-0 text-xs text-text-muted leading-relaxed max-w-[240px]">
            The engagement engine will queue replies for review when comments are
            received and confidence is between 0.5–0.85
          </p>
        </div>
      </div>
    );
  }

  // ── Reply cards (future) ─────────────────────────────────
  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4 overflow-y-auto max-h-[560px] flex flex-col gap-3">
      {replies.map((reply) => (
        <div
          key={reply.id}
          className="bg-bg-elevated border border-border rounded-lg p-3 flex flex-col gap-2"
        >
          {/* Original comment */}
          <div>
            <span className="text-xs text-text-muted uppercase tracking-wide">
              @{reply.username}
            </span>
            <p className="mt-1 mb-0 text-sm text-text-secondary leading-relaxed">
              {reply.originalComment}
            </p>
          </div>

          {/* Generated reply */}
          <div className="border-l-2 border-border-strong pl-2.5">
            <span className="text-xs text-text-muted uppercase tracking-wide">
              Draft reply
            </span>
            <p className="mt-1 mb-0 text-sm text-text-primary leading-relaxed">
              {reply.generatedReply}
            </p>
          </div>

          {/* Confidence + actions */}
          <div className="flex items-center justify-between gap-2 mt-1">
            <span className="text-xs text-text-muted">
              Confidence:{" "}
              <span className="text-warning font-medium">
                {Math.round(reply.confidence * 100)}%
              </span>
            </span>

            <div className="flex gap-1.5">
              <button className="text-xs px-2.5 py-0.5 rounded-sm border border-border bg-transparent text-error cursor-pointer">
                Reject
              </button>
              <button className="text-xs px-2.5 py-0.5 rounded-sm border border-border bg-transparent text-text-secondary cursor-pointer">
                Edit
              </button>
              <button
                className="text-xs px-2.5 py-0.5 rounded-sm border font-medium cursor-pointer"
                style={{
                  borderColor: "var(--color-green)",
                  background: "var(--color-green-dim)",
                  color: "var(--color-green)",
                }}
              >
                Approve
              </button>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
