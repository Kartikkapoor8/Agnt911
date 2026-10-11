import { useEffect, useState } from "react";
import { RotateCcw } from "lucide-react";
import { narrate } from "../narrator";
import type { WorldState } from "../types";
import { Panel } from "./Panel";

interface Props {
  state: WorldState;
  stale: boolean; // the latest poll failed; we are showing the last good state
  onReset: () => void; // fresh Protected run, client state cleared, back to the landing
}

/** True while the user is typing somewhere, so single-key shortcuts stay out of the way. */
function typing(): boolean {
  const el = document.activeElement;
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement || (el as HTMLElement | null)?.isContentEditable === true;
}

/** Status strip: the wordmark, one live sentence saying what is happening now, tick and mode, and Reset. */
export function TopBar({ state, stale, onReset }: Props) {
  const now = narrate(state);
  const [confirming, setConfirming] = useState(false);

  // R asks to reset; Enter or Y confirms, Esc or N cancels.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || typing()) return;
      const k = e.key.toLowerCase();
      if (!confirming && k === "r") setConfirming(true);
      else if (confirming && (k === "enter" || k === "y")) {
        setConfirming(false);
        onReset();
      } else if (confirming && (k === "escape" || k === "n")) setConfirming(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [confirming, onReset]);

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
          {confirming ? (
            <span className="ss-confirm" role="alertdialog" aria-label="Reset for the next judge?">
              <span>Reset for the next judge?</span>
              <button className="ss-yes" onClick={() => { setConfirming(false); onReset(); }}>Reset</button>
              <button className="ss-no" onClick={() => setConfirming(false)}>Cancel</button>
            </span>
          ) : (
            <button className="ss-reset" onClick={() => setConfirming(true)} title="Reset for the next judge (R)" aria-label="Reset">
              <RotateCcw size={16} />
            </button>
          )}
        </span>
      </div>
    </Panel>
  );
}
