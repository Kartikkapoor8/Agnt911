import { useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import { onLiveEvent } from "../dispatch";
import { glyphSvg, INCIDENT_DRAG_TYPE } from "../glyphs";
import type { Incident, IncidentType, Location, TypedEvent, Vehicle } from "../types";

const TOKEN = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined;
const STYLE = "mapbox://styles/mapbox/dark-v11";
const DOWNTOWN_SAN_JOSE: [number, number] = [-121.8811, 37.3352]; // lng, lat
const GLIDE_MS = 500; // one poll interval, so a marker arrives just as the next position lands
const LINES = "assignment-lines"; // GeoJSON source and layer: a thin line from each assigned vehicle to its call
const INCIDENT_TYPES: IncidentType[] = ["medical", "fire", "crash", "hazard", "shooting"];
const FLASH_MS = 1800; // longest one-shot marker animation in styles.css
const SCENE_LIFT_PX = 26; // on-scene vehicles sit this far above their call's pin...
const SCENE_SPACING_PX = 28; // ...side by side

interface Tracked {
  marker: mapboxgl.Marker;
  el: HTMLDivElement; // inner element: Mapbox owns the outer one's transform, so classes and animations go here
  from: [number, number];
  to: [number, number];
  start: number;
}

interface Pair {
  vehicle: string;
  incident: [number, number];
  untrusted: boolean;
}

/** What the pointer is over. MapView positions the popover; the caller renders what goes in it. */
export interface MapTarget {
  kind: "vehicle" | "incident";
  id: string;
}

/** A feed click: pulse this marker. nonce changes on every click so the same line can fire again. */
export interface Highlight extends MapTarget {
  nonce: number;
}

function boundsOf(points: Location[]): mapboxgl.LngLatBounds {
  const bounds = new mapboxgl.LngLatBounds();
  points.forEach((p) => bounds.extend([p.lng, p.lat]));
  return bounds;
}

/** A custom property from :root, so the line layer's colours live in styles.css with the rest. */
function cssVar(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

const flashTimers = new WeakMap<HTMLElement, Map<string, number>>();

/** Restart a one-shot animation class (styles.css, .anim-*) on a marker element. */
function flash(el: HTMLElement, cls: string, ms = FLASH_MS) {
  const timers = flashTimers.get(el) ?? new Map<string, number>();
  flashTimers.set(el, timers);
  window.clearTimeout(timers.get(cls));
  el.classList.remove(cls);
  void el.offsetWidth; // reflow, so re-adding the class restarts the animation
  el.classList.add(cls);
  timers.set(cls, window.setTimeout(() => el.classList.remove(cls), ms));
}

/** Which marker a live event animates, and how. null = no map animation for this event. */
function animationFor(e: TypedEvent): { key: string; cls: string } | null {
  const on = (kind: "vehicle" | "incident", cls: string) => {
    const id = kind === "vehicle" ? e.vehicle : e.incident;
    return id ? { key: `${kind}:${id}`, cls } : null;
  };
  switch (e.type) {
    case "call_received": return on("incident", "anim-new");
    case "call_escalated": return on("incident", "anim-escalate");
    case "escalated_to_human": return on("incident", "anim-escalate");
    case "incident_resolved": return on("incident", "anim-resolved");
    case "incident_discredited": return on("incident", "anim-discredited");
    case "claim_verified":
      if (e.data.result === "contradicted") return on("vehicle", "anim-caught");
      if (e.data.result === "confirmed") return on("vehicle", "anim-confirmed");
      return null;
    case "vehicle_untrusted": return on("vehicle", "anim-untrusted");
    case "vehicle_trusted_again": return on("vehicle", "anim-trusted");
    case "vehicle_arrived": return on("vehicle", "anim-arrive");
    case "vehicle_compromised": return on("vehicle", "anim-hacked");
    default: return null;
  }
}

interface Props {
  vehicles: Record<string, Vehicle>;
  incidents: Record<string, Incident>;
  center: Location | null;
  labels?: Record<string, string>; // incident id -> display text, shown next to the pin
  selectedId?: string | null;
  onSelectVehicle?: (vehicleId: string) => void;
  compromised?: string[]; // judge-only: these markers get a skull badge
  highlight?: Highlight | null;
  renderPopover?: (target: MapTarget) => ReactNode; // hover card content
  onDropIncident?: (type: IncidentType, at: Location) => void; // an incident icon was dropped on the map
  fitPadding?: number | mapboxgl.PaddingOptions; // keep framed points clear of floating panels
}

/** Show the Mapbox map with a moving marker per vehicle, a pin per incident, and a line from each assigned vehicle to its call. */
export function MapView({
  vehicles,
  incidents,
  center,
  labels = {},
  selectedId = null,
  onSelectVehicle,
  compromised = [],
  highlight = null,
  renderPopover,
  onDropIncident,
  fitPadding = 80,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<mapboxgl.Map | null>(null);
  const vehicleMarkers = useRef(new Map<string, Tracked>());
  const incidentMarkers = useRef(new Map<string, { marker: mapboxgl.Marker; el: HTMLDivElement }>());
  const centered = useRef(false);
  const onSelect = useRef(onSelectVehicle);
  onSelect.current = onSelectVehicle; // marker click handlers are created once, so read the latest callback
  const incidentsNow = useRef(incidents);
  incidentsNow.current = incidents;
  const pairs = useRef<Pair[]>([]);
  const linesReady = useRef(false);
  const linesDirty = useRef(true);
  const pending = useRef(new Map<string, string[]>()); // animations for markers that don't exist yet
  const [hover, setHover] = useState<MapTarget | null>(null);
  const hoverNow = useRef<MapTarget | null>(null);
  hoverNow.current = hover;
  const popover = useRef<HTMLDivElement>(null);

  // Create the map once, and run one animation loop that glides every vehicle marker, its line, and the popover.
  useEffect(() => {
    if (!TOKEN || !container.current) return;
    mapboxgl.accessToken = TOKEN;
    const m = new mapboxgl.Map({ container: container.current, style: STYLE, center: DOWNTOWN_SAN_JOSE, zoom: 13.3 });
    m.addControl(new mapboxgl.NavigationControl({ showCompass: false }), "bottom-right");
    map.current = m;
    m.on("load", () => {
      m.addSource(LINES, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({
        id: LINES,
        type: "line",
        source: LINES,
        layout: { "line-cap": "round" },
        paint: {
          "line-color": ["case", ["get", "untrusted"], cssVar("--route-untrusted", "#e5484d"), cssVar("--route", "#d0d4dc")],
          "line-width": 1.4,
          "line-opacity": 0.75,
        },
      });
      linesReady.current = true;
      linesDirty.current = true;
    });

    let frame = requestAnimationFrame(function animate(now) {
      let moving = false;
      for (const t of vehicleMarkers.current.values()) {
        const k = Math.min(1, (now - t.start) / GLIDE_MS);
        if (k < 1) moving = true;
        t.marker.setLngLat([t.from[0] + (t.to[0] - t.from[0]) * k, t.from[1] + (t.to[1] - t.from[1]) * k]);
      }
      if (linesReady.current && (moving || linesDirty.current)) {
        linesDirty.current = false;
        const features = pairs.current.flatMap((p) => {
          const t = vehicleMarkers.current.get(p.vehicle);
          if (!t) return [];
          const at = t.marker.getLngLat();
          return [{
            type: "Feature" as const,
            properties: { untrusted: p.untrusted },
            geometry: { type: "LineString" as const, coordinates: [[at.lng, at.lat], p.incident] },
          }];
        });
        (m.getSource(LINES) as mapboxgl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features });
      }
      const h = hoverNow.current;
      const pop = popover.current;
      if (h && pop) {
        const inc = incidentsNow.current[h.id];
        const at = h.kind === "vehicle"
          ? vehicleMarkers.current.get(h.id)?.marker.getLngLat()
          : inc && new mapboxgl.LngLat(inc.location.lng, inc.location.lat);
        if (at) {
          const p = m.project(at);
          pop.style.transform = `translate(${Math.round(p.x)}px, ${Math.round(p.y)}px)`;
          pop.classList.toggle("flip", p.x > m.getContainer().clientWidth - 360);
          pop.style.visibility = "visible";
        }
      }
      frame = requestAnimationFrame(animate);
    });

    return () => {
      cancelAnimationFrame(frame);
      vehicleMarkers.current.clear();
      incidentMarkers.current.clear();
      m.remove();
      map.current = null;
      centered.current = false;
      linesReady.current = false;
    };
  }, []);

  // Live events animate their marker once. A pin for a brand-new call is created after this runs, so queue it.
  useEffect(
    () =>
      onLiveEvent((e) => {
        const a = animationFor(e);
        if (!a) return;
        const [kind, id] = a.key.split(":");
        const el = kind === "vehicle" ? vehicleMarkers.current.get(id)?.el : incidentMarkers.current.get(id)?.el;
        if (el) flash(el, a.cls);
        else pending.current.set(a.key, [...(pending.current.get(a.key) ?? []), a.cls]);
      }),
    [],
  );

  function runPending(key: string, el: HTMLElement) {
    const queued = pending.current.get(key);
    if (!queued) return;
    pending.current.delete(key);
    queued.forEach((cls) => flash(el, cls));
  }

  // First data: frame the whole fleet and every call, so no vehicle starts off-screen.
  // Fall back to the scenario's map_center if there is nothing to frame.
  useEffect(() => {
    const m = map.current;
    if (!m || centered.current) return;
    const points = [...Object.values(vehicles), ...Object.values(incidents)].map((x) => x.location);
    if (points.length > 1) {
      m.fitBounds(boundsOf(points), { padding: fitPadding, maxZoom: 14.5, duration: 0 });
      centered.current = true;
    } else if (center) {
      m.jumpTo({ center: [center.lng, center.lat] });
      centered.current = true;
    }
  }, [vehicles, incidents, center]); // fitPadding is read once, on the first frame

  // Sync vehicle markers with the latest poll: new target, start a fresh glide from where it is now.
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const now = performance.now();
    const tracked = vehicleMarkers.current;
    // Vehicles on scene sit on their call's pin: lift them into a row above it so neither hides the other.
    const onScene = new Map<string, string[]>();
    for (const v of Object.values(vehicles)) {
      if (v.status === "on_scene" && v.target_incident) onScene.set(v.target_incident, [...(onScene.get(v.target_incident) ?? []), v.id]);
    }
    for (const v of Object.values(vehicles)) {
      const to: [number, number] = [v.location.lng, v.location.lat];
      let t = tracked.get(v.id);
      if (!t) {
        const wrap = document.createElement("div");
        wrap.className = "vehicle-wrap";
        const el = wrap.appendChild(document.createElement("div"));
        el.className = `vehicle-marker vehicle-${v.type}`;
        el.dataset.id = v.id;
        el.innerHTML =
          `<span class="vm-ring"></span><span class="vm-dot">${glyphSvg(v.type, 12)}</span>` +
          `<span class="vm-id">${v.id}</span><span class="vm-skull" title="Hacked (judge view only)">${glyphSvg("skull", 10)}</span>`;
        wrap.addEventListener("click", (e) => {
          e.stopPropagation();
          onSelect.current?.(v.id);
        });
        wrap.addEventListener("mouseenter", () => setHover({ kind: "vehicle", id: v.id }));
        wrap.addEventListener("mouseleave", () => setHover((h) => (h?.kind === "vehicle" && h.id === v.id ? null : h)));
        const marker = new mapboxgl.Marker({ element: wrap }).setLngLat(to).addTo(m);
        t = { marker, el, from: to, to, start: now };
        tracked.set(v.id, t);
        runPending(`vehicle:${v.id}`, el);
      } else if (t.to[0] !== to[0] || t.to[1] !== to[1]) {
        const at = t.marker.getLngLat();
        t.from = [at.lng, at.lat];
        t.to = to;
        t.start = now;
      }
      const row = v.status === "on_scene" && v.target_incident ? onScene.get(v.target_incident) ?? [] : [];
      const k = row.indexOf(v.id);
      t.marker.setOffset(k < 0 ? [0, 0] : [Math.round((k - (row.length - 1) / 2) * SCENE_SPACING_PX), -SCENE_LIFT_PX]);
      t.el.classList.toggle("untrusted", v.untrusted);
      t.el.classList.toggle("compromised", compromised.includes(v.id));
      t.el.classList.toggle("selected", v.id === selectedId);
      t.el.classList.toggle("selectable", Boolean(onSelect.current));
      t.el.dataset.status = v.status;
      t.el.dataset.lat = String(v.location.lat);
      t.el.dataset.lng = String(v.location.lng);
    }
    for (const [id, t] of tracked) {
      if (!(id in vehicles)) {
        t.marker.remove();
        tracked.delete(id);
      }
    }
  }, [vehicles, selectedId, compromised]);

  // One line per (vehicle, open call) it is assigned to or heading for. Positions follow the gliding marker.
  useEffect(() => {
    const seen = new Set<string>();
    const next: Pair[] = [];
    const add = (vid: string, iid: string) => {
      const v = vehicles[vid];
      const inc = incidents[iid];
      if (!v || !inc || inc.status === "resolved" || seen.has(`${vid}>${iid}`)) return;
      seen.add(`${vid}>${iid}`);
      next.push({ vehicle: vid, incident: [inc.location.lng, inc.location.lat], untrusted: v.untrusted });
    };
    Object.values(incidents).forEach((inc) => inc.assigned.forEach((vid) => add(vid, inc.id)));
    Object.values(vehicles).forEach((v) => v.target_incident && add(v.id, v.target_incident));
    pairs.current = next;
    linesDirty.current = true;
  }, [vehicles, incidents]);

  // Sync incident pins: position, severity number, label, and status.
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const pins = incidentMarkers.current;
    for (const inc of Object.values(incidents)) {
      const at: [number, number] = [inc.location.lng, inc.location.lat];
      let pin = pins.get(inc.id);
      if (!pin) {
        // Mapbox adds its own classes to the marker element, so the changing classes live on an inner label.
        const wrapper = document.createElement("div");
        wrapper.className = "incident-wrap";
        const el = wrapper.appendChild(document.createElement("div"));
        el.innerHTML = `<span class="ip-ring"></span><span class="ip-sev"></span><span class="ip-label"><b></b><span></span></span>`;
        wrapper.addEventListener("mouseenter", () => setHover({ kind: "incident", id: inc.id }));
        wrapper.addEventListener("mouseleave", () => setHover((h) => (h?.kind === "incident" && h.id === inc.id ? null : h)));
        // Mapbox needs a position before addTo, or it throws.
        const marker = new mapboxgl.Marker({ element: wrapper }).setLngLat(at).addTo(m);
        pin = { marker, el };
        pins.set(inc.id, pin);
        runPending(`incident:${inc.id}`, el);
      }
      pin.marker.setLngLat(at);
      const keep = [...pin.el.classList].filter((c) => c.startsWith("anim-")); // don't cut a running animation
      pin.el.className = [`incident-pin type-${inc.type} sev-${inc.severity} status-${inc.status}`, inc.false_report ? "false-report" : "", inc.source === "operator" ? "operator" : "", ...keep].filter(Boolean).join(" ");
      pin.el.dataset.id = inc.id;
      pin.el.dataset.lat = String(inc.location.lat);
      pin.el.dataset.lng = String(inc.location.lng);
      (pin.el.querySelector(".ip-sev") as HTMLElement).textContent = String(inc.severity);
      (pin.el.querySelector(".ip-label b") as HTMLElement).textContent = inc.id;
      (pin.el.querySelector(".ip-label span") as HTMLElement).textContent = labels[inc.id] ?? inc.type;
    }
    for (const [id, pin] of pins) {
      if (!(id in incidents)) {
        pin.marker.remove();
        pins.delete(id);
      }
    }
    // A new call landed off-screen: widen the view to include it, never zoom in.
    if (centered.current) {
      const view = m.getBounds();
      const outside = Object.values(incidents).some((i) => view && !view.contains([i.location.lng, i.location.lat]));
      if (outside) {
        const points = [...Object.values(vehicles), ...Object.values(incidents)].map((x) => x.location);
        m.fitBounds(boundsOf(points), { padding: fitPadding, maxZoom: m.getZoom(), duration: 800 });
      }
    }
  }, [incidents, labels]); // vehicles is only read to frame the refit; new incidents are what trigger it

  // A feed click: pulse the marker, and bring it into view if it is off-screen.
  useEffect(() => {
    const m = map.current;
    if (!m || !highlight) return;
    const t = highlight.kind === "vehicle" ? vehicleMarkers.current.get(highlight.id) : incidentMarkers.current.get(highlight.id);
    if (!t) return;
    flash(t.el, "anim-highlight", 2400);
    const at = t.marker.getLngLat();
    if (!m.getBounds()?.contains(at)) m.easeTo({ center: at, duration: 600 });
  }, [highlight]);

  // Drop target for the judge panel's incident icons.
  function onDragOver(e: DragEvent<HTMLDivElement>) {
    if (!onDropIncident || !e.dataTransfer.types.includes(INCIDENT_DRAG_TYPE)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }
  function onDrop(e: DragEvent<HTMLDivElement>) {
    const m = map.current;
    const type = e.dataTransfer.getData(INCIDENT_DRAG_TYPE) as IncidentType;
    if (!m || !onDropIncident || !INCIDENT_TYPES.includes(type)) return;
    e.preventDefault();
    const rect = m.getContainer().getBoundingClientRect();
    const at = m.unproject([e.clientX - rect.left, e.clientY - rect.top]);
    onDropIncident(type, { lat: at.lat, lng: at.lng });
  }

  if (!TOKEN) {
    return (
      <div className="map-missing">
        <strong>Map needs a Mapbox token.</strong>
        <span>Copy frontend/.env.example to frontend/.env, set VITE_MAPBOX_TOKEN, and restart the dev server.</span>
      </div>
    );
  }
  const content = hover && renderPopover ? renderPopover(hover) : null;
  return (
    <div className="map-wrap" onDragOver={onDragOver} onDrop={onDrop}>
      <div ref={container} className="map" />
      {content && (
        <div ref={popover} className="map-popover" style={{ visibility: "hidden" }}>
          <div className="map-popover-body">{content}</div>
        </div>
      )}
    </div>
  );
}
