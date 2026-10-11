// Where a caller's words land on the map: Mapbox forward geocoding, kept inside the demo map.

import type { Location } from "../types";

/** The demo map, with a margin around every scripted vehicle and call (scenarios/demo.json). */
export const DEMO_BOUNDS = { west: -121.9, south: 37.326, east: -121.876, north: 37.347 };

/** Used when the caller gives no place, Mapbox finds nothing, or the match is off the demo map. */
export const DEFAULT_CALL_LOCATION = { lat: 37.334306, lng: -121.889232, place: "1st St & San Fernando St" };

const MAP_CENTER = { lat: 37.3352, lng: -121.8881 };
const TOKEN = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined;

export interface Placed extends Location {
  place: string; // what the dispatcher can read back to the caller
  fallback: boolean; // true when DEFAULT_CALL_LOCATION was used
}

/** True if a point is on the demo map. */
export function inDemoBounds(p: Location): boolean {
  return p.lat >= DEMO_BOUNDS.south && p.lat <= DEMO_BOUNDS.north && p.lng >= DEMO_BOUNDS.west && p.lng <= DEMO_BOUNDS.east;
}

// Matches this precise or better count. Coarser ones ("San Jose", "Downtown San Jose", the county) are what
// Mapbox returns when it didn't understand the street, so they count as no match.
const PRECISE = new Set(["address", "secondary_address", "street", "block", "neighborhood"]);

/** Turn what the caller said into a point on the demo map, or the default corner. Never throws. */
export async function placeCall(text: string | undefined): Promise<Placed> {
  const fallback: Placed = { ...DEFAULT_CALL_LOCATION, fallback: true };
  if (!text?.trim() || !TOKEN) return fallback;
  const b = DEMO_BOUNDS;
  const q = /san jose/i.test(text) ? text : `${text}, San Jose, CA`;
  const url =
    "https://api.mapbox.com/search/geocode/v6/forward" +
    `?q=${encodeURIComponent(q)}&limit=1&country=us&bbox=${b.west},${b.south},${b.east},${b.north}` +
    `&proximity=${MAP_CENTER.lng},${MAP_CENTER.lat}&access_token=${TOKEN}`;
  try {
    const res = await fetch(url);
    if (!res.ok) return fallback;
    const feature = (await res.json()).features?.[0];
    const [lng, lat] = feature?.geometry?.coordinates ?? [];
    if (!PRECISE.has(feature?.properties?.feature_type)) return fallback;
    if (typeof lat !== "number" || typeof lng !== "number" || !inDemoBounds({ lat, lng })) return fallback;
    return { lat, lng, place: feature.properties?.name ?? text, fallback: false };
  } catch {
    return fallback;
  }
}
