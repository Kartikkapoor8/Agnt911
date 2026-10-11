// Where to draw a vehicle that is on its way to a call. The backend's straight-line progress (distance covered
// over the straight-line distance at the start of the trip) is mapped onto the road path, so the marker reaches
// the pin when the backend says it arrived. Between polls, and while a round freezes the backend clock, the
// marker keeps moving on the client clock, slowing as it gets ahead, and never moves backwards.

import { distanceM } from "./escalation";
import { makePath, type LngLat, type Path } from "./routes";
import type { Location } from "./types";

const SPEED_M_PER_TICK = 12; // engine world.tick default: 12 m/s, one simulated second per tick
const MAX_LEAD_TICKS = 15; // never draw a vehicle more than this many ticks ahead of the backend
const NO_EARLY_ARRIVAL = 0.985; // stay short of the pin until the backend gets there

export interface Trip {
  incident: string;
  origin: LngLat; // where the marker was drawn when the trip began
  dest: LngLat;
  totalM: number; // straight-line metres from the backend position at the start to the call
  path: Path;
  pBackend: number; // backend progress, 0-1
  pShown: number; // drawn progress, 0-1
  perTick: number; // progress per tick, estimated from the backend
  lastTick: number;
  lastP: number;
}

const toLoc = ([lng, lat]: LngLat): Location => ({ lng, lat });

function progress(trip: Trip, at: Location): number {
  if (trip.totalM <= 0) return 1;
  return Math.max(0, Math.min(1, 1 - distanceM(at, toLoc(trip.dest)) / trip.totalM));
}

/** Begin a trip from where the marker is drawn now, on a straight line until the road route arrives. */
export function startTrip(incident: string, dest: LngLat, origin: LngLat, backendAt: Location, tick: number, road: LngLat[] | null): Trip {
  const totalM = distanceM(backendAt, toLoc(dest));
  const trip: Trip = {
    incident,
    origin,
    dest,
    totalM,
    path: makePath(origin, dest, road),
    pBackend: 0,
    pShown: 0,
    perTick: totalM > 0 ? SPEED_M_PER_TICK / totalM : 1,
    lastTick: tick,
    lastP: 0,
  };
  return trip;
}

/** A new poll: update the backend progress and the per-tick estimate. */
export function updateTrip(trip: Trip, backendAt: Location, tick: number): void {
  const p = progress(trip, backendAt);
  if (tick > trip.lastTick && p > trip.lastP) {
    const observed = (p - trip.lastP) / (tick - trip.lastTick);
    trip.perTick = 0.5 * trip.perTick + 0.5 * observed;
  }
  if (tick !== trip.lastTick) {
    trip.lastTick = tick;
    trip.lastP = p;
  }
  trip.pBackend = p;
}

/**
 * Advance the drawn progress by dt seconds. ticksPerSecond is how fast the backend clock runs; frozen is true while
 * a round holds the clock. Pace is full speed when level with the backend, faster when behind, slower when ahead.
 */
export function advanceTrip(trip: Trip, dtS: number, ticksPerSecond: number, frozen: boolean): void {
  if (trip.perTick <= 0) return;
  const lead = (trip.pShown - trip.pBackend) / trip.perTick; // ticks ahead of the backend
  let pace = lead < 0 ? 1 + Math.min(3, -lead * 1.5) : Math.max(0.3, 1.25 - 0.45 * lead);
  if (frozen && lead >= 0) pace = Math.min(pace, 0.5);
  const cap = Math.min(trip.pBackend >= NO_EARLY_ARRIVAL ? 1 : NO_EARLY_ARRIVAL, trip.pBackend + MAX_LEAD_TICKS * trip.perTick);
  const next = Math.min(cap, trip.pShown + trip.perTick * ticksPerSecond * pace * dtS);
  trip.pShown = Math.max(trip.pShown, next);
}
