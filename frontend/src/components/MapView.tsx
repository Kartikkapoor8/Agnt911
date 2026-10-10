import { useEffect, useRef } from "react";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import type { Incident, Location, Vehicle } from "../types";

const TOKEN = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined;
const STYLE = "mapbox://styles/mapbox/dark-v11";
const DOWNTOWN_SAN_JOSE: [number, number] = [-121.8811, 37.3352]; // lng, lat
const GLIDE_MS = 500; // one poll interval, so a marker arrives just as the next position lands

interface Tracked {
  marker: mapboxgl.Marker;
  el: HTMLDivElement;
  from: [number, number];
  to: [number, number];
  start: number;
}

function boundsOf(points: Location[]): mapboxgl.LngLatBounds {
  const bounds = new mapboxgl.LngLatBounds();
  points.forEach((p) => bounds.extend([p.lng, p.lat]));
  return bounds;
}

interface Props {
  vehicles: Record<string, Vehicle>;
  incidents: Record<string, Incident>;
  center: Location | null;
  labels?: Record<string, string>; // incident id -> display text, for the pin tooltip
  selectedId?: string | null;
  onSelectVehicle?: (vehicleId: string) => void;
}

/** Show the Mapbox map with a moving marker per vehicle and a pin per incident. */
export function MapView({ vehicles, incidents, center, labels = {}, selectedId = null, onSelectVehicle }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<mapboxgl.Map | null>(null);
  const vehicleMarkers = useRef(new Map<string, Tracked>());
  const incidentMarkers = useRef(new Map<string, { marker: mapboxgl.Marker; el: HTMLDivElement }>());
  const centered = useRef(false);
  const onSelect = useRef(onSelectVehicle);
  onSelect.current = onSelectVehicle; // marker click handlers are created once, so read the latest callback

  // Create the map once, and run one animation loop that glides every vehicle marker.
  useEffect(() => {
    if (!TOKEN || !container.current) return;
    mapboxgl.accessToken = TOKEN;
    const m = new mapboxgl.Map({ container: container.current, style: STYLE, center: DOWNTOWN_SAN_JOSE, zoom: 13.3 });
    m.addControl(new mapboxgl.NavigationControl({ showCompass: false }), "top-right");
    map.current = m;

    let frame = requestAnimationFrame(function animate(now) {
      for (const t of vehicleMarkers.current.values()) {
        const k = Math.min(1, (now - t.start) / GLIDE_MS);
        t.marker.setLngLat([t.from[0] + (t.to[0] - t.from[0]) * k, t.from[1] + (t.to[1] - t.from[1]) * k]);
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
    };
  }, []);

  // First data: frame the whole fleet and every call, so no vehicle starts off-screen.
  // Fall back to the scenario's map_center if there is nothing to frame.
  useEffect(() => {
    const m = map.current;
    if (!m || centered.current) return;
    const points = [...Object.values(vehicles), ...Object.values(incidents)].map((x) => x.location);
    if (points.length > 1) {
      m.fitBounds(boundsOf(points), { padding: 80, maxZoom: 14.5, duration: 0 });
      centered.current = true;
    } else if (center) {
      m.jumpTo({ center: [center.lng, center.lat] });
      centered.current = true;
    }
  }, [vehicles, incidents, center]);

  // Sync vehicle markers with the latest poll: new target, start a fresh glide from where it is now.
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const now = performance.now();
    const tracked = vehicleMarkers.current;
    for (const v of Object.values(vehicles)) {
      const to: [number, number] = [v.location.lng, v.location.lat];
      let t = tracked.get(v.id);
      if (!t) {
        const el = document.createElement("div");
        el.className = `vehicle-marker vehicle-${v.type}`;
        el.textContent = v.id;
        el.addEventListener("click", (e) => {
          e.stopPropagation();
          onSelect.current?.(v.id);
        });
        const marker = new mapboxgl.Marker({ element: el }).setLngLat(to).addTo(m);
        t = { marker, el, from: to, to, start: now };
        tracked.set(v.id, t);
      } else if (t.to[0] !== to[0] || t.to[1] !== to[1]) {
        const at = t.marker.getLngLat();
        t.from = [at.lng, at.lat];
        t.to = to;
        t.start = now;
      }
      t.el.classList.toggle("untrusted", v.untrusted);
      t.el.classList.toggle("selected", v.id === selectedId);
      t.el.classList.toggle("selectable", Boolean(onSelect.current));
      t.el.dataset.lat = String(v.location.lat);
      t.el.dataset.lng = String(v.location.lng);
      t.el.title = `${v.id} · ${v.type} · ${v.status}${v.target_incident ? ` → ${v.target_incident}` : ""}`;
    }
    for (const [id, t] of tracked) {
      if (!(id in vehicles)) {
        t.marker.remove();
        tracked.delete(id);
      }
    }
  }, [vehicles, selectedId]);

  // Sync incident pins: position, severity label, and status.
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
        const el = wrapper.appendChild(document.createElement("div"));
        // Mapbox needs a position before addTo, or it throws.
        const marker = new mapboxgl.Marker({ element: wrapper }).setLngLat(at).addTo(m);
        pin = { marker, el };
        pins.set(inc.id, pin);
      }
      pin.marker.setLngLat(at);
      pin.el.className = `incident-pin sev-${inc.severity} status-${inc.status}${inc.false_report ? " false-report" : ""}`;
      pin.el.dataset.id = inc.id;
      pin.el.dataset.lat = String(inc.location.lat);
      pin.el.dataset.lng = String(inc.location.lng);
      pin.el.title = `${inc.id} ${labels[inc.id] ?? inc.type}, severity ${inc.severity}, ${inc.status}`
        + (inc.source === "operator" ? `, called in by ${inc.source_id}` : "")
        + (inc.false_report ? ", FALSE REPORT" : "");
    }
    for (const [id, pin] of pins) {
      if (!(id in incidents)) {
        pin.marker.remove();
        pins.delete(id);
      }
    }
    // A new call landed off-screen (I5 at tick 150 is ~2 km east): widen the view to include it, never zoom in.
    if (centered.current) {
      const view = m.getBounds();
      const outside = Object.values(incidents).some((i) => view && !view.contains([i.location.lng, i.location.lat]));
      if (outside) {
        const points = [...Object.values(vehicles), ...Object.values(incidents)].map((x) => x.location);
        m.fitBounds(boundsOf(points), { padding: 80, maxZoom: m.getZoom(), duration: 800 });
      }
    }
  }, [incidents, labels]); // vehicles is only read to frame the refit; new incidents are what trigger it

  if (!TOKEN) {
    return (
      <div className="map-missing">
        <strong>Map needs a Mapbox token.</strong>
        <span>Copy frontend/.env.example to frontend/.env, set VITE_MAPBOX_TOKEN, and restart the dev server.</span>
      </div>
    );
  }
  return <div ref={container} className="map" />;
}
