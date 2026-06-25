import { ShieldCheck } from "lucide-react";
import { useCrossNicheOverview } from "@/hooks/use-cross-niche-overview";

/**
 * Mission Control top-of-page SCOPED ACCESS banner.
 *
 * PR #554 (2026-06-25): consumer for the SR-F producer that PR #553
 * shipped server-side. The Mission Control aggregator now carves
 * out per-operator-allowlist data; this banner tells the operator
 * the response shape has been trimmed to their niches so they
 * understand WHY the totals/charts differ from what an admin sees.
 *
 * Design choices
 * --------------
 *
 * 1. **No "dismiss" button.**
 *    The scoped state reflects server-side authorization, not a
 *    transient UI choice. A frontend dismiss would lie about the
 *    underlying state — same rationale as CriticalAlertsBanner's
 *    no-dismiss design. Matches the operator's mental model:
 *    "this is the data I am allowed to see, and the banner
 *    confirms it."
 *
 * 2. **Informational tone (blue), not alarming.**
 *    Restricted access is not an error — it's the expected
 *    permission posture for a multi-tenant operator. Red would
 *    suggest something is broken; blue says "you are seeing what
 *    you are scoped to."
 *
 * 3. **Render the server's note verbatim.**
 *    The server's _sr_f_note explains the v1 limitations
 *    (14-day reach vs 30-day; total_likes/comments zeroed). The
 *    component does NOT interpret or summarise — surfacing the
 *    literal note lets the operator understand the exact data
 *    fidelity and gives the backend room to evolve the message
 *    without a coordinated frontend deploy.
 *
 * 4. **Hidden when not scoped.**
 *    Admin sessions see _sr_f_scoped undefined or false. No
 *    banner means "you are seeing the full overview" — symmetric
 *    with how the CriticalAlertsBanner hides at count=0.
 */

export function ScopedAccessBanner() {
  const { data } = useCrossNicheOverview();

  // The hook unwraps {success, data} so .data is the overview. Be
  // defensive about shape — in dev (no server, errors, loading) the
  // hook can return undefined or partial payloads.
  if (!data || typeof data !== "object") return null;
  const globalSection = (data as { global?: Record<string, unknown> }).global;
  if (!globalSection || typeof globalSection !== "object") return null;
  if (globalSection._sr_f_scoped !== true) return null;

  const note =
    typeof globalSection._sr_f_note === "string" && globalSection._sr_f_note
      ? globalSection._sr_f_note
      : "Scoped to your niche allowlist.";

  return (
    <div
      role="status"
      className="rounded-lg border bg-blue-500/10 border-blue-500/40 p-3 mb-4"
    >
      <div className="flex items-center gap-2">
        <ShieldCheck className="size-4 text-blue-400 shrink-0" aria-hidden />
        <span className="text-sm font-semibold text-blue-400">
          Scoped access
        </span>
      </div>
      <div className="mt-1 text-xs text-text-muted whitespace-pre-wrap leading-relaxed">
        {note}
      </div>
    </div>
  );
}
