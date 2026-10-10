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

**The good news:** 126 live checks on real Claude output, with real position rounding and jitter, and not one honest vehicle was wrongly contradicted. A later batch run found one; see item 1. Most unverifiable results had no witness: the fleet is spread 1-3 km apart, and nobody else was within 500 m.

**The bad news:** the hijacked A2 wasn't caught, because it never made a claim your rules can contradict.

- A2 has been parked at I3 since tick 29. At tick 60 it claimed `on_scene I3`, which is true, and P1 confirmed it in both runs.
- Under the sensor feed, A2 believed I3 was resolved. But it wrote that only in free-form observation fields, such as `{"I3_status_in_my_state": "resolved"}` and a `note` string, never as `value.status`.
- Even with `value.status: "resolved"`, `_resolution` would return unverifiable, because a suitable vehicle (A2 itself) is at the scene.

Items 2 and 3 below fix this from your side. Since then, kartik-7e's scenario redesign (5b12021) hijacks vehicles that are away from the incident they claim. In its real batch run, your checks caught A1 at tick 60 and A3 at tick 120, so the demo no longer depends on items 2 and 3.

## What I changed in your file (1e8f8ec, "audit fixes: trust (Chris)")

**`agnt911/trust.py`, `should_verify`:** an `observation` whose `value.status` is `resolved` or `clear` is now always verified, like a `resolved` claim.

- Before, such an observation reached `check_claim` only when the fixed hash sampled it. `_check_observation` already contradicts a false one, but A2's round-2 observation about I3, for example, is never sampled.
- With the sensor-feed hack, this kind of observation is how a hacked vehicle reports "it's handled".
- This is two lines. `tests/test_trust.py::test_suspects_high_stakes_and_numbers_are_always_checked` gains two cases.

Kartik's 59f0b40 also changed two things you'll see:
- `protocol._verifier` now returns your `trust.witness(claim, world)`, so the feed's checker is the vehicle your check actually used.
- `should_escalate` now receives `round_state["checks"]`, so `_evidence_line` can name the checker.

## What's left, in priority order

### 1-3. Done for you (commit "trust: observations about other vehicles; resolved needs all roles; lenient status read")

Kartik had kartik-7e make these in `trust.py`, since nothing new had landed from you after f3c5d2b. They're done, so don't redo them; review the diff when you're back.

1. **Observations about other vehicles.** An observation's numbers describe its subject, not its sender. `_subject` picks the subject: the vehicle named in `value` (`vehicle`, `subject`, `target_vehicle` or `other`); otherwise the single other vehicle id in the reasoning; otherwise the sender. `_numbers_lie` then measures `distance_m` and `eta_seconds` from that vehicle. When the subject is another vehicle, `_check_observation` confirms the report if that vehicle really is that far (within max(150 m, 25%)) from the named incident. Two or more candidate subjects with numbers attached: unverifiable, never contradicted. The protocol's bid-as-observation now carries `value.vehicle` = the bidder, so bids are still measured from the bidder. Tests: `test_observation_about_another_vehicle_is_checked_against_that_vehicle` (the exact live P1 claim from t120, now confirmed), `..._naming_another_vehicle_in_reasoning_only...`, `..._ambiguous_subject_is_unverifiable...`, and `test_bid_observation_naming_the_sender_is_still_checked_against_the_sender`.
2. **"Resolved" needs every required role on scene.** `REQUIRED_ROLES` (the same as `protocol.ROLES`) plus `_roles_on_scene`; `_resolution` contradicts unless every role the call needs is physically there. `_capable_on_scene` was removed (`_resolution` was its only caller). Test: `test_resolved_at_severe_crash_with_only_the_ambulance_is_contradicted`. No existing test pinned the old rule: the two this list flagged both use a medical call, where one ambulance is every role, and they still pass unchanged.
3. **Lenient status read.** `_says_handled(value)` is true for `resolved`/`clear` under any key containing "status" (case-insensitive). It is used in `should_verify` and `_check_observation`. Test: `test_resolved_read_from_any_status_key`, with the exact live value `{"I3_status_in_my_state": "resolved"}`.

### 4. Check bids, not just claims: done another way, nothing to build

Superseded (commit "scenario: tight map, catchable hijacks, fake-call preset, callers"). The protocol now hands every bid to your existing `check_claim` as an `observation` of that incident with `value: {"eta_seconds": ...}`, sent from the bidder's real position. Your `_numbers_lie` ETA rule and `witness` judge it, `update_trust` applies the result, and a contradicted bid is left out of the vote. Don't add `check_bid`. If you change the ETA rule, the closer_than_you_are hijack in the demo depends on it: A3 bids 30 s on I5 from 1,520 m with P2 112 m away.

### 5. Spot checks should vary by tick

`_sampled` hashes (vehicle, round, type, incident) and ignores the tick, so the same claims are picked every decision. For example, A2, A3 and P1 are never spot-checked on `available`, and P2 always is. The spec says one random spot check per round. Paste:

> In `backend/agnt911/trust.py`, `_sampled`: put `claim.tick` into the key, as `f"911|{claim.vehicle}|{claim.tick}|{round_no}|{claim.type}|{claim.incident}"`. It stays deterministic on the stepped clock, because ticks are fixed by the scenario.
>
> Update the three sampling tests: `test_ordinary_claims_are_sampled_about_one_in_three_by_a_fixed_hash` in `test_trust.py`, and `test_sampling_matches_the_spec_hash` and `test_sampling_ignores_claim_id_tick_confidence_and_location` in `test_trust_acceptance.py`.

### 6. Decide whether an assigned call disproves `sector_clear`

`_open_call_nearby` only counts `status == "open"`, and by tick 120 (F2's scripted hijack) every live call is `assigned`. A false `sector_clear` there can't be contradicted. Under the sensor feed, F2 mostly just stops bidding instead of claiming anything, so this is lower priority. Your call; `test_sector_clear_with_only_assigned_incident_nearby_is_not_contradicted` pins the current rule.

### 7. Escalation reason for a contested option (cosmetic)

Since ea14803, a severity 4-5 role whose options were all won by another call in the same round still escalates. Protocol passes those options to `should_escalate` with tally `0.0` each, so the popup reason reads "A2 leads with 0.0 of … honest weight". The real choice is which call gets A2. Protocol adds `{"contested": {vehicle: call}}` to the escalation after your function returns, but `round_state` doesn't carry it. Paste:

> Ask Kartik to add `"contested": contested` to the `round_state` dict in `protocol._resolve_role` (one line). Then, in `backend/agnt911/trust.py`, `should_escalate`: when `round_state.get("contested")` is non-empty, set `reason` to `f"{label} (severity {severity}): every option is committed to another call this round ({', '.join(f'{v} on {c}' for v, c in contested.items())}); choose which call gets it"`. Test with a `round_state` whose tally is all 0.0 and whose `contested` is `{"A2": "I6"}`, and assert the reason names I6.

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
