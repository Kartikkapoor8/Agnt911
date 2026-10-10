# Agnt911

Emergency vehicles that dispatch themselves.

Built at HACKDEV, the UCSC AI Hackathon (October 10-11, 2026), for the Cisco Cloud AI track: "When AI Agents Disagree".

## What it is

Today, a human dispatcher decides which ambulance, fire engine, or police car goes to each emergency.

In Agnt911 there is no dispatcher. Every vehicle is its own AI agent. When a call comes in, the vehicles talk to each other and decide who goes.

Then we hack one of the vehicles so that it lies. The other vehicles have to notice, stop trusting it, and still get help to the scene.

## Why the vehicles disagree

Each vehicle wants something different:

| Vehicle | What it cares about most |
|---|---|
| Ambulance | Reaching the patient fastest |
| Fire engine | Containing the fire |
| Police car | Keeping the scene safe |
| All of them | Staying close to their own district |

Because these goals pull in different directions, the vehicles have to negotiate.

## How a decision is made

1. **A call comes in.** For example, a car crash appears on the map.
2. **Vehicles bid.** Each vehicle that can help says how well placed it is, based on distance, equipment, and district coverage.
3. **Vehicles vote.** They argue for a few rounds, then vote on a plan.
4. **The plan locks.** The chosen vehicles drive to the scene.

If something changes, such as a crash turning into a pileup, the vehicles negotiate again.

## How a lying vehicle gets caught

A hacked vehicle might say "I'm already on scene, it's handled" when it is not. If the others believe it, nobody goes.

Agnt911 protects against this in three ways:

- **Signed messages.** Every message is signed by the vehicle that sent it, so everyone knows who said what.
- **Checking claims.** A vehicle near the scene compares the claim to what its own sensors see. Important claims are always checked. Others are checked at random.
- **Trust scores.** Every vehicle starts with the same trust. A vehicle caught lying loses trust, and its vote counts for less. If its trust falls far enough, the others ignore it and cover its calls.

The fleet has 7 vehicles and keeps working with up to 2 of them hacked.

## When it asks a human

Sometimes the vehicles cannot agree on something that matters, such as two critical calls and only one ambulance. In that case the system stops and shows a person both options and the evidence. The person decides.

## What the demo shows

The screen has three columns:

- **Live feed (left):** every call, claim, check, trust drop, and decision as it happens, color-coded by vehicle.
- **City map (center):** vehicles moving through real San Jose streets, incidents as pins, untrusted vehicles in red.
- **Fleet panel (right):** one card per vehicle with its status, trust bar, and current job. A judge clicks a card to hack that vehicle, using a preset lie or their own text.

Once a vehicle is hacked, nothing is staged. The feed shows its false claim, another vehicle's contradicting check, the trust drop, and the re-vote. A separate tab compares the results against a single central dispatcher.

1. Seven vehicles (3 ambulances, 2 fire engines, 2 police cars) handle 5 incidents.
2. A crash becomes a pileup and the plan changes live.
3. A judge picks a vehicle and types an instruction that makes it lie.
4. Another vehicle spots the lie. The liar turns red on the map and others cover its call.
5. A second vehicle is hacked. The system still works.
6. The vehicles reach a deadlock and ask a human.
7. A comparison run uses one central dispatcher that believes every report. The hacked vehicle says "handled" and the patient never gets help.

## What we measure

Every run records the same numbers, so the two modes can be compared side by side:

- Incidents covered
- Time to reach each incident
- Bad dispatches
- Rounds needed to agree
- Time taken to catch a liar
- Number of checks per round

## How it is built

| Part | What it does | Built with |
|---|---|---|
| Vehicle agents | Bid, argue, and vote | Python, Claude Sonnet 5.5 via Anthropic API, one call per vehicle per round, JSON output validated with Pydantic |
| Trust engine | Checks claims, keeps trust scores, weights votes, decides when to ask a human | Pure Python module, no API calls |
| World and message bus | Tracks the city, moves vehicles, delivers HMAC-signed messages, logs every round | Python 3.12, asyncio |
| Server | Serves the world state and the judge's hacking endpoint | FastAPI, polled every 500 ms, no websockets |
| Screen | Map, transcript, trust bars, comparison view | React + TypeScript, Vite, Mapbox GL JS on real San Jose streets |
| Logs | Stores the numbers from each run | In-memory dicts, one JSONL file per run |

Vehicles use real lat/lng and straight-line distance; they move toward targets in a straight line, no routing API.

## Running it

This README and schema.md were written before the opening ceremony on Oct 10. No application code existed before hacking started at 1:00 PM PT.

The project is being written during the hackathon. Setup and run steps will be added here when the first working version is ready.

## Team

| Who | Owns |
|---|---|
| Greg Chu | Core engine: world state, tick loop, message bus, /state and /inject endpoints, logging, integration |
| Christopher Zarraga | Trust engine: claim checking, trust scores, vote weights, escalation, baseline mode, metrics |
| Matthew Cendana | Frontend: Mapbox map, transcript, trust bars, baseline toggle, metrics view |
| Kartik Kapoor | Vehicle agents and negotiation protocol, scenario script, judge preset attacks, demo, Devpost, pitch |

## Credits

The trust-weighted voting idea draws on the AAAI 2026 paper "Rethinking the Reliability of Multi-agent Systems: A Perspective from Byzantine Fault Tolerance" ([arxiv.org/abs/2511.10400](https://arxiv.org/abs/2511.10400)).

All code is written during the event. Open-source libraries we use are listed with their licenses in the submission.