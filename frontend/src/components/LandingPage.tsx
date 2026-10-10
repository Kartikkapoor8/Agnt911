import type { Vehicle, VehicleStatus, WorldState } from "../types";
import { MapErrorBoundary } from "./MapErrorBoundary";
import { MapView } from "./MapView";

interface Props {
  state: WorldState;
  stale: boolean; // true when the latest poll failed and we are showing the last good state
  onStart: () => void;
}

const UNIT_NAME: Record<Vehicle["type"], string> = {
  ambulance: "Medic Unit",
  fire: "Engine Company",
  police: "Patrol Unit",
};
const STATUS_NAME: Record<VehicleStatus, string> = {
  free: "Idle",
  en_route: "En route",
  on_scene: "On scene",
};

/** First screen: the pitch and live fleet on the left, the live San Jose map on the right. */
export function LandingPage({ state, stale, onStart }: Props) {
  const vehicles = Object.values(state.vehicles);
  const trusted = vehicles.filter((v) => !v.untrusted).length;
  const consensus = state.mode === "baseline" ? "single dispatcher (baseline)" : "decentralized consensus active";

  return (
    <div className="landing">
      <section className="landing-left">
        <div className="wordmark">Agnt<span>911</span></div>
        <h1>Emergency vehicles that dispatch themselves.</h1>
        <p className="lede">
          Seven vehicle agents negotiate every 911 call. No dispatcher, no boss agent. Any of them can be hacked.
        </p>

        <div className="fleet">
          <label className="mono-label" htmlFor="fleet-select">Fleet overview &amp; status</label>
          <div className="select-wrap">
            <select id="fleet-select" defaultValue="all">
              <option value="all">All fleet vehicles ({vehicles.length} active)</option>
              {vehicles.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.id} · {UNIT_NAME[v.type]} ({STATUS_NAME[v.status]}){v.untrusted ? " · untrusted" : ""}
                </option>
              ))}
            </select>
          </div>
          <div className="fleet-status">
            <span className={`dot${trusted < vehicles.length ? " warn" : ""}`} />
            <span className="mono-label">
              {trusted}/{vehicles.length} agents operational · {consensus}
            </span>
          </div>
        </div>

        <div>
          <button className="start" onClick={onStart}>Start run</button>
        </div>

        <div className="pollinfo">
          tick {state.tick} · {Object.keys(state.incidents).length} incidents
          {stale && <span className="stale"> · backend not responding, showing last state</span>}
        </div>
      </section>

      <section className="landing-right">
        <MapErrorBoundary>
          <MapView vehicles={state.vehicles} incidents={state.incidents} center={state.map_center} labels={state.labels} />
        </MapErrorBoundary>
      </section>
    </div>
  );
}
