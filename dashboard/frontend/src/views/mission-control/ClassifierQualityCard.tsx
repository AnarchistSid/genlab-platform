/**
 * Phase 1.C observability card (2026-08-14) — classifier quality.
 *
 * Meta-learning surface. Answers: "of the auto-accept decisions
 * that were made this month, which decision paths (heuristic vs LLM
 * vs manual) actually helped when we applied them?"
 *
 * ## Data pipeline
 *
 * strategist_actions.apply → registers strategist_outcome_verification
 *   with classifier_source + classifier_name at t=0
 * run_outcome_verifier (every 6h) → sets verdict at t+48h
 * this card → aggregates GROUP BY (source, name) over last 30d
 *
 * ## Accuracy metric
 *
 * `improved / (improved + regressed)`. Unchanged decisions are non-
 * diagnostic (metric didn't move either way), so they're excluded
 * from the denominator. Null accuracy means denominator=0 (no
 * diagnostic verdicts yet — cold-start).
 *
 * ## Rows
 *
 * One per (source, name) combination. Sources: heuristic, llm,
 * manual, unknown. Names: proposal types (arm_add is the only one
 * with outcome verification wired today).
 */
import { useQuery } from "@tanstack/react-query";

import { classifierQuality } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type {
  ClassifierQualityRow,
  ClassifierSource,
} from "@/api/types";

function sourcePillClasses(s: ClassifierSource): string {
  switch (s) {
    case "heuristic":
      return "bg-blue-500/15 text-blue-300";
    case "llm":
      return "bg-purple-500/15 text-purple-300";
    case "manual":
      return "bg-emerald-500/15 text-emerald-300";
    case "unknown":
      return "bg-gray-500/15 text-gray-400";
  }
}

function accuracyClasses(acc: number | null): string {
  if (acc === null) return "text-gray-500";
  if (acc >= 0.7) return "text-emerald-300";
  if (acc >= 0.5) return "text-amber-300";
  return "text-red-300";
}

/** Percent bar showing the improved/unchanged/regressed mix. */
function VerdictBar({ r }: { r: ClassifierQualityRow }) {
  const total = r.n_verified || 1;
  const impPct = (r.n_improved / total) * 100;
  const unchPct = (r.n_unchanged / total) * 100;
  const regPct = (r.n_regressed / total) * 100;
  return (
    <div className="h-2 w-full rounded overflow-hidden flex bg-gray-800">
      {impPct > 0 && (
        <div
          className="bg-emerald-500/70"
          style={{ width: `${impPct}%` }}
          title={`${r.n_improved} improved`}
        />
      )}
      {unchPct > 0 && (
        <div
          className="bg-gray-500/50"
          style={{ width: `${unchPct}%` }}
          title={`${r.n_unchanged} unchanged`}
        />
      )}
      {regPct > 0 && (
        <div
          className="bg-red-500/70"
          style={{ width: `${regPct}%` }}
          title={`${r.n_regressed} regressed`}
        />
      )}
    </div>
  );
}

function Row({ r }: { r: ClassifierQualityRow }) {
  return (
    <div className="py-2 border-b border-gray-800 last:border-0">
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2">
          <span
            className={`px-2 py-0.5 rounded text-[11px] font-medium ${sourcePillClasses(
              r.classifier_source,
            )}`}
          >
            {r.classifier_source}
          </span>
          <span className="text-sm text-gray-200">{r.classifier_name}</span>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className="text-gray-500">n={r.n_verified}</span>
          <span className={accuracyClasses(r.accuracy)}>
            {r.accuracy !== null
              ? `${(r.accuracy * 100).toFixed(0)}%`
              : "no verdict"}
          </span>
        </div>
      </div>
      <VerdictBar r={r} />
      <div className="flex items-center gap-3 text-[10px] text-gray-500 mt-1">
        <span className="text-emerald-400">↑ {r.n_improved} improved</span>
        <span>= {r.n_unchanged} unchanged</span>
        <span className="text-red-400">↓ {r.n_regressed} regressed</span>
      </div>
    </div>
  );
}

export function ClassifierQualityCard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: queryKeys.classifierQuality.all(),
    queryFn: () => classifierQuality.fetch(),
    refetchInterval: 5 * 60_000, // 5 min — slow-moving aggregate
  });

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-200">
          Classifier quality
        </h3>
        <span className="text-[10px] text-gray-500">
          Phase 1.C — meta-learning · 30d window
        </span>
      </div>
      {isLoading && (
        <div className="text-xs text-gray-500 py-2">Loading…</div>
      )}
      {isError && (
        <div className="text-xs text-red-400 py-2">
          Failed to load. Endpoint may be unavailable.
        </div>
      )}
      {data && data.length === 0 && (
        <div className="text-xs text-gray-500 py-2">
          No verified verdicts yet. First outcome checks fire ~48h
          after this morning's apply worker (Sat 08-16 IST).
        </div>
      )}
      {data && data.length > 0 && (
        <div>
          {data.map((r) => (
            <Row
              key={`${r.classifier_source}:${r.classifier_name}`}
              r={r}
            />
          ))}
        </div>
      )}
      <div className="mt-3 text-[10px] text-gray-500 leading-relaxed">
        <span className="font-semibold text-gray-400">Accuracy:</span> improved
        / (improved + regressed).
        <span className="ml-2 text-emerald-400">≥70%</span> good.
        <span className="ml-2 text-amber-400">50-70%</span> weak.
        <span className="ml-2 text-red-400">&lt;50%</span> path is
        actively hurting — investigate.
      </div>
    </div>
  );
}
