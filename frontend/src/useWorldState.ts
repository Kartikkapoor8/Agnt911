import { useEffect, useState } from "react";
import type { WorldState } from "./types";

const POLL_MS = 500;
const STATE_URL = "/api/state"; // Vite proxies /api to the backend on port 8000

export interface WorldStateResult {
  state: WorldState | null;
  error: string | null; // set while the backend is unreachable; the last good state is kept
}

/** Poll GET /state every 500 ms and return the latest world plus any connection error. */
export function useWorldState(): WorldStateResult {
  const [state, setState] = useState<WorldState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;

    async function poll() {
      try {
        const res = await fetch(STATE_URL, { cache: "no-store" });
        if (!res.ok) throw new Error(`GET /state returned ${res.status}`);
        const body = (await res.json()) as WorldState;
        if (!stopped) {
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
