import { useState, useEffect, useCallback, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { pipeline } from "@/api/client";
import { getSocket } from "@/api/socket";
import type { PipelineLogEntry } from "@/api/socket";

const MAX_LOG_LINES = 500;

export function usePipelineLogs(nicheId: string | null) {
  const [lines, setLines] = useState<PipelineLogEntry[]>([]);
  const latestTsRef = useRef<string | null>(null);

  // Initial fetch when niche changes
  const { data: initialData } = useQuery({
    queryKey: ["pipeline-logs", nicheId],
    queryFn: () =>
      nicheId
        ? pipeline.logs({ niche_id: nicheId, limit: "200" })
        : Promise.resolve({ data: [] }),
    enabled: !!nicheId,
    staleTime: 10_000,
    refetchInterval: false, // Socket handles live updates
  });

  // Seed from initial fetch
  useEffect(() => {
    if (!initialData?.data) return;
    const entries = initialData.data as PipelineLogEntry[];
    setLines(entries.slice(-MAX_LOG_LINES));
    if (entries.length > 0) {
      latestTsRef.current = entries[entries.length - 1].ts;
    }
  }, [initialData]);

  // Reset on niche change
  useEffect(() => {
    setLines([]);
    latestTsRef.current = null;
  }, [nicheId]);

  // Socket listener for live log lines
  useEffect(() => {
    if (!nicheId) return;

    const socket = getSocket();

    const handleLogs = (event: { niche_id: string; lines: PipelineLogEntry[] }) => {
      if (event.niche_id !== nicheId) return;

      setLines((prev) => {
        const combined = [...prev, ...event.lines];
        const trimmed = combined.slice(-MAX_LOG_LINES);
        if (trimmed.length > 0) {
          latestTsRef.current = trimmed[trimmed.length - 1].ts;
        }
        return trimmed;
      });
    };

    socket.on("pipeline_logs", handleLogs);
    return () => {
      socket.off("pipeline_logs", handleLogs);
    };
  }, [nicheId]);

  const clear = useCallback(() => {
    setLines([]);
    latestTsRef.current = null;
  }, []);

  return { lines, clear };
}
