// Map choreography: replays each round's events on a timeline so the negotiation reads on the map without the feed.
//
// A round's bids, checks, votes and plan reach the browser in one poll (the engine merges protocol events on
// commit), so played as they arrive they would all land in the same frame. The choreographer collects each
// poll's live events, then plays them back over a couple of seconds:
//   bid_placed     a faint dashed arc from the bidder to the call, labelled with its ETA
//   vote_cast      the arcs of each role's leading option brighten
//   plan_committed winner arcs turn solid blue (amber for plan_no_consensus), loser arcs fade over 400 ms,
//                  then the winner arc fades and the route line takes over
// Bids on calls that no plan in the same batch decided are dropped (vehicles bid on every open call).
//
// The catch sequence plays on the same timeline:
//   claim_verified (contradicted)  a red line flashes from the checker to the claimed spot, with
//                                  "P1 checked A1: FALSE" at its midpoint for 2 s
//   trust_changed                  the liar's trust ring animates down (held at the old value until now)
//   vehicle_untrusted              its outline turns red, its route line dissolves, and "removed from voting"
//                                  shows under the marker for 3 s (held trusted-looking until now)

import mapboxgl from "mapbox-gl";
import type { TypedEvent } from "./types";

type LngLat = [number, number];
type ArcState = "bid" | "lead" | "won" | "won_amber" | "lost";

interface Arc {
  vehicle: string;
  eta: number | null;
  state: ArcState;
  opacity: number;
  fade: { from: number; start: number; ms: number } | null; // fading to 0, then removed
  label: mapboxgl.Marker;
}

interface Negotiation {
  incident: string;
  arcs: Map<string, Arc>;
  votes: Map<string, Map<string, number>>; // role -> choice -> votes
  decidedAt: number | null; // when the plan beat played
}

/** Delay before each event type plays, in ms after the previous one. */
const GAP: Partial<Record<TypedEvent["type"], number>> = {
  bid_placed: 140,
  bid_withdrawn: 100,
  vote_cast: 60,
  plan_committed: 500,
  plan_no_consensus: 500,
  escalated_to_human: 500,
  claim_verified: 0, // a failed check gets CATCH_GAP instead (see gapFor)
  trust_changed: 700,
  vehicle_untrusted: 700,
  incident_discredited: 300,
};
const CATCH_GAP = 600;
const CHECK_MS = 2000;
const DISSOLVE_MS = 400;
const NOTE_MS = 3000;
const CHECKS = "check-lines";
const OPACITY: Record<ArcState, number> = { bid: 0.4, lead: 0.9, won: 1, won_amber: 1, lost: 0.4 };
const FADE_MS = 400;
const WINNER_HOLD_MS = 1400;
const SOURCE = "negotiation-arcs";

/** Points along a gentle curve from a to b (quadratic Bezier, bowed to one side). */
function curve(a: LngLat, b: LngLat, segments = 24): LngLat[] {
  const k = Math.cos((a[1] * Math.PI) / 180);
  const dx = (b[0] - a[0]) * k;
  const dy = b[1] - a[1];
  const mid: LngLat = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const ctrl: LngLat = [mid[0] - (dy * 0.18) / k, mid[1] + dx * 0.18];
  const pts: LngLat[] = [];
  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const u = 1 - t;
    pts.push([u * u * a[0] + 2 * u * t * ctrl[0] + t * t * b[0], u * u * a[1] + 2 * u * t * ctrl[1] + t * t * b[1]]);
  }
  return pts;
}

interface CheckFlash {
  checker: string;
  liar: string;
  call: string | null; // the claimed spot is the call it named, else where the liar really is
  start: number;
  label: mapboxgl.Marker;
}

export interface Choreo {
  /** Queue one live event (call for every event of a poll, in order). */
  push(event: TypedEvent): void;
  /** Advance the timeline and redraw. Call once per animation frame. */
  frame(now: number): void;
  /** Vehicles whose negotiation arcs are still on screen: hide their route line until the arc hands over. */
  holding(): Set<string>;
  /** The trust to draw for a vehicle: held at the old value until its trust_changed plays. */
  trustShown(vehicle: string, actual: number): number;
  /** Whether to draw a vehicle as untrusted: held off until its vehicle_untrusted plays. */
  untrustedShown(vehicle: string, actual: boolean): boolean;
  /** Route line opacity for a vehicle: dissolves over 400 ms once it is removed from voting. */
  routeOpacity(vehicle: string, untrusted: boolean, now: number): number;
  /** Remove everything (map teardown or a new run). */
  clear(): void;
}

export interface ChoreoHooks {
  vehicleAt: (id: string) => LngLat | null;
  callAt: (id: string) => LngLat | null;
  color: (token: string, fallback: string) => string;
  /** Other one-shot effects (the catch sequence) get their turn on the same timeline. */
  play?: (event: TypedEvent, now: number) => void;
  /** A held trust or untrusted value changed: redraw that vehicle's ring and outline now. */
  vehicleChanged?: (vehicle: string) => void;
  /** Show a short note under a vehicle marker for ms. */
  note?: (vehicle: string, text: string, ms: number) => void;
}

/** Build the choreographer on a loaded map. */
export function createChoreo(map: mapboxgl.Map, hooks: ChoreoHooks): Choreo {
  const negotiations = new Map<string, Negotiation>();
  let queue: { at: number; event: TypedEvent }[] = [];
  let playhead = 0;
  let batch: TypedEvent[] = [];
  const trustHold = new Map<string, number>(); // vehicle -> trust to draw until its next trust_changed plays
  const untrustHold = new Set<string>(); // vehicles to keep drawing trusted until vehicle_untrusted plays
  const dissolving = new Map<string, number>(); // vehicle -> when its route started dissolving
  let flashes: CheckFlash[] = [];

  map.addSource(SOURCE, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  const accent = hooks.color("--accent", "#3b82ff");
  const color: mapboxgl.ExpressionSpecification = [
    "match", ["get", "state"],
    "won", accent,
    "won_amber", hooks.color("--amber", "#ffb020"),
    "lead", accent,
    "lost", hooks.color("--grey", "#8a90a0"),
    hooks.color("--accent-bright", "#6ea4ff"),
  ];
  map.addLayer({
    id: `${SOURCE}-dashed`, type: "line", source: SOURCE, filter: ["!", ["get", "solid"]],
    layout: { "line-cap": "butt" },
    paint: { "line-color": color, "line-width": ["match", ["get", "state"], "lead", 2, 1.5], "line-opacity": ["get", "opacity"], "line-dasharray": [2, 2] },
  });
  map.addLayer({
    id: `${SOURCE}-solid`, type: "line", source: SOURCE, filter: ["get", "solid"],
    layout: { "line-cap": "round" },
    paint: { "line-color": color, "line-width": 3, "line-opacity": ["get", "opacity"] },
  });
  map.addSource(CHECKS, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: CHECKS, type: "line", source: CHECKS, layout: { "line-cap": "round" },
    paint: { "line-color": hooks.color("--red", "#ff4d5e"), "line-width": 2.5, "line-opacity": ["get", "opacity"] },
  });

  function negotiation(incident: string): Negotiation {
    let n = negotiations.get(incident);
    if (!n) negotiations.set(incident, (n = { incident, arcs: new Map(), votes: new Map(), decidedAt: null }));
    return n;
  }

  function arc(n: Negotiation, vehicle: string, eta: number | null): Arc {
    let a = n.arcs.get(vehicle);
    if (!a) {
      const el = document.createElement("div");
      el.className = "arc-eta";
      const label = new mapboxgl.Marker({ element: el }).setLngLat(hooks.callAt(n.incident) ?? [0, 0]).addTo(map);
      n.arcs.set(vehicle, (a = { vehicle, eta, state: "bid", opacity: 0, fade: null, label }));
    }
    if (eta !== null) a.eta = eta;
    a.label.getElement().textContent = a.eta !== null ? `${a.eta} s` : "";
    return a;
  }

  function fade(a: Arc, now: number) {
    if (!a.fade) a.fade = { from: a.opacity, start: now, ms: FADE_MS };
  }

  function play(e: TypedEvent, now: number) {
    const inc = e.incident;
    switch (e.type) {
      case "bid_placed":
        if (inc && e.vehicle) {
          const a = arc(negotiation(inc), e.vehicle, typeof e.data.eta_seconds === "number" ? e.data.eta_seconds : null);
          a.state = a.state === "lost" ? "bid" : a.state;
          a.opacity = OPACITY[a.state];
        }
        break;
      case "bid_withdrawn": {
        const a = inc && e.vehicle ? negotiations.get(inc)?.arcs.get(e.vehicle) : undefined;
        if (a) fade(a, now);
        break;
      }
      case "vote_cast": {
        if (!inc || typeof e.data.choice !== "string") break;
        const n = negotiation(inc);
        const role = String(e.data.role ?? "");
        const tally = n.votes.get(role) ?? new Map<string, number>();
        n.votes.set(role, tally);
        tally.set(e.data.choice, (tally.get(e.data.choice) ?? 0) + 1);
        const leaders = new Set([...n.votes.values()].map((t) => [...t.entries()].sort((x, y) => y[1] - x[1])[0]?.[0]));
        for (const a of n.arcs.values()) {
          if (a.fade) continue;
          a.state = leaders.has(a.vehicle) ? "lead" : "bid";
          a.opacity = OPACITY[a.state];
        }
        break;
      }
      case "plan_committed":
      case "plan_no_consensus":
      case "escalated_to_human": {
        if (!inc) break;
        const n = negotiation(inc);
        const winners = e.type === "escalated_to_human" ? [] : Array.isArray(e.data.assigned) ? (e.data.assigned as string[]) : [];
        const amber = e.type === "plan_no_consensus";
        winners.forEach((v) => arc(n, v, null)); // a pulled or kept vehicle may never have bid
        for (const a of n.arcs.values()) {
          if (winners.includes(a.vehicle)) {
            a.state = amber ? "won_amber" : "won";
            a.opacity = 1;
            a.fade = null;
          } else {
            a.state = "lost";
            fade(a, now);
          }
        }
        n.decidedAt = now;
        break;
      }
      case "claim_verified": {
        const verifier = typeof e.data.verifier === "string" ? e.data.verifier : null;
        if (e.data.result !== "contradicted" || !verifier || !e.vehicle) break;
        const el = document.createElement("div");
        el.className = "check-label";
        el.textContent = `${verifier} checked ${e.vehicle}: FALSE`;
        const label = new mapboxgl.Marker({ element: el }).setLngLat(hooks.vehicleAt(verifier) ?? [0, 0]).addTo(map);
        const call = e.data.subject === "bid" ? null : inc;
        flashes.push({ checker: verifier, liar: e.vehicle, call, start: now, label });
        break;
      }
      case "trust_changed":
        if (e.vehicle && typeof e.data.new === "number") {
          trustHold.set(e.vehicle, e.data.new);
          if (!queue.some((q) => q.event.type === "trust_changed" && q.event.vehicle === e.vehicle)) trustHold.delete(e.vehicle);
          hooks.vehicleChanged?.(e.vehicle);
        }
        break;
      case "vehicle_trusted_again":
        if (e.vehicle) {
          dissolving.delete(e.vehicle);
          hooks.vehicleChanged?.(e.vehicle);
        }
        break;
      case "vehicle_untrusted":
        if (e.vehicle) {
          untrustHold.delete(e.vehicle);
          dissolving.set(e.vehicle, now);
          hooks.vehicleChanged?.(e.vehicle);
          hooks.note?.(e.vehicle, "removed from voting", NOTE_MS);
        }
        break;
      default:
        break;
    }
    hooks.play?.(e, now);
  }

  /** A poll's events, minus bids and votes on calls no plan in this batch decided, onto the timeline. */
  function flush() {
    const events = batch;
    batch = [];
    const decided = new Set(
      events.filter((e) => ["plan_committed", "plan_no_consensus", "escalated_to_human"].includes(e.type)).map((e) => e.incident),
    );
    const now = performance.now();
    playhead = Math.max(playhead, now);
    for (const e of events) {
      if ((e.type === "bid_placed" || e.type === "bid_withdrawn" || e.type === "vote_cast") && !decided.has(e.incident)) continue;
      // Hold what the poll already shows until the event that explains it plays.
      if (e.type === "trust_changed" && e.vehicle && !trustHold.has(e.vehicle) && typeof e.data.old === "number") {
        trustHold.set(e.vehicle, e.data.old);
        hooks.vehicleChanged?.(e.vehicle);
      }
      if (e.type === "vehicle_untrusted" && e.vehicle) {
        untrustHold.add(e.vehicle);
        hooks.vehicleChanged?.(e.vehicle);
      }
      const failed = e.type === "claim_verified" && e.data.result === "contradicted";
      playhead += failed ? CATCH_GAP : GAP[e.type] ?? 0;
      queue.push({ at: playhead, event: e });
    }
  }

  function render(now: number) {
    const features: { type: "Feature"; properties: Record<string, unknown>; geometry: { type: "LineString"; coordinates: LngLat[] } }[] = [];
    for (const [iid, n] of negotiations) {
      const to = hooks.callAt(iid);
      for (const [vid, a] of n.arcs) {
        if (a.fade) {
          const k = Math.min(1, (now - a.fade.start) / a.fade.ms);
          a.opacity = a.fade.from * (1 - k);
          if (k >= 1) {
            a.label.remove();
            n.arcs.delete(vid);
            continue;
          }
        }
        const from = hooks.vehicleAt(vid);
        if (!from || !to) continue;
        const pts = curve(from, to);
        const solid = a.state === "won" || a.state === "won_amber";
        features.push({ type: "Feature", properties: { state: a.state, solid, opacity: a.opacity }, geometry: { type: "LineString", coordinates: pts } });
        a.label.setLngLat(pts[Math.floor(pts.length / 2)]);
        const el = a.label.getElement();
        el.style.opacity = String(a.opacity);
        el.dataset.state = a.state;
      }
      if (n.decidedAt !== null && now - n.decidedAt > WINNER_HOLD_MS) n.arcs.forEach((a) => fade(a, now));
      if (n.arcs.size === 0 && n.decidedAt !== null) negotiations.delete(iid);
    }
    (map.getSource(SOURCE) as mapboxgl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features });

    // Check flashes: up to full red fast, hold, then fade out by CHECK_MS; the label goes with it.
    const checks: typeof features = [];
    flashes = flashes.filter((f) => {
      const age = now - f.start;
      if (age > CHECK_MS) {
        f.label.remove();
        return false;
      }
      // checker -> the spot it claimed -> where the liar really is: the second leg is the lie
      const checker = hooks.vehicleAt(f.checker);
      const liar = hooks.vehicleAt(f.liar);
      const claimed = (f.call && hooks.callAt(f.call)) || liar;
      if (!checker || !claimed || !liar) return true;
      const opacity = age < 150 ? age / 150 : age < 700 ? 1 : 1 - (age - 700) / (CHECK_MS - 700);
      checks.push({ type: "Feature", properties: { opacity }, geometry: { type: "LineString", coordinates: [checker, claimed, liar] } });
      const span = (a: LngLat, b: LngLat) => Math.hypot(a[0] - b[0], a[1] - b[1]);
      const [a, b] = span(claimed, liar) > span(checker, claimed) ? [claimed, liar] : [checker, claimed];
      f.label.setLngLat([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]);
      f.label.getElement().style.opacity = String(Math.min(1, opacity * 1.5));
      return true;
    });
    (map.getSource(CHECKS) as mapboxgl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features: checks });
  }

  return {
    push(event) {
      if (batch.length === 0) queueMicrotask(flush);
      batch.push(event);
    },
    frame(now) {
      while (queue.length && queue[0].at <= now) play(queue.shift()!.event, now);
      render(now);
    },
    holding() {
      const held = new Set<string>();
      // released as the winner arc starts fading, so the route line fades in underneath it
      for (const n of negotiations.values()) for (const a of n.arcs.values()) if ((a.state === "won" || a.state === "won_amber") && !a.fade) held.add(a.vehicle);
      for (const q of queue) if (q.event.type === "plan_committed" || q.event.type === "plan_no_consensus") {
        (Array.isArray(q.event.data.assigned) ? (q.event.data.assigned as string[]) : []).forEach((v) => held.add(v));
      }
      return held;
    },
    trustShown(vehicle, actual) {
      return trustHold.get(vehicle) ?? actual;
    },
    untrustedShown(vehicle, actual) {
      return actual && !untrustHold.has(vehicle);
    },
    routeOpacity(vehicle, untrusted, now) {
      const since = dissolving.get(vehicle);
      if (since !== undefined) return Math.max(0, 1 - (now - since) / DISSOLVE_MS);
      return untrusted && !untrustHold.has(vehicle) ? 0 : 1;
    },
    clear() {
      for (const n of negotiations.values()) n.arcs.forEach((a) => a.label.remove());
      flashes.forEach((f) => f.label.remove());
      flashes = [];
      negotiations.clear();
      trustHold.clear();
      untrustHold.clear();
      dissolving.clear();
      queue = [];
      batch = [];
    },
  };
}
