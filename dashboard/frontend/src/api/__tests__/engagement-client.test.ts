/**
 * PR W — engagementApi.recent unwrap contract pin (audit C1, 2026-03-31).
 *
 * Pins the inner ``.then(...)`` transform in
 * ``api/client.ts:831`` that converts the server envelope
 * ``{comments: EngagementComment[], total: number}`` into the array
 * shape ``EngagementComment[]`` that callers expect. Without this
 * transform the Engagement view crashes with "e.map is not a function"
 * (the original C1 audit symptom).
 *
 * We patch ``fetch`` to simulate the server-shape response and assert
 * the client unwraps it correctly. Two paths covered:
 *   1. Server returns wrapped ``{status, code, data: {comments, total}}``
 *      → client returns ``EngagementComment[]``.
 *   2. Server returns ``{status, code, data: {comments: []}}`` (no
 *      total) → client returns ``[]`` without crashing.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import { engagementApi } from "../client";

describe("engagementApi.recent (C1 unwrap pin)", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("unwraps {comments: [...], total: N} envelope to array", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          status: "success",
          code: 200,
          data: {
            comments: [
              {
                niche_id: "gaming",
                platform: "instagram",
                comment_id: "c1",
                text: "hello",
                username: "u1",
                timestamp: "2026-03-31T12:00:00Z",
              },
            ],
            total: 1,
          },
          message: "OK",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ) as never,
    );

    const result = await engagementApi.recent();

    expect(Array.isArray(result)).toBe(true);
    expect(result).toHaveLength(1);
    expect(result[0].text).toBe("hello");
  });

  it("returns empty array when server returns no comments", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          status: "success",
          code: 200,
          data: { comments: [], total: 0 },
          message: "OK",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ) as never,
    );

    const result = await engagementApi.recent();

    expect(Array.isArray(result)).toBe(true);
    expect(result).toHaveLength(0);
  });

  it("returns empty array when comments key is absent (defensive)", async () => {
    // If the server schema ever drifts (e.g. omits ``comments`` on
    // empty), we must NOT crash. The guard
    // ``Array.isArray(d) ? d : d?.comments ?? []`` should yield ``[]``.
    fetchSpy.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          status: "success",
          code: 200,
          data: { total: 0 },
          message: "OK",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ) as never,
    );

    const result = await engagementApi.recent();

    expect(Array.isArray(result)).toBe(true);
    expect(result).toHaveLength(0);
  });
});
