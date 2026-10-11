// Road routes for drawing. The backend moves vehicles in straight lines (that is the ETA math); the map shows
// them on real streets. One Mapbox Directions call per (origin, call) pair, cached in memory and localStorage.
// The scripted scenario's routes are prefetched at load, so nothing waits on the network during the demo.
// Any failure falls back to a straight line.

import scenario from "../../backend/agnt911/scenarios/demo.json";
import { distanceM } from "./escalation";
import type { Location } from "./types";

export type LngLat = [number, number];

/** A drawable path with cumulative lengths, for sampling the point at a fraction of the way. */
export interface Path {
  coords: LngLat[];
  cum: number[]; // metres from the start to each coordinate
  length: number;
  road: boolean; // false = straight-line fallback
}

interface ScenarioShape {
  vehicles: { location: Location }[];
  events: { type: string; incident?: { location?: Location } | string }[];
  presets?: { preset: { kind?: string; location?: Location } }[];
}

const TOKEN = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined;
const STORE_KEY = "agnt911.routes.v1";
const SNAP_M = 30; // a trip starting this close to a prefetched origin reuses that origin's routes
const CONCURRENCY = 4;

const cache = new Map<string, LngLat[]>(); // origin;destination -> road coordinates
const inflight = new Map<string, Promise<LngLat[] | null>>();
const failed = new Set<string>(); // asked once and got nothing: don't ask again this session
const anchors: LngLat[] = []; // prefetched origins

const toLoc = ([lng, lat]: LngLat): Location => ({ lng, lat });
const fromLoc = (l: Location): LngLat => [l.lng, l.lat];
const keyOf = (o: LngLat, d: LngLat) => `${o[0].toFixed(5)},${o[1].toFixed(5)};${d[0].toFixed(5)},${d[1].toFixed(5)}`;

try {
  const saved = localStorage.getItem(STORE_KEY);
  if (saved) Object.entries(JSON.parse(saved) as Record<string, LngLat[]>).forEach(([k, v]) => cache.set(k, v));
} catch {
  // private mode or bad JSON: start empty
}

let saveTimer: number | undefined;
function persist() {
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(Object.fromEntries(cache)));
    } catch {
      // storage full or blocked: the in-memory cache still works
    }
  }, 500);
}

/** A prefetched origin within SNAP_M of o, or o itself. */
function snap(o: LngLat): LngLat {
  return anchors.find((a) => distanceM(toLoc(a), toLoc(o)) < SNAP_M) ?? o;
}

async function fetchRoad(o: LngLat, d: LngLat): Promise<LngLat[] | null> {
  if (!TOKEN) return null;
  const url = `https://api.mapbox.com/directions/v5/mapbox/driving/${o[0]},${o[1]};${d[0]},${d[1]}?geometries=geojson&overview=full&access_token=${TOKEN}`;
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    const coords = (await res.json())?.routes?.[0]?.geometry?.coordinates;
    return Array.isArray(coords) && coords.length >= 2 ? (coords as LngLat[]) : null;
  } catch {
    return null;
  }
}

/** The cached road route from o to d, if there is one. */
export function cachedRoute(o: LngLat, d: LngLat): LngLat[] | null {
  return cache.get(keyOf(snap(o), d)) ?? null;
}

/** The road route from o to d: cached, or fetched once (concurrent callers share the request). null = use a straight line. */
export function getRoute(o: LngLat, d: LngLat): Promise<LngLat[] | null> {
  const from = snap(o);
  const key = keyOf(from, d);
  const hit = cache.get(key);
  if (hit) return Promise.resolve(hit);
  if (failed.has(key)) return Promise.resolve(null);
  let p = inflight.get(key);
  if (!p) {
    p = fetchRoad(from, d).then((road) => {
      inflight.delete(key);
      if (road) {
        cache.set(key, road);
        persist();
      } else failed.add(key);
      return road;
    });
    inflight.set(key, p);
  }
  return p;
}

/** A path from the vehicle's real position, along the road, to the exact call location. */
export function makePath(origin: LngLat, dest: LngLat, road: LngLat[] | null): Path {
  const coords: LngLat[] = road ? [origin, ...road, dest] : [origin, dest];
  const cum = [0];
  for (let i = 1; i < coords.length; i++) cum.push(cum[i - 1] + distanceM(toLoc(coords[i - 1]), toLoc(coords[i])));
  return { coords, cum, length: cum[cum.length - 1], road: Boolean(road) };
}

/** The point at fraction f (0-1) of the way along the path, and the index of the segment it is on. */
export function pointAt(p: Path, f: number): { at: LngLat; seg: number } {
  if (p.length === 0) return { at: p.coords[p.coords.length - 1], seg: p.coords.length - 2 };
  const want = Math.max(0, Math.min(1, f)) * p.length;
  let i = 1;
  while (i < p.cum.length - 1 && p.cum[i] < want) i++;
  const span = p.cum[i] - p.cum[i - 1];
  const k = span > 0 ? (want - p.cum[i - 1]) / span : 1;
  const a = p.coords[i - 1];
  const b = p.coords[i];
  return { at: [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k], seg: i - 1 };
}

/** What is left of the path from fraction f: drawn as the route line, so it shortens as the vehicle advances. */
export function remaining(p: Path, f: number): LngLat[] {
  const { at, seg } = pointAt(p, f);
  return [at, ...p.coords.slice(seg + 1)];
}

let prefetched = false;

/** Fetch every (vehicle start or call) -> call route in the scripted scenario, a few at a time. Runs once. */
export function prefetchScenario(): void {
  if (prefetched || !TOKEN) return;
  prefetched = true;
  const s = scenario as unknown as ScenarioShape;
  const calls: LngLat[] = [];
  s.events.forEach((e) => {
    if (e.type === "incident" && typeof e.incident === "object" && e.incident?.location) calls.push(fromLoc(e.incident.location));
  });
  (s.presets ?? []).forEach((p) => p.preset.kind === "fake_call" && p.preset.location && calls.push(fromLoc(p.preset.location)));
  const origins = [...s.vehicles.map((v) => fromLoc(v.location)), ...calls];
  anchors.push(...origins);
  const jobs: [LngLat, LngLat][] = [];
  origins.forEach((o) => calls.forEach((d) => distanceM(toLoc(o), toLoc(d)) > 1 && jobs.push([o, d])));
  let next = 0;
  const worker = async () => {
    while (next < jobs.length) {
      const [o, d] = jobs[next++];
      await getRoute(o, d);
    }
  };
  for (let i = 0; i < CONCURRENCY; i++) void worker();
}
