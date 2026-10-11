// Talk to the ElevenLabs dispatcher agent over WebRTC. The agent fills in the call through three client tools
// (tools.ts); the fleet then negotiates it like any other call and the map draws it from /state.

import { useEffect, useMemo, useRef, useState } from "react";
import { ConversationProvider, useConversation } from "@elevenlabs/react";
import { Phone, PhoneOff, Timer } from "lucide-react";
import { API_HEADERS } from "../api";
import { toast } from "../toast";
import { createLatencyTracker, VOICE_DEBUG, type LatencyTracker, type Timing } from "./latency";
import { MOCK_VOICE, useMockConversation } from "./mock";
import { dispatcherTools, newCallSession } from "./tools";
import "./voice.css";

const COUNTDOWN_S = 20;
const HARD_STOP_MS = 25_000; // client-side, whatever the agent's own limit is
const useSession = MOCK_VOICE ? useMockConversation : useConversation; // fixed for the page's lifetime

type Phase = "idle" | "connecting" | "live" | "ended" | "error";
interface Line {
  role: "user" | "agent" | "timing";
  text: string;
}

async function fetchToken(): Promise<string> {
  const res = await fetch("/api/voice/token", { headers: API_HEADERS });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || typeof body.token !== "string") throw new Error(body.detail ?? `token request failed (${res.status})`);
  return body.token;
}

/** Start Call / End Call for the voice dispatcher, with status, countdown and a live transcript. */
export function VoiceCall() {
  return (
    <ConversationProvider>
      <CallPanel />
    </ConversationProvider>
  );
}

function CallPanel() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [left, setLeft] = useState(COUNTDOWN_S);
  const [incident, setIncident] = useState<string | null>(null);
  const session = useRef(newCallSession());
  const active = useRef(false);
  const timers = useRef<number[]>([]);
  const latency = useRef<LatencyTracker | null>(null);
  const showTiming = (t: Timing) => {
    if (VOICE_DEBUG) setLines((l) => [...l, { role: "timing" as const, text: `${t.name}: ${Math.round(t.ms)} ms${t.detail ? ` · ${t.detail}` : ""}` }].slice(-40));
  };

  const stopTimers = () => {
    timers.current.forEach((t) => window.clearTimeout(t));
    timers.current = [];
  };
  const settle = (next: "ended" | "error") => {
    active.current = false;
    stopTimers();
    setIncident(session.current.incidentId);
    setPhase((p) => (p === "error" ? p : next));
  };

  const clientTools = useMemo(
    () => dispatcherTools(() => session.current, (message) => toast(`Voice dispatcher: ${message}`, "error"), () => latency.current?.tool),
    [],
  );
  const conversation = useSession({
    clientTools,
    onConnect: () => active.current && setPhase("live"),
    onMessage: ({ message, role }) => setLines((l) => [...l, { role, text: message }].slice(-40)),
    onDisconnect: () => settle("ended"),
    onError: (message) => fail(message),
  });
  const convo = useRef(conversation);
  convo.current = conversation;

  // While live, sample mic and agent levels every 20 ms for the end-of-speech -> agent-audio timing.
  useEffect(() => {
    if (phase !== "live") return;
    const c = convo.current as Partial<{ getInputVolume: () => number; getOutputVolume: () => number }>;
    if (typeof c.getInputVolume !== "function" || typeof c.getOutputVolume !== "function") return; // the mock has none
    const t = window.setInterval(() => {
      const cur = convo.current as unknown as { getInputVolume: () => number; getOutputVolume: () => number };
      latency.current?.levels(cur.getInputVolume(), cur.getOutputVolume());
    }, 20);
    return () => window.clearInterval(t);
  }, [phase]);

  function hangUp() {
    try {
      convo.current.endSession();
    } catch {
      // already closed
    }
  }

  function fail(message: string) {
    if (!active.current) return;
    toast(`Voice call failed: ${message}`, "error");
    setPhase("error");
    settle("error");
    hangUp();
  }

  async function start() {
    if (active.current) return;
    active.current = true;
    session.current = newCallSession();
    latency.current = createLatencyTracker(showTiming);
    setLines([]);
    setIncident(null);
    setLeft(COUNTDOWN_S);
    setPhase("connecting");
    for (let s = 1; s <= COUNTDOWN_S; s++) timers.current.push(window.setTimeout(() => setLeft(COUNTDOWN_S - s), s * 1000));
    timers.current.push(
      window.setTimeout(() => {
        if (!active.current) return;
        toast("Voice call stopped at the 25 s demo limit.", "info");
        settle("ended");
        hangUp();
      }, HARD_STOP_MS),
    );
    try {
      const token = MOCK_VOICE ? "mock" : await fetchToken();
      if (!active.current) return; // stopped while the token was on its way
      convo.current.startSession({ conversationToken: token, connectionType: "webrtc" });
    } catch (err) {
      fail(err instanceof Error ? err.message : String(err));
    }
  }

  function end() {
    if (!active.current) return;
    settle("ended");
    hangUp();
  }

  const busy = phase === "connecting" || phase === "live";
  return (
    <div className="voice-call" data-phase={phase}>
      <p className="voice-notice">Demo only. Does not contact emergency services.</p>
      <div className="voice-row">
        {busy ? (
          <button className="voice-btn end" onClick={end}>
            <PhoneOff size={16} aria-hidden />
            End Call
          </button>
        ) : (
          <button className="voice-btn start" onClick={start}>
            <Phone size={16} aria-hidden />
            Start Call
          </button>
        )}
        {phase !== "idle" && (
          <span className={`voice-status is-${phase}`} role="status">
            {phase}
          </span>
        )}
        {busy && (
          <span className="voice-countdown" aria-label="seconds left">
            0:{String(left).padStart(2, "0")}
          </span>
        )}
      </div>
      {(busy || lines.length > 0) && (
        <div className="voice-transcript" aria-live="polite">
          {lines.length === 0 && <p className="voice-line wait">{phase === "connecting" ? "Ringing…" : "Listening…"}</p>}
          {lines.map((l, i) => (
            <p key={i} className={`voice-line ${l.role}`}>
              <b>{l.role === "agent" ? "Dispatcher" : l.role === "timing" ? <Timer size={12} aria-label="timing" /> : "Caller"}</b> {l.text}
            </p>
          ))}
        </div>
      )}
      {!busy && incident && <p className="voice-result">Call {incident} is on the map.</p>}
    </div>
  );
}
