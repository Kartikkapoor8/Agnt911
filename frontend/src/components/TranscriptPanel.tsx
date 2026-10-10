import { useEffect, useRef, useState } from "react";
import type { Message } from "../types";

interface Props {
  runId: string;
  messages: Message[]; // the backend's latest window (at most 500), oldest first
}

const MAX_LINES = 3000; // keep the DOM bounded on long runs

/** One readable line for a claim, bid, or vote. */
function describe(m: Message): string {
  if (m.kind === "claim") {
    const about = m.incident ? ` ${m.incident}` : "";
    return `claims ${m.type}${about} (conf ${m.confidence.toFixed(2)}): ${m.reasoning}`;
  }
  if (m.kind === "bid") {
    return `bids on ${m.incident}: ETA ${m.eta_seconds}s, capability ${m.capability_match.toFixed(2)}: ${m.reasoning}`;
  }
  return `votes ${m.incident}${m.role ? ` ${m.role}` : ""} option #${m.option_index}: ${m.reasoning}`;
}

/** Scrolling negotiation log: every claim, bid, and vote, deduped across polls, newest at the bottom. */
export function TranscriptPanel({ runId, messages }: Props) {
  const [lines, setLines] = useState<Message[]>([]);
  const seen = useRef(new Set<string>());
  const run = useRef(runId);
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true); // follow the newest line unless the user scrolled up

  // Merge each poll's window into the accumulated log. sig ("sequence:hmac") is unique per message in a run.
  useEffect(() => {
    let reset = false;
    if (run.current !== runId) {
      run.current = runId;
      seen.current = new Set();
      reset = true;
    }
    const fresh = messages.filter((m) => !seen.current.has(m.sig));
    fresh.forEach((m) => seen.current.add(m.sig));
    if (fresh.length || reset) {
      setLines((prev) => (reset ? fresh : [...prev, ...fresh]).slice(-MAX_LINES));
    }
  }, [runId, messages]);

  useEffect(() => {
    if (stick.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines]);

  return (
    <section className="panel transcript">
      <h2>Transcript <span className="count">{lines.length}</span></h2>
      <div
        className="lines"
        ref={box}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
      >
        {lines.length === 0 && <p className="hint">Waiting for the first negotiation…</p>}
        {lines.map((m) => (
          <div key={m.sig} className={`line kind-${m.kind}${m.compromised ? " compromised" : ""}`} data-sig={m.sig}>
            <span className="meta">
              {m.kind === "claim" ? `t${m.tick} ` : ""}R{m.round}
            </span>{" "}
            <b className={`who who-${m.vehicle[0]}`}>{m.vehicle}</b>
            {m.compromised && <span className="tag" title="Sent by a hacked vehicle. Judge view only; the fleet doesn't see this flag.">hacked</span>}{" "}
            {describe(m)}
          </div>
        ))}
      </div>
    </section>
  );
}
