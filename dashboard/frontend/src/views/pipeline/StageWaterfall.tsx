import { useRef, useEffect, useState, useMemo } from "react";
import { Check, X, Loader2 } from "lucide-react";
import type { PipelineStage } from "@/niches/registry";
import type { PipelineRunStage } from "@/hooks/use-pipeline-monitor";

interface Props {
  stages: PipelineStage[];
  currentStage: string | null;
  lastRunStages: PipelineRunStage[] | null;
  accentColor: string;
}

function formatDuration(seconds: number): string {
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

export function StageWaterfall({ stages, currentStage, lastRunStages, accentColor }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hasOverflow, setHasOverflow] = useState(false);

  // Use ResizeObserver for responsive overflow detection
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const check = () => setHasOverflow(el.scrollWidth > el.clientWidth);
    check();
    const observer = new ResizeObserver(check);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Build a lookup from display stage name → last run stage data.
  // Run stages use class names (FetchTrendingVideos) while display stages use
  // short labels ('1', '2', etc.). Match by index position when names don't align.
  const stageMap = useMemo(() => {
    const map = new Map<string, PipelineRunStage>();
    if (lastRunStages) {
      // First try name match
      for (const s of lastRunStages) {
        map.set(s.name, s);
      }
      // If no name matches found against display stages, match by index
      const hasNameMatch = stages.some((s) => map.has(s.name));
      if (!hasNameMatch && lastRunStages.length > 0) {
        stages.forEach((displayStage, i) => {
          if (i < lastRunStages.length) {
            map.set(displayStage.name, lastRunStages[i]);
          }
        });
      }
    }
    return map;
  }, [lastRunStages, stages]);

  // Determine which stages are "before" the current running stage
  const currentIdx = currentStage
    ? stages.findIndex((s) => s.name === currentStage)
    : -1;

  return (
    <div
      ref={containerRef}
      className={`waterfall-container ${hasOverflow ? "has-overflow" : ""}`}
      style={{ overflowX: "auto" }}
    >
      <div className="waterfall-track">
        {stages.map((stage, i) => {
          const runStage = stageMap.get(stage.name);
          const isRunning = currentStage === stage.name;
          const isCompleted = runStage?.status === "completed";
          const isError = runStage?.status === "error";
          const isFuture = currentIdx >= 0 ? i > currentIdx : !isCompleted && !isError;

          let circleClass = "waterfall-circle pending";
          if (isRunning) circleClass = "waterfall-circle running";
          else if (isCompleted) circleClass = "waterfall-circle completed";
          else if (isError) circleClass = "waterfall-circle error";

          // Connector before this node (skip for first)
          const showConnector = i > 0;
          const prevDone = i <= currentIdx || (currentIdx < 0 && stageMap.has(stages[i - 1]?.name));
          let connectorClass = "waterfall-connector";
          if (prevDone) connectorClass += " done";
          else if (isFuture) connectorClass += " dashed";

          return (
            <div key={stage.name} className="contents">
              {showConnector && (
                <div
                  className={connectorClass}
                  style={
                    prevDone
                      ? { ["--connector-color" as string]: accentColor, background: accentColor }
                      : undefined
                  }
                />
              )}
              <div className="waterfall-node" title={stage.description}>
                <div
                  className={circleClass}
                  style={
                    isRunning || isCompleted
                      ? {
                          background: isRunning
                            ? `color-mix(in srgb, ${accentColor} 20%, var(--surface-0))`
                            : accentColor,
                          borderColor: accentColor,
                          ["--glow-color" as string]: accentColor,
                        }
                      : undefined
                  }
                >
                  {isCompleted && <Check className="size-3.5 text-white" />}
                  {isRunning && (
                    <Loader2
                      className="size-3.5 animate-spin"
                      style={{ color: accentColor }}
                    />
                  )}
                  {isError && <X className="size-3.5 text-error" />}
                </div>
                <span className={`waterfall-label ${isRunning || isCompleted ? "active" : ""}`}>
                  {stage.label}
                </span>
                {runStage && runStage.duration_seconds > 0 && !isRunning && (
                  <span className="waterfall-timing">
                    {formatDuration(runStage.duration_seconds)}
                  </span>
                )}
                {isRunning && (
                  <span className="waterfall-timing" style={{ color: accentColor }}>
                    Running...
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
