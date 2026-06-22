import { useState, useCallback } from "react";
import {
  Check,
  X,
  RotateCcw,
  SkipForward,
  Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { useReviewBlueprint } from "@/hooks/use-blueprints";

const ISSUE_OPTIONS = [
  { value: "weak_hook", label: "Weak Hook" },
  { value: "too_generic", label: "Too Generic" },
  { value: "unsupported_claim", label: "Unsupported Claim" },
  { value: "bad_fit", label: "Bad Fit" },
  { value: "too_long", label: "Too Long" },
  { value: "low_value", label: "Low Value" },
] as const;

type IssueValue = (typeof ISSUE_OPTIONS)[number]["value"];

interface ReviewActionsProps {
  blueprintId: string;
  onComplete?: () => void;
}

type ReviewAction = "approved" | "rejected" | "revised" | "skipped";

const ACTION_CONFIG: Record<
  ReviewAction,
  { label: string; icon: typeof Check; className: string; needsFeedback: boolean }
> = {
  approved: {
    label: "Approve",
    icon: Check,
    className: "bg-green-600/20 text-green-400 hover:bg-green-600/30 border-green-600/30",
    needsFeedback: false,
  },
  rejected: {
    label: "Reject",
    icon: X,
    className: "bg-red-600/20 text-red-400 hover:bg-red-600/30 border-red-600/30",
    needsFeedback: true,
  },
  revised: {
    label: "Revise",
    icon: RotateCcw,
    className: "bg-yellow-600/20 text-yellow-400 hover:bg-yellow-600/30 border-yellow-600/30",
    needsFeedback: true,
  },
  skipped: {
    label: "Skip",
    icon: SkipForward,
    className: "bg-bg-hover text-text-secondary hover:bg-text-disabled/50 border-text-disabled",
    needsFeedback: false,
  },
};

export function ReviewActions({ blueprintId, onComplete }: ReviewActionsProps) {
  const [pendingAction, setPendingAction] = useState<ReviewAction | null>(null);
  const [issue, setIssue] = useState<IssueValue | "">("");
  const [notes, setNotes] = useState("");

  const reviewMutation = useReviewBlueprint();

  const handleAction = useCallback(
    (action: ReviewAction) => {
      const config = ACTION_CONFIG[action];

      if (config.needsFeedback && pendingAction !== action) {
        setPendingAction(action);
        return;
      }

      reviewMutation.mutate(
        {
          id: blueprintId,
          body: {
            action,
            ...(issue ? { issue } : {}),
            ...(notes.trim() ? { notes: notes.trim() } : {}),
          },
        },
        {
          onSuccess: () => {
            setPendingAction(null);
            setIssue("");
            setNotes("");
            onComplete?.();
          },
        }
      );
    },
    [blueprintId, pendingAction, issue, notes, reviewMutation, onComplete]
  );

  const handleCancel = useCallback(() => {
    setPendingAction(null);
    setIssue("");
    setNotes("");
  }, []);

  return (
    <div className="space-y-3">
      {/* Action buttons row */}
      <div className="flex gap-2">
        {(Object.entries(ACTION_CONFIG) as Array<[ReviewAction, typeof ACTION_CONFIG[ReviewAction]]>).map(
          ([action, config]) => {
            const Icon = config.icon;
            const isActive = pendingAction === action;

            return (
              <Button
                key={action}
                variant="outline"
                size="sm"
                disabled={reviewMutation.isPending}
                className={cn(
                  "flex-1 gap-1.5 border transition-colors",
                  isActive ? config.className : "border-bg-hover"
                )}
                onClick={() => handleAction(action)}
              >
                {reviewMutation.isPending && pendingAction === action ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Icon className="size-3.5" />
                )}
                {config.label}
              </Button>
            );
          }
        )}
      </div>

      {/* Feedback form (shown for reject/revise) */}
      {pendingAction && ACTION_CONFIG[pendingAction].needsFeedback && (
        <div className="space-y-2 rounded-lg border border-bg-hover bg-bg-surface p-3">
          {/* 2026-06-22 Loop 8 close — issue is REQUIRED so the
              backend's auto_approval_calibration.feedback_category
              column actually fills (was 0 rows on prod despite PR
              #424 shipping backend the day before). The asterisk +
              disabled-Confirm enforce a categorical choice. */}
          <label className="mb-1 block text-xs font-medium text-text-secondary">
            Why? <span className="text-red-400">*</span>
          </label>
          <Select
            value={issue}
            onValueChange={(val) => setIssue(val as IssueValue)}
          >
            <SelectTrigger size="sm" className="w-full" aria-required="true">
              <SelectValue placeholder="Select an issue..." />
            </SelectTrigger>
            <SelectContent>
              {ISSUE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Additional notes (optional)..."
            className="min-h-[60px] resize-none text-sm"
          />

          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="xs"
              onClick={handleCancel}
              disabled={reviewMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              size="xs"
              onClick={() => handleAction(pendingAction)}
              disabled={reviewMutation.isPending || !issue}
              className={ACTION_CONFIG[pendingAction].className}
              title={!issue ? "Select an issue above to enable" : undefined}
            >
              {reviewMutation.isPending ? (
                <Loader2 className="size-3 animate-spin" />
              ) : null}
              Confirm {ACTION_CONFIG[pendingAction].label}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
