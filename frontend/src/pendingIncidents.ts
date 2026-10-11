// Calls the judge drops on the map. The pin shows at once in a "pending" style; it is dropped when a poll brings
// a matching incident, or when the POST fails (with a toast saying why).

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, postIncident } from "./api";
import { distanceM } from "./escalation";
import { toast } from "./toast";
import type { Incident, IncidentType, Location } from "./types";

export const DROP_SEVERITY = 4;
const MATCH_M = 25; // a new incident of the same type this close to the drop point is the confirmation
const GIVE_UP_MS = 15000; // accepted but never showed up in /state

export interface PendingIncident {
  key: string;
  type: IncidentType;
  severity: number;
  location: Location;
  label: string;
  sentAt: number;
  accepted: boolean; // the POST returned 2xx; waiting for a poll
}

let seq = 0;

/** Optimistic drops: returns the pins still waiting and a drop(type, location) function. */
export function usePendingIncidents(incidents: Record<string, Incident>) {
  const [pending, setPending] = useState<PendingIncident[]>([]);
  const known = useRef(new Set(Object.keys(incidents))); // incidents that existed before each drop

  const drop = useCallback(async (type: IncidentType, location: Location) => {
    const label = `${type[0].toUpperCase()}${type.slice(1)} call dropped by the judge`;
    const p: PendingIncident = { key: `drop-${++seq}`, type, severity: DROP_SEVERITY, location, label, sentAt: Date.now(), accepted: false };
    setPending((list) => [...list, p]);
    try {
      await postIncident({ type, severity: DROP_SEVERITY, location, label });
      setPending((list) => list.map((x) => (x.key === p.key ? { ...x, accepted: true } : x)));
    } catch (err) {
      setPending((list) => list.filter((x) => x.key !== p.key));
      if (err instanceof ApiError && err.status === 404) toast("POST /incident is not on the backend yet (404), so the call was not placed.", "error");
      else toast(`Could not place the call: ${err instanceof Error ? err.message : String(err)}`, "error");
    }
  }, []);

  // Each poll: a new incident of the same type near a pending drop confirms it. (Toasts stay out of the state updater,
  // which StrictMode runs twice.)
  const pendingNow = useRef(pending);
  pendingNow.current = pending;
  useEffect(() => {
    const fresh = Object.values(incidents).filter((i) => !known.current.has(i.id));
    fresh.forEach((i) => known.current.add(i.id));
    const now = Date.now();
    const done = new Set<string>();
    for (const p of pendingNow.current) {
      if (fresh.some((i) => i.type === p.type && distanceM(i.location, p.location) < MATCH_M)) {
        done.add(p.key);
        toast(`Placed a ${p.type} call.`, "ok");
      } else if (p.accepted && now - p.sentAt > GIVE_UP_MS) {
        done.add(p.key);
        toast(`The ${p.type} call was accepted but never appeared.`, "error");
      }
    }
    if (done.size) setPending((list) => list.filter((x) => !done.has(x.key)));
  }, [incidents]);

  return { pending, drop };
}
