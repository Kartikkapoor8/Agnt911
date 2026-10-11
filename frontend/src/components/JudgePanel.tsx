import { useState } from "react";
import { Phone, PlusCircle, Skull, type LucideIcon } from "lucide-react";
import type { InjectRequest } from "../api";
import type { IncidentType, WorldState } from "../types";
import { DropIncident } from "./DropIncident";
import { FakeCallBox } from "./FakeCallBox";
import { FleetPanel } from "./FleetPanel";
import { HackBox } from "./HackBox";
import { Panel } from "./Panel";

interface Props {
  state: WorldState;
  selectedId: string | null;
  onSelect: (vehicleId: string) => void;
  onInject: (req: InjectRequest) => Promise<void>;
  placing: IncidentType | null;
  onArm: (type: IncidentType | null) => void;
  dropSeverity: number;
  onDropSeverity: (severity: number) => void;
}

type Tab = "hijack" | "drop" | "fake";
const TABS: [Tab, string, LucideIcon][] = [
  ["hijack", "Hijack", Skull],
  ["drop", "Drop call", PlusCircle],
  ["fake", "Fake call", Phone],
];

/** Right-hand judge console: a segmented control picks Hijack, Drop call or Fake call; only that one's controls show. */
export function JudgePanel({ state, selectedId, onSelect, onInject, placing, onArm, dropSeverity, onDropSeverity }: Props) {
  const [tab, setTab] = useState<Tab>("hijack");
  return (
    <Panel className="judge-panel" title="Judge">
      <div className="segmented" role="tablist" aria-label="Judge tools">
        {TABS.map(([id, label, Icon]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? "on" : ""}
            onClick={() => {
              setTab(id);
              if (id !== "drop") onArm(null);
            }}
          >
            <Icon size={16} aria-hidden />
            {label}
          </button>
        ))}
      </div>
      <div className="judge-body">
        {tab === "hijack" && (
          <>
            <FleetPanel state={state} selectedId={selectedId} onSelect={onSelect} />
            <HackBox selectedId={selectedId} presets={state.presets} onInject={onInject} />
          </>
        )}
        {tab === "drop" && <DropIncident armed={placing} onArm={onArm} severity={dropSeverity} onSeverity={onDropSeverity} />}
        {tab === "fake" && <FakeCallBox presets={state.presets} onInject={onInject} />}
      </div>
    </Panel>
  );
}
