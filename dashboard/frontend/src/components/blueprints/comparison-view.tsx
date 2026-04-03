import { useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Check, Image as ImageIcon } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/shared/status-badge";
import { CarouselViewer } from "@/components/shared/carousel-viewer";
import { cn } from "@/lib/utils";
import { formatRelativeTime } from "@/lib/format";
import { useBlueprint, useReviewBlueprint, useBatchReview } from "@/hooks/use-blueprints";
import type { Blueprint } from "@/api/types";

interface ComparisonViewProps {
  blueprintIds: [string, string];
  onClose: () => void;
}

type ComparisonField = {
  label: string;
  key: keyof Blueprint;
  render?: (value: unknown) => string;
};

const COMPARISON_FIELDS: ComparisonField[] = [
  { label: "Hook Text", key: "hook_text" },
  { label: "Caption", key: "caption" },
  { label: "Hashtags", key: "hashtags" },
  { label: "Status", key: "status" },
  {
    label: "Priority Score",
    key: "priority_score",
    render: (v) => (typeof v === "number" ? v.toFixed(2) : "--"),
  },
  { label: "Template ID", key: "template_id" },
  {
    label: "Scheduled For",
    key: "scheduled_for",
    render: (v) =>
      typeof v === "string" && v ? formatRelativeTime(v) : "--",
  },
];

function getFieldValue(blueprint: Blueprint, field: ComparisonField): string {
  const raw = blueprint[field.key];
  if (field.render) return field.render(raw);
  if (raw == null) return "--";
  return String(raw);
}

function fieldsAreDifferent(a: string, b: string): boolean {
  return a !== b;
}

function MediaSection({ blueprint }: { blueprint: Blueprint }) {
  const slides = blueprint.slide_previews;
  const hasVideo = Boolean(blueprint.visual_paths);
  const hasSlides = slides && slides.length > 0;

  if (hasSlides) {
    return (
      <CarouselViewer
        images={slides.map((s) => ({ url: s.url }))}
        className="rounded-lg"
      />
    );
  }

  if (hasVideo) {
    return (
      <video
        src={blueprint.visual_paths!}
        controls
        className="w-full rounded-lg bg-bg-surface"
        preload="metadata"
      />
    );
  }

  return (
    <div className="flex aspect-video items-center justify-center rounded-lg bg-bg-surface border border-bg-hover">
      <ImageIcon className="size-10 text-text-disabled" />
    </div>
  );
}

function ColumnSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="aspect-video w-full rounded-lg" />
      <Skeleton className="h-5 w-full" />
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-16 w-full rounded-lg" />
      <div className="flex gap-2">
        <Skeleton className="h-5 w-16 rounded-full" />
        <Skeleton className="h-5 w-20" />
      </div>
    </div>
  );
}

function ComparisonColumn({
  blueprint,
  isLoading,
  side,
  diffFields,
}: {
  blueprint: Blueprint | undefined;
  isLoading: boolean;
  side: "a" | "b";
  diffFields: Set<string>;
}) {
  if (isLoading) {
    return <ColumnSkeleton />;
  }

  if (!blueprint) {
    return (
      <div className="flex items-center justify-center py-20">
        <p className="text-sm text-text-ghost">Blueprint not found</p>
      </div>
    );
  }

  const highlightClass =
    side === "a" ? "bg-blue-500/10 border-blue-500/20" : "bg-green-500/10 border-green-500/20";

  return (
    <div className="space-y-3">
      {/* ID label */}
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs text-text-ghost">
          {blueprint.id.slice(0, 12)}...
        </span>
        <StatusBadge status={blueprint.status} />
      </div>

      {/* Media */}
      <MediaSection blueprint={blueprint} />

      {/* Comparison fields */}
      {COMPARISON_FIELDS.map((field) => {
        const fieldValue = getFieldValue(blueprint, field);
        const isDiff = diffFields.has(field.key);

        return (
          <div
            key={field.key}
            className={cn(
              "rounded-md px-3 py-2 transition-colors",
              isDiff ? `border ${highlightClass}` : "border border-transparent"
            )}
          >
            <span className="block text-[10px] font-semibold uppercase tracking-wider text-text-ghost mb-0.5">
              {field.label}
            </span>
            {field.key === "status" ? (
              <StatusBadge status={fieldValue} />
            ) : (
              <p className="text-sm text-text-secondary whitespace-pre-wrap break-words">
                {fieldValue}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function ComparisonView({ blueprintIds, onClose }: ComparisonViewProps) {
  const [idA, idB] = blueprintIds;

  const queryA = useBlueprint(idA);
  const queryB = useBlueprint(idB);
  const reviewMutation = useReviewBlueprint();
  const batchReview = useBatchReview();

  const blueprintA = queryA.data?.data;
  const blueprintB = queryB.data?.data;

  // Compute which fields differ
  const diffFields = useMemo(() => {
    const diffs = new Set<string>();
    if (!blueprintA || !blueprintB) return diffs;

    for (const field of COMPARISON_FIELDS) {
      const valA = getFieldValue(blueprintA, field);
      const valB = getFieldValue(blueprintB, field);
      if (fieldsAreDifferent(valA, valB)) {
        diffs.add(field.key);
      }
    }
    return diffs;
  }, [blueprintA, blueprintB]);

  const handleApprove = (id: string) => {
    reviewMutation.mutate(
      { id, body: { action: "approved" } },
      { onSuccess: onClose }
    );
  };

  const handleApproveBoth = () => {
    batchReview.mutate(
      { ids: [idA, idB], action: "approved" },
      { onSuccess: onClose },
    );
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        className="max-w-5xl h-[90vh] flex flex-col bg-bg-primary border-bg-hover p-0"
        showCloseButton={false}
      >
        <AnimatePresence>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="flex h-full flex-col"
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-bg-hover px-6 py-4">
              <DialogTitle className="text-lg font-semibold text-text-primary">
                Compare Blueprints
              </DialogTitle>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={onClose}
              >
                <X className="size-4" />
              </Button>
            </div>

            {/* Two-column comparison */}
            <ScrollArea className="flex-1">
              <div className="grid grid-cols-2 gap-6 p-6">
                <ComparisonColumn
                  blueprint={blueprintA}
                  isLoading={queryA.isLoading}
                  side="a"
                  diffFields={diffFields}
                />
                <ComparisonColumn
                  blueprint={blueprintB}
                  isLoading={queryB.isLoading}
                  side="b"
                  diffFields={diffFields}
                />
              </div>
            </ScrollArea>

            {/* Action bar */}
            <div className="flex items-center justify-center gap-3 border-t border-bg-hover px-6 py-4">
              <Button
                size="sm"
                className="bg-indigo-600 text-white hover:bg-indigo-700"
                onClick={() => handleApprove(idA)}
                disabled={reviewMutation.isPending}
              >
                <Check className="size-3.5" />
                Approve A
              </Button>
              <Button
                size="sm"
                className="bg-indigo-600 text-white hover:bg-indigo-700"
                onClick={() => handleApprove(idB)}
                disabled={reviewMutation.isPending}
              >
                <Check className="size-3.5" />
                Approve B
              </Button>
              <Separator orientation="vertical" className="h-6 bg-bg-hover" />
              <Button
                size="sm"
                className="bg-green-600 text-white hover:bg-green-700"
                onClick={handleApproveBoth}
                disabled={reviewMutation.isPending || batchReview.isPending}
              >
                <Check className="size-3.5" />
                Approve Both
              </Button>
            </div>
          </motion.div>
        </AnimatePresence>
      </DialogContent>
    </Dialog>
  );
}
