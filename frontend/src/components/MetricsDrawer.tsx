import { useState } from "react";
import type { Mode } from "../api";
import type { Metrics, WorldState } from "../types";
import { Panel } from "./Panel";

interface Props {
  state: WorldState;
  onSetMode: (mode: Mode) => Promise<void>;
}

type Row = [label: string, value: (m: Partial<Metrics>) => string];

const dash = (x: number | null | undefined, digits = 0) => (typeof x === "number" ? x.toFixed(digits) : "–");

const ROWS: Row[] = [
  ["Calls served", (m) => dash(m.incidents_served)],
  ["Unserved", (m) => dash(m.unserved)],
  ["Closed with nobody sent", (m) => dash(m.incidents_false_handled)],
  ["Mean response (ticks)", (m) => dash(m.mean_response_ticks, 1)],
  ["Claims checked", (m) => dash(m.checks_total)],
  ["Asked a human", (m) => dash(m.escalations)],
  [
    "Liars caught",
    (m) => {
      const caught = Object.entries(m.first_untrusted_tick ?? {});
      return caught.length ? caught.map(([v, t]) => `${v} t${t}`).join(", ") : "none";
    },
  ],
];

const MODE_LABEL: Record<Mode, string> = { protected: "Protected", baseline: "Baseline" };

/** Bottom drawer, collapsed by default: switch protected/baseline (POST /mode restarts the run) and compare runs. */
export function MetricsDrawer({ state, onSetMode }: Props) {
  const [pending, setPending] = useState<Mode | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  // The current run first, then finished runs newest first (comparison only fills on a mode switch).
  const columns = [
    { key: "live", title: `This run · ${MODE_LABEL[state.mode]}`, sub: `t${state.tick}`, metrics: state.metrics },
    ...[...state.comparison].reverse().map((c) => ({
      key: c.run_id,
      title: c.baseline ? "Baseline" : "Protected",
      sub: c.run_id.slice(0, 6),
      metrics: c.metrics,
    })),
  ];

  const toggle = (
    <div className="mode-toggle" title="Switching mode restarts the scenario">
      {(["protected", "baseline"] as Mode[]).map((m) => (
        <button key={m} className={state.mode === m ? "active" : ""} disabled={pending !== null || state.mode === m} onClick={() => choose(m)}>
          {MODE_LABEL[m]}
          {pending === m ? "…" : ""}
        </button>
      ))}
    </div>
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
            {ROWS.map(([label, value]) => (
              <tr key={label}>
                <th scope="row">{label}</th>
                {columns.map((c) => (
                  <td key={c.key} className="mono">{value(c.metrics)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {state.comparison.length === 0 && <p className="hint">Switch mode to file this run and compare it with the next.</p>}
      </div>
    </Panel>
  );
}
