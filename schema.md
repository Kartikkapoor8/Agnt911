# Schema

Agreed by the whole team before any code. Every component builds against this.

## Vehicle
Source of truth: `backend/agnt911/models.py`. Locations are `{"lat": float, "lng": float}`.

```json
{
  "id": "A1",
  "type": "ambulance",
  "location": {"lat": 37.3352, "lng": -121.8881},
  "home_district": "Downtown",
  "status": "free",
  "target_incident": null,
  "trust": 1.0,
  "untrusted": false
}
```

| Field | Type | Notes |
|---|---|---|
| id | string | A1-A3 ambulances, F1-F2 fire, P1-P2 police |
| type | `"ambulance"` \| `"fire"` \| `"police"` | |
| location | Location | |
| home_district | string | district the vehicle should keep covered |
| status | `"free"` \| `"en_route"` \| `"on_scene"` | default `"free"` |
| target_incident | string or null | incident id when en route or on scene |
| trust | float | default 1.0, owned by the trust engine |
| untrusted | bool | default false, set when trust falls too far |

## Incident
```json
{
  "id": "I1",
  "type": "medical",
  "severity": 5,
  "location": {"lat": 37.3355, "lng": -121.8860},
  "status": "open",
  "assigned": []
}
```

| Field | Type | Notes |
|---|---|---|
| id | string | I1-I5 in the demo |
| type | `"medical"` \| `"fire"` \| `"crash"` \| `"hazard"` | |
| severity | int | 1 to 5 |
| location | Location | |
| status | `"open"` \| `"assigned"` \| `"resolved"` | default `"open"` |
| assigned | list of vehicle ids | default empty |

## Message
`World.recent_messages` holds three kinds of message, told apart by `kind`: a `Claim` (one vehicle telling the fleet something), a `Bid`, or a `Vote`. Each carries `round` (1 propose, 2 counter, 3 vote) so the feed can group them. The message bus adds `sig` (HMAC); agents and the protocol leave it `""`. The protocol overwrites `id`, `tick` and `vehicle` on every claim with the real sender, so a vehicle cannot speak as another.

```json
{
  "kind": "claim",
  "id": "c1",
  "tick": 3,
  "vehicle": "A1",
  "type": "on_scene",
  "incident": "I1",
  "location": {"lat": 37.3355, "lng": -121.8860},
  "value": null,
  "confidence": 0.9,
  "reasoning": "I am at the scene.",
  "round": 1,
  "sig": ""
}
```

| Field | Type | Notes |
|---|---|---|
| kind | `"claim"` | default `"claim"` |
| id | string | unique per claim, `{vehicle}-r{round}-c{n}` |
| tick | int | world tick when sent |
| vehicle | string | sender id |
| type | `"on_scene"` \| `"en_route"` \| `"resolved"` \| `"sector_clear"` \| `"available"` \| `"capability"` \| `"observation"` | |
| incident | string or null | incident the claim is about |
| location | Location | where the sender says it is |
| value | object or null | free-form payload |
| confidence | float | 0 to 1 |
| reasoning | string | |
| round | int | protocol round it was sent in, default 0 |
| sig | string | default `""` |

### Other negotiation models
- `Bid {kind: "bid", vehicle, incident, eta_seconds: int, capability_match: 0-1, cost_to_district: float >= 0, confidence: 0-1, reasoning, round, sig}`: offer to respond to one incident.
- `Vote {kind: "vote", vehicle, incident, role: "ambulance" | "fire" | "police", option_index: int, reasoning, round, sig}`: vote for one option of one role. `option_index` counts within that role's options (the entries of `Plan.options` whose vehicle has that type).
- `Plan {incident, assigned: [vehicle id] (at most 2), round_reached: int, consensus: bool, escalated: bool, options: [vehicle id], tally: {vehicle id: weight}, checks: [Check], evidence: object or null}`: outcome of negotiating one incident. `assigned` holds one vehicle per filled role, in role order. `options` are every role's vote choices as voted on, grouped by role, best bid first. `tally` is trust-weighted votes for the options still free at resolve time; a vehicle already assigned to a higher-severity incident is left out. `consensus` is true only if every required role won with more than 60% of honest weight. `evidence` is set when a role escalated (`{"role", "options", "evidence"}` from `should_escalate`, with any second escalated role under `"also"`) or, in baseline, `{"believed": Claim}` when the dispatcher took a report at face value.

### Roles
Each incident type needs these vehicle types, voted on separately. Severity 1-2 needs only the first.

| Incident type | Roles |
|---|---|
| medical | ambulance |
| fire | fire |
| crash | ambulance, police |
| hazard | fire, police |

A bid is an option only if its vehicle has that role's type, its `capability_match` is at least 0.5 and the vehicle is not untrusted; at most 4 per role. Incidents resolve highest severity first; once a vehicle is assigned it drops out of every later incident's options.

Fallback for severity 1-2 (one role needed): if the first role has no eligible bids, the next role in the list takes the slot and is voted on. If the slot's options were all assigned to higher-severity incidents by resolve time, the best free bid of the next role is assigned without a vote: `consensus` is false and `evidence` is `{"fallback": {"vehicle", "role", "reason"}}`.
- `Check {claim_id, verifier: vehicle id or null, result: "confirmed" | "contradicted" | "unverifiable", vehicle, incident, round}`: one verification, stored on the plan for that incident.
- `AgentOutput {claims: [Claim], bids: [Bid], vote: Vote or null}`: what one vehicle returns per round, at most one bid per incident.
- `World {tick: int, vehicles: {id: Vehicle}, incidents: {id: Incident}, recent_messages: [Claim | Bid | Vote], baseline: bool = false}`: the whole city at one tick.

## Requests

### Kartik -> Greg (engine)
- `async def protocol.run_round(world, incident_ids=None, llm_fn=None) -> list[Plan]`. It is a coroutine: `await` it from the tick loop. It appends to `world.recent_messages` and writes trust changes into `world.vehicles`; it does not touch `incident.assigned` or vehicle targets, the engine applies the plans.
- `/inject` needs a place the agents can read: proposal `World.injections: {vehicle id: text}` (not added yet), which `VehicleAgent.system_prompt` will append.

### Kartik -> Chris (trust)
`backend/agnt911/trust.py` with these signatures. Until it exists, protocol uses `trust_stub.py`, which has the same shapes. `tests/test_contracts.py` checks the names and parameters.
- `should_verify(claim: Claim, vehicle: Vehicle, round_no: int) -> bool`: `vehicle` is the claim's sender.
- `check_claim(claim: Claim, world: World) -> "confirmed" | "contradicted" | "unverifiable"`
- `update_trust(vehicle: Vehicle, result: str) -> Vehicle`: return the updated vehicle (protocol stores it), including `untrusted` once trust is below 0.4.
- `vote_weight(vehicle: Vehicle) -> float`
- `should_escalate(round_state: dict) -> dict | None`: called once per stuck role. Return `{"options": [vehicle id, vehicle id], "evidence": {...}}` to ask a human, or `None` to let the heaviest option win without consensus.

Stuck-vote payload (`round_state`), sent per role, only for severity 4-5 when no free option has more than 60% of honest weight:
```json
{
  "incident": "I1",
  "severity": 5,
  "role": "ambulance",
  "round": 4,
  "options": [Bid, ...],
  "tally": {"A1": 3.0, "A3": 4.0},
  "honest_total": 7.0,
  "threshold": 4.2,
  "votes": [Vote, ...],
  "contradicting_claims": [Claim, ...]
}
```
`contradicting_claims` = observation claims about that incident plus any claim a check found `contradicted`.

## GET /state response
(to fill)

## POST /inject body
(to fill)
