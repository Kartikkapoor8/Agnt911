import { useState } from "react";
import { ChevronUp } from "lucide-react";
import type { Mode } from "../api";
import { compromisedIn } from "../runs";
import type { Metrics, WorldState } from "../types";

interface Props {
  state: WorldState;
  onSetMode: (mode: Mode) => Promise<void>;
}

interface Run {
  title: string;
  metrics: Partial<Metrics>;
  calls: number; // calls in the run so far
  hacked: string[] | null; // null: this browser didn't watch the run, so lies can't be told from mistakes
}

const flagged = (m: Partial<Metrics>) => Object.keys(m.first_untrusted_tick ?? {});
const caught = (r: Run) => (r.hacked ? flagged(r.metrics).filter((v) => r.hacked!.includes(v)).length : null);
const wrongly = (r: Run) => (r.hacked ? flagged(r.metrics).filter((v) => !r.hacked!.includes(v)).length : null);
const n = (x: number | null | undefined) => (typeof x === "number" ? String(x) : "–");

/** The three numbers that tell the story: calls answered, lies caught, honest units wrongly flagged. */
function headline(r: Run): [string, string, string] {
  const lies = caught(r);
  return [
    `${n(r.metrics.incidents_served)}/${r.calls}`,
    r.hacked ? `${n(lies)}/${r.hacked.length}` : "?",
    n(wrongly(r)),
  ];
}

/** Scoreboard, bottom centre: a thin bar with this run's three numbers; open it for Protected vs Dumb dispatcher. */
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
  const live: Run = { title: baseline ? "Dumb dispatcher" : "Protected", metrics: state.metrics, calls: Object.keys(state.incident_timing).length, hacked: state.compromised };
  const last = [...state.comparison].reverse().find((c) => c.baseline !== baseline);
  const other: Run | null = last
    ? { title: last.baseline ? "Dumb dispatcher" : "Protected", metrics: last.metrics, calls: Object.keys(last.incident_timing).length, hacked: compromisedIn(last.run_id) }
    : null;
  const protectedRun = baseline ? other : live;
  const dumbRun = baseline ? live : other;
  const [answered, lies, wrong] = headline(live);

  const rows: [string, (r: Run) => string, string?][] = [
    ["Calls answered", (r) => headline(r)[0]],
    ["Lies caught", (r) => headline(r)[1], "Hacked vehicles the fleet stopped trusting, of those hacked"],
    ["Honest units wrongly flagged", (r) => headline(r)[2]],
    ["Avg response (s)", (r) => (typeof r.metrics.mean_response_ticks === "number" ? (r.metrics.mean_response_ticks * secondsPerTick).toFixed(1) : "–")],
    ["Times a human was asked", (r) => n(r.metrics.escalations)],
  ];

  return (
    <section className={`float-panel scoreboard${open ? " open" : ""}`}>
      <button className="score-bar" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="score"><b className={baseline ? "" : "good"}>{answered}</b> calls answered</span>
        <span className="score-sep" aria-hidden />
        <span className="score"><b className="good">{lies}</b> lies caught</span>
        <span className="score-sep" aria-hidden />
        <span className="score"><b className={wrong !== "0" && wrong !== "–" ? "bad" : ""}>{wrong}</b> honest units wrongly flagged</span>
        <ChevronUp className="score-chevron" size={16} aria-hidden />
      </button>
      {open && (
        <div className="score-body">
          <table className="score-table">
            <thead>
              <tr>
                <th />
                <th className={!baseline ? "live-col" : ""}>Protected{!baseline && <span className="sub"> this run</span>}</th>
                <th className={baseline ? "live-col" : ""}>Dumb dispatcher{baseline && <span className="sub"> this run</span>}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([label, value, hint]) => (
                <tr key={label}>
                  <th scope="row" title={hint}>{label}</th>
                  <td className="mono">{protectedRun ? value(protectedRun) : "–"}</td>
                  <td className="mono">{dumbRun ? value(dumbRun) : "–"}</td>
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
            {!other && <span className="hint">Run both modes to fill both columns.</span>}
            {error && <span className="error-text">{error}</span>}
          </div>
        </div>
      )}
    </section>
  );
}
