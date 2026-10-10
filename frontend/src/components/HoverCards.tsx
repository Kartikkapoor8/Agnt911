import { Glyph } from "../glyphs";
import type { CheckEvent, Claim, WorldState } from "../types";

const STATUS: Record<string, string> = { free: "free", en_route: "en route", on_scene: "on scene" };

/** The vehicle's newest claim in the signed transcript. */
function lastClaim(state: WorldState, id: string): Claim | null {
  for (let i = state.recent_messages.length - 1; i >= 0; i--) {
    const m = state.recent_messages[i];
    if (m.kind === "claim" && m.vehicle === id) return m;
  }
  return null;
}

/** The newest check of this vehicle's claims or bids. */
function lastCheck(state: WorldState, id: string): CheckEvent | null {
  const rows = state.check_events ?? [];
  for (let i = rows.length - 1; i >= 0; i--) if (rows[i].target === id) return rows[i];
  return null;
}

/** Trust as a 0-1 bar plus the number. */
export function TrustBar({ value, untrusted }: { value: number; untrusted: boolean }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const level = untrusted ? "bad" : value < 0.75 ? "warn" : "good";
  return (
    <span className={`trust-bar trust-${level}`} title={`trust ${value.toFixed(2)}`}>
      <span className="trust-fill" style={{ width: `${pct}%` }} />
    </span>
  );
}

/** Hover card for a vehicle: id, type, status, trust, assignment, last claim, last check result. */
export function VehicleCard({ state, id }: { state: WorldState; id: string }) {
  const v = state.vehicles[id];
  if (!v) return null;
  const claim = lastClaim(state, id);
  const check = lastCheck(state, id);
  const hacked = state.compromised.includes(id);
  const target = v.target_incident;
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
        <dt>Assigned</dt>
        <dd>{target ? <><b className="mono">{target}</b> {state.labels[target] ?? ""}</> : "none"}</dd>
        <dt>Last claim</dt>
        <dd>
          {claim ? (
            <>
              <span className="mono">t{claim.tick}</span> {claim.type.replace(/_/g, " ")}
              {claim.incident ? ` ${claim.incident}` : ""}
              {claim.reasoning && <span className="hc-quote">“{claim.reasoning}”</span>}
            </>
          ) : (
            "none yet"
          )}
        </dd>
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
