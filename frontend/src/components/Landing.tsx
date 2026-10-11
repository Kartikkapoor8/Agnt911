import { useEffect, useRef } from "react";
import { Glyph } from "../glyphs";
import type { VehicleType, WorldState } from "../types";
import { MapErrorBoundary } from "./MapErrorBoundary";
import { MapView, type StaticCamera } from "./MapView";

/**
 * The landing map's fixed view. Tune here. It has to hold every vehicle, on the roads it drives, and every call
 * for the whole scripted run (checked at 1440x900 and 1280x720).
 */
export const LANDING_CAMERA: StaticCamera = { center: [-121.8885, 37.335], zoom: 14.5 };

interface Props {
  state: WorldState;
  leaving: boolean; // fading out (400 ms) into the live map
  starting: boolean; // the fresh run is on its way
  onStart: () => void;
  onRestart: () => Promise<void>; // a fresh Protected run, so the map beside the card is never standing still
}

const WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];
/** "Seven", from the live fleet size (digits past twelve). */
const count = (n: number) => WORDS[n] ?? String(n);

// The order the chips always take: ambulances, fire engines, police cars.
const TYPE_ORDER: Record<VehicleType, number> = { ambulance: 0, fire: 1, police: 2 };

const IDLE_MS = 2500; // how long the fleet may stand still (or the run stay paused) before the demo restarts
// The scripted run has quiet gaps (nothing en route between t54 and t100), so standing still only means "finished"
// once it is past the last scripted event (the second call at t140) and that call has been served.
const FINISHED_TICK = 170;
const RESTART_GAP_MS = 6000; // never restart more often than this

/** Landing: a 40% card (wordmark, headline, description, the seven vehicles, Start run) beside a static 60% map of the live fleet. */
export function Landing({ state, leaving, starting, onStart, onRestart }: Props) {
  const fleet = Object.values(state.vehicles)
    .map((v) => ({ id: v.id, type: v.type }))
    .sort((a, b) => TYPE_ORDER[a.type] - TYPE_ORDER[b.type] || a.id.localeCompare(b.id));
  const moving = Object.values(state.vehicles).some((v) => v.status === "en_route");
  const lastRestart = useRef(0);
  const idleSince = useRef<number | null>(null);

  // Only this landing and Start demo start a fresh run, so the restart waits for the scripted run to finish (or stop
  // for a human) and never fires while Start is already creating the real run.
  useEffect(() => {
    if (starting || leaving) return;
    const stuck = state.paused || (state.tick >= FINISHED_TICK && !moving);
    if (!stuck) {
      idleSince.current = null;
      return;
    }
    const now = Date.now();
    idleSince.current ??= now;
    if (now - idleSince.current >= IDLE_MS && now - lastRestart.current >= RESTART_GAP_MS) {
      lastRestart.current = now;
      idleSince.current = null;
      void onRestart().catch(() => {});
    }
  }, [state, moving, starting, leaving, onRestart]);

  return (
    <div className={`landing${leaving ? " leaving" : ""}`}>
      <section className="landing-left">
        <h1 className="landing-wordmark">Agnt<span>911</span></h1>
        <h2 className="landing-headline">Dispatch support for 911 operators.</h2>
        <p className="landing-desc">Drop an emergency on the map and {count(fleet.length).toLowerCase()} vehicle agents negotiate who responds. They catch hijacked vehicles and fake calls, and you step in when they can't agree.</p>

        <span className="landing-label">Fleet</span>
        <ul className="landing-chips" aria-label="Fleet">
          {fleet.map((v) => (
            <li key={v.id} className="landing-chip">
              <Glyph name={v.type} size={16} />
              <b>{v.id}</b>
            </li>
          ))}
        </ul>
        <p className="landing-note">Simulated future fleet: every vehicle is rich in sensors and data, and acts as its own AI agent.</p>

        <button className="start-demo" onClick={onStart} disabled={starting || leaving}>
          {starting ? "Starting…" : "Start run"}
        </button>
      </section>

      <section className="landing-right">
        <MapErrorBoundary>
          <MapView
            vehicles={state.vehicles}
            incidents={state.incidents}
            center={state.map_center}
            labels={state.labels}
            tick={state.tick}
            staticCamera={LANDING_CAMERA}
          />
        </MapErrorBoundary>
      </section>
    </div>
  );
}
