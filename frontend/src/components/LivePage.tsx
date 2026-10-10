import { useState } from "react";
import type { InjectRequest, Mode } from "../api";
import type { IncidentType, Location, WorldState } from "../types";
import { EscalationModal } from "./EscalationModal";
import { FeedPanel } from "./FeedPanel";
import { IncidentCard, VehicleCard } from "./HoverCards";
import { JudgePanel } from "./JudgePanel";
import { MapErrorBoundary } from "./MapErrorBoundary";
import { MapView, type Highlight, type MapTarget } from "./MapView";
import { MetricsDrawer } from "./MetricsDrawer";
import { Toasts } from "./Toasts";
import { TopBar } from "./TopBar";

interface Props {
  state: WorldState;
  stale: boolean;
  selectedId: string | null;
  onSelect: (vehicleId: string) => void;
  onSetMode: (mode: Mode) => Promise<void>;
  onInject: (req: InjectRequest) => Promise<void>;
  onResolve: (assignments: Record<string, string | null>) => Promise<void>;
  onDropIncident: (type: IncidentType, at: Location) => void;
}

// Keep framed points clear of the floating panels: feed on the left, judge console on the right.
const FIT_PADDING = { top: 72, bottom: 72, left: 360, right: 340 };

/** Live console: a full-bleed map with the status strip, feed, judge console, and metrics drawer floating over it. */
export function LivePage(props: Props) {
  const { state, stale, selectedId, onSelect } = props;
  const [highlight, setHighlight] = useState<Highlight | null>(null);

  const renderPopover = (t: MapTarget) =>
    t.kind === "vehicle" ? <VehicleCard state={state} id={t.id} /> : <IncidentCard state={state} id={t.id} />;

  return (
    <div className="live">
      <section className="live-map">
        <MapErrorBoundary>
          <MapView
            vehicles={state.vehicles}
            incidents={state.incidents}
            center={state.map_center}
            labels={state.labels}
            selectedId={selectedId}
            onSelectVehicle={onSelect}
            compromised={state.compromised}
            highlight={highlight}
            renderPopover={renderPopover}
            onDropIncident={props.onDropIncident}
            fitPadding={FIT_PADDING}
          />
        </MapErrorBoundary>
      </section>
      <TopBar state={state} stale={stale} />
      <FeedPanel runId={state.run_id} messages={state.recent_messages} onPick={(t) => setHighlight({ ...t, nonce: Date.now() })} />
      <JudgePanel state={state} selectedId={selectedId} onSelect={onSelect} onInject={props.onInject} />
      <MetricsDrawer state={state} onSetMode={props.onSetMode} />
      <Toasts />
      <EscalationModal state={state} onResolve={props.onResolve} />
    </div>
  );
}
