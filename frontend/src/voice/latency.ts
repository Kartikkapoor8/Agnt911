// Voice latency, measured in the browser:
//   turn  = caller's end of speech -> first agent audio, from the audio itself: the conversation's input (mic) and
//           output (agent) levels are sampled every 20 ms. End of speech is the last loud mic sample before 300 ms of
//           quiet; first agent audio is the first loud output sample after it. (Over WebRTC no VAD scores arrive,
//           and the caller's transcript lands just before the agent speaks, so neither marks end of speech.)
//   tool  = client tool call received -> tool response returned (the handler's own time).
// Every measurement goes to the console as "[voice-latency]" and to window.__agnt911VoiceTimings; with
// VITE_VOICE_DEBUG=true the call's transcript card shows them too.

export interface Timing {
  kind: "turn" | "tool";
  name: string; // "turn 1" or the tool name
  ms: number;
  detail?: string;
  at: number; // performance.now() when recorded
}

declare global {
  interface Window {
    __agnt911VoiceTimings?: Timing[];
  }
}

const MIC_ON = 0.02; // input level that counts as the caller talking
const AGENT_ON = 0.02; // output level that counts as agent audio
const QUIET_MS = 300; // this much mic quiet ends the caller's turn

/** True when VITE_VOICE_DEBUG=true: show timings in the transcript card. */
export const VOICE_DEBUG = import.meta.env.VITE_VOICE_DEBUG === "true";

/** One call's latency tracker. Feed it level samples and tool calls; it reports each measurement through onTiming. */
export function createLatencyTracker(onTiming: (t: Timing) => void) {
  let talking = false; // the caller is mid-turn
  let lastLoud = 0; // last mic sample above MIC_ON
  let endOfSpeech: number | null = null; // the caller finished; waiting for the agent's first audio
  let turns = 0;

  const record = (t: Omit<Timing, "at">) => {
    const full = { ...t, at: performance.now() };
    (window.__agnt911VoiceTimings ??= []).push(full);
    console.info(`[voice-latency] ${full.name}: ${Math.round(full.ms)} ms${full.detail ? ` (${full.detail})` : ""}`);
    onTiming(full);
  };

  return {
    /** One sample of the conversation's input (caller) and output (agent) levels, 0-1. */
    levels(input: number, output: number) {
      const now = performance.now();
      if (input >= MIC_ON) {
        talking = true;
        lastLoud = now;
        endOfSpeech = null; // still talking (or talking again): no turn to time yet
      } else if (talking && now - lastLoud >= QUIET_MS) {
        talking = false;
        endOfSpeech = lastLoud;
      }
      if (endOfSpeech !== null && output >= AGENT_ON) {
        turns++;
        record({ kind: "turn", name: `turn ${turns}`, ms: now - endOfSpeech, detail: "end of speech -> first agent audio" });
        endOfSpeech = null;
      }
    },
    /** Time one client tool: call it when the call arrives, then call the result when the response is returned. */
    tool(name: string) {
      const start = performance.now();
      return (detail?: string) => record({ kind: "tool", name, ms: performance.now() - start, detail });
    },
  };
}

export type LatencyTracker = ReturnType<typeof createLatencyTracker>;
