# Engine: what changed and what's next (main 59f0b40)

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

### 1. Fill `/state.metrics` from Chris's functions (Matthew's comparison tab is empty)

Paste this into your agent:

> In `backend/agnt911/engine/runtime.py`, wire Chris's metrics into the engine. Add `from .. import trust` at the top; use the real module directly, not `protocol.trust`, because two tests pin `protocol.trust` to the stub.
>
> 1. **`_new_run`:** add `self._incident_times, self._tick_records = {}, []`, `self._checks_this_tick = 0` and `self._escalated_this_tick = False`.
> 2. **`run_round`, the commit block:** after the loop that appends to `self.checks`, add `self._checks_this_tick += len(checks)`. Where it sets `self.escalation`, also set `self._escalated_this_tick = self._escalated_this_tick or bool(escalated)`.
> 3. **`step()`:** after `self._apply_events()`, add:
>    ```python
>    self._incident_times = trust.track_incidents(self._incident_times, self.world)
>    self._tick_records.append(trust.tick_record(self.world, self._incident_times,
>        "baseline" if self.world.baseline else "protected", self._checks_this_tick, self._escalated_this_tick))
>    self._checks_this_tick, self._escalated_this_tick = 0, False
>    self.metrics = trust.metrics(self._tick_records, self._incident_times)
>    ```
>    `step()` returns early while an escalation is pending. That's why the escalation flag is carried until the first step after the human resolves it.
> 4. **`_state()`:** add `tick_records=list(self._tick_records)` so Matthew can chart checks per tick and trust per vehicle.
> 5. **Tests:** in `tests/test_engine.py::test_incident_negotiation_dispatch_movement_and_api_end_to_end`, change `assert not state["integration"]["metrics_available"]` to `assert state["integration"]["metrics_available"]`. Add a test that steps the demo to tick 41 with the fake LLM and asserts:
>    - `state["metrics"]["incidents_served"] >= 1`,
>    - `len(state["tick_records"]) == 41`,
>    - `state["metrics"]["checks_total"] > 0`.
>
> Note: `set_mode` already copies `self.metrics` into `comparison`, so the baseline and protected runs line up once this lands.

### 2. Checks in the shape Matthew asked for: done another way, nothing to build

Superseded by the typed event stream ("events: typed stream for frontend animations"). `/state.events` now carries `claim_verified` (`claim_id`, `verifier`, `result`, `claim_type`) and `trust_changed` (`old`, `new`, `reason`) for every check, emitted at verification time and merged when the round commits. If Matthew wants pass/fail wording, map it on the client: `confirmed` is pass, `contradicted` is fail, `unverifiable` is inconclusive (the same mapping as `trust.feed_result`). See docs/events.md. Don't add `check_events`.

### 3. Pacing: vehicles barely arrive in a 3-minute demo

At 12 m/s, P1 (dispatched to I3 at tick 5) and F1 (sent to I2 at tick 0) were still en route at tick 80, which is 3.5 minutes of wall time. Agree a speed with the team first. Then paste:

> 1. **`backend/agnt911/engine/runtime.py`:** add `speed_mps: float = 12.0` to `Engine.__init__`, with the same positive and finite check as `service_s`, and store it. In `step()`, call `tick(self.world, dt_s, self.speed_mps)`.
> 2. **`api.create_app`:** pass `speed_mps=float(os.getenv("AGNT911_SPEED_MPS", "12"))`.
> 3. **Test:** in `test_api_reads_clock_and_service_settings`, set `AGNT911_SPEED_MPS=25` and assert `engine.speed_mps == 25`.

### 4. Check the human popup live

No escalation fired in my two live runs, which stopped at tick 80. Since ea14803 the scenario has an escalation beat at tick 140: two severity-5 medical calls (I6, I7) 600 m apart, with A2 the only trusted free ambulance. `scripts/check_scenario.py` asserts `escalated_to_human` fires there. Run it once against the real API and confirm in the UI that `/state.paused` goes true and `/resolve` resumes the clock.

## Also changed in your files since the audit (kartik-7e, ea14803)

- **`Engine._plan_assignments(plans, human=False)`:** with `human=True`, a vehicle already committed to another call may be moved. Only `resolve()` passes it; protocol rounds still can't steal a committed vehicle.
- **`Engine.resolve()`:** after a human re-assignment, it re-pends every call left `open` (for example, the call the moved vehicle came from), so the next round renegotiates it.

## FYI (Kartik's call, not yours)

The scenario was redesigned in 5b12021 (a tight map and catchable hijacks). In kartik-7e's real batch run, the fleet caught hijacked A1 at tick 60 and A3 at tick 120.
