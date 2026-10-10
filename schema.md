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
A message is a `Claim`: one vehicle telling the fleet something. The message bus adds `sig` (HMAC); agents leave it `""`.

```json
{
  "id": "c1",
  "tick": 3,
  "vehicle": "A1",
  "type": "on_scene",
  "incident": "I1",
  "location": {"lat": 37.3355, "lng": -121.8860},
  "value": null,
  "confidence": 0.9,
  "reasoning": "I am at the scene.",
  "sig": ""
}
```

| Field | Type | Notes |
|---|---|---|
| id | string | unique per claim |
| tick | int | world tick when sent |
| vehicle | string | sender id |
| type | `"on_scene"` \| `"en_route"` \| `"resolved"` \| `"sector_clear"` \| `"available"` \| `"capability"` \| `"observation"` | |
| incident | string or null | incident the claim is about |
| location | Location | where the sender says it is |
| value | object or null | free-form payload |
| confidence | float | 0 to 1 |
| reasoning | string | |
| sig | string | default `""` |

### Other negotiation models
- `Bid {vehicle, incident, eta_seconds: int, capability_match: 0-1, cost_to_district: float >= 0, confidence: 0-1, reasoning}`: offer to respond to one incident.
- `Vote {vehicle, incident, option_index: int, reasoning}`: vote for one option of a proposed plan.
- `Plan {incident, assigned: [vehicle id], round_reached: int, consensus: bool, escalated: bool}`: outcome of negotiating one incident (max 4 rounds).
- `AgentOutput {claims: [Claim], bid: Bid or null, vote: Vote or null}`: what one vehicle returns per round.
- `World {tick: int, vehicles: {id: Vehicle}, incidents: {id: Incident}, recent_messages: [Claim], baseline: bool = false}`: the whole city at one tick.

## GET /state response
(to fill)

## POST /inject body
(to fill)
