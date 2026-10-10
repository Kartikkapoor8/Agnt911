# Engine integration

Greg's simulation, signed event bus, API, and JSONL logs now live inside the team's
existing `backend/agnt911` package. `agnt911.models` remains the source of truth;
the engine calls `await agnt911.protocol.run_round(world, incident_ids, llm_fn)`.
The agent, protocol, shared model, prompt, trust stub, and original team test files
are preserved.

## What changed from Greg's first branch

| Area | Original core | Integrated backend |
|---|---|---|
| Package | root `agnt911/` and its own manifest | `backend/agnt911/engine/`, installed with the team's manifest |
| Vehicle | flat `lat`/`lng`, `ambulance-1`, `idle` | shared `Vehicle`: `A1`, `location`, `home_district`, `free`, `target_incident`, `trust`, `untrusted` |
| Incident | `priority` 1–3, `required_types`, `covered` status | shared `Incident`: `severity` 1–5, `location`, `assigned`, `open/assigned/resolved` |
| Agents | async observation callbacks | existing synchronous `VehicleAgent.act()` through protocol's `asyncio.to_thread` |
| Negotiation | one callback batch and an external adapter | team's full propose/counter/vote/resolve coroutine; no second voting implementation |
| Transport | separate message envelope | shared `Claim`, `Bid`, `Vote`, signed through their existing `sig` field |
| Injection | `{vehicle_id, instruction}` | `{vehicle_id, preset_id}` or `{vehicle_id, text}`, stored as a preset in `World.injections` and applied by `feed.corrupt_view` to that vehicle's sensor view |
| Tests | 22 standalone core cases | original core was validated separately, then its behaviors migrated/adapted in `backend/tests/test_engine.py` alongside the untouched team suite |

The old root package/manifest/test module are removed so Python imports and pytest
have one canonical location. The original code remains in the earlier commits of
`greg/core-engine` and PR #1.

## Start and test

From the repository root:

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m pytest -q
.venv/bin/python -m uvicorn agnt911.engine.api:app --host 127.0.0.1 --port 8000
```

Default `AGNT911_LLM=fake` uses the team's deterministic `fake_llm`, with no API
key or network calls. The real protocol and actual vehicle prompt builders still
run. `AGNT911_LLM=real` delegates to the unchanged Anthropic client and requires
`ANTHROPIC_API_KEY` in the environment or `backend/.env`; real calls were not used
to validate this integration. Keys must be supplied securely, never committed.
The old `AGNT911_ADAPTER` setting is retired.

Use one server worker because state is in memory. The engine loads the existing
`scenarios/demo.json`. `AGNT911_SCENARIO` selects another scenario path.
`AGNT911_CLOCK` defaults to `stepped`: finish negotiation, advance one tick, then
sleep. `AGNT911_TICK_SECONDS` defaults to 1 simulation second per tick; vehicles
move at 12 m/s. Negotiation adds wall time to the 150-tick script. Set the clock
to `wall` to run movement and negotiation concurrently using elapsed time.
`AGNT911_SERVICE_SECONDS` defaults to 20 simulation seconds of service after
physical coverage, then responders are released for other calls.
`AGNT911_SEED` overrides the scenario seed. `AGNT911_LOG_DIR` defaults to ignored
`logs/`. `AGNT911_CORS_ORIGINS` is a comma-separated list of frontend origins.

## Runtime behavior

Incident creation, severity changes, injection, and service completion queue
negotiation. In stepped mode, one task finishes pending negotiations before
moving vehicles and applying due scenario events. Vehicles pause during LLM
requests; `/state` remains responsive because negotiation does not hold the
state lock. Wall mode runs movement and negotiation in separate tasks. External
changes such as judge injection still discard stale results in either mode;
scheduled events cannot interrupt stepped rounds. Results never copy vehicle
positions back from the old snapshot.

The existing fake LLM bids even when busy. The engine's LLM wrapper removes bids
that would steal a unit from its current call, while keeping that unit's claims
and votes. A unit can bid again for its existing incident during re-negotiation.
Committed assignments are validated together before applying them. No vehicle
can receive two simultaneous targets. Protocol-defined minor-incident role
fallbacks are accepted.

Physical coverage is a boolean under `/state.coverage[incident_id]`, with first
coverage time under `incident_timing`. This avoids adding a new `covered` value to
the shared incident status enum. `await engine.resolve_incident(id)` explicitly
finishes a physically covered call, releases vehicles, and reconsiders open calls.
`step()` also completes physically covered calls after the configured service
time. Arrival and resolution are separate logged events. After release, current
`coverage` becomes false; `covered_at_s` retains the historical arrival time.

Baseline runs the team's existing round-1-only dispatcher policy. Its believed
reports are shown under `reported_handled`, separate from physical coverage and
resolution. A false "resolved" report can suppress dispatch while coverage stays
false. Switching modes resets the original scenario, injections and RNG, preserving
previous timing/metric summaries in `comparison`.

## HTTP contract

- `GET /state`: the team's World fields (`tick`, `vehicles`, `incidents`,
  `recent_messages`, `baseline`) plus `run_id`, `seed`, `time_s`, `decision`,
  `mode`, `compromised`, `plans`, `checks`, `events`, `coverage`, `incident_timing`,
  `reported_handled`, `metrics`, `comparison`, `escalation`, `paused`, `integration`,
  `map_center`, `presets`, and `labels` (incident ID to display text). Labels stay
  available when their creation events leave the bounded feed and reset with the
  scenario. `add_incident()` and `update_incident()` accept a string `label`
  without changing the shared Incident model. Injection text and signing keys
  are omitted.
- `POST /inject`: `{"vehicle_id":"A2","preset_id":"on_scene_resolved"}` or
  `{"vehicle_id":"A2","text":"free text"}` (a raw sensor note). Returns
  `{"accepted":true,"vehicle_id":"A2"}`. Unknown ID: 404; unknown preset, invalid
  text, both or neither: 422.
- `POST /mode`: `{"mode":"baseline"}` or `{"mode":"protected"}`. Returns the
  new state; this starts a new run even if the mode is unchanged.
- `POST /resolve`: `{"assignments":{"A1":"I1"}}`, with optional null targets
  to release units. Available only during escalation. All pending escalated calls
  must receive compatible responders for all required roles; invalid choices: 409.

The judge controls are unauthenticated for the loopback laptop demo. Public hosting
requires authentication before exposing mutation endpoints. Matthew should poll
`/state` every 500 ms and use the shared nested `location` format. `/resolve`
resolves a human escalation, not the physical incident's service lifecycle.

## Signing and logs

`SignedHistory` intercepts the protocol's existing `recent_messages.append` calls.
It signs messages **before** they enter counter-round prompts or trust checks,
without editing `protocol.py`. Claims get IDs prefixed with a decision number to
remain unique across repeated negotiations. The existing `sig` string contains
`sequence:HMAC-SHA256`; the HMAC binds the run ID, sender sequence, and all shared
message fields except `sig`. Invalid signatures, other-run messages, and replayed
sequences are rejected. Keys remain inside the one trusted Python process.

Each JSONL line includes `run_id`, `event`, `time_s`, `tick`, and `decision`;
message/check records also carry protocol `round` (1 propose, 2 counter, 3 vote).
`vehicle_hacked`, `claim`, `bid`, `vote`, `verification`, `trust_changed`,
`dispatch`, `vehicle_arrived`, `incident_created`, `incident_updated`,
`incident_covered`, `incident_resolved`, and plan/escalation/failure events feed
the audit trail. Successful plans' check records are deduplicated by claim ID.
Trust changes are committed/logged once per completed decision, not once per
intermediate trust update inside the protocol. Discarded/failed decisions do not
publish their checks/trust changes. Raw injection requests are not logged, but
agent output can quote them.

The transcript, checks, and UI event feed are bounded at 500 entries; logs retain
all accepted events for the run. State reads return copies. Seeded spot checks can
use `agnt911.engine.context.current_rng()` inside synchronous trust functions;
this preserves the team's existing function signatures. The stub does not consume
this RNG. Wall-clock movement and live LLM replies are not deterministic.

## Remaining work and limits

- Chris's `agnt911.trust` is on main (f3c5d2b), so protocol no longer falls back to
  `trust_stub` and `/state.integration.trust_stub` is false. Two engine tests pin
  the stub on purpose. HMAC authenticates transport; it does
  not prove claim truth or Byzantine tolerance. Test-only trust fixtures are
  labeled fakes, not production security.
- The default fake LLM is honest and trusts its own view, so a corrupted sensor
  feed misleads it the same way it misleads the real model (tests/test_feed.py).
  scripts/probe_injection.py checks the four presets against the live model.
- Chris's metrics calculator is not connected or invented. `metrics` remains
  empty and `integration.metrics_available` false. Physical coverage and timestamps
  are available for a later calculator.
- Real SDK calls run in worker threads. A 90-second decision timeout keeps the
  engine responsive, but cancelling a coroutine cannot terminate an in-flight
  synchronous network request; the SDK's own timeout controls that request.
- The existing protocol makes a vote call per vehicle **per incident role**, not
  just seven vote calls overall. Five fake-world calls require 56 vehicle turns.
  This integration preserves that protocol and does not claim a seven-call cost.
- The UI must adopt the team's models/IDs and `/inject` `preset_id`/`text`; clients written for
  Greg's initial flat state or `instruction` payload need updating.
