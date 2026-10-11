// The story feed: cards that say what happened, built from the typed event stream.
//
// One decision card per incident decision, opened by call_received and closed by plan_committed,
// plan_no_consensus, or escalated_to_human (a re-negotiated call gets its card when the outcome lands). Vehicles
// also bid on calls a round isn't deciding, so a card takes only the bids and votes from the round that decided it. Failed checks, vehicles
// going untrusted, hacks, human decisions, and resolved calls get their own cards. Everything else (bids, votes,
// passing checks, arrivals) is kept as a hidden raw line: the decision card's table shows the bids and votes,
// and "show all" puts the raw lines back in the stream.
//
// Like the old feed, the store lives outside React so nothing is lost while the panel is unmounted.

import { useSyncExternalStore } from "react";
import { describeEvent, GROUP, type FeedLine } from "./feed";
import type { TypedEvent, Vehicle } from "./types";

export interface StoryBid {
  vehicle: string;
  eta: number | null;
  withdrawn: boolean;
  check: { result: string; verifier: string | null } | null; // the ETA check on this bid
}

export interface StoryVote {
  voter: string;
  role: string | null;
  choice: string | null;
  counted: boolean; // false when the voter was untrusted at the time
}

export type DecisionStatus = "negotiating" | "agreed" | "dispatched" | "no_consensus" | "escalated";

export interface DecisionCard {
  kind: "decision";
  key: string;
  seq: number;
  tick: number;
  incident: string;
  label: string | null;
  severity: number | null;
  status: DecisionStatus;
  revote: boolean; // this call was decided before; this is a re-negotiation
  bids: StoryBid[];
  votes: StoryVote[];
  assigned: string[];
  kept: string[]; // incumbents that stayed on
  fallback: string | null;
  roundReached: number | null;
  escalation: { role: string | null; options: string[]; contested: Record<string, string> } | null;
}

export interface CheckCard {
  kind: "check";
  key: string;
  seq: number;
  tick: number;
  vehicle: string; // whose claim failed
  verifiers: string[];
  whats: string[]; // "position", "ETA to I5", ...
  trustFrom: number | null;
  trustTo: number | null;
}

export type NoteKind = "untrusted" | "trusted" | "trust" | "hack" | "human" | "resolved" | "false_report" | "call_escalated";

export interface NoteCard {
  kind: NoteKind;
  key: string;
  seq: number;
  tick: number;
  vehicle: string | null;
  incident: string | null;
  text: string;
}

export interface RawItem {
  kind: "raw";
  key: string;
  seq: number;
  tick: number;
  line: FeedLine;
}

export type StoryItem = DecisionCard | CheckCard | NoteCard | RawItem;

const MAX_ITEMS = 1500;

const num = (x: unknown): number | null => (typeof x === "number" && Number.isFinite(x) ? x : null);
const str = (x: unknown): string | null => (typeof x === "string" && x.length > 0 ? x : null);
const strs = (x: unknown): string[] => (Array.isArray(x) ? x.filter((v): v is string => typeof v === "string") : []);
const record = (x: unknown): Record<string, string> => (x && typeof x === "object" ? (x as Record<string, string>) : {});

/** 1.0, 0.7, 0.55: one decimal unless a second one matters. */
export function fmtTrust(x: number): string {
  return Math.abs(x * 10 - Math.round(x * 10)) < 1e-6 ? x.toFixed(1) : x.toFixed(2);
}

/** "A1", "A1 and P1", "A1, A2 and P1". */
export function joinAnd(items: string[]): string {
  return items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** What a failed check was about, in words: "position", "ETA to I5", "report that I3 is handled". */
function checkedWhat(e: TypedEvent): string {
  const inc = e.incident;
  if (e.data.subject === "bid") return inc ? `ETA to ${inc}` : "ETA";
  switch (e.data.claim_type) {
    case "on_scene": return "position";
    case "en_route": return "route";
    case "resolved": return inc ? `report that ${inc} is handled` : "report that its call is handled";
    case "sector_clear": return "“sector clear” report";
    case "available": return "availability";
    case "capability": return "capability";
    default: return inc ? `report on ${inc}` : "report";
  }
}

// ---- store ----

let runId = "";
let seq = 0;
let n = 0;
const items = new Map<string, StoryItem>();
const open = new Map<string, string>(); // incident -> key of its open decision card
const decided = new Set<string>(); // incidents with at least one closed decision
const labels = new Map<string, string>();
const severities = new Map<string, number>();
const untrusted = new Set<string>();
// This round's bids and votes per call. All events of a round share its tick, so an older tick means a stale buffer.
const rounds = new Map<string, { tick: number; bids: StoryBid[]; votes: StoryVote[] }>();
let snapshot: StoryItem[] = [];
let scheduled = false;
const listeners = new Set<() => void>();

function publish() {
  scheduled = false;
  if (items.size > MAX_ITEMS) {
    const oldest = [...items.values()].sort((a, b) => (a.kind === "raw" ? 0 : 1) - (b.kind === "raw" ? 0 : 1) || a.seq - b.seq);
    oldest.slice(0, items.size - MAX_ITEMS).forEach((x) => items.delete(x.key));
  }
  snapshot = [...items.values()].sort((a, b) => b.seq - a.seq);
  listeners.forEach((fn) => fn());
}

function changed() {
  if (scheduled) return;
  scheduled = true;
  queueMicrotask(publish); // one re-render per poll, not per event
}

function put<T extends StoryItem>(item: T): T {
  items.set(item.key, item);
  changed();
  return item;
}

const newKey = (prefix: string) => `${prefix}-${runId}-${n++}`;

function openDecision(incident: string, tick: number): DecisionCard {
  const key = open.get(incident);
  if (key) return items.get(key) as DecisionCard;
  const card = put<DecisionCard>({
    kind: "decision",
    key: newKey("d"),
    seq: ++seq,
    tick,
    incident,
    label: labels.get(incident) ?? null,
    severity: severities.get(incident) ?? null,
    status: "negotiating",
    revote: decided.has(incident),
    bids: [],
    votes: [],
    assigned: [],
    kept: [],
    fallback: null,
    roundReached: null,
    escalation: null,
  });
  open.set(incident, card.key);
  return card;
}

/** Close the incident's decision with its outcome and this round's bids and votes, and move it to the top. */
function closeDecision(incident: string, tick: number, outcome: Partial<DecisionCard>) {
  const card = openDecision(incident, tick);
  const round = rounds.get(incident);
  const mine = round && round.tick === tick ? round : { bids: [], votes: [] };
  rounds.delete(incident);
  put<DecisionCard>({
    ...card,
    ...outcome,
    bids: [...mine.bids],
    votes: [...mine.votes],
    seq: ++seq,
    tick,
    label: labels.get(incident) ?? card.label,
    severity: severities.get(incident) ?? card.severity,
  });
  open.delete(incident);
  decided.add(incident);
}

function roundFor(incident: string, tick: number) {
  let r = rounds.get(incident);
  if (!r || r.tick !== tick) rounds.set(incident, (r = { tick, bids: [], votes: [] }));
  return r;
}

function updateBid(incident: string, tick: number, vehicle: string, change: Partial<StoryBid>) {
  const r = roundFor(incident, tick);
  const at = r.bids.findIndex((b) => b.vehicle === vehicle);
  const bid: StoryBid = { ...(at >= 0 ? r.bids[at] : { vehicle, eta: null, withdrawn: false, check: null }), ...change };
  if (at >= 0) r.bids[at] = bid;
  else r.bids.push(bid);
}

function raw(e: TypedEvent) {
  const { who, text, tone } = describeEvent(e);
  const key = newKey("r");
  put<RawItem>({
    kind: "raw",
    key,
    seq: ++seq,
    tick: e.tick,
    line: { key, tick: e.tick, type: e.type, group: GROUP[e.type] ?? "plan", tone, who, vehicle: e.vehicle, incident: e.incident, text },
  });
}

function note(kind: NoteKind, e: TypedEvent, text: string) {
  put<NoteCard>({ kind, key: newKey("n"), seq: ++seq, tick: e.tick, vehicle: e.vehicle, incident: e.incident, text });
}

/** Fold one event into the story. Called only by the dispatcher. */
export function pushStory(e: TypedEvent): void {
  const d = e.data;
  const v = e.vehicle ?? "?";
  const inc = e.incident;
  switch (e.type) {
    case "call_received":
      if (!inc) break;
      if (str(d.label)) labels.set(inc, d.label as string);
      if (num(d.severity) !== null) severities.set(inc, d.severity as number);
      openDecision(inc, e.tick);
      break;
    case "call_escalated": {
      if (!inc) break;
      if (str(d.label)) labels.set(inc, d.label as string);
      if (num(d.severity) !== null) severities.set(inc, d.severity as number);
      note("call_escalated", e, `${inc} escalated to sev ${num(d.severity) ?? "?"}${str(d.label) ? `: ${d.label}` : ""}`);
      break;
    }
    case "bid_placed":
      if (inc) updateBid(inc, e.tick, v, { eta: num(d.eta_seconds), withdrawn: false });
      raw(e);
      break;
    case "bid_withdrawn":
      if (inc) updateBid(inc, e.tick, v, { withdrawn: true });
      raw(e);
      break;
    case "vote_cast":
      if (inc) roundFor(inc, e.tick).votes.push({ voter: v, role: str(d.role), choice: str(d.choice), counted: !untrusted.has(v) });
      raw(e);
      break;
    case "claim_verified": {
      const result = str(d.result) ?? "unverifiable";
      if (d.subject === "bid" && inc) updateBid(inc, e.tick, v, { check: { result, verifier: str(d.verifier) } });
      if (result !== "contradicted") {
        raw(e);
        break;
      }
      // One card per liar per tick, however many of its claims failed.
      const key = `c-${runId}-${v}-${e.tick}`;
      const prev = items.get(key) as CheckCard | undefined;
      const verifier = str(d.verifier) ?? "a check";
      const what = checkedWhat(e);
      put<CheckCard>({
        kind: "check",
        key,
        seq: prev?.seq ?? ++seq,
        tick: e.tick,
        vehicle: v,
        verifiers: prev ? (prev.verifiers.includes(verifier) ? prev.verifiers : [...prev.verifiers, verifier]) : [verifier],
        whats: prev ? (prev.whats.includes(what) ? prev.whats : [...prev.whats, what]) : [what],
        trustFrom: prev?.trustFrom ?? null,
        trustTo: prev?.trustTo ?? null,
      });
      break;
    }
    case "trust_changed": {
      const card = items.get(`c-${runId}-${v}-${e.tick}`) as CheckCard | undefined;
      const from = num(d.old);
      const to = num(d.new);
      if (card) put<CheckCard>({ ...card, trustFrom: card.trustFrom ?? from, trustTo: to });
      else if (from !== null && to !== null) note("trust", e, `${v} trust ${fmtTrust(from)} → ${fmtTrust(to)}`);
      break;
    }
    case "vehicle_untrusted":
      untrusted.add(v);
      note("untrusted", e, `${v} removed from voting (trust ${fmtTrust(num(d.trust) ?? 0)}).`);
      break;
    case "vehicle_trusted_again":
      untrusted.delete(v);
      note("trusted", e, `${v} back in the vote (trust ${fmtTrust(num(d.trust) ?? 0)}).`);
      break;
    case "vehicle_compromised":
      note("hack", e, str(d.preset) ?? "raw");
      break;
    case "plan_committed":
      if (!inc) break;
      closeDecision(inc, e.tick, {
        status: d.baseline ? "dispatched" : "agreed",
        assigned: strs(d.assigned),
        kept: Object.values(record(d.kept)),
        fallback: str(d.fallback),
        roundReached: num(d.round_reached),
      });
      break;
    case "plan_no_consensus":
      if (!inc) break;
      closeDecision(inc, e.tick, {
        status: "no_consensus",
        assigned: strs(d.assigned),
        kept: Object.values(record(d.kept)),
        fallback: str(d.fallback),
        roundReached: num(d.round_reached),
      });
      break;
    case "escalated_to_human":
      if (!inc) break;
      closeDecision(inc, e.tick, { status: "escalated", escalation: { role: str(d.role), options: strs(d.options), contested: record(d.contested) } });
      break;
    case "human_resolved": {
      const sent = Object.entries(record(d.assignments)).map(([veh, at]) => `${veh} → ${at ?? "free"}`);
      note("human", e, `Human decided: ${sent.join(", ") || "nothing"}.`);
      break;
    }
    case "incident_resolved": {
      if (!inc) break;
      const key = open.get(inc);
      const card = key ? (items.get(key) as DecisionCard) : undefined;
      if (card && card.bids.length === 0 && card.votes.length === 0) {
        items.delete(card.key); // a call that closed before anyone negotiated it
        open.delete(inc);
      }
      note("resolved", e, `${inc} resolved`);
      break;
    }
    case "incident_discredited":
      note(
        "false_report",
        e,
        `${inc ?? "?"} was a false report: ${str(d.verifier) ?? v} found nothing there. ${str(d.source_id) ?? "The caller"}'s credibility ${fmtTrust(num(d.credibility_old) ?? 1)} → ${fmtTrust(num(d.credibility_new) ?? 0)}.`,
      );
      break;
    default:
      raw(e);
  }
}

/** Start a fresh story for a new run. */
export function resetStory(nextRun: string): void {
  runId = nextRun;
  seq = 0;
  n = 0;
  items.clear();
  open.clear();
  decided.clear();
  labels.clear();
  severities.clear();
  untrusted.clear();
  rounds.clear();
  changed();
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Story items, newest first. */
export function useStory(): StoryItem[] {
  return useSyncExternalStore(subscribe, () => snapshot);
}

// ---- sentences ----

/** "A2 (ambulance, 43 s)" */
function unit(card: DecisionCard, id: string, vehicles: Record<string, Vehicle>): string {
  const eta = card.bids.find((b) => b.vehicle === id)?.eta;
  const parts = [vehicles[id]?.type, eta != null ? `${eta} s` : null].filter(Boolean);
  return parts.length ? `${id} (${parts.join(", ")})` : id;
}

/** "7–0", or "A2 7–0, P1 6–1" when the roles split differently. Counted votes only. null = no votes seen. */
function voteSplit(card: DecisionCard, chosen: string[], vehicles: Record<string, Vehicle>): string | null {
  const counted = card.votes.filter((x) => x.counted);
  if (counted.length === 0) return null;
  const splits = chosen.map((id) => {
    const role = card.votes.find((x) => x.choice === id)?.role ?? vehicles[id]?.type ?? null;
    const inRole = counted.filter((x) => x.role === role);
    return `${inRole.filter((x) => x.choice === id).length}–${inRole.filter((x) => x.choice !== id).length}`;
  });
  return splits.every((x) => x === splits[0]) ? splits[0] : chosen.map((id, i) => `${id} ${splits[i]}`).join(", ");
}

/** The one-sentence body of a decision card. */
export function decisionSentence(card: DecisionCard, vehicles: Record<string, Vehicle>): string {
  const bidders = card.bids.length;
  const lead = bidders ? `${bidders} unit${bidders === 1 ? "" : "s"} bid. ` : "";
  const units = (ids: string[]) => joinAnd(ids.map((id) => unit(card, id, vehicles)));
  const ignored = [...new Set(card.votes.filter((x) => !x.counted).map((x) => x.voter))];
  const ignoredNote = ignored.length ? ` ${joinAnd(ignored)}'s vote${ignored.length === 1 ? "" : "s"} not counted.` : "";
  switch (card.status) {
    case "negotiating":
      return "Fleet negotiating…";
    case "dispatched":
      return `Dispatcher sent ${card.assigned.length ? units(card.assigned) : "nobody"}. No vote, no checks.`;
    case "agreed": {
      const chosen = card.assigned.filter((id) => !card.kept.includes(id));
      const parts: string[] = [];
      if (chosen.length) {
        const split = voteSplit(card, chosen, vehicles);
        parts.push(`${units(chosen)} chosen${split ? ` by vote ${split}` : ""}.`);
      }
      if (card.kept.length) parts.push(`${joinAnd(card.kept)} stay${card.kept.length === 1 ? "s" : ""} on.`);
      if (!card.assigned.length) parts.push("Nobody assigned.");
      return `${lead}${parts.join(" ")}${ignoredNote}`;
    }
    case "no_consensus": {
      const round = card.roundReached ? ` after round ${card.roundReached}` : "";
      const sent = card.assigned.length ? `${units(card.assigned)} sent${card.fallback ? ` as the ${card.fallback} fallback` : ""}.` : "Nobody sent yet.";
      return `${lead}No agreement${round}. ${sent}${ignoredNote}`;
    }
    case "escalated": {
      const e = card.escalation;
      const role = e?.role ?? "unit";
      if (!e || e.options.length === 0) return `${lead}No ${role} could bid. Asking a human.`;
      const held = Object.entries(e.contested).map(([id, at]) => `${id} is on ${at}`);
      const options = e.options.length === 1 ? `only ${e.options[0]} can go` : `${joinAnd(e.options)} are the options`;
      return `${lead}No agreement. Asking a human: ${options}${held.length ? `, and ${joinAnd(held)}` : ""}.`;
    }
  }
}

/** The failed-check card's sentence: "P1 checked A1's position: FALSE. A1 trust 1.0 → 0.7" */
export function checkSentence(card: CheckCard): string {
  const trust = card.trustFrom !== null && card.trustTo !== null && card.trustFrom !== card.trustTo
    ? ` ${card.vehicle} trust ${fmtTrust(card.trustFrom)} → ${fmtTrust(card.trustTo)}`
    : "";
  return `${joinAnd(card.verifiers)} checked ${card.vehicle}'s ${joinAnd(card.whats)}: FALSE.${trust}`;
}

/** Incidents with a decision still open (call received, no outcome yet), oldest first. */
export function negotiatingIncidents(story: StoryItem[]): string[] {
  return story
    .filter((x): x is DecisionCard => x.kind === "decision" && x.status === "negotiating")
    .sort((a, b) => a.seq - b.seq)
    .map((x) => x.incident);
}
