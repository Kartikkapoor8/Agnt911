import { useState } from "react";
import { postInject, postMode, postResolve, type InjectRequest, type Mode } from "./api";
import { useWorldState } from "./useWorldState";
import { LandingPage } from "./components/LandingPage";
import { LivePage } from "./components/LivePage";
import { ResultsPlaceholder } from "./components/ResultsPlaceholder";

type Page = "landing" | "live" | "results";

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

  if (page === "results") return <ResultsPlaceholder onBack={() => setPage("live")} />;
  if (page === "live") {
    return (
      <LivePage
        state={state}
        stale={error !== null}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onEndRun={() => setPage("results")}
        {...handlers}
      />
    );
  }
  // The backend has no start/reset endpoint: the scripted run starts with the server, so Start run just opens the live page.
  return <LandingPage state={state} stale={error !== null} onStart={() => setPage("live")} />;
}
