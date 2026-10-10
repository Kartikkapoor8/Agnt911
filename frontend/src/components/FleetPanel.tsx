import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { onLiveEvent } from "../dispatch";
import { Glyph } from "../glyphs";
import type { WorldState } from "../types";
import { TrustBar, VehicleCard } from "./HoverCards";

interface Props {
  state: WorldState;
  selectedId: string | null;
  onSelect: (vehicleId: string) => void;
}

interface Delta {
  value: number;
  n: number; // remounts the floating text so a second drop animates again
}

/** Compact fleet strip: one chip per vehicle (id, trust bar, status dot). Hover for the full card; click to pick it for the hijack. */
export function FleetPanel({ state, selectedId, onSelect }: Props) {
  const [hover, setHover] = useState<{ id: string; top: number; right: number } | null>(null);
  const [deltas, setDeltas] = useState<Record<string, Delta>>({});

  // trust_changed floats "-0.30" up from the chip, once per event.
  useEffect(
    () =>
      onLiveEvent((e) => {
        if (e.type !== "trust_changed" || !e.vehicle) return;
        const id = e.vehicle;
        const value = Number(e.data.new) - Number(e.data.old);
        if (!Number.isFinite(value) || value === 0) return;
        setDeltas((d) => ({ ...d, [id]: { value, n: (d[id]?.n ?? 0) + 1 } }));
      }),
    [],
  );

  return (
    <section className="judge-section fleet-strip">
      <h3>Fleet</h3>
      <div className="fleet-chips">
        {Object.values(state.vehicles).map((v) => {
          const hacked = state.compromised.includes(v.id);
          const delta = deltas[v.id];
          return (
            <button
              key={v.id}
              className={`fleet-chip type-${v.type} status-${v.status}${v.untrusted ? " untrusted" : ""}${hacked ? " compromised" : ""}${v.id === selectedId ? " selected" : ""}`}
              onClick={() => onSelect(v.id)}
              onMouseEnter={(e) => {
                const r = e.currentTarget.getBoundingClientRect();
                setHover({ id: v.id, top: Math.max(8, Math.min(r.top, window.innerHeight - 300)), right: window.innerWidth - r.left + 8 });
              }}
              onMouseLeave={() => setHover((h) => (h?.id === v.id ? null : h))}
            >
              <span className={`status-dot status-${v.status}`} title={v.status.replace("_", " ")} />
              <span className="chip-id mono">{v.id}</span>
              {hacked && <span className="chip-skull" title="Hacked (judge view only)"><Glyph name="skull" size={10} /></span>}
              <TrustBar value={v.trust} untrusted={v.untrusted} />
              {delta && (
                <span key={delta.n} className={`trust-delta ${delta.value < 0 ? "down" : "up"}`} aria-hidden>
                  {delta.value > 0 ? "+" : ""}
                  {delta.value.toFixed(2)}
                </span>
              )}
            </button>
          );
        })}
      </div>
      {/* Portal: the panel's backdrop-filter would otherwise make it the box that position: fixed is relative to. */}
      {hover &&
        createPortal(
          <div className="fleet-hover" style={{ top: hover.top, right: hover.right }}>
            <VehicleCard state={state} id={hover.id} />
          </div>,
          document.body,
        )}
    </section>
  );
}
