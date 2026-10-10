# Scenarios

`demo.json` is the scripted 3-minute demo. The engine loads it, builds the `World` from `vehicles` (each has `id`, `type`, `home_district`, `location`, matching `models.Vehicle`), and applies every entry in `events` when `world.tick` reaches its `tick`. An `incident` event adds the embedded incident (the `label` is display text for the feed, not a model field) and starts `run_round` for it. An `inject` event names a preset by `preset_id` (or gives free `text`, stored as a raw sensor note) and sets `world.injections[vehicle]` to that preset; from then on the vehicle's sensor view is corrupted by `feed.corrupt_view` (see schema.md, Injection). Its prompt and model do not change. An `escalate` event sets the named incident's `severity` to the new value, records the `label`, and re-triggers `run_round` for that incident. `presets` are the five hijack-box buttons, each `{"id", "label", "preset": {"kind": ...}}`; `fake_call` needs no vehicle and its inject event has no `"vehicle"`. `seed` is for any randomness and `map_center` is the initial map view.

## Demo script

All seven vehicles and seven calls sit within about 1 x 1.5 km of downtown San Jose, so every vehicle has another vehicle within 500 m at every scripted and decision tick, and every hack has a witness. `scripts/check_scenario.py` asserts this with the fake LLM, along with the escalation beat.

1. Tick 0: cardiac arrest at 2nd & Santa Clara (I1, sev 5, A1 is 150 m away) and a fire near St James Park (I2, F1). I1 resolves at t33, leaving A1 free.
2. Tick 5: a crash at Almaden & San Fernando (I3). P1 takes police (on scene t30), A2 takes the ambulance (on scene t53).
3. Tick 20: a gas smell at 1st & San Carlos (I4, sev 2, F2), resolved by t54.
4. Tick 40: the crash escalates to a pileup (sev 5); the re-run keeps A2 and P1.
5. Tick 60: A1, free and 742 m from any active call, is hijacked ("on scene and resolved"). Its feed puts it at I3, so it reports being there and the call being handled. P1, on scene at I3, contradicts it, and A1's trust drops (with real Claude it went untrusted at t60).
6. Tick 100: unresponsive person at Almaden & San Carlos (I5, sev 4), 200 m from A2, which takes it. I5 resolves at t137.
7. Tick 120: A3 is hijacked ("200 m from the nearest incident"). It bids 30 s on I5, which is 1,256 m away. P2, 112 m from A3, sees where A3 really is, so trust contradicts the ETA. The bid is left out of the vote.
8. Tick 140, the human beat: two cardiac-level calls 600 m apart (I6 at Market & San Salvador, I7 at 4th & San Salvador, both sev 5). A1 and A3 are untrusted, so A2 is the only trusted ambulance. A2 wins I6, and I7 goes to the human, offering A2 marked contested (`{"A2": "I6"}`). The judge decides which call gets it, or sends an untrusted unit. If A2 is moved, I6 goes back to the fleet.

The `fake_call` preset (hijack box, no vehicle) places a sev 4 shooting from caller-1. The first police car to arrive finds nothing, the call is marked a false report, and caller-1's credibility drops from 1.0 to 0.7.
