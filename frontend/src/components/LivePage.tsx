import { useEffect, useState } from "react";
import { Play } from "lucide-react";
import type { InjectRequest, Mode } from "../api";
import { activeCalls } from "../narrator";
import { DROP_SEVERITY, usePendingIncidents } from "../pendingIncidents";
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
  intro: boolean; // first load: the map and a Start demo button, nothing else
  onStart: () => void;
}

const NONE = {}; // the intro shows the bare map

// Keep framed points clear of the floating panels: feed on the left, judge console on the right.
const FIT_PADDING = { top: 72, bottom: 72, left: 360, right: 340 };

/** Live console: a full-bleed map with the status strip, feed, judge console, and metrics drawer floating over it. */
export function LivePage(props: Props) {
  const { state, stale, selectedId, onSelect } = props;
  const [highlight, setHighlight] = useState<Highlight | null>(null);
  const [placing, setPlacing] = useState<IncidentType | null>(null); // click-to-place
  const [dropSeverity, setDropSeverity] = useState(DROP_SEVERITY);
  const { pending, drop } = usePendingIncidents(state.incidents);

  const onDropIncident = (type: IncidentType, at: Location) => {
    setPlacing(null);
    void drop(type, at, dropSeverity);
  };

  // Esc puts an armed incident icon down.
  useEffect(() => {
    if (!placing) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setPlacing(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [placing]);

  const renderPopover = (t: MapTarget) =>
    t.kind === "vehicle" ? <VehicleCard state={state} id={t.id} showCheck /> : <IncidentCard state={state} id={t.id} />;

  return (
    <div className="live">
      <section className="live-map">
        <MapErrorBoundary>
          <MapView
            vehicles={props.intro ? NONE : state.vehicles}
            incidents={props.intro ? NONE : state.incidents}
            center={state.map_center}
            labels={state.labels}
            selectedId={selectedId}
            onSelectVehicle={onSelect}
            compromised={state.compromised}
            highlight={highlight}
            renderPopover={renderPopover}
            onDropIncident={onDropIncident}
            fitPadding={FIT_PADDING}
            tick={state.tick}
            negotiating={false /* the map never dims during a round; the status strip pulses instead */}
            pendingIncidents={pending}
            placing={placing}
            activeIncidents={activeCalls(state)}
          />
        </MapErrorBoundary>
      </section>
      {props.intro ? (
        <div className="intro">
          <button className="start-demo" onClick={props.onStart}>
            <Play size={20} aria-hidden />
            Start demo
          </button>
        </div>
      ) : (
        <>
      <TopBar state={state} stale={stale} />
      <FeedPanel
        runId={state.run_id}
        messages={state.recent_messages}
        vehicles={state.vehicles}
        labels={state.labels}
        presets={state.presets}
        onPick={(t) => setHighlight({ ...t, nonce: Date.now() })}
      />
      <JudgePanel state={state} selectedId={selectedId} onSelect={onSelect} onInject={props.onInject} placing={placing} onArm={setPlacing} dropSeverity={dropSeverity} onDropSeverity={setDropSeverity} />
      <MetricsDrawer state={state} onSetMode={props.onSetMode} />
      <Toasts />
      <EscalationModal state={state} onResolve={props.onResolve} />
        </>
      )}
    </div>
  );
}
