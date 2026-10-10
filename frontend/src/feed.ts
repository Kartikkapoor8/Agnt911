// The event feed: one short line per typed event (templates follow docs/events.md), newest first.
// Lines live in a module store, not in a component, so events that arrive before the feed panel
// mounts (the landing page, a collapsed panel) are still there when it renders.

import { useSyncExternalStore } from "react";
import type { EventType, TypedEvent } from "./types";

/** Which family a line belongs to; checks and trust get their own look. */
export type FeedGroup = "call" | "bid" | "vote" | "plan" | "check" | "trust" | "hack" | "human" | "scene";

/** good / bad / unknown colour the check and trust lines; plain is everything else. */
export type FeedTone = "plain" | "good" | "bad" | "unknown" | "alert";

export interface FeedLine {
  key: string; // unique within the run
  tick: number;
  type: EventType;
  group: FeedGroup;
  tone: FeedTone;
  who: string; // what leads the line: a vehicle id, an incident id, or "Human"
  vehicle: string | null; // clicking the line highlights this vehicle...
  incident: string | null; // ...or this incident when there is no vehicle
  text: string; // the rest of the line
}

const MAX_LINES = 1000;

const num = (x: unknown): number | null => (typeof x === "number" && Number.isFinite(x) ? x : null);
const str = (x: unknown): string | null => (typeof x === "string" && x.length > 0 ? x : null);
const list = (x: unknown): string[] => (Array.isArray(x) ? x.filter((v): v is string => typeof v === "string") : []);
const words = (s: string) => s.replace(/_/g, " ");
const trust = (x: unknown) => (num(x) ?? 0).toFixed(2);

const GROUP: Record<EventType, FeedGroup> = {
  call_received: "call",
  call_escalated: "call",
  bid_placed: "bid",
  bid_withdrawn: "bid",
  vote_cast: "vote",
  plan_committed: "plan",
  plan_no_consensus: "plan",
  vehicle_compromised: "hack",
  claim_verified: "check",
  trust_changed: "trust",
  vehicle_untrusted: "trust",
  vehicle_trusted_again: "trust",
  escalated_to_human: "human",
  human_resolved: "human",
  vehicle_arrived: "scene",
  incident_resolved: "scene",
  incident_discredited: "scene",
};

/** The line's lead and text for one event, following the per-type notes in docs/events.md. */
export function describeEvent(e: TypedEvent): { who: string; text: string; tone: FeedTone } {
  const d = e.data;
  const v = e.vehicle ?? "?";
  const inc = e.incident ?? "?";
  switch (e.type) {
    case "call_received":
      return { who: inc, tone: "alert", text: `new call, sev ${num(d.severity) ?? "?"}: ${str(d.label) ?? str(d.incident_type) ?? "unlabelled"}` };
    case "call_escalated":
      return { who: inc, tone: "alert", text: `escalated to sev ${num(d.severity) ?? "?"}${str(d.label) ? `: ${d.label}` : ""}` };
    case "bid_placed":
      return { who: v, tone: "plain", text: `${d.revised ? "revises its bid" : "bids"} on ${inc}, ETA ${num(d.eta_seconds) ?? "?"} s` };
    case "bid_withdrawn":
      return { who: v, tone: "plain", text: `withdraws from ${inc}` };
    case "vote_cast":
      return { who: v, tone: "plain", text: `votes ${str(d.choice) ?? `option ${num(d.option_index) ?? "?"}`} for ${inc}${str(d.role) ? ` (${d.role})` : ""}` };
    case "plan_committed": {
      const sent = list(d.assigned).join(", ") || "no one";
      if (d.baseline) return { who: inc, tone: "plain", text: `dispatcher sends ${sent}` };
      const kept = d.kept && typeof d.kept === "object" ? Object.values(d.kept as Record<string, string>) : [];
      return { who: inc, tone: "good", text: `agreed: ${sent} (round ${num(d.round_reached) ?? "?"})${kept.length ? `, ${kept.join(", ")} stays` : ""}` };
    }
    case "plan_no_consensus": {
      const sent = list(d.assigned);
      const fallback = str(d.fallback) ? `, fallback ${d.fallback}` : "";
      return { who: inc, tone: "unknown", text: `no consensus${sent.length ? `, sent ${sent.join(", ")}` : ", nobody sent"}${fallback}` };
    }
    case "vehicle_compromised":
      return { who: v, tone: "bad", text: `hacked by the judge (${words(str(d.preset) ?? "raw")})` };
    case "claim_verified": {
      const result = str(d.result) ?? "unverifiable";
      const tone: FeedTone = result === "confirmed" ? "good" : result === "contradicted" ? "bad" : "unknown";
      const what = d.subject === "bid"
        ? `ETA ${num(d.eta_seconds) ?? "?"} s to ${inc}`
        : `${words(str(d.claim_type) ?? "claim")}${e.incident ? ` at ${e.incident}` : ""}`;
      const by = str(d.verifier) ? ` by ${d.verifier}` : ", no one in range";
      return { who: v, tone, text: `${what}: ${result}${by}` };
    }
    case "trust_changed": {
      const delta = (num(d.new) ?? 0) - (num(d.old) ?? 0);
      return { who: v, tone: delta < 0 ? "bad" : "good", text: `trust ${trust(d.old)} → ${trust(d.new)} (${delta >= 0 ? "+" : ""}${delta.toFixed(2)})` };
    }
    case "vehicle_untrusted":
      return { who: v, tone: "bad", text: `untrusted at ${trust(d.trust)}: its votes stop counting` };
    case "vehicle_trusted_again":
      return { who: v, tone: "good", text: `trusted again at ${trust(d.trust)}` };
    case "escalated_to_human": {
      const options = list(d.options);
      const contested = d.contested && typeof d.contested === "object" ? (d.contested as Record<string, string>) : {};
      const held = Object.entries(contested).map(([veh, at]) => `${veh} holds ${at}`);
      return {
        who: inc,
        tone: "alert",
        text: options.length
          ? `asks a human: ${str(d.role) ?? "unit"} from ${options.join(" or ")}${held.length ? ` (${held.join(", ")})` : ""}`
          : `asks a human: no ${str(d.role) ?? "unit"} could bid`,
      };
    }
    case "human_resolved": {
      const a = d.assignments && typeof d.assignments === "object" ? (d.assignments as Record<string, string | null>) : {};
      return { who: "Human", tone: "good", text: `sent ${Object.entries(a).map(([veh, at]) => `${veh} → ${at ?? "free"}`).join(", ") || "nothing"}` };
    }
    case "vehicle_arrived":
      return { who: v, tone: "plain", text: `on scene at ${inc}` };
    case "incident_resolved":
      return { who: inc, tone: "plain", text: "resolved" };
    case "incident_discredited":
      return {
        who: inc,
        tone: "bad",
        text: `false report, ${str(d.verifier) ?? v} found nothing; ${str(d.source_id) ?? "caller"} credibility ${trust(d.credibility_old)} → ${trust(d.credibility_new)}`,
      };
  }
  return { who: e.vehicle ?? e.incident ?? "?", tone: "plain", text: words(e.type) };
}

// ---- store ----

let lines: FeedLine[] = [];
let runId: string | null = null;
let seq = 0;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((fn) => fn());

/** Add one event as a line at the top of the feed. Called only by the dispatcher. */
export function pushFeed(e: TypedEvent): void {
  const { who, text, tone } = describeEvent(e);
  const line: FeedLine = {
    key: `${runId}-${seq++}`,
    tick: e.tick,
    type: e.type,
    group: GROUP[e.type] ?? "plan",
    tone,
    who,
    vehicle: e.vehicle,
    incident: e.incident,
    text,
  };
  lines = [line, ...lines].slice(0, MAX_LINES);
  notify();
}

/** Clear the feed for a new run. */
export function resetFeed(nextRun: string): void {
  runId = nextRun;
  seq = 0;
  lines = [];
  notify();
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** The feed lines, newest first; re-renders when a line is added or the run resets. */
export function useFeed(): FeedLine[] {
  return useSyncExternalStore(subscribe, () => lines);
}
