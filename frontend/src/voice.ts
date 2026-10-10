// Voice dispatch: every spoken line in the app goes through this file.
//
// Today it uses the browser's built-in speechSynthesis, so it works with no key.
// To switch to ElevenLabs later, set VOICE_ENDPOINT to the backend's text-to-speech route
// (the backend holds the API key; it must never be in frontend code or a VITE_ variable).
// speak() then fetches audio from that route and plays it through an <audio> element.
// Nothing outside this file changes: callers only use speak, stop, setMuted, isMuted, onSpeakingChange.

/** Backend text-to-speech route, e.g. "/api/voice". null = use the browser's speechSynthesis. */
const VOICE_ENDPOINT: string | null = null;

/** How a speak() call ended up: it played, the browser blocked autoplay, voice is muted, or there is no voice. */
export type SpeakResult = "played" | "blocked" | "muted" | "unsupported";

const MUTE_KEY = "agnt911.voiceMuted";
const START_TIMEOUT_MS = 1500; // speech that hasn't started by then is treated as blocked

let muted = readMuted();
let speaking = false;
let audio: HTMLAudioElement | null = null; // only used with VOICE_ENDPOINT
const listeners = new Set<(speaking: boolean) => void>();

function readMuted(): boolean {
  try {
    return sessionStorage.getItem(MUTE_KEY) === "1";
  } catch {
    return false;
  }
}

function setSpeaking(value: boolean) {
  if (speaking === value) return;
  speaking = value;
  listeners.forEach((fn) => fn(value));
}

/** Call fn whenever speech starts (true) or ends (false). Returns a function that unsubscribes. */
export function onSpeakingChange(fn: (speaking: boolean) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** True while a line is being spoken. */
export function isSpeaking(): boolean {
  return speaking;
}

/** True when the user muted voice for this browser session. */
export function isMuted(): boolean {
  return muted;
}

/** Mute (stops anything playing) or unmute voice for the rest of this browser session. */
export function setMuted(value: boolean): void {
  muted = value;
  try {
    sessionStorage.setItem(MUTE_KEY, value ? "1" : "0");
  } catch {
    // private mode or blocked storage: mute still holds until reload
  }
  if (value) stop();
}

/** Stop whatever is being spoken right now. */
export function stop(): void {
  if (audio) {
    audio.pause();
    audio = null;
  }
  if (typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel();
  setSpeaking(false);
}

/** Speak one line, replacing anything already playing. Never throws; resolves once speech starts or fails. */
export function speak(text: string): Promise<SpeakResult> {
  if (muted) return Promise.resolve("muted");
  stop();
  return VOICE_ENDPOINT ? speakFromEndpoint(VOICE_ENDPOINT, text) : speakWithBrowser(text);
}

function speakWithBrowser(text: string): Promise<SpeakResult> {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return Promise.resolve("unsupported");
  const synth = window.speechSynthesis;
  return new Promise((resolve) => {
    let settled = false;
    const settle = (result: SpeakResult) => {
      if (!settled) {
        settled = true;
        resolve(result);
      }
    };
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.05;
    u.onstart = () => {
      setSpeaking(true);
      settle("played");
    };
    u.onend = () => setSpeaking(false);
    u.onerror = (e) => {
      setSpeaking(false);
      // "not-allowed" is the autoplay block; "interrupted"/"canceled" mean stop() or a newer line replaced it.
      settle(e.error === "not-allowed" ? "blocked" : e.error === "interrupted" || e.error === "canceled" ? "played" : "unsupported");
    };
    // Some browsers queue silently instead of erroring when there was no user gesture.
    window.setTimeout(() => {
      if (!settled) {
        synth.cancel();
        settle("blocked");
      }
    }, START_TIMEOUT_MS);
    synth.speak(u);
  });
}

// Backend audio path. Expects POST {endpoint} with {"text": "..."} -> audio/mpeg bytes. Not used until VOICE_ENDPOINT is set.
async function speakFromEndpoint(endpoint: string, text: string): Promise<SpeakResult> {
  try {
    const res = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    if (!res.ok) return "unsupported";
    const url = URL.createObjectURL(await res.blob());
    const el = new Audio(url);
    audio = el;
    el.onplaying = () => setSpeaking(true);
    el.onended = el.onpause = () => {
      setSpeaking(false);
      URL.revokeObjectURL(url);
    };
    await el.play(); // rejects with NotAllowedError when autoplay is blocked
    return "played";
  } catch (err) {
    setSpeaking(false);
    return err instanceof DOMException && err.name === "NotAllowedError" ? "blocked" : "unsupported";
  }
}
