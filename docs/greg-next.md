# Engine: what changed and what's next (main 59f0b40, updated for c183b9b)

From Kartik's audit of main on 2026-10-10. Paths are relative to `backend/`. Run `pytest -q` from `backend/` after each change. Main is green: 347 passed, 1 xfailed.

## Done, and checked live

Items 1, 3 and 4 from the last list all work against the real API:

- **Item 1, stepped clock (68a8ae5):** two runs through tick 80 committed rounds at ticks 0, 5, 20, 40 and 60, with zero `round_discarded`.
- **Item 3, service-time resolution (01da849):** I1 was covered at tick 16 and resolved at tick 36, and A1 was released.
- **Item 4, labels (b41cc81):** `/state.labels` holds every scenario label.

Each run made 148 API calls and took 212 s of wall time to reach tick 80.

## What I changed in your files (713dabd, "audit fixes: engine (Greg)")

1. **`agnt911/engine/runtime.py`, `Engine._new_run`** now calls `llm.reset_call_count()` (and `runtime.py` imports `llm`).
   - Why: `AGNT911_MAX_CALLS` (default 500) counts per process, and nothing reset it. One demo through tick 80 uses 148 calls, so a long-running server would stop after about one and a half full demos.
   - After the limit, every vehicle turn raises `CallLimitExceeded` and every round fails. Each run, at startup and on every `/mode`, now gets the full budget.
2. **`agnt911/engine/runtime.py`, `Engine.run_round`** now uses `SignedHistory(active_bus.history()[-100:], …)`. This was item 2 of the last list.
   - Why: by decision 5, every agent prompt carried 199-206 messages, about 120k characters of log. With the cap it's about 60k, and it stays bounded after that.
   - `/state.recent_messages` still holds the full bus history, because `on_message` refreshes it from `active_bus.history()`.
3. **`agnt911/engine/README.md`, "Remaining work":** the bullet saying `agnt911.trust` was absent and `integration.trust_stub` was true was stale. It now says Chris's `trust.py` is on main and the flag is false.

Separately, kartik-7e's 665718e changed your inject path for the sensor-feed hack:
- `_inject` stores a preset dict.
- `inject(vehicle_id, text=None, *, preset_id=None)`.
- `world.resolve_injection`.
- The api `Injection` body takes `preset_id` or `text`, and a `ValueError` maps to 422.

The same commit pinned `trust_stub` in two of your `test_engine` tests that asserted the stub.

## What's left, in priority order

### 1. Metrics: done (your 1f2e151)

`/state.metrics` and `/state.tick_records` are filled now. kartik-7e's end-to-end run on c183b9b found two follow-ups, which are items 5 and 6.

### 2. Checks in the shape Matthew asked for: done another way, nothing to build

Since c183b9b, `/state.check_events` exists again. It is derived from the typed stream (`events.check_events`) and has pass/fail with `trust_before` and `trust_after`.

Superseded by the typed event stream ("events: typed stream for frontend animations"). `/state.events` now carries `claim_verified` (`claim_id`, `verifier`, `result`, `claim_type`) and `trust_changed` (`old`, `new`, `reason`) for every check, emitted at verification time and merged when the round commits. If Matthew wants pass/fail wording, map it on the client: `confirmed` is pass, `contradicted` is fail, `unverifiable` is inconclusive (the same mapping as `trust.feed_result`). See docs/events.md. Don't add `check_events`.

### 3. Pacing: vehicles barely arrive in a 3-minute demo

At 12 m/s, P1 (dispatched to I3 at tick 5) and F1 (sent to I2 at tick 0) were still en route at tick 80, which is 3.5 minutes of wall time. Agree a speed with the team first. Then paste:

> 1. **`backend/agnt911/engine/runtime.py`:** add `speed_mps: float = 12.0` to `Engine.__init__`, with the same positive and finite check as `service_s`, and store it. In `step()`, call `tick(self.world, dt_s, self.speed_mps)`.
> 2. **`api.create_app`:** pass `speed_mps=float(os.getenv("AGNT911_SPEED_MPS", "12"))`.
> 3. **Test:** in `test_api_reads_clock_and_service_settings`, set `AGNT911_SPEED_MPS=25` and assert `engine.speed_mps == 25`.

### 4. Check the human popup live

No escalation fired in my two live runs, which stopped at tick 80. Since ea14803 the scenario has an escalation beat at tick 140: two severity-5 medical calls (I6, I7) 600 m apart, with A2 the only trusted free ambulance. `scripts/check_scenario.py` asserts `escalated_to_human` fires there. Run it once against the real API and confirm in the UI that `/state.paused` goes true and `/resolve` resumes the clock.

### 5. `first_untrusted_tick` is one tick late

In kartik-7e's real run, the `trust_changed` events that made vehicles untrusted came at ticks 100 and 140, but `metrics.first_untrusted_tick` says 101 and 141. The cause is in `Engine.step()`: the `trust.tick_record(...)` block runs after `tick(self.world, dt_s)` has already advanced `world.tick`, so a round's trust changes and checks get filed under the next tick. Paste:

> In `backend/agnt911/engine/runtime.py`, `Engine.step()`: build the tick record before calling `tick(self.world, dt_s)`, so that record T describes tick T after its round. Move the `track_incidents` + `tick_record` + append + reset + `metrics` + `_log("tick_record")` lines to just after the `dt_s = ...` line. Keep the `track_incidents` call before service completion, because it captures arrivals before responders are released. Test: with a fake LLM that makes A2 lie at tick 5, assert `metrics["first_untrusted_tick"]["A2"]` equals the tick of A2's `trust_changed` event that sets `untrusted`.

### 6. `comparison` only files a run when the mode switches

`set_mode` appends the finished run to `comparison`, but the run in progress lives only in `metrics`. Matthew has to pair `metrics` (now) with `comparison` (earlier runs). Either tell Matthew that, or paste:

> In `backend/agnt911/engine/runtime.py`, `_state()`: add `current={"run_id": self.run_id, "baseline": self.world.baseline, "metrics": self.metrics}` next to `comparison`, so the frontend reads the same record shape for the live run as for finished ones.

## Also changed in your files since the audit (kartik-7e, ea14803)

- **`Engine._plan_assignments(plans, human=False)`:** with `human=True`, a vehicle already committed to another call may be moved. Only `resolve()` passes it; protocol rounds still can't steal a committed vehicle.
- **`Engine.resolve()`:** after a human re-assignment, it re-pends every call left `open` (for example, the call the moved vehicle came from), so the next round renegotiates it.

## FYI (Kartik's call, not yours)

The scenario was redesigned in 5b12021 (a tight map and catchable hijacks). In kartik-7e's real batch run, the fleet caught hijacked A1 at tick 60 and A3 at tick 120.
