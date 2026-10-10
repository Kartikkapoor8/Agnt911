import type { InjectRequest, Mode } from "../api";
import type { WorldState } from "../types";
import { EscalationModal } from "./EscalationModal";
import { FleetPanel } from "./FleetPanel";
import { HackBox } from "./HackBox";
import { MapErrorBoundary } from "./MapErrorBoundary";
import { MapView } from "./MapView";
import { TopBar } from "./TopBar";
import { TranscriptPanel } from "./TranscriptPanel";

interface Props {
  state: WorldState;
  stale: boolean;
  selectedId: string | null;
  onSelect: (vehicleId: string) => void;
  onSetMode: (mode: Mode) => Promise<void>;
  onInject: (req: InjectRequest) => Promise<void>;
  onResolve: (assignments: Record<string, string | null>) => Promise<void>;
  onEndRun: () => void;
}

/** Live console: top bar, transcript on the left, map in the center, fleet and hack box on the right. */
export function LivePage(props: Props) {
  const { state, stale, selectedId, onSelect } = props;
  return (
    <div className="live">
      <TopBar state={state} onSetMode={props.onSetMode} onEndRun={props.onEndRun} />
      {stale && <div className="stale-banner">Backend not responding. Showing the last state received.</div>}
      <div className="live-columns">
        <TranscriptPanel runId={state.run_id} messages={state.recent_messages} />
        <section className="live-map">
          <MapErrorBoundary>
            <MapView
              vehicles={state.vehicles}
              incidents={state.incidents}
              center={state.map_center}
              labels={state.labels}
              selectedId={selectedId}
              onSelectVehicle={onSelect}
            />
          </MapErrorBoundary>
        </section>
        <div className="live-right">
          <FleetPanel state={state} selectedId={selectedId} onSelect={onSelect} />
          <HackBox selectedId={selectedId} presets={state.presets} onInject={props.onInject} />
        </div>
      </div>
      <EscalationModal state={state} onResolve={props.onResolve} />
    </div>
  );
}
