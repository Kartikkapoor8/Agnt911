import { useState } from "react";
import { ChevronUp } from "lucide-react";
import type { Mode } from "../api";
import { lastTick, scoreAt, type Score } from "../runs";
import type { WorldState } from "../types";

interface Props {
  state: WorldState;
  onSetMode: (mode: Mode) => Promise<void>;
}

const answered = (s: Score | null) => (s ? `${s.served}/${s.calls}` : "–");
const lies = (s: Score | null) => (s ? `${s.caught}/${s.hacked}` : "–");
const wrong = (s: Score | null) => (s ? String(s.wronglyFlagged) : "–");

/** Scoreboard, bottom centre: a thin bar with this run's three numbers; open it for Protected vs Dumb dispatcher at the same tick. */
export function MetricsDrawer({ state, onSetMode }: Props) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const baseline = state.mode === "baseline";

  async function setBaseline(on: boolean) {
    setPending(true);
    setError(null);
    try {
      await onSetMode(on ? "baseline" : "protected"); // starts a fresh run in that mode
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  const secondsPerTick = state.tick > 0 && state.time_s > 0 ? state.time_s / state.tick : 1;
  const now = scoreAt(state.run_id, state.tick);

  // The latest finished run of the other mode that this page watched; both columns are read at the shorter run's end.
  const other = [...state.comparison].reverse().find((c) => c.baseline !== baseline && lastTick(c.run_id) !== null);
  const at = other ? Math.min(state.tick, lastTick(other.run_id) ?? 0) : state.tick;
  const liveAt = scoreAt(state.run_id, at);
  const otherAt = other ? scoreAt(other.run_id, at) : null;
  const protectedScore = baseline ? otherAt : liveAt;
  const dumbScore = baseline ? liveAt : otherAt;

  const rows: [string, (s: Score | null) => string, string?][] = [
    ["Calls answered", answered],
    ["Lies caught", lies, "Hacked vehicles the fleet stopped trusting, of those hacked by then"],
    ["Honest units wrongly flagged", wrong],
    ["Avg response (s)", (s) => (s && s.avgResponseTicks !== null ? (s.avgResponseTicks * secondsPerTick).toFixed(1) : "–")],
    ["Times a human was asked", (s) => (s ? String(s.asked) : "–")],
  ];

  return (
    <section className={`float-panel scoreboard${open ? " open" : ""}`}>
      <button className="score-bar" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="score"><b className={baseline ? "" : "good"}>{answered(now)}</b> calls answered</span>
        <span className="score-sep" aria-hidden />
        <span className="score"><b className="good">{lies(now)}</b> lies caught</span>
        <span className="score-sep" aria-hidden />
        <span className="score"><b className={now && now.wronglyFlagged > 0 ? "bad" : ""}>{wrong(now)}</b> honest units wrongly flagged</span>
        <ChevronUp className="score-chevron" size={16} aria-hidden />
      </button>
      {open && (
        <div className="score-body">
          <table className="score-table">
            <thead>
              <tr>
                <th />
                <th className={!baseline ? "live-col" : ""}>Protected <span className="sub mono">{protectedScore ? `at t${at}` : ""}</span></th>
                <th className={baseline ? "live-col" : ""}>Dumb dispatcher <span className="sub mono">{dumbScore ? `at t${at}` : ""}</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([label, value, hint]) => (
                <tr key={label}>
                  <th scope="row" title={hint}>{label}</th>
                  <td className="mono">{value(protectedScore)}</td>
                  <td className="mono">{value(dumbScore)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="score-foot">
            <label className={`baseline-switch${pending ? " busy" : ""}`} title="Resets the scenario and runs it with one central dispatcher: no votes, no checks">
              <input type="checkbox" role="switch" checked={baseline} disabled={pending} onChange={(e) => setBaseline(e.target.checked)} />
              <span className="switch-track" aria-hidden><span className="switch-thumb" /></span>
              Run as dumb dispatcher
            </label>
            {other ? <span className="hint">Both columns at t{at}, the shorter run’s end.</span> : <span className="hint">Run both modes to fill both columns.</span>}
            {error && <span className="error-text">{error}</span>}
          </div>
        </div>
      )}
    </section>
  );
}
