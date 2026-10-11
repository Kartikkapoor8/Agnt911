// Every icon in the app is a Lucide icon, named here once. Map markers are plain DOM, so each icon is also
// available as an SVG string.

import { renderToStaticMarkup } from "react-dom/server";
import { Ambulance, CarFront, Crosshair, Flame, HeartPulse, Shield, Skull, TriangleAlert, type LucideIcon } from "lucide-react";
import type { IncidentType, VehicleType } from "./types";

export type GlyphName = VehicleType | IncidentType | "skull";

const ICONS: Record<GlyphName, LucideIcon> = {
  ambulance: Ambulance,
  fire: Flame,
  police: Shield,
  medical: HeartPulse,
  crash: CarFront,
  hazard: TriangleAlert,
  shooting: Crosshair,
  skull: Skull,
};

/** The icon as a React element. 16 px in panels by default. */
export function Glyph({ name, size = 16 }: { name: GlyphName; size?: number }) {
  const Icon = ICONS[name];
  return <Icon className={`glyph glyph-${name}`} size={size} strokeWidth={2} aria-hidden />;
}

const svgCache = new Map<string, string>();

/** The icon as an SVG string, for DOM markers. 20 px on the map by default. */
export function glyphSvg(name: GlyphName, size = 20): string {
  const key = `${name}:${size}`;
  let svg = svgCache.get(key);
  if (!svg) svgCache.set(key, (svg = renderToStaticMarkup(<Glyph name={name} size={size} />)));
  return svg;
}

/** dataTransfer type for an incident icon dragged from the judge panel onto the map. */
export const INCIDENT_DRAG_TYPE = "application/x-agnt911-incident";
