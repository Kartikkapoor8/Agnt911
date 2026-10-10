// Turns /state's escalation data into what the decision popup shows and says. Pure functions, no React.
//
// The backend escalates per call and role: "who goes to I3 as the ambulance: A2 or A1?". Each stuck role is
// one decision (a "slot"); each option is a vehicle. Severity, distance, and supporting checks are not in
// the escalation payload, so they are worked out here from the rest of /state.

import type { Claim, EscalationChoice, EvidenceLine, Location, Plan, StuckRole, WorldState } from "./types";

export interface OptionCard {
  vehicle: string;
  title: string; // "Send A2"
  distanceM: number | null; // vehicle to the call, straight line
  support: string[]; // confirmed checks backing this vehicle, green
  backers: string[]; // voters who picked it; empty when nobody could vote for it
  holds: string | null; // contested: the call this vehicle already holds; picking it moves it off that call
  busyWith: string | null; // the call it is on now (shown when nobody could bid)
  untrusted: boolean;
}

/** One entry in the "any vehicle of the role" dropdown. */
export interface AnyVehicle {
  vehicle: string;
  note: string; // "free", "on I6", "untrusted"
}

export interface DecisionSlot {
  key: string; // "incident/role"
  incident: string;
  role: string;
  callName: string;
  severity: number | null;
  question: string; // spoken and shown as the caption
  options: OptionCard[];
  noBids: boolean; // nobody could bid, so the options are every vehicle of the role
  anyVehicle: AnyVehicle[]; // every vehicle of the role, for the human to pull one the fleet didn't offer
  evidence: EvidenceLine[]; // who said what, who checked it, the result
  reason: string | null; // the trust module's one-line account of why the vote fell short
}

export interface Decision {
  id: string; // stable for one pause (run, decision round, escalation ids), used for the clock and to speak once
  slots: DecisionSlot[];
  failed: string[]; // failed claims that belong to no option, red
  alreadyAgreed: Record<string, string>; // roles that did reach consensus: vehicle -> incident, sent with the choice
}

/** Straight-line distance in metres between two points (same haversine the backend uses). */
export function distanceM(a: Location, b: Location): number {
  const R = 6371000;
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLng = (b.lng - a.lng) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

/** "450 m" or "1.2 km". */
export function formatDistance(m: number): string {
  return m < 1000 ? `${Math.round(m / 10) * 10} m` : `${(m / 1000).toFixed(1)} km`;
}

const article = (word: string) => (/^[aeiou]/i.test(word) ? "an" : "a");

function stuckRoles(plan: Plan): StuckRole[] {
  const ev = plan.evidence as StuckRole | null;
  if (!ev || !Array.isArray(ev.options)) return [];
  return [ev, ...(Array.isArray(ev.also) ? ev.also : [])];
}

/** The role's choices from the trust module, or the bare option ids with backers counted from the votes. */
function choicesFor(role: StuckRole, plan: Plan, state: WorldState): EscalationChoice[] {
  if (Array.isArray(role.choices) && role.choices.length) return role.choices;
  const ballot = plan.options.filter((v) => state.vehicles[v]?.type === role.role); // what option_index counts into
  return role.options.map((vehicle) => ({
    vehicle,
    label: `Send ${vehicle}`,
    backers: (role.evidence?.votes ?? []).filter((v) => ballot[v.option_index] === vehicle).map((v) => v.vehicle),
    weight: plan.tally[vehicle] ?? 0,
  }));
}

/** Every vehicle of the role, trusted and free first: what a human can still pull when nobody could bid. */
function anyVehicleOf(role: string, state: WorldState): EscalationChoice[] {
  return Object.values(state.vehicles)
    .filter((v) => v.type === role)
    .sort((a, b) => Number(a.untrusted) - Number(b.untrusted) || Number(a.target_incident !== null) - Number(b.target_incident !== null))
    .map((v) => ({ vehicle: v.id, label: `Send ${v.id}`, backers: [], weight: 0 }));
}

function claimById(state: WorldState, id: string): Claim | undefined {
  return state.recent_messages.find((m): m is Claim => m.kind === "claim" && m.id === id);
}

/** Confirmed checks on this vehicle's claims about this call, newest first, as "P1 confirmed A2 on scene". */
function supportFor(vehicle: string, incident: string, role: StuckRole, state: WorldState): string[] {
  const lines = (role.evidence?.lines ?? [])
    .filter((l) => l.source === vehicle && l.result === "confirmed")
    .map((l) => `${l.checker ?? "A vehicle"} confirmed: ${l.claim}`);
  const checks = state.checks
    .filter((c) => c.vehicle === vehicle && c.incident === incident && c.result === "confirmed" && c.verifier)
    .reverse()
    .map((c) => {
      const claim = claimById(state, c.claim_id);
      return `${c.verifier} confirmed ${vehicle} ${claim ? claim.type.replace("_", " ") : "claim"}`;
    });
  return [...new Set([...lines, ...checks])].slice(0, 2);
}

/** "A3 said I3 is resolved. Claim failed, vote not counted." from an evidence line like "A3 resolved I3". */
function failedLine(source: string, claimText: string, state: WorldState): string {
  const [, type = "", incident] = claimText.split(" ");
  const call = incident ? state.labels[incident] ?? incident : "";
  const said: Record<string, string> = {
    resolved: `said ${call || "the call"} is resolved`,
    on_scene: `said it was on scene${call ? ` at ${call}` : ""}`,
    sector_clear: "said its sector is clear",
    en_route: `said it was en route${call ? ` to ${call}` : ""}`,
    available: "said it was available",
    observation: `reported ${call || "a call"}`,
  };
  const untrusted = state.vehicles[source]?.untrusted;
  return `${source} ${said[type] ?? `claimed ${type.replace("_", " ")}`}. Claim failed${untrusted ? ", vote not counted" : ""}.`;
}

function questionFor(callName: string, severity: number | null, role: string, cards: OptionCard[], noBids: boolean, state: WorldState): string {
  const need = `${callName}${severity ? `, severity ${severity},` : ""} needs ${article(role)} ${role}.`;
  if (noBids) return `${need} Every ${role} is busy. Which one do you pull?`;
  const only = cards[0];
  if (cards.length === 1 && only.holds) {
    const held = state.labels[only.holds] ?? only.holds;
    return `${need} Only ${only.vehicle} can go, and it is already assigned to ${held}. Send ${only.vehicle} and leave ${held}?`;
  }
  if (cards.length === 1) return `${need} Only ${only.vehicle} is available, and the vote fell short. Send ${only.vehicle}?`;
  const split = cards.map((c) => c.backers.length).join(" to ");
  return `${need} The vote is split ${split}. Which ${role} goes: ${cards.map((c) => c.vehicle).join(" or ")}?`;
}

/** Every vehicle of the role with a short note on where it is, for the "any vehicle" dropdown. */
function anyVehicleList(role: string, state: WorldState): AnyVehicle[] {
  return anyVehicleOf(role, state).map(({ vehicle }) => {
    const v = state.vehicles[vehicle];
    const where = v?.target_incident ? `on ${v.target_incident}` : "free";
    return { vehicle, note: v?.untrusted ? `${where}, untrusted` : where };
  });
}

/** Build the popup's content from /state, or null when the run is not waiting on a human. */
export function buildDecision(state: WorldState): Decision | null {
  const plans = state.escalation?.plans ?? [];
  if (!state.paused || plans.length === 0) return null;

  const alreadyAgreed: Record<string, string> = {};
  plans.forEach((p) => p.assigned.forEach((v) => (alreadyAgreed[v] = p.incident)));

  const slots: DecisionSlot[] = [];
  const failed = new Set<string>();
  const ids: string[] = [];

  for (const plan of plans) {
    const inc = state.incidents[plan.incident];
    const callName = state.labels[plan.incident] ?? (inc ? `${inc.type} call ${inc.id}` : plan.incident);
    const severity = inc?.severity ?? null;
    for (const role of stuckRoles(plan)) {
      ids.push(role.id ?? `${plan.incident}/${role.role}`);
      const voted = choicesFor(role, plan, state);
      const noBids = voted.length === 0;
      const choices = noBids ? anyVehicleOf(role.role, state) : voted;
      const options: OptionCard[] = choices.map((c) => {
        const v = state.vehicles[c.vehicle];
        return {
          vehicle: c.vehicle,
          title: `Send ${c.vehicle}`,
          distanceM: v && inc ? distanceM(v.location, inc.location) : null,
          support: supportFor(c.vehicle, plan.incident, role, state),
          backers: c.backers,
          holds: role.contested?.[c.vehicle] ?? null,
          busyWith: v?.target_incident ?? null,
          untrusted: Boolean(v?.untrusted),
        };
      });
      const optionIds = new Set(options.map((o) => o.vehicle));
      (role.evidence?.lines ?? [])
        .filter((l) => l.result === "contradicted" && l.source && !optionIds.has(l.source))
        .forEach((l) => failed.add(failedLine(l.source as string, l.claim, state)));
      slots.push({
        key: `${plan.incident}/${role.role}`,
        incident: plan.incident,
        role: role.role,
        callName,
        severity,
        question: questionFor(callName, severity, role.role, options, noBids, state),
        options,
        noBids,
        anyVehicle: anyVehicleList(role.role, state),
        evidence: role.evidence?.lines ?? [],
        reason: role.reason ?? null,
      });
    }
  }
  // trust.py's escalation id is a content hash, so a call that escalates again reuses it; the run and the
  // decision round make each pause its own (fresh clock, spoken again).
  return { id: `${state.run_id}-${state.decision}-${ids.join("+")}`, slots, failed: [...failed], alreadyAgreed };
}
