import { io, type Socket } from "socket.io-client";
import type { BlueprintUpdatedEvent, PipelineProgressEvent } from "./types";

let socket: Socket | null = null;

export function getSocket(): Socket {
  if (!socket) {
    socket = io({
      transports: ["polling", "websocket"],
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 30000,
      reconnectionAttempts: 20,
    });
  }
  return socket;
}

/**
 * Socket event names use underscores to match server-side socketio.emit().
 * All listeners MUST use these exact names.
 */
export interface PipelineLogEntry {
  ts: string;
  level: string;
  logger: string;
  message: string;
  run_id: string;
  niche_id: string;
  stage: string | null;
}

export interface PipelineLogsEvent {
  niche_id: string;
  lines: PipelineLogEntry[];
}

export type SocketEvents = {
  "pipeline_progress": PipelineProgressEvent;
  "pipeline_complete": { run_id: string; niche_id?: string; state?: string; summary: unknown; duration: number };
  "pipeline_started": { run_id: string; niche_id?: string; mode?: string };
  "pipeline_logs": PipelineLogsEvent;
  "blueprint_updated": BlueprintUpdatedEvent & { record_id?: string; action?: string };
  "blueprints_updated": Record<string, never>;
  "express_progress": { type: string; step?: string; run_id?: string };
};
