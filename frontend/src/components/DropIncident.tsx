import { Glyph, INCIDENT_DRAG_TYPE } from "../glyphs";
import type { IncidentType } from "../types";

const TYPES: IncidentType[] = ["medical", "fire", "crash", "hazard", "shooting"];

interface Props {
  armed: IncidentType | null; // click-to-place: this icon waits for a map click
  onArm: (type: IncidentType | null) => void;
}

/** Five incident icons: drag one onto the map, or click one and then click the map. Either way MapView's onDropIncident posts it. */
export function DropIncident({ armed, onArm }: Props) {
  return (
    <section className="judge-section drop-incident">
      <h3>Drop incident</h3>
      <p className="hint">
        {armed ? `Click the map to place the ${armed} call (Esc cancels).` : "Drag an icon onto the map, or click one and then the map. Severity 4."}
      </p>
      <div className="drop-icons">
        {TYPES.map((t) => (
          <div
            key={t}
            role="button"
            tabIndex={0}
            aria-pressed={armed === t}
            className={`drop-icon type-${t}${armed === t ? " armed" : ""}`}
            onClick={() => onArm(armed === t ? null : t)}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onArm(armed === t ? null : t)}
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData(INCIDENT_DRAG_TYPE, t);
              e.dataTransfer.effectAllowed = "copy";
              onArm(null);
            }}
            title={`Drag a ${t} call onto the map, or click and then click the map`}
          >
            <Glyph name={t} size={16} />
            <span>{t}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
