/**
 * Pins for humanize-api-error (H4, 2026-07-08 audit).
 *
 * Each pin maps a raw backend error phrase → the user-facing string
 * an operator will see in the form's error region. If a future edit
 * loosens the regex or drops an entry, a specific pin fires.
 *
 * The `detail` field is preserved verbatim so a details drawer can
 * show the raw text when the operator asks — that's what makes the
 * humanized message a safe default rather than information loss.
 */
import { describe, expect, it } from "vitest";
import { humanizeApiError } from "../humanize-api-error";

describe("humanizeApiError — URL validation", () => {
  it("maps 'invalid URL format' → operator-friendly YouTube example", () => {
    const { message, detail } = humanizeApiError(
      new Error("ValueError: invalid URL format"),
    );
    expect(message).not.toContain("ValueError");
    expect(message).toMatch(/youtube\.com/i);
    // Detail preserves the raw string for the disclosure surface.
    expect(detail).toBe("ValueError: invalid URL format");
  });

  it("maps 'not a valid youtube channel URL' → same YouTube example", () => {
    const { message } = humanizeApiError(
      new Error("not a valid youtube channel URL"),
    );
    expect(message).toMatch(/youtube\.com/i);
  });

  it("maps 'URL must start with https://' → https-only reminder", () => {
    const { message } = humanizeApiError(
      new Error("URL must start with https://"),
    );
    expect(message).toMatch(/https/i);
    expect(message).not.toContain("must start with");
  });
});

describe("humanizeApiError — missing fields", () => {
  it("maps 'field required: name' → mentions the specific field", () => {
    const { message } = humanizeApiError(
      new Error("field required: name"),
    );
    expect(message).toMatch(/name/i);
    expect(message).toMatch(/required/i);
  });

  it("maps 'field required' with no name → generic prompt", () => {
    const { message } = humanizeApiError(new Error("field required"));
    expect(message).toMatch(/required field/i);
  });
});

describe("humanizeApiError — conflicts + not-found", () => {
  it("maps '409' → 'already in the list' (no HTTP number)", () => {
    const { message } = humanizeApiError(new Error("HTTP 409 Conflict"));
    expect(message).toMatch(/already/i);
    expect(message).not.toMatch(/409/);
  });

  it("maps 'already present' → same", () => {
    const { message } = humanizeApiError(
      new Error("URL already present in sources.yaml"),
    );
    expect(message).toMatch(/already/i);
  });

  it("maps '404' → 'refresh the page'", () => {
    const { message } = humanizeApiError(new Error("HTTP 404 Not Found"));
    expect(message).toMatch(/refresh/i);
    expect(message).not.toMatch(/404/);
  });
});

describe("humanizeApiError — auth", () => {
  it("maps '401' → 'sign in again'", () => {
    const { message } = humanizeApiError(new Error("401 Unauthorized"));
    expect(message).toMatch(/sign in/i);
  });

  it("maps '403' → 'permission'", () => {
    const { message } = humanizeApiError(new Error("HTTP 403 Forbidden"));
    expect(message).toMatch(/permission/i);
  });
});

describe("humanizeApiError — server + network", () => {
  it("maps '500' → 'try again' (no raw code)", () => {
    const { message } = humanizeApiError(
      new Error("HTTP 500 Internal Server Error"),
    );
    expect(message).toMatch(/try again/i);
    expect(message).not.toMatch(/500|Internal Server/);
  });

  it("maps '503' via 5\\d\\d catch-all", () => {
    const { message } = humanizeApiError(new Error("HTTP 503"));
    expect(message).toMatch(/try again/i);
  });

  it("maps 'Failed to fetch' → 'check your connection'", () => {
    const { message } = humanizeApiError(new Error("Failed to fetch"));
    expect(message).toMatch(/connection/i);
  });

  it("maps 'aborted' → 'try again' (timeout wording)", () => {
    const { message } = humanizeApiError(new Error("AbortError: aborted"));
    expect(message).toMatch(/try again/i);
  });
});

describe("humanizeApiError — fallback", () => {
  it("returns a generic message for un-mapped errors", () => {
    const { message, detail } = humanizeApiError(
      new Error("Something the backend hasn't taught us yet"),
    );
    expect(message).toMatch(/something went wrong/i);
    expect(detail).toContain("Something the backend hasn't taught us yet");
  });
});

describe("humanizeApiError — non-Error inputs", () => {
  it("accepts a string", () => {
    const { message, detail } = humanizeApiError(
      "HTTP 404 Not Found",
    );
    expect(message).toMatch(/refresh/i);
    expect(detail).toBe("HTTP 404 Not Found");
  });

  it("accepts a plain object with .message", () => {
    const { message } = humanizeApiError({ message: "HTTP 409" });
    expect(message).toMatch(/already/i);
  });

  it("accepts null / undefined without throwing", () => {
    expect(() => humanizeApiError(null)).not.toThrow();
    expect(() => humanizeApiError(undefined)).not.toThrow();
    const { message } = humanizeApiError(null);
    expect(message).toMatch(/something went wrong/i);
  });
});

describe("humanizeApiError — detail preservation", () => {
  it("passes the raw text through to `detail` verbatim", () => {
    const raw = "ValueError: invalid URL format";
    const { detail } = humanizeApiError(new Error(raw));
    expect(detail).toBe(raw);
  });

  it("never returns the humanized message identical to detail", () => {
    // Locks in that we're TRANSFORMING errors, not just copying them.
    // If a future edit accidentally short-circuits the mapping and
    // returns raw text, this test fires.
    const { message, detail } = humanizeApiError(
      new Error("ValueError: invalid URL format"),
    );
    expect(message).not.toBe(detail);
  });
});
