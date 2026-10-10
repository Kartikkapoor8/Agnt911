# Scenarios

`demo.json` is the scripted 3-minute demo. The engine loads it, builds the `World` from `vehicles` (each has `id`, `type`, `home_district`, `location`, matching `models.Vehicle`), and applies every entry in `events` when `world.tick` reaches its `tick`. An `incident` event adds the embedded incident (the `label` is display text for the feed, not a model field) and starts `run_round` for it. An `inject` event names a preset by `preset_id` (or gives free `text`, stored as a raw sensor note) and sets `world.injections[vehicle]` to that preset; from then on the vehicle's sensor view is corrupted by `feed.corrupt_view` (see schema.md, Injection). Its prompt and model do not change. An `escalate` event sets the named incident's `severity` to the new value, records the `label`, and re-triggers `run_round` for that incident. `presets` are the four hijack-box buttons, each `{"id", "label", "preset": {"type": ...}}`. `seed` is for any randomness and `map_center` is the initial map view.

## Demo script

1. Tick 0: cardiac arrest at 1st & Santa Clara (sev 5) and a Japantown fire appear; the feed shows bids and A1 wins the ambulance vote, F-unit takes the fire.
2. Tick 5: a crash near Diridon appears; the judge sees an ambulance and a police car bid on it.
3. Tick 20: a low-severity gas hazard appears; fire and police cover it without drama.
4. Tick 40: the crash escalates to a pileup (sev 5); the feed re-runs the round and A2 plus a police car are committed.
5. Tick 60: A2 is hijacked ("on scene and resolved"); the judge sees the false claim, an honest vehicle's contradicting observation, A2's trust bar drop and turn red, and the others cover its call.
6. Tick 120: F2 is hijacked ("sector clear"); same self-correction, F2 goes red and its vote is outvoted.
7. Tick 150: a sev 5 medical call far east of downtown, more than 2 km from every ambulance; no option reaches consensus and the human popup appears.
