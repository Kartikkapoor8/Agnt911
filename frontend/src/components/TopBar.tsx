import { negotiatingIncidents, useStory } from "../story";
import type { WorldState } from "../types";
import { Panel } from "./Panel";

interface Props {
  state: WorldState;
  stale: boolean; // the latest poll failed; we are showing the last good state
}

/**
 * The calls a running round is probably deciding. /state doesn't say (the engine's pending set isn't sent), so:
 * calls with an open decision card, then unassigned calls, then every call not yet resolved.
 */
export function callsInRound(state: WorldState, openDecisions: string[]): string[] {
  const live = (id: string) => state.incidents[id] && state.incidents[id].status !== "resolved";
  const fromStory = openDecisions.filter(live);
  if (fromStory.length) return fromStory;
  const unassigned = Object.values(state.incidents).filter((i) => i.status === "open").map((i) => i.id);
  if (unassigned.length) return unassigned;
  return Object.keys(state.incidents).filter(live);
}

/** Status strip: wordmark, tick, mode, and while a round runs, which call the fleet is negotiating. */
export function TopBar({ state, stale }: Props) {
  const story = useStory();
  const running = state.integration.last_round_status === "running";
  const calls = running ? callsInRound(state, negotiatingIncidents(story)) : [];
  const name = calls.length === 1 && state.labels[calls[0]] ? `${calls[0]} · ${state.labels[calls[0]]}` : calls.join(", ");
  return (
    <Panel className="status-strip">
      <div className="status-row">
        <div className="wordmark">Agnt<span>911</span></div>
        <span className="ss-tick mono">t{state.tick}</span>
        <span className={`ss-mode mode-${state.mode}`} title={state.mode === "protected" ? "Fleet consensus with verification and trust" : "One central dispatcher, no votes, no checks"}>
          {state.mode === "protected" ? "Protected" : "Baseline"}
        </span>
        <span className={`negotiating${running ? " on" : ""}`} title={`Round status: ${state.integration.last_round_status}`}>
          <i aria-hidden />
          {running ? <span className="ss-calls">Fleet negotiating {name || "calls"}…</span> : "idle"}
        </span>
        {state.paused && <span className="ss-paused">Paused for a human</span>}
        {stale && <span className="ss-stale">Backend not responding</span>}
      </div>
    </Panel>
  );
}
