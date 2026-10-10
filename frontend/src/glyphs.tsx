// Small 24x24 icons for vehicle types, incident types, and the hacked badge.
// Map markers are plain DOM, so each glyph is available as an SVG string as well as a React element.

import type { IncidentType, VehicleType } from "./types";

export type GlyphName = VehicleType | IncidentType | "skull";

interface GlyphDef {
  d: string;
  stroke?: boolean; // drawn as a line instead of filled
  evenodd?: boolean; // holes (the hazard's "!", the skull's eyes)
}

const CROSS: GlyphDef = { d: "M9.5 3h5v6.5H21v5h-6.5V21h-5v-6.5H3v-5h6.5z" };
const FLAME: GlyphDef = { d: "M12.5 2c.4 3.4 5.3 5.7 5.3 11.1A5.8 5.8 0 0 1 12 19a5.8 5.8 0 0 1-5.8-5.9c0-2.7 1.4-4.5 2.8-5.6.1 1.7.9 3 2.2 3.6.1-3.6-.4-6 1.3-9.1z" };

const GLYPHS: Record<GlyphName, GlyphDef> = {
  ambulance: CROSS,
  fire: FLAME,
  police: { d: "M12 2l8 3v6.2c0 5-3.4 9.3-8 10.8-4.6-1.5-8-5.8-8-10.8V5z" },
  medical: CROSS,
  crash: { d: "M12 2l2.3 5.5 5.7-1.7-3.3 5 5.3 3.3-6.1.5.5 6.2L12 16.6l-4.4 4.2.5-6.2-6.1-.5 5.3-3.3-3.3-5 5.7 1.7z" },
  hazard: { d: "M12 2.5 22.5 21h-21zM11 9v6h2V9zm0 7.5v2h2v-2z", evenodd: true },
  shooting: { d: "M12 5a7 7 0 1 0 0 14 7 7 0 1 0 0-14M12 1.5v6M12 16.5v6M1.5 12h6M16.5 12h6", stroke: true },
  skull: {
    d: "M12 2a8 8 0 0 0-8 8c0 2.6 1.2 4.5 3 5.6V20h2.2v-2h1.6v2h2.4v-2h1.6v2H17v-4.4c1.8-1.1 3-3 3-5.6a8 8 0 0 0-8-8zM8.6 8.8a2 2 0 1 1 0 4 2 2 0 0 1 0-4zm6.8 0a2 2 0 1 1 0 4 2 2 0 0 1 0-4z",
    evenodd: true,
  },
};

function attrs(g: GlyphDef): Record<string, string> {
  return g.stroke
    ? { fill: "none", stroke: "currentColor", "stroke-width": "2.2", "stroke-linecap": "round" }
    : { fill: "currentColor", ...(g.evenodd ? { "fill-rule": "evenodd" } : {}) };
}

/** The glyph as an SVG string, for DOM markers. */
export function glyphSvg(name: GlyphName, size = 12): string {
  const g = GLYPHS[name];
  const a = Object.entries(attrs(g)).map(([k, v]) => `${k}="${v}"`).join(" ");
  return `<svg class="glyph glyph-${name}" width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true"><path d="${g.d}" ${a}/></svg>`;
}

/** The glyph as a React element. */
export function Glyph({ name, size = 14 }: { name: GlyphName; size?: number }) {
  const g = GLYPHS[name];
  return (
    <svg className={`glyph glyph-${name}`} width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      {g.stroke ? (
        <path d={g.d} fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" />
      ) : (
        <path d={g.d} fill="currentColor" fillRule={g.evenodd ? "evenodd" : undefined} />
      )}
    </svg>
  );
}

/** dataTransfer type for an incident icon dragged from the judge panel onto the map. */
export const INCIDENT_DRAG_TYPE = "application/x-agnt911-incident";
