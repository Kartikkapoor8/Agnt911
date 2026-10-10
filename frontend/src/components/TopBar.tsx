import { useState } from "react";
import type { Mode } from "../api";
import type { WorldState } from "../types";

interface Props {
  state: WorldState;
  onSetMode: (mode: Mode) => Promise<void>;
  onEndRun: () => void;
}

const PHASE: Record<number, string> = { 1: "propose", 2: "counter", 3: "vote" };

/** Header: mode toggle, decision/round/tick readout, and the End run button. */
export function TopBar({ state, onSetMode, onEndRun }: Props) {
  const [pending, setPending] = useState<Mode | null>(null);
  const [error, setError] = useState<string | null>(null);

  // There is no "current round" field: the latest message's round is the phase reached in the latest decision.
  const last = state.recent_messages[state.recent_messages.length - 1];
  const round = last ? `round ${last.round} (${PHASE[last.round] ?? "?"})` : "no rounds yet";

  async function choose(mode: Mode) {
    setPending(mode);
    setError(null);
    try {
      await onSetMode(mode);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(null);
    }
  }

  return (
    <header className="topbar">
      <div className="wordmark">Agnt<span>911</span></div>

      <div className="mode-toggle" title="Switching mode restarts the scenario">
        {(["protected", "baseline"] as Mode[]).map((m) => (
          <button
            key={m}
            className={state.mode === m ? "active" : ""}
            disabled={pending !== null || state.mode === m}
            onClick={() => choose(m)}
          >
            {m === "protected" ? "Fleet consensus" : "Single dispatcher"}
            {pending === m ? "…" : ""}
          </button>
        ))}
      </div>
      {error && <span className="error-text">{error}</span>}

      <div className="readout">
        <span>decision {state.decision}</span>
        <span>{round}</span>
        <span>{state.integration.last_round_status}</span>
        <span>tick {state.tick}</span>
        {state.paused && <span className="paused">Paused</span>}
      </div>

      <button className="end-run" onClick={onEndRun}>End run</button>
    </header>
  );
}
