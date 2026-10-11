import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Hand, Volume2, X } from "lucide-react";
import { buildDecision, formatDistance, type DecisionSlot } from "../escalation";
import type { WorldState } from "../types";
import { isMuted, onSpeakingChange, setMuted, speak, stop } from "../voice";

interface Props {
  state: WorldState;
  onResolve: (assignments: Record<string, string | null>) => Promise<void>;
}

// Kept outside the component so a remount (or React StrictMode's double effects) neither restarts the clock nor speaks twice.
const pausedAt = new Map<string, number>();
const spoken = new Set<string>();

/** The one line that says why the fleet stopped: it won't choose which critical call waits. */
function why(slot: DecisionSlot): string {
  const role = slot.role;
  const units = slot.options.length;
  const calls = units + 1;
  const count = (k: number) => (k === 1 ? "one" : k === 2 ? "Two" : String(k));
  return units <= 1
    ? `${count(calls)} critical calls, one trusted ${role}. The fleet won’t choose who waits.`
    : `${count(calls)} critical calls, ${units} trusted ${role}s. The fleet won’t choose who waits.`;
}

function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Decision popup: the fleet pages the human by voice, and clicking an option card resolves the escalation. */
export function EscalationModal({ state, onResolve }: Props) {
  const decision = useMemo(() => buildDecision(state), [state]);
  const [picks, setPicks] = useState<Record<string, string>>({}); // slot key -> vehicle, for calls with several stuck roles
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [speaking, setSpeakingState] = useState(false);
  const [muted, setMutedState] = useState(isMuted());
  const [blocked, setBlocked] = useState(false);
  const [other, setOther] = useState(""); // the "any vehicle of the role" dropdown
  const lastId = useRef<string | null>(null);

  const id = decision?.id ?? null;
  const slot: DecisionSlot | undefined = decision?.slots.find((s) => !picks[s.key]) ?? decision?.slots[decision.slots.length - 1];
  const step = decision && slot ? decision.slots.indexOf(slot) + 1 : 0;

  useEffect(() => onSpeakingChange(setSpeakingState), []);

  // Each question starts with an empty dropdown.
  const slotKey = id && slot ? `${id}#${slot.key}` : null;
  useEffect(() => setOther(""), [slotKey]);

  // New pause: start its clock, clear old picks. Popup closed: stop talking.
  useEffect(() => {
    if (id && !pausedAt.has(id)) pausedAt.set(id, Date.now());
    if (id !== lastId.current) {
      setPicks({});
      setError(null);
      setBlocked(false);
      lastId.current = id;
    }
    if (!id) stop();
  }, [id]);

  // Tick the waiting clock once a second while open.
  useEffect(() => {
    if (!id) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [id]);

  // Read each question aloud once. Autoplay may be blocked; the caption stays and Replay starts it.
  const question = slot?.question ?? "";
  const speakKey = id && slot ? `${id}#${slot.key}` : null;
  useEffect(() => {
    if (!speakKey || spoken.has(speakKey)) return;
    spoken.add(speakKey);
    speak(question).then((r) => setBlocked(r === "blocked"));
  }, [speakKey, question]);

  if (!decision || !slot) return null;

  async function choose(vehicle: string) {
    if (!decision || !slot || sending) return;
    const next = { ...picks, [slot.key]: vehicle };
    setPicks(next);
    if (decision.slots.some((s) => !next[s.key])) return; // more roles to decide; the next question shows (and is spoken)
    const assignments: Record<string, string> = { ...decision.alreadyAgreed };
    decision.slots.forEach((s) => (assignments[next[s.key]] = s.incident));
    setSending(true);
    setError(null);
    stop();
    try {
      await onResolve(assignments); // the popup closes when /state is no longer paused
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPicks({}); // start the choice over
    } finally {
      setSending(false);
    }
  }

  function replay() {
    speak(question).then((r) => setBlocked(r === "blocked"));
  }

  function toggleMute() {
    const next = !muted;
    setMuted(next);
    setMutedState(next);
    if (!next) setBlocked(false);
  }

  const started = id ? pausedAt.get(id) ?? now : now;

  return (
    <div className="modal-backdrop">
      <div className="decision" role="dialog" aria-modal="true" aria-labelledby="decision-title">
        <header className="decision-head">
          <Hand size={16} aria-hidden />
          <h2 id="decision-title">Decision needed</h2>
          {decision.slots.length > 1 && <span className="decision-step">{step} of {decision.slots.length}</span>}
          <span className="waiting">Waiting {clock(now - started)}</span>
        </header>

        <p className="decision-why">{why(slot)}</p>

        <p className="decision-incident">
          <b className="mono">{slot.incident}</b> {slot.callName}
          {slot.severity !== null && <span className="sev-tag">Severity {slot.severity}</span>}
          <span className="decision-role">needs {slot.role === "ambulance" ? "an" : "a"} {slot.role}</span>
        </p>

        <section className="voice-card">
          <div className="voice-bar">
            <SpeakerIcon />
            <span className="voice-label">Voice dispatch</span>
            <span className={`wave${speaking ? " on" : ""}`} aria-hidden>
              {Array.from({ length: 16 }, (_, i) => <i key={i} style={{ animationDelay: `${(i % 7) * 70}ms` }} />)}
            </span>
            <span className="voice-actions">
              <button onClick={replay} disabled={muted}>Replay</button>
              <span className="sep" aria-hidden />
              <button onClick={toggleMute} aria-pressed={muted}>{muted ? "Unmute" : "Mute"}</button>
            </span>
          </div>
          <p className="caption" aria-live="polite">{question}</p>
          {blocked && !muted && <p className="voice-note">The browser blocked sound. Press Replay to hear it.</p>}
        </section>

        <div className={`option-grid n${Math.min(slot.options.length, 3)}`}>
          {slot.options.map((o) => (
            <button key={o.vehicle} className="option-card" disabled={sending} onClick={() => choose(o.vehicle)}>
              <span className="option-top">
                <span className="option-title">{o.title}</span>
                {o.untrusted && <span className="untrusted-tag">Untrusted</span>}
                {slot.severity !== null && <span className="sev-tag">Severity {slot.severity}</span>}
              </span>
              <span className="option-dist">
                {o.distanceM !== null ? `${formatDistance(o.distanceM)} from ${slot.callName}` : slot.callName}
              </span>
              {o.holds && (
                <span className="contested">
                  Contested: already assigned to <b>{o.holds}</b>
                  {state.labels[o.holds] ? ` (${state.labels[o.holds]})` : ""}. Sending it leaves {o.holds} to be renegotiated.
                </span>
              )}
              {o.support.map((line) => (
                <span key={line} className="support"><CheckIcon />{line}</span>
              ))}
              <span className="backed">
                {o.backers.length ? (
                  <>Backed by <b>{o.backers.join(", ")}</b></>
                ) : slot.noBids ? (
                  o.busyWith ? <>No bid: pulled from <b>{o.busyWith}</b></> : "No bid: free"
                ) : (
                  "No votes"
                )}
              </span>
            </button>
          ))}
        </div>

        <div className="any-vehicle">
          <label htmlFor="any-vehicle">Or send any {slot.role}:</label>
          <select id="any-vehicle" value={other} onChange={(e) => setOther(e.target.value)} disabled={sending}>
            <option value="" disabled>Pick one</option>
            {slot.anyVehicle.map((a) => (
              <option key={a.vehicle} value={a.vehicle}>{a.vehicle} · {a.note}</option>
            ))}
          </select>
          <button disabled={!other || sending} onClick={() => choose(other)}>Send {other || ""}</button>
        </div>

        {decision.failed.map((line) => (
          <p key={line} className="failed"><XIcon />{line}</p>
        ))}

        {(slot.evidence.length > 0 || slot.reason) && (
          <details className="evidence" open>
            <summary>Evidence</summary>
            {slot.reason && <p className="evidence-reason">{slot.reason}</p>}
            <ul>
              {slot.evidence.map((l, i) => (
                <li key={`${l.claim_id ?? i}-${l.checker}`} className={`result-${l.result}`}>
                  <span className="mono">{l.claim}</span>
                  <span className="evidence-check">
                    {l.checker ? `checked by ${l.checker}` : "no checker"}: <b>{l.result}</b>
                  </span>
                </li>
              ))}
            </ul>
          </details>
        )}
        {sending && <p className="voice-note">Sending your decision…</p>}
        {error && <p className="failed"><XIcon />Could not send the decision: {error}</p>}
      </div>
    </div>
  );
}

function SpeakerIcon() {
  return <Volume2 size={16} aria-hidden />;
}

function CheckIcon() {
  return <Check size={14} aria-hidden />;
}

function XIcon() {
  return <X size={14} aria-hidden />;
}
