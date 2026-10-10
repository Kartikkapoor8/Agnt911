import { Glyph, INCIDENT_DRAG_TYPE } from "../glyphs";
import type { IncidentType } from "../types";

const TYPES: IncidentType[] = ["medical", "fire", "crash", "hazard", "shooting"];

/** Five draggable incident icons. Dropping one on the map calls MapView's onDropIncident, which posts it. */
export function DropIncident() {
  return (
    <section className="judge-section drop-incident">
      <h3>Drop incident</h3>
      <p className="hint">Drag an icon onto the map. Severity 4.</p>
      <div className="drop-icons">
        {TYPES.map((t) => (
          <div
            key={t}
            className={`drop-icon type-${t}`}
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData(INCIDENT_DRAG_TYPE, t);
              e.dataTransfer.effectAllowed = "copy";
            }}
            title={`Drag a ${t} call onto the map`}
          >
            <Glyph name={t} size={16} />
            <span>{t}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
