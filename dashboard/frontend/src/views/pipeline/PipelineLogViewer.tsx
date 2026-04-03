import { useState, useRef, useEffect, useMemo } from "react";
import {
  Terminal,
  ChevronDown,
  ChevronUp,
  Trash2,
  Filter,
} from "lucide-react";
import { usePipelineLogs } from "@/hooks/use-pipeline-logs";
import type { PipelineLogEntry } from "@/api/socket";

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: "var(--text-muted)",
  INFO: "var(--color-blue, #3b82f6)",
  WARNING: "var(--color-amber, #d97706)",
  ERROR: "var(--color-red, #ef4444)",
  CRITICAL: "var(--color-red, #ef4444)",
};

function LogLine({ entry }: { entry: PipelineLogEntry }) {
  const ts = entry.ts.slice(11, 19); // HH:MM:SS
  const levelColor = LEVEL_COLORS[entry.level] || "var(--text-muted)";
  const stageTag = entry.stage ? (
    <span className="log-stage-tag">{entry.stage}</span>
  ) : null;

  return (
    <div className="log-line">
      <span className="log-ts">{ts}</span>
      <span className="log-level" style={{ color: levelColor }}>
        {entry.level.slice(0, 4).padEnd(4)}
      </span>
      {stageTag}
      <span className="log-msg">{entry.message}</span>
    </div>
  );
}

export function PipelineLogViewer({ nicheId }: { nicheId: string | null }) {
  const { lines, clear } = usePipelineLogs(nicheId);
  const [expanded, setExpanded] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [levelFilter, setLevelFilter] = useState<string>("ALL");
  const scrollRef = useRef<HTMLDivElement>(null);

  const filteredLines = useMemo(() => {
    if (levelFilter === "ALL") return lines;
    const levels: Record<string, number> = {
      DEBUG: 0,
      INFO: 1,
      WARNING: 2,
      ERROR: 3,
      CRITICAL: 4,
    };
    const minLevel = levels[levelFilter] ?? 0;
    return lines.filter((l) => (levels[l.level] ?? 0) >= minLevel);
  }, [lines, levelFilter]);

  // Auto-scroll to bottom when new lines arrive
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [filteredLines, autoScroll]);

  // Detect manual scroll-up → pause auto-scroll
  const handleScroll = () => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    const atBottom = scrollHeight - scrollTop - clientHeight < 40;
    setAutoScroll(atBottom);
  };

  if (!nicheId) return null;

  const hasLines = lines.length > 0;
  const errorCount = lines.filter((l) => l.level === "ERROR" || l.level === "CRITICAL").length;

  return (
    <div className="log-viewer">
      <button
        className="log-viewer-toggle"
        onClick={() => setExpanded(!expanded)}
      >
        <Terminal className="size-3.5" />
        <span>Pipeline Logs</span>
        {hasLines && (
          <span className="log-count-badge">
            {lines.length}
            {errorCount > 0 && (
              <span className="text-error ml-1">
                ({errorCount} err)
              </span>
            )}
          </span>
        )}
        {expanded ? (
          <ChevronUp className="size-3" />
        ) : (
          <ChevronDown className="size-3" />
        )}
      </button>

      {expanded && (
        <>
          <div className="log-toolbar">
            <div className="log-filter">
              <Filter className="size-3" />
              <select
                value={levelFilter}
                onChange={(e) => setLevelFilter(e.target.value)}
                className="log-level-select"
              >
                <option value="ALL">All levels</option>
                <option value="DEBUG">DEBUG+</option>
                <option value="INFO">INFO+</option>
                <option value="WARNING">WARN+</option>
                <option value="ERROR">ERROR+</option>
              </select>
            </div>
            <div className="flex items-center gap-2">
              {!autoScroll && (
                <button
                  className="log-action-btn"
                  onClick={() => {
                    setAutoScroll(true);
                    if (scrollRef.current) {
                      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
                    }
                  }}
                  title="Resume auto-scroll"
                >
                  <ChevronDown className="size-3" />
                  Resume
                </button>
              )}
              <button
                className="log-action-btn"
                onClick={clear}
                title="Clear logs"
              >
                <Trash2 className="size-3" />
              </button>
            </div>
          </div>
          <div
            ref={scrollRef}
            className="log-scroll-area"
            onScroll={handleScroll}
          >
            {filteredLines.length === 0 ? (
              <div className="log-empty">
                No logs yet. Logs appear when a pipeline run starts.
              </div>
            ) : (
              filteredLines.map((entry, i) => (
                <LogLine key={`${entry.ts}-${i}`} entry={entry} />
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}
