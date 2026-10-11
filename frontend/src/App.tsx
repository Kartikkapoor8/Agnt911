import { useState } from "react";
import { postInject, postMode, postResolve, type InjectRequest, type Mode } from "./api";
import { toast } from "./toast";
import { useWorldState } from "./useWorldState";
import { LandingPage } from "./components/LandingPage";
import { LivePage } from "./components/LivePage";

type Page = "landing" | "live";

/** Top-level app: owns the polled state, the current page, and the selected vehicle. */
export function App() {
  const { state, error } = useWorldState();
  const [page, setPage] = useState<Page>("landing");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  if (!state) {
    return (
      <div className="notice">
        <div className="wordmark">Agnt<span>911</span></div>
        {error ? (
          <>
            <h2>Backend not reachable</h2>
            <p>Could not load /state ({error}). Start the backend and this page will connect on its own.</p>
          </>
        ) : (
          <p>Connecting to the backend…</p>
        )}
      </div>
    );
  }

  // POSTs only change the backend; the next poll brings the new state, so the UI always shows what /state says.
  const handlers = {
    onSetMode: async (mode: Mode) => void (await postMode(mode)),
    onInject: async (req: InjectRequest) => void (await postInject(req)),
    onResolve: async (assignments: Record<string, string | null>) => void (await postResolve(assignments)),
  };

  // A run starts in Protected: if the backend was left on the baseline, Start run switches it back (a new run).
  async function start() {
    if (state?.mode === "baseline") {
      try {
        await postMode("protected");
      } catch (err) {
        toast(`Could not switch to Protected: ${err instanceof Error ? err.message : String(err)}`, "error");
      }
    }
    setPage("live");
  }

  if (page === "live") {
    return <LivePage state={state} stale={error !== null} selectedId={selectedId} onSelect={setSelectedId} {...handlers} />;
  }
  // The backend has no start/reset endpoint: the scripted run starts with the server, so Start run opens the live page
  // (switching a leftover baseline run back to Protected first).
  return <LandingPage state={state} stale={error !== null} onStart={() => void start()} />;
}
