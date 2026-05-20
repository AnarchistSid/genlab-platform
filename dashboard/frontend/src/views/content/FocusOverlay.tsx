import { useEffect, useCallback, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Check,
  X,
  ChevronLeft,
  ChevronRight,
  Play,
  Hash,
  Calendar,
} from "lucide-react";
import { PlatformIcon } from "@/components/shared/PlatformIcon";
import { StatusBadge } from "@/components/shared/status-badge";
import { Button } from "@/components/ui/button";
import type { Blueprint } from "@/api/types";
import { PLATFORM_IDS } from "@/lib/platforms";
import { getThumbnailInfo } from "@/lib/format";
import { useUpdateContent, useApproveAndSchedule } from "@/hooks/use-blueprints";

// ── Helpers ──────────────────────────────────────────────────────────────────

function getPlatformCaption(
  bp: Blueprint,
  platform: string,
): string | null {
  if (platform === "instagram") return bp.caption ?? null;
  if (platform === "youtube") {
    const yt = bp.youtube_content;
    if (!yt) return null;
    if (typeof yt === "string") return yt || null;
    return (yt as Record<string, unknown>).description as string ?? (yt as Record<string, unknown>).title as string ?? null;
  }
  if (platform === "x_twitter" || platform === "twitter" || platform === "x") {
    const tw = bp.twitter_content;
    if (!tw) return null;
    if (typeof tw === "string") return tw || null;
    return (tw as Record<string, unknown>).text as string ?? (tw as Record<string, unknown>).tweet_text as string ?? null;
  }
  if (platform === "facebook") {
    const fb = bp.facebook_content;
    if (!fb) return null;
    if (typeof fb === "string") return fb || null;
    return (fb as Record<string, unknown>).message as string ?? (fb as Record<string, unknown>).caption as string ?? null;
  }
  if (platform === "threads") {
    const th = (bp as unknown as Record<string, unknown>).threads_content;
    if (typeof th === "string") return th || null;
    return null;
  }
  return null;
}

// ── Main overlay ─────────────────────────────────────────────────────────────

export interface FocusOverlayProps {
  blueprints: Blueprint[];
  currentIndex: number;
  isOpen: boolean;
  onNavigate: (index: number) => void;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  onClose: () => void;
}

export function FocusOverlay({
  blueprints,
  currentIndex,
  isOpen,
  onNavigate,
  onApprove,
  onReject,
  onClose,
}: FocusOverlayProps) {
  const bp = isOpen ? blueprints[currentIndex] : undefined;

  const updateContent = useUpdateContent();
  const approveAndSchedule = useApproveAndSchedule();

  const [editingHook, setEditingHook] = useState(false);
  const [hookDraft, setHookDraft] = useState("");

  // Reset editing state when navigating between blueprints
  useEffect(() => {
    setEditingHook(false);
    setHookDraft("");
  }, [currentIndex]);

  const goPrev = useCallback(() => {
    if (currentIndex > 0) onNavigate(currentIndex - 1);
  }, [currentIndex, onNavigate]);

  const goNext = useCallback(() => {
    if (currentIndex < blueprints.length - 1) onNavigate(currentIndex + 1);
  }, [currentIndex, blueprints.length, onNavigate]);

  // Keyboard shortcuts
  useEffect(() => {
    if (!isOpen) return;

    function handleKey(e: KeyboardEvent) {
      const tag = (document.activeElement as HTMLElement)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea") return;

      if (e.key === "Escape") { onClose(); return; }
      if (e.key === "ArrowLeft"  || e.key === "ArrowUp")   { e.preventDefault(); goPrev(); return; }
      if (e.key === "ArrowRight" || e.key === "ArrowDown")  { e.preventDefault(); goNext(); return; }
      const bpScheduled = bp?.status === "VISUAL_READY" && bp?.action_taken === "approved" && !!bp?.scheduled_for;
      if (e.key === "a" && bp && !bpScheduled) { onApprove(bp.id); return; }
      if (e.key === "s" && bp && !bpScheduled) { approveAndSchedule.mutate(bp.id); return; }
      if (e.key === "r" && bp && !bpScheduled) { onReject(bp.id); return; }
    }

    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [isOpen, goPrev, goNext, onClose, onApprove, onReject, bp, approveAndSchedule]);

  // Lock body scroll while open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => { document.body.style.overflow = ""; };
  }, [isOpen]);

  const thumbInfo = bp ? getThumbnailInfo(bp) : null;
  const thumb = thumbInfo?.url ?? null;
  const isVideo = thumbInfo?.isVideo ?? false;
  const isVisualReady = bp?.status === "VISUAL_READY";
  const isScheduled = isVisualReady && bp?.action_taken === "approved" && !!bp?.scheduled_for;

  return (
    <AnimatePresence>
      {isOpen && bp && (
        <>
          {/* Dark backdrop */}
          <motion.div
            key="overlay-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-40 bg-black/80 backdrop-blur-sm"
            onClick={onClose}
          />

          {/* Content panel */}
          <motion.div
            key={`overlay-panel-${bp.id}`}
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none"
          >
            <div
              className="pointer-events-auto w-full max-w-5xl max-h-[90vh] overflow-y-auto rounded-xl border border-border-strong bg-bg-raised shadow-2xl flex flex-col"
              onClick={(e) => e.stopPropagation()}
              role="dialog"
              aria-modal="true"
              aria-label="Blueprint focus review"
            >
              {/* ── Header ── */}
              <div className="flex items-center justify-between px-5 pt-4 pb-3 border-b border-border-subtle">
                <div className="flex items-center gap-2">
                  <StatusBadge status={bp.status} />
                  {typeof bp.priority_score === "number" && (
                    <span className="text-xs text-text-muted">
                      P {bp.priority_score.toFixed(1)}
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-2">
                  {/* Nav controls */}
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      disabled={currentIndex === 0}
                      onClick={goPrev}
                      aria-label="Previous blueprint (← key)"
                    >
                      <ChevronLeft className="size-4" />
                    </Button>
                    <span className="text-xs text-text-muted tabular-nums">
                      {currentIndex + 1} / {blueprints.length}
                    </span>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      disabled={currentIndex === blueprints.length - 1}
                      onClick={goNext}
                      aria-label="Next blueprint (→ key)"
                    >
                      <ChevronRight className="size-4" />
                    </Button>
                  </div>

                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={onClose}
                    aria-label="Close (Esc)"
                  >
                    <X className="size-4" />
                  </Button>
                </div>
              </div>

              {/* ── Side-by-side layout: video left, content right ── */}
              <div className="flex flex-1 min-h-0">
                {/* Video — large portrait preview */}
                <div className="relative bg-black flex items-center justify-center overflow-hidden shrink-0" style={{ width: "340px", maxHeight: "80vh" }}>
                  {thumb ? (
                    isVideo ? (
                      <video
                        src={thumb}
                        controls
                        autoPlay
                        loop
                        muted
                        playsInline
                        preload="auto"
                        className="w-full h-full object-contain"
                        style={{ maxHeight: "80vh" }}
                      />
                    ) : (
                      <img
                        src={thumb}
                        alt={bp.hook_text ?? bp.candidate_id ?? "blueprint"}
                        className="w-full h-full object-contain"
                      />
                    )
                  ) : (
                    <div className="flex flex-col items-center gap-2 text-text-ghost py-20">
                      <Play className="size-10" />
                      <span className="text-xs">No preview available</span>
                    </div>
                  )}
                </div>

                {/* Content panel — scrollable right side */}
                <div className="flex-1 overflow-y-auto">
              <div className="flex flex-col gap-4 px-5 py-4">
                {/* Hook + caption */}
                <div className="space-y-1">
                  {editingHook ? (
                    <textarea
                      value={hookDraft}
                      onChange={(e) => setHookDraft(e.target.value)}
                      onBlur={() => {
                        if (hookDraft.trim() && hookDraft !== (bp?.hook_text ?? "")) {
                          updateContent.mutate({ id: bp!.id, body: { hook_text: hookDraft.trim() } });
                        }
                        setEditingHook(false);
                      }}
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.currentTarget.blur(); } if (e.key === "Escape") setEditingHook(false); }}
                      className="w-full text-base font-bold text-text-primary bg-bg-elevated border border-border rounded-md px-2 py-1 resize-none focus:outline-none focus:border-accent"
                      autoFocus
                      rows={2}
                      maxLength={60}
                    />
                  ) : (
                    <p
                      className="text-base font-bold text-text-primary leading-snug cursor-pointer hover:bg-bg-elevated rounded px-1 -mx-1 transition-colors"
                      onClick={() => { setHookDraft(bp?.hook_text ?? ""); setEditingHook(true); }}
                      title="Click to edit hook"
                    >
                      {bp?.hook_text || "Untitled"}
                    </p>
                  )}
                  {bp.caption && (
                    <p className="text-sm text-text-secondary whitespace-pre-wrap">
                      {bp.caption}
                    </p>
                  )}
                </div>

                {/* Platform captions */}
                {PLATFORM_IDS.map((platform) => {
                  const caption = getPlatformCaption(bp, platform);
                  if (!caption) return null;
                  return (
                    <div key={platform} className="space-y-1">
                      <div className="flex items-center gap-1.5 text-xs font-medium text-text-muted uppercase tracking-wide">
                        <PlatformIcon platform={platform} size={12} />
                        {platform}
                      </div>
                      <p className="text-sm text-text-secondary bg-bg-elevated rounded-md px-3 py-2 border border-border-subtle leading-relaxed whitespace-pre-wrap">
                        {caption}
                      </p>
                    </div>
                  );
                })}

                {/* Hashtags */}
                {bp.hashtags && (
                  <div className="flex items-start gap-1.5 flex-wrap">
                    <Hash className="size-3 text-text-ghost mt-0.5 shrink-0" />
                    <p className="text-xs text-text-muted leading-relaxed">
                      {bp.hashtags}
                    </p>
                  </div>
                )}
              </div>

              {/* ── Action footer (VISUAL_READY only) ── */}
              {isVisualReady && !isScheduled && (
                <div className="flex items-center gap-3 px-5 pb-5 border-t border-border-subtle pt-4">
                  <Button
                    variant="outline"
                    size="sm"
                    className="border-green-600/30 text-green-400 hover:bg-green-600/15 flex-1"
                    onClick={() => onApprove(bp.id)}
                  >
                    <Check className="size-3.5" />
                    Approve
                    <span className="text-xs text-text-ghost ml-1">(A)</span>
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="border-indigo-600/30 text-indigo-400 hover:bg-indigo-600/15 flex-1"
                    onClick={() => approveAndSchedule.mutate(bp.id)}
                    disabled={approveAndSchedule.isPending}
                  >
                    <Calendar className="size-3.5" />
                    Schedule
                    <span className="text-xs text-text-ghost ml-1">(S)</span>
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="border-red-600/30 text-red-400 hover:bg-red-600/15 flex-1"
                    onClick={() => onReject(bp.id)}
                  >
                    <X className="size-3.5" />
                    Reject
                    <span className="text-xs text-text-ghost ml-1">(R)</span>
                  </Button>
                </div>
              )}

              {/* ── Scheduled indicator ── */}
              {isScheduled && bp.scheduled_for && (
                <div className="flex items-center gap-2 px-5 pb-5 border-t border-border-subtle pt-4 text-sm text-text-muted">
                  <Check className="size-4 text-green-400" />
                  <span className="text-green-400 font-medium">Approved</span>
                  <span className="mx-1">·</span>
                  <Calendar className="size-4 text-indigo-400" />
                  <span>
                    Scheduled for{" "}
                    <span className="font-medium text-text-primary">
                      {new Date(bp.scheduled_for).toLocaleDateString("en-US", {
                        weekday: "short",
                        month: "short",
                        day: "numeric",
                        hour: "numeric",
                        minute: "2-digit",
                      })}
                    </span>
                  </span>
                </div>
              )}

              {/* Keyboard hint */}
              <p className="text-center text-xs text-text-ghost pb-3">
                ← → navigate{!isScheduled ? " · A approve · S schedule · R reject" : ""} · Esc close
              </p>
                </div>{/* close overflow-y-auto right panel */}
              </div>{/* close side-by-side flex container */}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
