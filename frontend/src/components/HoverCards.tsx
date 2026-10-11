import { distanceM, formatDistance } from "../escalation";
import { Glyph } from "../glyphs";
import type { CheckEvent, Message, Vehicle, WorldState } from "../types";

const STATUS: Record<string, string> = { free: "free", en_route: "en route", on_scene: "on scene" };

/** The vehicle's newest message of any kind (claim, bid, or vote). */
function lastMessage(state: WorldState, id: string): Message | null {
  for (let i = state.recent_messages.length - 1; i >= 0; i--) if (state.recent_messages[i].vehicle === id) return state.recent_messages[i];
  return null;
}

/** The newest check of this vehicle's claims or bids. */
function lastCheck(state: WorldState, id: string): CheckEvent | null {
  const rows = state.check_events ?? [];
  for (let i = rows.length - 1; i >= 0; i--) if (rows[i].target === id) return rows[i];
  return null;
}

/** What the vehicle is doing, as a sentence. */
function doing(v: Vehicle, state: WorldState): string {
  const call = v.target_incident ? state.incidents[v.target_incident] : undefined;
  const name = v.target_incident ? `${v.target_incident}${state.labels[v.target_incident] ? ` (${state.labels[v.target_incident]})` : ""}` : "";
  let text = "Free, waiting for a call";
  if (v.status === "en_route" && call) text = `Driving to ${name}, ${formatDistance(distanceM(v.location, call.location))} away`;
  else if (v.status === "on_scene" && call) text = `On scene at ${name}`;
  else if (v.target_incident) text = `Assigned to ${name}`;
  return v.untrusted ? `${text}. Its votes don't count.` : `${text}.`;
}

/** The vehicle's last message in plain words, plus its own reasoning when that adds something. */
function said(m: Message, state: WorldState): { words: string; why: string | null } {
  if (m.kind === "bid") return { words: `I can reach ${m.incident} in ${m.eta_seconds} s.`, why: null };
  if (m.kind === "vote") {
    const ballot = (state.plans[m.incident]?.options ?? []).filter((v) => !m.role || state.vehicles[v]?.type === m.role);
    const choice = ballot[m.option_index];
    return { words: choice ? `I vote ${choice} for ${m.incident}.` : `I voted on ${m.incident}.`, why: null };
  }
  const at = m.incident ?? "the call";
  const words: Record<string, string> = {
    on_scene: `I'm on scene at ${at}.`,
    en_route: `I'm on my way to ${at}.`,
    resolved: `${at} is handled.`,
    sector_clear: "My sector is clear.",
    available: "I'm free.",
    capability: `I can handle ${at}.`,
  };
  const w = words[m.type];
  return w ? { words: w, why: m.reasoning || null } : { words: m.reasoning || `Reporting on ${at}.`, why: null };
}

/** Trust as a 0-1 bar. Red only when the fleet has stopped trusting the vehicle. */
export function TrustBar({ value, untrusted }: { value: number; untrusted: boolean }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const level = untrusted ? "bad" : value < 0.75 ? "warn" : "good";
  return (
    <span className={`trust-bar trust-${level}`} title={`trust ${value.toFixed(2)}`}>
      <span className="trust-fill" style={{ width: `${pct}%` }} />
    </span>
  );
}

/** Hover card for a vehicle: id, type, status, trust, what it's doing, and the last thing it said. The map's popover adds the last check. */
export function VehicleCard({ state, id, showCheck = false }: { state: WorldState; id: string; showCheck?: boolean }) {
  const v = state.vehicles[id];
  if (!v) return null;
  const msg = lastMessage(state, id);
  const s = msg ? said(msg, state) : null;
  const check = showCheck ? lastCheck(state, id) : null;
  const hacked = state.compromised.includes(id);
  return (
    <div className={`hover-card vehicle-card type-${v.type}${v.untrusted ? " untrusted" : ""}`}>
      <div className="hc-head">
        <span className="hc-icon"><Glyph name={v.type} size={13} /></span>
        <b className="hc-id">{v.id}</b>
        <span className="hc-sub">{v.type}</span>
        {hacked && <span className="hc-hacked" title="Hacked by the judge. The fleet doesn't see this."><Glyph name="skull" size={11} /> hacked</span>}
      </div>
      <dl>
        <dt>Status</dt>
        <dd>{STATUS[v.status] ?? v.status}</dd>
        <dt>Trust</dt>
        <dd className="hc-trust">
          <TrustBar value={v.trust} untrusted={v.untrusted} />
          <span className="mono">{v.trust.toFixed(2)}</span>
          {v.untrusted && <span className="hc-bad">untrusted</span>}
        </dd>
        <dt>Doing</dt>
        <dd>{doing(v, state)}</dd>
        <dt>Last said</dt>
        <dd>
          {s ? (
            <>
              <span className="hc-said">“{s.words}”</span>
              {s.why && s.why !== s.words && <span className="hc-why">{s.why}</span>}
            </>
          ) : (
            "Nothing yet."
          )}
        </dd>
        {showCheck && (
          <>
            <dt>Last check</dt>
            <dd>
              {check ? (
                <>
                  <span className="mono">t{check.tick}</span> {check.checker ?? "no one"} checked its {check.subject}:{" "}
                  <span className={`hc-result result-${check.result}`}>{check.result}</span>
                  {check.trust_after !== check.trust_before && (
                    <span className="mono"> ({check.trust_before.toFixed(2)} → {check.trust_after.toFixed(2)})</span>
                  )}
                </>
              ) : (
                "none yet"
              )}
            </dd>
          </>
        )}
      </dl>
    </div>
  );
}

/** Hover card for an incident: type, severity, status, assigned units, and the caller's credibility for an operator call. */
export function IncidentCard({ state, id }: { state: WorldState; id: string }) {
  const inc = state.incidents[id];
  if (!inc) return null;
  const credibility = inc.source_id ? state.callers[inc.source_id] : undefined;
  return (
    <div className={`hover-card incident-card sev-${inc.severity} status-${inc.status}`}>
      <div className="hc-head">
        <span className="hc-icon"><Glyph name={inc.type} size={13} /></span>
        <b className="hc-id">{inc.id}</b>
        <span className="hc-sub">{inc.type}</span>
        <span className="hc-sev">sev {inc.severity}</span>
      </div>
      {state.labels[id] && <p className="hc-label">{state.labels[id]}</p>}
      <dl>
        <dt>Status</dt>
        <dd>{inc.status}{inc.false_report ? <span className="hc-bad"> false report</span> : null}</dd>
        <dt>Units</dt>
        <dd className="mono">{inc.assigned.length ? inc.assigned.join(", ") : "none"}</dd>
        <dt>Source</dt>
        <dd>
          {inc.source === "operator" ? (
            <>
              caller <span className="mono">{inc.source_id ?? "?"}</span>
              {credibility !== undefined && <>, credibility <span className="mono">{credibility.toFixed(2)}</span></>}
            </>
          ) : (
            "911"
          )}
        </dd>
      </dl>
    </div>
  );
}
