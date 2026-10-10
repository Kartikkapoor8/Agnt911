import { useEffect, useRef, useState } from "react";
import { newEvents, onEvent, onRunChange } from "./dispatch";
import type { TypedEvent, WorldState } from "./types";

const POLL_MS = 500;
const STATE_URL = "/api/state"; // Vite proxies /api to the backend on port 8000

export interface WorldStateResult {
  state: WorldState | null;
  error: string | null; // set while the backend is unreachable; the last good state is kept
}

/** Poll GET /state every 500 ms and return the latest world plus any connection error. New typed events go to onEvent. */
export function useWorldState(): WorldStateResult {
  const [state, setState] = useState<WorldState | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The last poll's run and event window, so each event is dispatched once. A ref survives StrictMode's re-run.
  const seen = useRef<{ runId: string | null; events: TypedEvent[] }>({ runId: null, events: [] });

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;

    /** Reset on a new run_id, then hand every event that wasn't in the previous window to the dispatcher. */
    function dispatchNew(body: WorldState) {
      const first = seen.current.runId === null; // joined mid-run: feed the backlog, don't animate it
      if (body.run_id !== seen.current.runId) {
        onRunChange(body.run_id);
        seen.current = { runId: body.run_id, events: [] };
      }
      const fresh = newEvents(seen.current.events, body.events ?? []);
      seen.current.events = body.events ?? [];
      fresh.forEach((e) => onEvent(e, { runId: body.run_id, backfill: first }));
    }

    async function poll() {
      try {
        const res = await fetch(STATE_URL, { cache: "no-store" });
        if (!res.ok) throw new Error(`GET /state returned ${res.status}`);
        const body = (await res.json()) as WorldState;
        if (!stopped) {
          dispatchNew(body);
          setState(body);
          setError(null);
        }
      } catch (err) {
        if (!stopped) setError(err instanceof Error ? err.message : String(err));
      } finally {
        // Schedule after the response so slow replies never stack up.
        if (!stopped) timer = window.setTimeout(poll, POLL_MS);
      }
    }

    poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, []);

  return { state, error };
}
