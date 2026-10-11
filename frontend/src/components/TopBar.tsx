import { narrate } from "../narrator";
import type { WorldState } from "../types";
import { Panel } from "./Panel";

interface Props {
  state: WorldState;
  stale: boolean; // the latest poll failed; we are showing the last good state
}

/** Status strip: the wordmark, then one live sentence saying what is happening now; tick and mode small on the right. */
export function TopBar({ state, stale }: Props) {
  const now = narrate(state);
  return (
    <Panel className={`status-strip${now.pulse ? " pulsing" : ""}`}>
      <div className="status-row">
        <div className="wordmark">Agnt<span>911</span></div>
        <span className={`ss-now tone-${now.tone}`} aria-live="polite">
          <i aria-hidden />
          {stale ? "Backend not responding, showing the last state" : now.text}
        </span>
        <span className="ss-meta">
          <span className="ss-tick mono">t{state.tick}</span>
          <span className={`ss-mode mode-${state.mode}`}>{state.mode === "protected" ? "Protected" : "Baseline"}</span>
        </span>
      </div>
    </Panel>
  );
}
