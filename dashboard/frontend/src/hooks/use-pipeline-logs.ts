import { useState, useEffect, useCallback, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { pipeline } from "@/api/client";
import { getSocket } from "@/api/socket";
import type { PipelineLogEntry } from "@/api/socket";

const MAX_LOG_LINES = 500;

export function usePipelineLogs(nicheId: string | null) {
  const [lines, setLines] = useState<PipelineLogEntry[]>([]);
  const latestTsRef = useRef<string | null>(null);

  // Reset on niche change (React 19 idiom — see stories.tsx).
  // The ref reset cannot happen during render (react-hooks/refs); it
  // moves into an effect coupled to nicheId.
  const [prevNicheId, setPrevNicheId] = useState(nicheId);
  if (prevNicheId !== nicheId) {
    setPrevNicheId(nicheId);
    setLines([]);
  }
  useEffect(() => {
    latestTsRef.current = null;
  }, [nicheId]);

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

  // Seed from initial fetch. This is a synchronization between an
  // external (network) system and React state, which is exactly what
  // useEffect is for — ESLint's set-state-in-effect rule is overly
  // conservative for the external-data-arrives-fresh pattern.
  useEffect(() => {
    if (!initialData?.data) return;
    const entries = initialData.data as PipelineLogEntry[];
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLines(entries.slice(-MAX_LOG_LINES));
    if (entries.length > 0) {
      latestTsRef.current = entries[entries.length - 1].ts;
    }
  }, [initialData]);

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
