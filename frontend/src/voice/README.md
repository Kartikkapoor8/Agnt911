# Voice dispatcher

A judge talks to our ElevenLabs dispatcher agent (`agent_1101m4m2jypsfw6scn9fskzn47je`) from the **Fake call** tab. The agent fills in the call through three **client tools**. These run in the browser against our own backend through the Vite `/api` proxy, so ElevenLabs never needs to reach this laptop: no webhook, and no tunnel on venue wifi. The fleet then negotiates the call like any other, and the map draws it from `/state`. *Demo only: it does not contact emergency services.*

## Files

- `VoiceCall.tsx`: Start Call / End Call, status (connecting / live / ended / error), live transcript, a 20 s countdown, and a hard client-side stop at 25 s. Uses `ConversationProvider` and `useConversation({ clientTools })` over WebRTC.
- `tools.ts`: the three tool handlers. One phone call = one incident.
- `geocode.ts`: Mapbox geocoding bounded to the demo map, plus `DEFAULT_CALL_LOCATION` (1st St & San Fernando St).
- `badge.ts`: the "called in" badge on the call's map pin.
- `mock.ts`, `voice.e2e.mjs`: a mock conversation and the Playwright check that drives the tools without a mic.

The backend side is `GET /voice/token` (a WebRTC conversation token) and `POST /incident/{id}` (update a call), both in `backend/agnt911/engine/api.py`. `POST /incident` also accepts `source: "caller"`.

## Environment

| Variable | Where | What |
|---|---|---|
| `ELEVENLABS_API_KEY` | `backend/.env` | Used only by `GET /voice/token`; never sent to the browser |
| `ELEVENLABS_AGENT_ID` | `backend/.env` | `agent_1101m4m2jypsfw6scn9fskzn47je` |
| `VITE_MAPBOX_TOKEN` | `frontend/.env` | The existing map token, also used for geocoding. Without it, every call lands on `DEFAULT_CALL_LOCATION` |

## Tools to add in the ElevenLabs agent dashboard

Add each one under Agent → Tools → Add tool → **Client**, with "Wait for response" on. The names must match exactly.

```json
{
  "type": "client",
  "name": "update_incident",
  "description": "Put the caller's emergency on the dispatch map, or update it. Call it as soon as you know the type of emergency, then again whenever the caller gives a better location, severity, name or details. Every call during one phone call updates the same incident; it never creates a second one. Returns incident_id, label and location (with the place name and fallback=true if the location could not be found and a default corner was used).",
  "expects_response": true,
  "parameters": {
    "type": "object",
    "properties": {
      "type": {
        "type": "string",
        "enum": ["medical", "fire", "crash", "hazard", "shooting"],
        "description": "Kind of emergency. medical: illness or injury. fire: smoke or flames. crash: vehicle collision. hazard: gas leak, chemical, downed line. shooting: shots fired or armed person."
      },
      "severity": {
        "type": "integer",
        "description": "3, 4 or 5. 5 = life-threatening right now (not breathing, trapped, active shooter). 4 = serious. 3 = urgent but stable. Leave out if unsure."
      },
      "location_text": {
        "type": "string",
        "description": "Where it is in downtown San Jose, as a street address or intersection with full street names, e.g. 'S 2nd St and E Santa Clara St'."
      },
      "caller_name": {
        "type": "string",
        "description": "The caller's first name, if they gave it."
      },
      "details": {
        "type": "string",
        "description": "One short line on what is happening, e.g. 'man collapsed, not breathing'."
      }
    },
    "required": ["type"]
  }
}
```

```json
{
  "type": "client",
  "name": "request_demo_dispatch",
  "description": "Tell the vehicle fleet to respond to the incident created by update_incident. Call it once, right before you tell the caller help is being dispatched. Calling it again is safe and only reports the current status. Returns status ('negotiating' at first) and incident_id.",
  "expects_response": true,
  "parameters": { "type": "object", "properties": {} }
}
```

```json
{
  "type": "client",
  "name": "get_dispatch_status",
  "description": "Check which units the fleet has sent. Returns status (negotiating, dispatched, on_scene, resolved, waiting_for_human), consensus, and assigned: a list of {id, type, eta_seconds}. assigned is empty while the fleet is still negotiating. Only state unit counts, types and ETAs that appear in this response; never guess.",
  "expects_response": true,
  "parameters": { "type": "object", "properties": {} }
}
```

Lines to add to the agent's system prompt:

> This is a demo. You never contact real emergency services. Call update_incident as soon as you know the type of emergency, and again when you learn more. Then call request_demo_dispatch and say help is being dispatched. Call get_dispatch_status before you mention any unit, and say only the counts, types and ETAs it returns. If assigned is empty, say the units are being assigned. Keep the call under 20 seconds and end it with end_call.

## Behaviour notes

- **The first `update_incident` creates the call** with `POST /incident` (`source: "caller"`, label `"<caller_name>: <details>"`). Later calls send `POST /incident/{id}` with severity, label and, if `location_text` is given, a new location. The incident type is fixed by the first call.
- **Geocoding** appends ", San Jose, CA" and searches only inside the demo map. Mapbox's coarse matches (the city, "Downtown San Jose", the county) count as no match and fall back to `DEFAULT_CALL_LOCATION`. Informal phrasings such as "2nd and Santa Clara" usually fall back; full street names ("S 2nd St and E Santa Clara St") resolve to the corner. That's why the tool description asks for full street names.
- **Severity** defaults to 4 when the agent leaves it out. **ETAs** are the straight-line distance at the engine's 12 m/s, recomputed on every status call.
- **The badge** appears on the pin when the next `/state` poll draws it, at most ~0.5 s after `update_incident` returns.

## Test without a mic

Start the backend with `AGNT911_LLM=fake` and the Vite dev server, then:

```
PLAYWRIGHT_DIR=<dir containing node_modules/playwright-core> CHROMIUM=<path to Chromium> \
APP_URL=http://127.0.0.1:5173 node src/voice/voice.e2e.mjs
```

The test sets `window.__agnt911VoiceMock = true` before the page loads, so `useConversation` is swapped for `mock.ts`, which works in the dev server only. During a mock call, `window.__agnt911Voice` exposes `say`, `tool`, `hangUp` and `fail`.

## Removing it

Delete `frontend/src/voice/` and the `@elevenlabs/react` dependency; the glob import in `components/FakeCallBox.tsx` then finds nothing, and the old mic placeholder comes back.
Optionally drop `GET /voice/token`, `POST /incident/{id}` and the `"caller"` source from `backend/agnt911/engine/api.py`, `models.py`, `schema.md` and `frontend/src/types.ts`, plus `backend/tests/test_voice.py`.
