# Trust: what changed and what's next (main 59f0b40)

From Kartik's audit of main on 2026-10-10. Paths are relative to `backend/`. Run `pytest -q` from `backend/` after each change. Main is green: 347 passed, 1 xfailed.

## `trust.py` on live data

Your module matches the Requests contract: the five functions with the exact parameters, trust 1.0 → 0.7 → 0.4 → 0.1 with untrusted below 0.4 (sticky), high-stakes claims always checked, and `metrics`/`tick_record`/`track_incidents` present. Protocol loads it, not the stub.

I ran the scripted demo against the real API through tick 80, twice. Both runs used the stepped clock and seed 911, with A2 hijacked at tick 60 by the scenario.

- **Run 1:** f3c5d2b, firmware-prompt injection.
- **Run 2:** 59f0b40, kartik-7e's sensor-feed injection plus the fixes below.

**Trust per vehicle (the same in both runs):**

| tick | A1 | A2 | A3 | F1 | F2 | P1 | P2 |
|---|---|---|---|---|---|---|---|
| 50 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 60 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 70 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 80 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |

**Checks:**

| | run 1 | run 2 |
|---|---|---|
| checks | 71 | 55 |
| confirmed | 39 | 19 |
| unverifiable | 32 | 36 |
| contradicted | 0 | 0 |

**The good news:** 126 live checks on real Claude output, with real position rounding and jitter, and not one honest vehicle was wrongly contradicted. Most unverifiable results had no witness: the fleet is spread 1-3 km apart, and nobody else was within 500 m.

**The bad news:** the hijacked A2 wasn't caught, because it never made a claim your rules can contradict.

- A2 has been parked at I3 since tick 29. At tick 60 it claimed `on_scene I3`, which is true, and P1 confirmed it in both runs.
- Under the sensor feed, A2 believed I3 was resolved. But it wrote that only in free-form observation fields, such as `{"I3_status_in_my_state": "resolved"}` and a `note` string, never as `value.status`.
- Even with `value.status: "resolved"`, `_resolution` would return unverifiable, because a suitable vehicle (A2 itself) is at the scene.

Items 1 and 2 below fix this from your side. The scenario target and the observation `value` shape are on Kartik's list from this audit.

## What I changed in your file (1e8f8ec, "audit fixes: trust (Chris)")

**`agnt911/trust.py`, `should_verify`:** an `observation` whose `value.status` is `resolved` or `clear` is now always verified, like a `resolved` claim.

- Before, such an observation reached `check_claim` only when the fixed hash sampled it. `_check_observation` already contradicts a false one, but A2's round-2 observation about I3, for example, is never sampled.
- With the sensor-feed hack, this kind of observation is how a hacked vehicle reports "it's handled".
- This is two lines. `tests/test_trust.py::test_suspects_high_stakes_and_numbers_are_always_checked` gains two cases.

Kartik's 59f0b40 also changed two things you'll see:
- `protocol._verifier` now returns your `trust.witness(claim, world)`, so the feed's checker is the vehicle your check actually used.
- `should_escalate` now receives `round_state["checks"]`, so `_evidence_line` can name the checker.

## What's left, in priority order

### 1. "Resolved" should need every required role on scene

Paste this into your agent:

> In `backend/agnt911/trust.py`, a "resolved" claim (or an observation with status `resolved`/`clear`) is currently plausible whenever any suitable vehicle is within 100 m, including the sender. A hacked ambulance alone at a severity-5 crash, with the police car still en route, can call it resolved and only get "unverifiable".
>
> Change `_resolution` so it is contradicted unless every required role is physically present:
> 1. Add a module constant `REQUIRED_ROLES = {"medical": ["ambulance"], "fire": ["fire"], "crash": ["ambulance", "police"], "hazard": ["fire", "police"], "shooting": ["police", "ambulance"]}`. It is the same as `protocol.ROLES`; severity 1-2 needs only the first role.
> 2. Add a helper:
>    ```python
>    def _roles_on_scene(world, incident):
>        """Say if every vehicle type the incident needs is physically at it."""
>        needed = REQUIRED_ROLES[incident.type][:1] if incident.severity <= 2 else REQUIRED_ROLES[incident.type]
>        present = {v.type for v in world.vehicles.values() if _at_scene(v, incident)}
>        return set(needed) <= present
>    ```
> 3. In `_resolution`, return `UNVERIFIABLE if _roles_on_scene(world, incident) else CONTRADICTED`.
> 4. Update the tests that pin the old rule: `test_resolved_with_acceptable_vehicle_on_scene_is_not_contradicted` (I1 is medical, so A1 alone still makes it unverifiable; keep that case) and `test_resolved_counts_untrusted_vehicles_as_physically_present`.
> 5. Add `test_resolved_at_severe_crash_with_only_the_ambulance_is_contradicted`: make I3 a severity-5 crash, park A2 on it, put P1 300 m away (a witness, but not on scene), and assert that `check_claim` of A2's `resolved I3` is `CONTRADICTED`. Also assert the same for an `observation` with `value={"status": "resolved"}`.
>
> A check also needs a witness, and P1 en route within 500 m counts.

### 2. Read "it's resolved" in more than one key, until the prompts fix the shape

Kartik's list includes telling vehicles in `prompts/shared.md` to use `value: {"status": "open" | "resolved" | "clear"}` for observations. Live models will still sometimes use their own keys. Paste:

> In `backend/agnt911/trust.py`, add `_says_handled(value) -> bool`:
> - True if `value.get("status")` is `resolved` or `clear`, or if any key containing `"status"` has a string value equal to `resolved` or `clear` (case-insensitive).
>
> Use it in `should_verify` (the observation line added in 1e8f8ec) and in `_check_observation` in place of `value.get("status") in ("resolved", "clear")`.
>
> Test: an observation with `value={"I3_status_in_my_state": "resolved"}` from a vehicle at I3 is always verified, and it is contradicted once item 1 lands. That value is the exact one A2 sent live.

### 3. Check bids, not just claims: done another way, nothing to build

Superseded (commit "scenario: tight map, catchable hijacks, fake-call preset, callers"). The protocol now hands every bid to your existing `check_claim` as an `observation` of that incident with `value: {"eta_seconds": ...}`, sent from the bidder's real position. Your `_numbers_lie` ETA rule and `witness` judge it, `update_trust` applies the result, and a contradicted bid is left out of the vote. Don't add `check_bid`. If you change the ETA rule, the closer_than_you_are hijack in the demo depends on it: A3 bids 30 s on I5 from 1,520 m with P2 112 m away.

### 4. Spot checks should vary by tick

`_sampled` hashes (vehicle, round, type, incident) and ignores the tick, so the same claims are picked every decision. For example, A2, A3 and P1 are never spot-checked on `available`, and P2 always is. The spec says one random spot check per round. Paste:

> In `backend/agnt911/trust.py`, `_sampled`: put `claim.tick` into the key, as `f"911|{claim.vehicle}|{claim.tick}|{round_no}|{claim.type}|{claim.incident}"`. It stays deterministic on the stepped clock, because ticks are fixed by the scenario.
>
> Update the three sampling tests: `test_ordinary_claims_are_sampled_about_one_in_three_by_a_fixed_hash` in `test_trust.py`, and `test_sampling_matches_the_spec_hash` and `test_sampling_ignores_claim_id_tick_confidence_and_location` in `test_trust_acceptance.py`.

### 5. Decide whether an assigned call disproves `sector_clear`

`_open_call_nearby` only counts `status == "open"`, and by tick 120 (F2's scripted hijack) every live call is `assigned`. A false `sector_clear` there can't be contradicted. Under the sensor feed, F2 mostly just stops bidding instead of claiming anything, so this is lower priority. Your call; `test_sector_clear_with_only_assigned_incident_nearby_is_not_contradicted` pins the current rule.

## What the engine gives you, and what to do meanwhile

- **District geometry: not available.** `Vehicle.home_district` is only a name ("Downtown", "Diridon", "Japantown").
  - Meanwhile, put the centroids of each district's starting positions from `scenarios/demo.json` in `trust.py`: `DISTRICT_CENTRES = {"Downtown": (37.3365, -121.8893), "Diridon": (37.3301, -121.9069), "Japantown": (37.3485, -121.8930)}`, with a fixed radius such as 800 m.
  - Ask Greg to add district centres to the scenario and `/state` if you need them for real.
- **Seeded RNG: available inside engine rounds.** `agnt911.engine.context.current_rng()` returns the run's `random.Random(seed)` (911 for the demo) while `protocol.run_round` runs under the engine. Outside the engine it raises `LookupError`; that includes `scripts/run_fake.py` and your unit tests.
  - Meanwhile, keep the hash; it's deterministic everywhere.
  - If you switch to the RNG, use `try: rng = current_rng() except LookupError: <hash fallback>`. Importing from `engine/` bends the "trust is pure" rule a little, so say so in the docstring.
- **Untrusted flag: already persisted within a run.** After each completed decision, `Engine.run_round` copies `trust` and `untrusted` from protocol's snapshot into the live world and logs `trust_changed`, and your `update_trust` never clears `untrusted`.
  - Two gaps: changes from a failed or discarded round are dropped (neither live run discarded any), and `/mode` starts a fresh run with everyone at 1.0, which is intended.
  - Meanwhile: nothing needed.
- **Metrics: your functions exist, but the engine doesn't call them yet.** That's item 1 on Greg's list.
  - Meanwhile, you can check `metrics` offline: step an `Engine` by hand (as `tests/test_integration.py::step_until` does), call `track_incidents` and `tick_record` after each `step`, and compare against the JSONL.
