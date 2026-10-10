// Shapes of GET /state, copied from backend/agnt911/models.py and Engine._state() in
// backend/agnt911/engine/runtime.py. Only fields the backend actually sends.

export interface Location {
  lat: number;
  lng: number;
}

export type VehicleType = "ambulance" | "fire" | "police";
export type VehicleStatus = "free" | "en_route" | "on_scene";
export type IncidentType = "medical" | "fire" | "crash" | "hazard" | "shooting";
export type IncidentStatus = "open" | "assigned" | "resolved";
export type ClaimType =
  | "on_scene"
  | "en_route"
  | "resolved"
  | "sector_clear"
  | "available"
  | "capability"
  | "observation";
export type CheckResult = "confirmed" | "contradicted" | "unverifiable";

export interface Vehicle {
  id: string;
  type: VehicleType;
  location: Location;
  home_district: string;
  status: VehicleStatus;
  target_incident: string | null;
  trust: number;
  untrusted: boolean;
}

export interface Incident {
  id: string;
  type: IncidentType;
  severity: number; // 1 to 5
  location: Location;
  status: IncidentStatus;
  assigned: string[];
  source: "911" | "operator"; // "operator" = placed by an outside caller (the fake_call preset)
  source_id: string | null; // that caller's id, e.g. "caller-1"
  false_report: boolean; // a police car arrived and found nothing; the call is then resolved
}

/** One vehicle telling the fleet something. Only claims carry an id and a tick. */
export interface Claim {
  kind: "claim";
  id: string;
  tick: number;
  vehicle: string;
  type: ClaimType;
  incident: string | null;
  location: Location;
  value: Record<string, unknown> | null;
  confidence: number;
  reasoning: string;
  round: number; // 1 propose, 2 counter, 3 vote
  sig: string; // "sequence:hmac", unique per message
  compromised: boolean; // sender is hacked; for the judge's view only
}

export interface Bid {
  kind: "bid";
  vehicle: string;
  incident: string;
  eta_seconds: number;
  capability_match: number;
  cost_to_district: number;
  confidence: number;
  reasoning: string;
  round: number;
  sig: string;
  compromised: boolean;
}

export interface Vote {
  kind: "vote";
  vehicle: string;
  incident: string;
  role: VehicleType | null;
  option_index: number;
  reasoning: string;
  round: number;
  sig: string;
  compromised: boolean;
}

export type Message = Claim | Bid | Vote;

/** One verification of a claim, built in protocol._verify. */
export interface Check {
  claim_id: string;
  verifier: string | null;
  result: CheckResult;
  vehicle: string;
  incident: string | null;
  round: number;
}

export interface Plan {
  incident: string;
  assigned: string[];
  round_reached: number;
  consensus: boolean;
  escalated: boolean;
  options: string[];
  tally: Record<string, number>;
  checks: Check[];
  evidence: Record<string, unknown> | null;
}

/** One engine audit record from /state.log. Extra keys depend on `event` (dispatch, claim, vehicle_arrived, ...). */
export interface LogRecord {
  run_id: string;
  event: string;
  time_s: number;
  tick: number;
  decision: number;
  [key: string]: unknown;
}

/** Typed event types, from EventType in models.py (docs/events.md describes each one's data). */
export type EventType =
  | "call_received" | "call_escalated" | "bid_placed" | "bid_withdrawn" | "vote_cast" | "plan_committed"
  | "plan_no_consensus" | "vehicle_compromised" | "claim_verified" | "trust_changed" | "vehicle_untrusted"
  | "vehicle_trusted_again" | "escalated_to_human" | "human_resolved" | "vehicle_arrived" | "incident_resolved"
  | "incident_discredited";

/** One typed event from /state.events, for animations. No id: the list is append-only, oldest first. */
export interface TypedEvent {
  tick: number;
  type: EventType;
  vehicle: string | null;
  incident: string | null;
  data: Record<string, unknown>;
}

export interface IncidentTiming {
  created_at_s: number;
  covered_at_s: number | null;
  resolved_at_s: number | null;
}

export interface ComparisonEntry {
  run_id: string;
  baseline: boolean;
  metrics: Record<string, unknown>;
  incident_timing: Record<string, IncidentTiming>;
}

export interface Integration {
  protocol: string;
  llm: "real" | "fake_or_custom";
  trust_stub: boolean;
  metrics_available: boolean;
  last_round_status: "waiting" | "running" | "completed" | "failed" | "discarded";
  loop_error: string | null;
}

/** A judge hack from the scenario. Send its id as preset_id; fake_call needs no vehicle. */
export interface Preset {
  id: string;
  label: string;
  preset: { kind: string; [key: string]: unknown };
}

/** One option in a human escalation, built by trust.should_escalate. */
export interface EscalationChoice {
  vehicle: string;
  label: string;
  backers: string[];
  weight: number;
}

/** One evidence line in a human escalation: who said what, who checked it, and the result. */
export interface EvidenceLine {
  source: string | null;
  checker: string | null;
  claim_id: string | null;
  claim: string;
  result: string;
}

/** Plan.evidence for an escalated plan: one stuck role, with any other stuck roles under `also`. */
export interface StuckRole {
  role: string;
  id?: string;
  reason?: string;
  options: string[];
  choices?: EscalationChoice[];
  evidence?: { votes?: Vote[]; contradicting_claims?: Claim[]; lines?: EvidenceLine[] };
  also?: StuckRole[];
}

export interface WorldState {
  // Shared World fields (injections are stripped by the engine)
  tick: number;
  vehicles: Record<string, Vehicle>;
  incidents: Record<string, Incident>;
  recent_messages: Message[]; // newest last, at most 500
  baseline: boolean;
  // Engine metadata (World.injections is never sent)
  run_id: string;
  seed: number;
  time_s: number;
  decision: number;
  mode: "protected" | "baseline";
  compromised: string[]; // vehicles a judge (or the script) injected
  checks: Check[];
  events: TypedEvent[]; // typed stream, oldest first, last 500
  log: LogRecord[]; // engine audit records, oldest first, last 500
  plans: Record<string, Plan>;
  labels: Record<string, string>; // incident id -> display text
  callers: Record<string, number>; // outside caller id -> credibility (starts 1.0, -0.3 per false call)
  metrics: Record<string, unknown>; // empty until the trust/metrics module lands
  comparison: ComparisonEntry[];
  incident_timing: Record<string, IncidentTiming>;
  coverage: Record<string, boolean>;
  reported_handled: Record<string, Claim>;
  escalation: { plans: Plan[] } | null;
  paused: boolean;
  integration: Integration;
  map_center: Location | null;
  presets: Preset[];
}
