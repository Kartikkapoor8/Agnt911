# Frontend merge notes: Matthew's design on live data

For Matthew, after `8ee4f2e` ("landing page, 40/60 card with live static map and fleet chips"). Two-minute read.

## What I checked

Your commits on `main` since the design pass: only `8ee4f2e`. It touches `App.tsx`, `Landing.tsx`, `MapView.tsx` (the `staticCamera` option) and `styles.css`. I compared each file against `03af695`, the last version before it.

`mine/matthew-experiments` (`da57d2e`) is an earlier draft of the same landing. It branches from before the voice work, so merging it would delete `frontend/src/voice/`. It is not on main, and I left it alone.

## Kept exactly as you made it

- The landing layout: a 40% card (wordmark, tagline, description, "Fleet" label, the seven type chips, **Start run**) beside a 60% map, and the 400 ms fade into the live map.
- The static landing map: fixed `LANDING_CAMERA`, not interactive, no zoom buttons, quieter road labels, neutral vehicle markers, amber call pins, no rings, arcs or routes.
- Your type, colours, spacing and button shape on the landing (`.landing-*`, `.start-demo`).
- The landing's auto-restart once the scripted run has finished, or paused for a human (see "One thing to decide" below).
- Everything you didn't touch: the live page (status strip, story cards, trust rings, negotiation arcs, catch sequence, judge panel, scoreboard, human popup, voice call, reset).

## Rewired to /state

- The description's "Seven vehicle agents" now counts the fleet in `/state` and keeps your wording ("Seven" today). That was the only hardcoded fact.
- Nothing else needed rewiring. The fleet chips (`state.vehicles`) and the static map (`state.vehicles`, `state.incidents`) were already live. No mock data, placeholder arrays or static JSON came in with your commit.

## Restored

- Nothing. Your commit removed no working feature. The old video-backdrop landing was replaced by your design, and I kept yours.

## Removed

- `frontend/public/landing.mp4`, the clip the video landing played. Your landing doesn't use it; it stays in git history.

## Walkthrough (mock backend, Playwright, 13/13)

Screenshots are in `frontend/screenshots/walkthrough/`:

| step | file | checked |
|---|---|---|
| Landing | `01-landing.png` | your card; chips equal the `/state` fleet; the map draws all 7 vehicles |
| Start run | (fade) | lands on the live map at t0-t1 |
| Negotiation | `02-negotiation-arcs.png` | dashed bid arcs with ETA labels at the t5 crash |
| Hijack A1 | `03-hijack.png` | "Compromise A1", skull chip on the marker and the fleet chip |
| Catch | `04-catch.png` | red "F2 checked A1: FALSE" line, failed-check card, ring drops |
| Drop a call | `05-drop-call.png` | pending pin, then the real J1 |
| Voice | `06-voice-tab.png` | Fake call tab with the ElevenLabs Start Call |
| Sev-5 popup | `07-sev5-popup.png` | two sev-5 medical drops after t145: one amber popup, "Two critical calls, one trusted ambulance…" |
| Scoreboard | `08-scoreboard.png` | Protected vs Dumb dispatcher, both "at t71" |
| Reset | `09a-reset-confirm.png`, `09-reset-landing.png` | R and Enter: fresh Protected run, back on your landing |

## One thing to decide

Kartik's rule is that a run restarts only from Start demo and from Reset. A page refresh keeps the run. Your landing also restarts the run on its own once the scripted run has finished (t170 and nothing moving) or paused for a human. This only happens while the landing is on screen, so a judge on the live map is never affected. It's a third trigger, though, so confirm it's wanted.
