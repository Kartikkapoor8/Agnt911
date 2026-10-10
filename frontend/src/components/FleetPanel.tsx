import type { WorldState } from "../types";

interface Props {
  state: WorldState;
  selectedId: string | null;
  onSelect: (vehicleId: string) => void;
}

/** One row per vehicle: ID, type, status, trust; click a row to select it for the hack box. */
export function FleetPanel({ state, selectedId, onSelect }: Props) {
  return (
    <section className="panel fleet-panel">
      <h2>Fleet</h2>
      <table>
        <thead>
          <tr><th>ID</th><th>Type</th><th>Status</th><th>Trust</th></tr>
        </thead>
        <tbody>
          {Object.values(state.vehicles).map((v) => (
            <tr
              key={v.id}
              className={`${v.untrusted ? "untrusted" : ""} ${v.id === selectedId ? "selected" : ""}`}
              onClick={() => onSelect(v.id)}
            >
              <td className="mono">
                {v.id}
                {state.compromised.includes(v.id) && <span className="tag" title="Injected by the judge. Other vehicles don't see this.">hacked</span>}
              </td>
              <td>{v.type}</td>
              <td>
                {v.status.replace("_", " ")}
                {v.target_incident ? ` → ${v.target_incident}` : ""}
              </td>
              <td className="mono">
                {v.trust.toFixed(2)}
                {v.untrusted && " untrusted"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
