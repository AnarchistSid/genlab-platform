/**
 * humanize-api-error — turn raw fetch/backend errors into operator-
 * friendly messages (H4, 2026-07-08 audit).
 *
 * Before H4, editor forms surfaced `err.message` unchanged. A
 * validator's `ValueError: invalid URL format` landed on the user
 * as literal Python. A 409 landed as `HTTP 409`. Operators would
 * see the backend's diagnostic text and think the app was broken.
 *
 * This module transforms the raw error into a string a
 * non-engineer would understand while preserving the diagnostic
 * detail in ``detail`` for the details drawer.
 *
 * Not a full i18n system — just a small ordered pattern list. When
 * a new backend error phrase shows up in support tickets, add one
 * more entry here.
 */

/** Shape returned to callers.
 *
 * ``message`` — what the form's error region shows. Must always be
 * a full sentence; no leading `Error:` prefix.
 *
 * ``detail`` — the raw error text. UI can hide this behind a
 * disclosure ("Show technical details") for the rare operator who
 * needs it, without cluttering the primary message.
 */
export interface HumanizedApiError {
  message: string;
  detail: string;
}

/** Ordered pattern list. First match wins.
 *
 * Order matters: put narrower patterns above broader ones so the
 * 409-specific message isn't shadowed by a generic 4xx catch-all.
 * Every entry is documented with an example of the raw string that
 * triggered it, so a future editor sees exactly what they're
 * matching against.
 */
const _PATTERNS: {
  test: RegExp;
  humanize: (raw: string) => string;
}[] = [
  // --- URL-shape validation ---
  {
    // Backend: "ValueError: invalid URL format" from the channel
    // URL validator. Also "not a valid youtube channel URL".
    test: /invalid\s+url\s+format|not\s+a\s+valid\s+youtube/i,
    humanize: () =>
      "That URL doesn't look like a YouTube channel. Try something like https://www.youtube.com/@handle or https://www.youtube.com/channel/UC...",
  },
  {
    // Backend: "URL must start with https://"
    test: /url\s+must\s+start\s+with\s+https/i,
    humanize: () =>
      "Please use an https:// URL — plain http links aren't accepted.",
  },
  {
    // Backend: "field required: url" — Pydantic-style missing field.
    test: /field\s+required/i,
    humanize: (raw) => {
      const field = raw.match(/field\s+required[:\s]+["']?(\w+)/i)?.[1];
      return field
        ? `Missing ${field}. Please fill in every required field.`
        : "Please fill in every required field.";
    },
  },

  // --- Duplicate / conflict ---
  {
    // Backend: HTTP 409 Conflict — usually "URL already present"
    test: /\b409\b|already\s+present|already\s+exists/i,
    humanize: () =>
      "That entry is already in the list. Nothing was changed.",
  },

  // --- Not found ---
  {
    // Backend: HTTP 404 — niche_id unknown, or config file missing
    test: /\b404\b|not\s+found/i,
    humanize: () =>
      "The niche's config wasn't found on the server. Try refreshing the page.",
  },

  // --- Auth / permission ---
  {
    // 401 (session expired) — operator likely needs to sign in again
    test: /\b401\b|unauthorized/i,
    humanize: () =>
      "Your session expired. Refresh the page and sign in again.",
  },
  {
    // 403 (allowlist blocked the write) — rare but happens on
    // niches the operator's role doesn't cover
    test: /\b403\b|forbidden/i,
    humanize: () =>
      "You don't have permission to change this niche's config.",
  },

  // --- Server-side / transient ---
  {
    // 500/502/503/504 — surface as a "try again" rather than raw code
    test: /\b5\d\d\b|internal\s+server\s+error|bad\s+gateway|service\s+unavailable|gateway\s+timeout/i,
    humanize: () =>
      "The server hit an unexpected error. Wait a moment and try again.",
  },

  // --- Network / timeout ---
  {
    // fetch throws with these on connection loss / DNS
    test: /failed\s+to\s+fetch|networkerror|network\s+error|network\s+request\s+failed/i,
    humanize: () =>
      "Couldn't reach the server. Check your connection and try again.",
  },
  {
    // Abort / user-caused timeout
    test: /aborted|abort\s+error|timed?\s*out/i,
    humanize: () =>
      "The request took too long and was cancelled. Try again.",
  },
];

/** Convert an arbitrary error into a user-facing string.
 *
 * Accepts anything — Error object, string, or ``unknown`` — because
 * React Query's onError callbacks type as ``Error`` but the runtime
 * value can be anything a mutationFn throws.
 */
export function humanizeApiError(err: unknown): HumanizedApiError {
  const raw = _extractRawMessage(err);

  for (const { test, humanize } of _PATTERNS) {
    if (test.test(raw)) {
      return { message: humanize(raw), detail: raw };
    }
  }

  // Fallback: no pattern matched. Return a generic message and keep
  // the raw text in ``detail`` for the disclosure surface. This is
  // the case new patterns should be added for next.
  return {
    message:
      "Something went wrong saving that change. Please try again.",
    detail: raw,
  };
}

/** Best-effort text extraction across the shapes React Query gives us. */
function _extractRawMessage(err: unknown): string {
  if (err == null) return "unknown error";
  if (typeof err === "string") return err;
  if (err instanceof Error) return err.message;
  // Some mutation clients throw plain objects with a ``message`` field
  if (typeof err === "object" && "message" in err) {
    const m = (err as { message: unknown }).message;
    if (typeof m === "string") return m;
  }
  return String(err);
}
