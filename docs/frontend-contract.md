# Frontend contract

Everything the frontend talks to, taken from the backend as it ran on 2026-10-10. The server ran with the fake LLM and the stepped clock, the demo was driven to tick 160 through the HTTP API only, and every 5 ticks of `/state` was saved. The TypeScript below was generated from those 37 real `GET /state` responses. Enum-like fields are narrowed to their literal values from `models.py` (marked `// narrowed`).

## Running the backend

From `backend/`:

```
AGNT911_LLM=fake AGNT911_CLOCK=stepped .venv/bin/uvicorn agnt911.engine.api:app --port 8000
```

| env var | default | meaning |
|---|---|---|
| `AGNT911_LLM` | `fake` | `real` uses Claude; it needs `ANTHROPIC_API_KEY` in the env or `backend/.env` |
| `AGNT911_CLOCK` | `stepped` | `stepped`: one round, then one tick; `wall`: wall-clock ticks with rounds in parallel |
| `AGNT911_TICK_SECONDS` | `1` | simulated seconds per tick, also the real-time pause between ticks |
| `AGNT911_SERVICE_SECONDS` | `20` | time on scene before a covered call resolves |
| `AGNT911_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | allowed browser origins |
| `AGNT911_SCENARIO`, `AGNT911_SEED`, `AGNT911_LOG_DIR` | demo, scenario seed, `logs` | |

The engine starts ticking as soon as the server starts. A client that connects later joins mid-run.

## GET /state

Poll it, every 500 ms suggested. Measured at t160 with the fake LLM: **496 KB**, response time 21.4/21.7/26.9 ms (min/median/max over 20 polls, localhost).

```ts
export interface State {
  baseline: boolean;
  callers: Record<string, number>;
  check_events: CheckEvent[];
  checks: PlanCheck[];
  comparison: ComparisonEntry[];
  compromised: string[];
  coverage: Record<string, boolean>;
  decision: number;
  escalation: Escalation | null;
  events: TypedEvent[];
  incident_timing: Record<string, IncidentTiming>;
  incidents: Record<string, Incident>;
  integration: Integration;
  labels: Record<string, string>;
  log: (Record<string, unknown>)[];
  map_center: Location;
  metrics: Metrics;
  mode: 'protected' | 'baseline';  // narrowed
  paused: boolean;
  plans: Record<string, Plan>;
  presets: Preset[];
  recent_messages: (BidMessage | ClaimMessage | VoteMessage)[];
  reported_handled: Record<string, unknown>;
  run_id: string;
  seed: number;
  tick: number;
  tick_records: TickRecord[];
  time_s: number;
  vehicles: Record<string, Vehicle>;
}

export interface BidMessage {
  capability_match: number;
  compromised: boolean;
  confidence: number;
  cost_to_district: number;
  eta_seconds: number;
  incident: string;
  kind: 'bid';  // narrowed
  reasoning: string;
  round: number;
  sig: string;
  vehicle: string;
}

export interface CheckEvent {
  checker: null | string;
  id: string;
  incident: null | string;
  result: 'pass' | 'fail' | 'inconclusive';  // narrowed
  subject: 'claim' | 'bid';  // narrowed
  target: string;
  tick: number;
  trust_after: number;
  trust_before: number;
}

export interface ClaimMessage {
  compromised: boolean;
  confidence: number;
  id: string;
  incident: null | string;
  kind: 'claim';  // narrowed
  location: { lat: number; lng: number };
  reasoning: string;
  round: number;
  sig: string;
  tick: number;
  type: 'on_scene' | 'en_route' | 'resolved' | 'sector_clear' | 'available' | 'capability' | 'observation';  // narrowed
  value: Record<string, unknown> | null;
  vehicle: string;
}

export interface ComparisonEntry {
  baseline: boolean;
  incident_timing: Record<string, IncidentTiming>;
  metrics: Metrics;
  run_id: string;
}

export interface Escalation {
  plans: Plan[];
}

export interface Incident {
  assigned: string[];
  false_report: boolean;
  id: string;
  location: Location;
  severity: number;
  source: '911' | 'operator';  // narrowed
  source_id: null | string;
  status: 'open' | 'assigned' | 'resolved';  // narrowed
  type: 'medical' | 'fire' | 'crash' | 'hazard' | 'shooting';  // narrowed
}

export interface IncidentTiming {
  covered_at_s: null | number;
  created_at_s: number;
  resolved_at_s: null | number;
}

export interface Integration {
  last_round_status: 'waiting' | 'running' | 'completed' | 'failed' | 'discarded';  // narrowed
  llm: string;
  loop_error: string | null;  // narrowed
  metrics_available: boolean;
  protocol: string;
  trust_stub: boolean;
}

export interface Location {
  lat: number;
  lng: number;
}

export interface Metrics {
  checks_total: number;
  escalations: number;
  first_untrusted_tick: Record<string, number>;
  incidents_false_handled: number;
  incidents_served: number;
  mean_response_ticks: number | null;  // narrowed
  unserved: number;
}

export interface Plan {
  assigned: string[];
  checks: PlanCheck[];
  consensus: boolean;
  escalated: boolean;
  evidence: Record<string, unknown> | null;
  incident: string;
  options: string[];
  round_reached: number;
  tally: Record<string, number>;
}

export interface PlanCheck {
  claim_id: string;
  incident: null | string;
  result: 'confirmed' | 'contradicted' | 'unverifiable';  // narrowed
  round: number;
  vehicle: string;
  verifier: null | string;
}

export interface Preset {
  id: string;
  label: string;
  preset: Record<string, unknown>;
}

export interface TickRecord {
  checks: number;
  escalation: boolean;
  incidents_false_handled: string[];
  incidents_open: string[];
  incidents_served: string[];
  mode: 'protected' | 'baseline';  // narrowed
  tick: number;
  trust: Record<string, number>;
  untrusted: string[];
}

export interface TypedEvent {
  data: Record<string, unknown>;
  incident: null | string;
  tick: number;
  type: EventType;  // narrowed
  vehicle: null | string;
}

export interface Vehicle {
  home_district: string;
  id: string;
  location: Location;
  status: 'free' | 'en_route' | 'on_scene';  // narrowed
  target_incident: null | string;
  trust: number;
  type: 'ambulance' | 'fire' | 'police';  // narrowed
  untrusted: boolean;
}

export interface VoteMessage {
  compromised: boolean;
  incident: string;
  kind: 'vote';  // narrowed
  option_index: number;
  reasoning: string;
  role: 'ambulance' | 'fire' | 'police';  // narrowed
  round: number;
  sig: string;
  vehicle: string;
}

export type Message = ClaimMessage | BidMessage | VoteMessage;

export type EventType =
  | 'call_received' | 'call_escalated' | 'bid_placed' | 'bid_withdrawn' | 'vote_cast'
  | 'plan_committed' | 'plan_no_consensus' | 'vehicle_compromised' | 'claim_verified' | 'trust_changed'
  | 'vehicle_untrusted' | 'vehicle_trusted_again' | 'escalated_to_human' | 'human_resolved'
  | 'vehicle_arrived' | 'incident_resolved' | 'incident_discredited';
```

### Field notes

| field | what it is |
|---|---|
| `vehicles`, `incidents` | Keyed by id. Draw from `location`. `vehicles[].untrusted` drives the red marker; `trust` (0-1) drives the trust bar. |
| `incidents[].source` / `source_id` / `false_report` | `"operator"` plus a caller id for a fake call. `false_report` turns true when the first police car on scene finds nothing (the call is then `resolved`). |
| `labels` | Incident id to display text. Incidents carry no label field, so use this map. |
| `events` | The typed animation stream, last 500, oldest first. See Event types below. |
| `check_events` | One row per verification, last 500: `result` pass/fail/inconclusive, `trust_before`/`trust_after`, `subject` claim or bid. Derived from `events`. |
| `checks` | Raw protocol check dicts (`confirmed`/`contradicted`/`unverifiable`), claims only. Prefer `check_events`. |
| `recent_messages` | Every signed claim, bid and vote of the run (not capped). `kind` tells them apart; claims have an `id`, bids and votes don't. |
| `plans` | Latest plan per incident: `assigned`, `options`, `tally`, `consensus`, `escalated`, `evidence`. |
| `metrics` | Headline numbers for the current run, recomputed every tick. |
| `tick_records` | One record per tick of the current run (not capped). |
| `comparison` | Finished runs only, appended on `POST /mode` (last 20). The current run is `metrics`. |
| `paused` / `escalation` | `paused` is true while a human decision is pending; `escalation.plans[]` holds the stuck plan(s). The run stays paused until `POST /resolve` or `POST /mode`. |
| `compromised` | Vehicles the judge hacked. Judge-only: the fleet's view is `vehicles[].untrusted`. |
| `callers` | Caller id to credibility (starts at 1.0, -0.3 per discredited call). |
| `presets` | The hijack-box buttons. |
| `baseline`, `mode` | `mode` is `protected` or `baseline`; `baseline` is the same as a boolean. |
| `integration.last_round_status` | `running` while vehicles negotiate; use it to show "negotiating…". |
| `log` | The engine's own record feed (last 500), for debugging. |

## Endpoints

| method | path | request body | success | errors |
|---|---|---|---|---|
| GET | `/state` | none | 200, `State` | |
| POST | `/inject` | `{"vehicle_id": "A2", "preset_id": "on_scene_resolved"}`, or `{"vehicle_id": "A2", "text": "free text"}`, or `{"preset_id": "fake_call"}` (no vehicle) | 200, `{"accepted": true, "vehicle_id": "A2" or null}` | 404 unknown vehicle; 422 unknown preset, blank text, both or neither, or a vehicle preset without `vehicle_id` |
| POST | `/resolve` | `{"assignments": {"A2": "I7"}}`: vehicle id to incident id, covering every required role of each pending escalation | 200, `State` (now `paused: false`) | 409 if nothing is pending or the choice doesn't cover the pending call(s); the body says which |
| POST | `/mode` | `{"mode": "baseline"}` or `{"mode": "protected"}` | 200, `State` of the **new run** (`run_id` changes, `tick` resets, the finished run is appended to `comparison`) | 422 unknown mode |
| POST | `/incident` | none | **missing** (404) | see Missing below |

Error bodies are FastAPI's: `{"detail": "Unknown vehicle"}` or `{"detail": [{"type": ..., "loc": [...], "msg": ...}]}` for validation errors.

Free text sent to `/inject` never appears in `/state` (checked): it reaches only that vehicle's sensor view.

## Presets (`presets[].id`)

| id | label | needs `vehicle_id` | effect |
|---|---|---|---|
| `on_scene_resolved` | Report on scene and resolved | yes | its feed puts it at the nearest unresolved call, shown as resolved |
| `sector_clear` | Claim your sector is clear | yes | nothing within 500 m in its feed; nearby calls broadcast as resolved |
| `closer_than_you_are` | Claim you are 200 m from the nearest incident | yes | every call 200 m away, ETA 30 s in its feed |
| `refuse_all` | Refuse every assignment | yes | its own status reads unavailable |
| `fake_call` | Place a fake shooting call | **no** | adds incident `X1`, `X2`, … from `caller-1` (sev 4 shooting) |
| free `text` | | yes | delivered as a `sensor_note` in that vehicle's view |

## Event types (`events[].type`, from docs/events.md)

Each event is `{tick, type, vehicle, incident, data}`.

- `call_received` (engine): `incident`; data `incident_type`, `severity`, `label`. Drop the incident pin with a ripple sized by severity, and add the label to the feed.
- `call_escalated` (engine): `incident`; data `severity`, `label`. Pin grows and flashes, and the severity badge counts up to the new value.
- `bid_placed` (protocol, rounds 1-2): `vehicle`, `incident`; data `round`, `revised`, `eta_seconds`, `capability_match`, `cost_to_district`. Dashed line from the vehicle to the pin, labelled with the ETA. If `revised`, redraw it with the new ETA.
- `bid_withdrawn` (protocol, round 2): `vehicle`, `incident`; data `round`. Dashed bid line fades out toward the vehicle.
- `vote_cast` (protocol, round 3): `vehicle` (voter), `incident`; data `role`, `option_index`, `choice`. Small chip flies from the voter to the chosen vehicle's card, and that option's tally bar ticks up.
- `plan_committed` (protocol): `incident`; data `assigned`, `tally`, `round_reached`, `baseline`, `fallback`, `believed`, `kept` (role to vehicle that stayed on, if any). Bid lines collapse into solid route lines from each assigned vehicle, and the pin shows a check mark. In baseline it is the dispatcher's pick, so use a plain line with no tally.
- `plan_no_consensus` (protocol): `incident`; data the same as plan_committed. Amber route lines for any `assigned`, with "no consensus" on the pin. If `fallback` is set, show "fallback: {role}". If `assigned` is empty, the pin pulses grey.
- `vehicle_compromised` (engine): `vehicle`; data `preset` (type only; free text stays private). Judge-only "compromised" tag on the vehicle's card, with a glitch flicker. The map marker does not change, because the fleet doesn't know yet.
- `claim_verified` (protocol, rounds 1-2): `vehicle` (claimant), `incident`; data `claim_id`, `verifier`, `result` (`confirmed` / `contradicted` / `unverifiable`), `claim_type`, `subject` (`claim`, or `bid` for a bid's ETA check, which also carries `eta_seconds`). Scan beam from the verifier's marker to the claimant's. Green tick if confirmed, red cross if contradicted, grey "?" if unverifiable or there is no verifier.
- `trust_changed` (protocol): `vehicle`; data `old`, `new`, `reason`. Trust bar animates from old to new, and red delta text (for example "-0.3") floats up from the card.
- `vehicle_untrusted` (protocol): `vehicle`; data `trust`. Marker goes blue to red, pulses once, and is isolated: its lines are cut and its card is dimmed.
- `vehicle_trusted_again` (protocol): `vehicle`; data `trust`. Marker goes red to blue and reconnects, with a single soft pulse.
- `escalated_to_human` (protocol): `incident`; data `options` (vehicle ids), `evidence` (votes, contradicting claims), `role`, `contested` (vehicle id to the call already holding it, when the only options were taken by another call this round). Map dims, the incident pin glows, and the human popup opens with the options and the evidence.
- `human_resolved` (engine): data `assignments` (vehicle id to incident id). Popup closes, and solid route lines are drawn for the human's choice with a "human" badge.
- `vehicle_arrived` (engine): `vehicle`, `incident`. Marker settles on the pin with a small bounce, and the route line turns solid.
- `incident_discredited` (engine): `vehicle` (the police car that found nothing), `incident`; data `source_id`, `verifier`, `credibility_old`, `credibility_new`. The pin cracks and turns hatched grey with a "false report" stamp, and the caller's credibility meter drops. An `incident_resolved` for the same call follows.
- `incident_resolved` (engine): `incident`. Pin fades to grey with a check mark, route lines clear, and responders return to idle.

## Gotchas

1. **`comparison` only fills on a mode switch.** It holds finished runs; the current run's numbers are in `metrics`. For "protected vs baseline" side by side, run one mode, `POST /mode` to the other (this files the first run), and show `comparison` plus the live `metrics`.
2. **Vehicles freeze during a round with the real API.** On the stepped clock no tick advances while the fleet negotiates, which takes about 15-30 s with Claude and is instant with the fake LLM. Show a "negotiating" state when `integration.last_round_status == "running"`, and don't treat a frozen tick as a dead server.
3. **`/state` is big and grows.** It was about 500 KB at t160, because `recent_messages` and `tick_records` are uncapped (`events`, `check_events` and `log` are capped at 500). At 500 ms polling that's about 1 MB/s on localhost, which is fine for the demo. Don't deep-diff the whole object every poll; key off `tick`, `run_id` and array lengths.
4. **A run reset changes `run_id`.** `/mode` starts a new run: `tick` goes back to 0 and `events` starts over. Reset the feed and the map when `run_id` changes.
5. **A popup can offer no options.** `escalation.plans[].evidence.options` can be `[]` when every candidate is untrusted or hacked (seen after several injects). The popup must then let the judge pick any vehicle of `evidence.role`. `evidence.contested` (`{vehicle: incident}`) marks options already holding another call; picking one moves that vehicle, and the call it leaves is renegotiated.
6. **`paused` stays true until a human answers.** Only `POST /resolve` (or `POST /mode`) clears it, and nothing ticks meanwhile.
7. **Join mid-run.** The engine starts ticking when the server starts; the first poll may land at tick 5 or later.
8. **`metrics.first_untrusted_tick` is one tick later than the `vehicle_untrusted` event** (101 vs 100). Use the event's tick for animations.
9. **Message ids:** claims have `id`; bids and votes don't. Key bids and votes on `(vehicle, incident, round)`.

## Missing or wrong

| item | owner | smallest fix |
|---|---|---|
| No `POST /incident` (the engine has `add_incident`, `update_incident` and `resolve_incident`, but no routes) | Greg | Add `POST /incident` (an `Incident` body plus optional `label`) calling `engine.add_incident`. Optional: `PATCH /incident/{id}` calling `update_incident`. |
| `/state` size grows without bound (`recent_messages`, `tick_records`) | Greg | Cap both to the last N in `_state()`, or add `?since=<tick>` |
| `first_untrusted_tick` one tick late | Greg / Chris | Build the tick record before advancing the tick (see the e2e report) |
| `false_report` not observed in this run | none | The fake call was placed after t160 and the run switched mode before a police car arrived. The path is covered by `tests/test_scenario.py::test_fake_call_is_discredited_when_police_arrive`. |
