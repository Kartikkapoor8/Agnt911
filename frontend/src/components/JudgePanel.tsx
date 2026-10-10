import type { InjectRequest } from "../api";
import type { WorldState } from "../types";
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
}

/** Right-hand judge console: hijack a vehicle, drop an incident, place a fake call, then the fleet strip. */
export function JudgePanel({ state, selectedId, onSelect, onInject }: Props) {
  return (
    <Panel className="judge-panel" title="Judge">
      <div className="judge-body">
        <HackBox selectedId={selectedId} vehicles={state.vehicles} onSelect={onSelect} presets={state.presets} onInject={onInject} />
        <DropIncident />
        <FakeCallBox presets={state.presets} onInject={onInject} />
        <FleetPanel state={state} selectedId={selectedId} onSelect={onSelect} />
      </div>
    </Panel>
  );
}
