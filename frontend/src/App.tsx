import { useState } from "react";
import { ApiError, postIncident, postInject, postMode, postResolve, type InjectRequest, type Mode } from "./api";
import { toast } from "./toast";
import type { IncidentType, Location } from "./types";
import { useWorldState } from "./useWorldState";
import { LandingPage } from "./components/LandingPage";
import { LivePage } from "./components/LivePage";

type Page = "landing" | "live";

/** A dropped call is sent at severity 4 with a label that says where it came from. */
async function dropIncident(type: IncidentType, location: Location) {
  const label = `${type[0].toUpperCase()}${type.slice(1)} call dropped by the judge`;
  try {
    await postIncident({ type, severity: 4, location, label });
    toast(`Placed a ${type} call.`, "ok");
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) toast("POST /incident is not on the backend yet (404), so the call was not placed.", "error");
    else toast(`Could not place the call: ${err instanceof Error ? err.message : String(err)}`, "error");
  }
}

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
    onDropIncident: (type: IncidentType, at: Location) => void dropIncident(type, at),
  };

  if (page === "live") {
    return <LivePage state={state} stale={error !== null} selectedId={selectedId} onSelect={setSelectedId} {...handlers} />;
  }
  // The backend has no start/reset endpoint: the scripted run starts with the server, so Start run just opens the live page.
  return <LandingPage state={state} stale={error !== null} onStart={() => setPage("live")} />;
}
