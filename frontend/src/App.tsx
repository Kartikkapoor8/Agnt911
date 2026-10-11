import { useEffect, useState } from "react";
import { postInject, postMode, postResolve, type InjectRequest, type Mode } from "./api";
import { toast } from "./toast";
import type { WorldState } from "./types";
import { useWorldState } from "./useWorldState";
import { Landing } from "./components/Landing";
import { LivePage } from "./components/LivePage";

const FADE_MS = 400;
let resetOnLoad = false; // module flag: StrictMode runs the mount effect twice, the reset should happen once

/** A fresh run in Protected: every vehicle at trust 1.0, nobody compromised, no callers. Returns its run id. */
async function freshRun(): Promise<string | null> {
  try {
    const body = (await postMode("protected")) as WorldState;
    return body.run_id;
  } catch (err) {
    toast(`Could not start a fresh run: ${err instanceof Error ? err.message : String(err)}`, "error");
    return null;
  }
}

/** Top-level app: the landing screen over the live page, the polled state, the selected vehicle, and reset. */
export function App() {
  const { state, error } = useWorldState();
  const [landing, setLanding] = useState(true);
  const [leaving, setLeaving] = useState(false); // landing fading out
  const [awaitRun, setAwaitRun] = useState<string | null>(null); // Start demo waits for its fresh run to show up
  const [liveKey, setLiveKey] = useState(0); // remounting the live page clears its client state
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // A page refresh is a reset for the next judge.
  useEffect(() => {
    if (resetOnLoad) return;
    resetOnLoad = true;
    void freshRun();
  }, []);

  // The fresh run is on screen (t0): fade the landing out into the live map.
  useEffect(() => {
    if (!awaitRun || state?.run_id !== awaitRun) return;
    setAwaitRun(null);
    setLeaving(true);
  }, [state?.run_id, awaitRun]);

  // After the 400 ms fade, the landing is gone.
  useEffect(() => {
    if (!leaving) return;
    const t = window.setTimeout(() => {
      setLanding(false);
      setLeaving(false);
    }, FADE_MS);
    return () => window.clearTimeout(t);
  }, [leaving]);

  async function start() {
    const run = await freshRun();
    if (run) setAwaitRun(run);
  }

  /** Reset for the next judge: a fresh Protected run, client state cleared (cached routes stay), back to the landing. */
  async function reset() {
    await freshRun();
    setSelectedId(null);
    setLiveKey((k) => k + 1);
    setLanding(true);
    setLeaving(false);
  }

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

  return (
    <>
      <LivePage
        key={liveKey}
        state={state}
        stale={error !== null}
        selectedId={selectedId}
        onSelect={setSelectedId}
        intro={landing && !leaving}
        onReset={() => void reset()}
        {...handlers}
      />
      {landing && <Landing leaving={leaving} starting={awaitRun !== null} onStart={() => void start()} />}
    </>
  );
}
