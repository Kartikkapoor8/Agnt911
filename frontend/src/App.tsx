import { useState } from "react";
import { postInject, postMode, postResolve, type InjectRequest, type Mode } from "./api";
import { toast } from "./toast";
import { useWorldState } from "./useWorldState";
import { LivePage } from "./components/LivePage";

type Page = "intro" | "live";

/** Top-level app: owns the polled state, the current page, and the selected vehicle. */
export function App() {
  const { state, error } = useWorldState();
  const [page, setPage] = useState<Page>("intro");
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

  // Start demo: a fresh run of the scripted scenario in Protected (POST /mode resets to t0), then the panels.
  async function start() {
    try {
      await postMode("protected");
    } catch (err) {
      toast(`Could not start a fresh run: ${err instanceof Error ? err.message : String(err)}`, "error");
    }
    setPage("live");
  }

  return (
    <LivePage
      state={state}
      stale={error !== null}
      selectedId={selectedId}
      onSelect={setSelectedId}
      intro={page === "intro"}
      onStart={() => void start()}
      {...handlers}
    />
  );
}
