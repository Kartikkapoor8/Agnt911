// The dispatcher agent's three client tools. They run in the browser against our backend (through the Vite
// /api proxy), so ElevenLabs never needs to reach this machine. Each returns a JSON string for the agent.

import type { Incident, IncidentType, Plan, Vehicle } from "../types";
import { badgeIncident } from "./badge";
import { placeCall, type Placed } from "./geocode";

const BASE = "/api";
const SPEED_M_PER_S = 12; // the engine moves vehicles at 12 m/s (engine/world.py tick)
const DEFAULT_SEVERITY = 4; // a caller who doesn't say how bad it is still gets an urgent call

export interface UpdateIncidentArgs {
  type: IncidentType;
  severity?: 3 | 4 | 5;
  location_text?: string;
  caller_name?: string;
  details?: string;
}

/** What the dispatcher may say about who is coming. Unit counts come only from here. */
export interface DispatchStatus {
  incident_id: string | null;
  status: "no_incident" | "negotiating" | "dispatched" | "on_scene" | "resolved" | "waiting_for_human";
  consensus: boolean;
  assigned: { id: string; type: string; eta_seconds: number }[];
}

/** One phone call's memory: the call it created and whether dispatch was requested. A new call starts fresh. */
export interface CallSession {
  incidentId: string | null;
  creating: Promise<string> | null; // the first update_incident's POST, so a quick second call can't make another
  caller: string;
  details: string;
  place: Placed | null;
  requested: boolean;
}

/** A blank session for a new phone call. */
export function newCallSession(): CallSession {
  return { incidentId: null, creating: null, caller: "", details: "", place: null, requested: false };
}

async function call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().then((b) => b.detail, () => res.statusText);
    throw new Error(`${method} ${path}: ${res.status} ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
  }
  return res.json() as Promise<T>;
}

function label(s: CallSession, type: IncidentType): string {
  const what = s.details || `${type} at ${s.place?.place ?? "unknown location"}`;
  return `${s.caller || "Caller"}: ${what}`.slice(0, 200);
}

function metres(a: { lat: number; lng: number }, b: { lat: number; lng: number }): number {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLng = (b.lng - a.lng) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}

/** Create the session's call on the first use, then only update that same call. */
export async function updateIncident(s: CallSession, args: UpdateIncidentArgs) {
  if (args.caller_name) s.caller = args.caller_name.trim();
  if (args.details) s.details = args.details.trim();
  if (!s.creating) {
    s.creating = (async () => {
      s.place = await placeCall(args.location_text);
      const { lat, lng } = s.place;
      const made = await call<{ incident: string }>("POST", "/incident", {
        type: args.type, severity: args.severity ?? DEFAULT_SEVERITY, location: { lat, lng },
        label: label(s, args.type), source: "caller",
      });
      s.incidentId = made.incident;
      badgeIncident(made.incident);
      return made.incident;
    })();
    s.creating.catch(() => (s.creating = null)); // a failed create may be retried by the next call
    const id = await s.creating;
    return { incident_id: id, label: label(s, args.type), location: s.place };
  }
  const id = await s.creating;
  const change: Record<string, unknown> = { label: label(s, args.type) };
  if (args.severity) change.severity = args.severity;
  if (args.location_text?.trim()) {
    s.place = await placeCall(args.location_text);
    change.location = { lat: s.place.lat, lng: s.place.lng };
  }
  await call("POST", `/incident/${encodeURIComponent(id)}`, change);
  badgeIncident(id);
  return { incident_id: id, label: change.label as string, location: s.place };
}

/** Read who the fleet has sent to the session's call, straight from /state. */
export async function dispatchStatus(s: CallSession): Promise<DispatchStatus> {
  if (!s.incidentId) return { incident_id: null, status: "no_incident", consensus: false, assigned: [] };
  const id = s.incidentId;
  const state = await call<{ incidents: Record<string, Incident>; plans: Record<string, Plan>; vehicles: Record<string, Vehicle> }>("GET", "/state");
  const incident = state.incidents[id];
  const plan = state.plans[id];
  const consensus = plan?.consensus ?? false;
  if (!incident) return { incident_id: id, status: "no_incident", consensus, assigned: [] }; // the run was reset
  if (incident.status === "resolved") return { incident_id: id, status: "resolved", consensus, assigned: [] };
  if (plan?.escalated) return { incident_id: id, status: "waiting_for_human", consensus, assigned: [] };
  const units = incident.assigned.map((vid) => state.vehicles[vid]).filter((v): v is Vehicle => Boolean(v));
  if (!units.length) return { incident_id: id, status: "negotiating", consensus, assigned: [] };
  const assigned = units.map((v) => ({
    id: v.id,
    type: v.type,
    eta_seconds: v.status === "on_scene" && v.target_incident === id ? 0 : Math.round(metres(v.location, incident.location) / SPEED_M_PER_S),
  }));
  const here = units.some((v) => v.status === "on_scene" && v.target_incident === id);
  return { incident_id: id, status: here ? "on_scene" : "dispatched", consensus, assigned };
}

/** Mark dispatch as requested once; asking again only reports where it stands. */
export async function requestDispatch(s: CallSession) {
  if (!s.incidentId) return { status: "no_incident", incident_id: null, note: "Call update_incident first." };
  if (s.requested) {
    const { status } = await dispatchStatus(s);
    return { status, incident_id: s.incidentId };
  }
  s.requested = true; // the fleet already negotiates any open call; this marks the moment the agent says "dispatching"
  return { status: "negotiating", incident_id: s.incidentId };
}

/** The clientTools object for useConversation. Errors go back to the agent as {error} and to onError. */
export function dispatcherTools(session: () => CallSession, onError: (message: string) => void) {
  const wrap = <A,>(fn: (s: CallSession, args: A) => Promise<unknown>) => async (args: A) => {
    try {
      return JSON.stringify(await fn(session(), args));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      onError(message);
      return JSON.stringify({ error: message });
    }
  };
  return {
    update_incident: wrap<UpdateIncidentArgs>(updateIncident),
    request_demo_dispatch: wrap<Record<string, never>>((s) => requestDispatch(s)),
    get_dispatch_status: wrap<Record<string, never>>((s) => dispatchStatus(s)),
  };
}
