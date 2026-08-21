/**
 * Grid-area integrity pin (2026-08-21).
 *
 * OperatorBriefingCard shipped 2026-08-15 wrapped in `.area-kpi` — the SAME
 * grid area as KpiHero. CSS Grid places both children in one cell, so the KPI
 * cards painted on top of the briefing markdown and the operator saw text
 * fragments bleeding out between cards.
 *
 * Nothing caught it: both components rendered, both had valid class names, and
 * every unit test passed. The defect only exists in the relationship BETWEEN
 * two files, which is exactly the gap a per-component test cannot see.
 *
 * These assertions are structural rather than about this one card, so the next
 * card added to Mission Control is covered too.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..", "..", "..");
const CSS = readFileSync(join(SRC, "styles", "layouts.css"), "utf8");
const TSX = readFileSync(join(__dirname, "..", "MissionControl.tsx"), "utf8");

/** `area-foo` class names used as grid children inside .mc-grid-v2. */
function areaClassesUsed(): string[] {
  return [...TSX.matchAll(/className="(area-[a-z-]+)"/g)].map((m) => m[1]);
}

/** Area names declared via `.area-foo { grid-area: foo; }`. */
function areaClassesDeclared(): Map<string, string> {
  const out = new Map<string, string>();
  for (const m of CSS.matchAll(/\.(area-[a-z-]+)\s*\{\s*grid-area:\s*([a-z-]+);/g)) {
    out.set(m[1], m[2]);
  }
  return out;
}

/** Every grid-template-areas block in layouts.css, as sets of area names. */
function templateAreaSets(): string[][] {
  return [...CSS.matchAll(/grid-template-areas:\s*([^;]+);/g)].map((m) =>
    [...new Set(m[1].match(/[a-z][a-z-]*/g) ?? [])],
  );
}

describe("Mission Control grid areas", () => {
  it("no two grid children share a grid area", () => {
    const used = areaClassesUsed();
    const dupes = used.filter((c, i) => used.indexOf(c) !== i);
    expect(dupes,
      `these area classes are used by more than one child of .mc-grid-v2: ` +
      `${[...new Set(dupes)].join(", ")}. Two elements in one grid area stack ` +
      `on top of each other — the second paints over the first.`,
    ).toEqual([]);
  });

  it("every area class used in TSX is declared in CSS", () => {
    const declared = areaClassesDeclared();
    const missing = areaClassesUsed().filter((c) => !declared.has(c));
    expect(missing,
      `used in MissionControl.tsx but never declared in layouts.css: ` +
      `${missing.join(", ")}. An undeclared area class is inert — the child ` +
      `falls into auto-placement and lands wherever there is room.`,
    ).toEqual([]);
  });

  it("every declared area appears in every responsive template", () => {
    const templates = templateAreaSets();
    expect(templates.length).toBeGreaterThanOrEqual(3);

    const usedAreas = areaClassesUsed()
      .map((c) => areaClassesDeclared().get(c))
      .filter((a): a is string => Boolean(a));

    for (const [i, tpl] of templates.entries()) {
      const missing = [...new Set(usedAreas)].filter((a) => !tpl.includes(a));
      expect(missing,
        `grid-template-areas block #${i + 1} is missing: ${missing.join(", ")}. ` +
        `A card present at one breakpoint and absent at another disappears or ` +
        `auto-places when the window is resized.`,
      ).toEqual([]);
    }
  });

  it("the briefing card has its own area, above kpi", () => {
    // Regression pin for the specific 2026-08-21 defect.
    expect(TSX).toContain('className="area-briefing"');
    const desktop = templateAreaSets()[0];
    expect(desktop.indexOf("briefing")).toBeGreaterThanOrEqual(0);
    expect(desktop.indexOf("briefing")).toBeLessThan(desktop.indexOf("kpi"));
  });
});
