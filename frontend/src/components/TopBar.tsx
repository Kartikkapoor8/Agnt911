import type { WorldState } from "../types";
import { Panel } from "./Panel";

interface Props {
  state: WorldState;
  stale: boolean; // the latest poll failed; we are showing the last good state
}

/** Status strip: wordmark, tick, mode, and a dot that pulses while the fleet negotiates. */
export function TopBar({ state, stale }: Props) {
  const negotiating = state.integration.last_round_status === "running";
  return (
    <Panel className="status-strip">
      <div className="status-row">
        <div className="wordmark">Agnt<span>911</span></div>
        <span className="ss-tick mono">t{state.tick}</span>
        <span className={`ss-mode mode-${state.mode}`} title={state.mode === "protected" ? "Fleet consensus with verification and trust" : "Single dispatcher, no checks"}>
          {state.mode === "protected" ? "Protected" : "Baseline"}
        </span>
        <span className={`negotiating${negotiating ? " on" : ""}`} title={`Round status: ${state.integration.last_round_status}`}>
          <i aria-hidden />
          negotiating
        </span>
        {state.paused && <span className="ss-paused">Paused for a human</span>}
        {stale && <span className="ss-stale">Backend not responding</span>}
      </div>
    </Panel>
  );
}
