export const ARCHIVE_REASON_LABELS: Record<string, string> = {
  auto_archived_rejected_raw_clock_angle: "Clock angle (no moment)",
  auto_archived_rejected_scheduled_game: "Scheduled game (no result)",
  auto_archived_generic_hook: "Generic hook (template)",
  auto_archived_no_video: "No video clip",
  auto_archived_template_hook: "Template hook",
  auto_archived_low_score: "Score too low",
  auto_archived_no_clip: "No clip found",
  auto_archived_duplicate: "Duplicate content",
  auto_archived_stale: "Stale content",
};

export function getArchiveReasonLabel(reason: string): string {
  return (
    ARCHIVE_REASON_LABELS[reason] ??
    reason.replace("auto_archived_", "").replace(/_/g, " ")
  );
}
