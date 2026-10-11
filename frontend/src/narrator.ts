// The status strip's one sentence: what is happening right now, so a judge never needs narration.
// Pure: /state in, sentence out. Highest priority first.

import type { TypedEvent, WorldState } from "./types";

export type NarrationTone = "human" | "fleet" | "alarm" | "calm";

export interface Narration {
  text: string;
  tone: NarrationTone; // amber, blue, red, grey
  pulse: boolean; // a round is running
}

const RECENT_CATCH_TICKS = 6;
const RECENT_DECISION_TICKS = 3;

/** "I3 · Two-car crash, Almaden & San Fernando", or just the id. */
export function callName(state: WorldState, id: string): string {
  return state.labels[id] ? `${id} · ${state.labels[id]}` : id;
}

function lastEvent(state: WorldState, match: (e: TypedEvent) => boolean): TypedEvent | null {
  for (let i = state.events.length - 1; i >= 0; i--) if (match(state.events[i])) return state.events[i];
  return null;
}

/** The calls a running round is probably deciding: unassigned calls first, else calls whose latest event is a new call or escalation. */
export function callsInRound(state: WorldState): string[] {
  const open = Object.values(state.incidents).filter((i) => i.status === "open").map((i) => i.id);
  if (open.length) return open;
  const fresh = lastEvent(state, (e) => e.type === "call_received" || e.type === "call_escalated");
  return fresh?.incident && state.incidents[fresh.incident]?.status !== "resolved" ? [fresh.incident] : [];
}

const list = (ids: string[]) => (ids.length <= 1 ? ids.join("") : `${ids.slice(0, -1).join(", ")} and ${ids[ids.length - 1]}`);

/** The one sentence for the status strip. */
export function narrate(state: WorldState): Narration {
  const tick = state.tick;
  const running = state.integration.last_round_status === "running";
  const vehicles = Object.values(state.vehicles);
  const untrusted = vehicles.filter((v) => v.untrusted).map((v) => v.id);
  const open = Object.values(state.incidents).filter((i) => i.status !== "resolved");

  if (state.paused) {
    const [plan] = state.escalation?.plans ?? [];
    return { text: plan ? `Waiting for a human: ${callName(state, plan.incident)}` : "Waiting for a human decision", tone: "human", pulse: false };
  }
  if (running) {
    const calls = callsInRound(state);
    const what = calls.length === 1 ? callName(state, calls[0]) : calls.length ? list(calls) : "the open calls";
    return { text: `Fleet negotiating ${what}`, tone: "fleet", pulse: true };
  }
  const removed = lastEvent(state, (e) => e.type === "vehicle_untrusted");
  if (removed && tick - removed.tick <= RECENT_CATCH_TICKS) {
    return { text: `${removed.vehicle} caught lying, removed from voting`, tone: "alarm", pulse: false };
  }
  const caught = lastEvent(state, (e) => e.type === "claim_verified" && e.data.result === "contradicted");
  if (caught && tick - caught.tick <= RECENT_CATCH_TICKS) {
    const by = typeof caught.data.verifier === "string" ? caught.data.verifier : "The fleet";
    return { text: `${by} checked ${caught.vehicle}: FALSE, trust dropping`, tone: "alarm", pulse: false };
  }
  const suspect = state.compromised.filter((id) => !state.vehicles[id]?.untrusted);
  if (suspect.length && state.mode === "protected") {
    return { text: `${list(suspect)} compromised, fleet verifying`, tone: "alarm", pulse: false };
  }
  const decided = lastEvent(state, (e) => e.type === "plan_committed" || e.type === "plan_no_consensus");
  if (decided?.incident && tick - decided.tick <= RECENT_DECISION_TICKS) {
    const sent = Array.isArray(decided.data.assigned) ? (decided.data.assigned as string[]) : [];
    const how = state.mode === "baseline" ? "dispatcher sent" : decided.data.reason ? String(decided.data.reason).replace(/^pulled/, "fleet pulled") + ":" : "fleet sent";
    return { text: `${callName(state, decided.incident)}: ${how} ${sent.length ? list(sent) : "nobody yet"}`, tone: "fleet", pulse: false };
  }
  const trust = state.mode === "baseline" ? "no checks (dumb dispatcher)" : untrusted.length ? `${list(untrusted)} untrusted` : "all trusted";
  const calls = `${open.length} call${open.length === 1 ? "" : "s"} open`;
  return { text: `${vehicles.length} units, ${calls}, ${trust}`, tone: "calm", pulse: false };
}

const ACTIVE_TICKS = 4;

/** Calls whose pin shows its label without hover: being decided, waiting on a human, unassigned, or just decided. */
export function activeCalls(state: WorldState): string[] {
  const live = (id: string | null): id is string => Boolean(id && state.incidents[id] && state.incidents[id].status !== "resolved");
  const ids = new Set<string>();
  Object.values(state.incidents).forEach((i) => i.status === "open" && ids.add(i.id));
  (state.escalation?.plans ?? []).forEach((p) => ids.add(p.incident));
  if (state.integration.last_round_status === "running") callsInRound(state).forEach((id) => ids.add(id));
  for (let i = state.events.length - 1; i >= 0; i--) {
    const e = state.events[i];
    if (state.tick - e.tick > ACTIVE_TICKS) break;
    if (["call_received", "call_escalated", "plan_committed", "plan_no_consensus", "escalated_to_human"].includes(e.type) && live(e.incident)) ids.add(e.incident);
  }
  return [...ids].filter(live);
}
