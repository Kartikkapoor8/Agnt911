# Event stream

`/state.events` is the typed event stream for frontend animations: the last 500 `Event`s, oldest first. Each event is `{tick, type, vehicle, incident, data}`, where `vehicle` and `incident` are ids or null. The run's JSONL keeps every event as a `typed_event` record. Vehicles never see the stream.

Protocol events (bids, votes, checks, trust, plans) are emitted while the round runs, but they only reach the stream if the engine commits that round. A round discarded as stale leaves no events. Events from the same round share its tick, in the order they happened. Animate in list order, and use `tick` to place them on the timeline.

One line per type: what it carries, and the animation it is meant to drive.

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
- `escalated_to_human` (protocol): `incident`; data `options` (vehicle ids), `evidence` (votes, contradicting claims), `role`. Map dims, the incident pin glows, and the human popup opens with the options and the evidence.
- `human_resolved` (engine): data `assignments` (vehicle id to incident id). Popup closes, and solid route lines are drawn for the human's choice with a "human" badge.
- `vehicle_arrived` (engine): `vehicle`, `incident`. Marker settles on the pin with a small bounce, and the route line turns solid.
- `incident_discredited` (engine): `vehicle` (the police car that found nothing), `incident`; data `source_id`, `verifier`, `credibility_old`, `credibility_new`. The pin cracks and turns hatched grey with a "false report" stamp, and the caller's credibility meter drops. An `incident_resolved` for the same call follows.
- `incident_resolved` (engine): `incident`. Pin fades to grey with a check mark, route lines clear, and responders return to idle.
