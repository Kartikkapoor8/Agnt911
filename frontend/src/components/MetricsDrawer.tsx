import { useState } from "react";
import type { Mode } from "../api";
import { compromisedIn } from "../runs";
import type { Metrics, WorldState } from "../types";
import { Panel } from "./Panel";

interface Props {
  state: WorldState;
  onSetMode: (mode: Mode) => Promise<void>;
}

interface Column {
  key: string;
  title: string;
  sub: string;
  metrics: Partial<Metrics>;
  compromised: string[] | null; // null: this browser didn't see the run, so lies can't be told from mistakes
  baseline: boolean;
}

type Row = [label: string, value: (m: Partial<Metrics>, c: Column) => string, hint?: string];

const dash = (x: number | null | undefined) => (typeof x === "number" ? String(x) : "–");
const flagged = (m: Partial<Metrics>) => Object.keys(m.first_untrusted_tick ?? {});

/** The table rows, worded for someone who doesn't read code. secondsPerTick converts the response time. */
function rows(secondsPerTick: number): Row[] {
  return [
    ["Calls answered", (m) => dash(m.incidents_served)],
    ["Calls left unanswered", (m) => dash(m.unserved)],
    ["Calls closed with nobody sent", (m) => dash(m.incidents_false_handled)],
    ["Avg response (s)", (m) => (typeof m.mean_response_ticks === "number" ? (m.mean_response_ticks * secondsPerTick).toFixed(1) : "–")],
    ["Lies caught", (m, c) => (c.compromised ? String(flagged(m).filter((v) => c.compromised!.includes(v)).length) : "?"), "Hacked vehicles the fleet stopped trusting"],
    ["Honest units wrongly flagged", (m, c) => (c.compromised ? String(flagged(m).filter((v) => !c.compromised!.includes(v)).length) : "?"), "Vehicles that were not hacked but lost the fleet's trust"],
    ["Times it asked a human", (m) => dash(m.escalations)],
  ];
}

const MODE_LABEL: Record<Mode, string> = { protected: "Protected", baseline: "Dumb dispatcher" };

/** Bottom drawer, collapsed by default: this run next to the latest run of the other mode, the baseline switch, and history. */
export function MetricsDrawer({ state, onSetMode }: Props) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState(false);

  async function setBaseline(on: boolean) {
    setPending(true);
    setError(null);
    try {
      await onSetMode(on ? "baseline" : "protected");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  const live: Column = { key: "live", title: `This run · ${MODE_LABEL[state.mode]}`, sub: `t${state.tick}`, metrics: state.metrics, compromised: state.compromised, baseline: state.mode === "baseline" };
  // comparison only fills on a mode switch, oldest first; the newest run of the other mode is the one to beat.
  const finished: Column[] = [...state.comparison].reverse().map((c) => ({
    key: c.run_id,
    title: c.baseline ? MODE_LABEL.baseline : MODE_LABEL.protected,
    sub: c.run_id.slice(0, 6),
    metrics: c.metrics,
    compromised: compromisedIn(c.run_id),
    baseline: c.baseline,
  }));
  const other = finished.find((c) => c.baseline !== live.baseline);
  const rest = finished.filter((c) => c !== other);
  const columns = [live, ...(other ? [other] : []), ...(history ? rest : [])];
  const secondsPerTick = state.tick > 0 && state.time_s > 0 ? state.time_s / state.tick : 1;

  const toggle = (
    <label className={`baseline-switch${pending ? " busy" : ""}`} title="Restarts the scenario with one central dispatcher: no votes, no checks">
      <input type="checkbox" role="switch" checked={state.mode === "baseline"} disabled={pending} onChange={(e) => setBaseline(e.target.checked)} />
      <span className="switch-track" aria-hidden><span className="switch-thumb" /></span>
      Compare: dumb dispatcher
    </label>
  );

  return (
    <Panel className="metrics-drawer" title="Metrics" actions={toggle} collapsible defaultCollapsed>
      <div className="metrics-body">
        {error && <p className="error-text">{error}</p>}
        <table className="metrics-table">
          <thead>
            <tr>
              <th />
              {columns.map((c) => (
                <th key={c.key} className={c.key === "live" ? "live-col" : ""}>
                  {c.title} <span className="mono sub">{c.sub}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows(secondsPerTick).map(([label, value, hint]) => (
              <tr key={label}>
                <th scope="row" title={hint}>{label}</th>
                {columns.map((c) => (
                  <td key={c.key} className="mono">{value(c.metrics, c)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <div className="metrics-foot">
          {!other && <p className="hint">Flip “Compare: dumb dispatcher” to run the same calls with one central dispatcher, then compare.</p>}
          {rest.length > 0 && (
            <button className="history-toggle" aria-expanded={history} onClick={() => setHistory(!history)}>
              {history ? "Hide history" : `History (${rest.length} more run${rest.length === 1 ? "" : "s"})`}
            </button>
          )}
          {columns.some((c) => !c.compromised) && <p className="hint">? = this browser didn't watch that run, so it can't tell lies from mistakes.</p>}
        </div>
      </div>
    </Panel>
  );
}
