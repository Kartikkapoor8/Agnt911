// A stand-in for useConversation so Playwright can drive a call without a mic or ElevenLabs.
// Active only in the dev server, and only when a test sets window.__agnt911VoiceMock before the app loads.
// While a mock call is live, window.__agnt911Voice lets the test speak lines and call the client tools.

import { useCallback, useRef } from "react";
import type { UseConversationOptions } from "@elevenlabs/react";

type Role = "user" | "agent";

/** What a test can do during a mock call. */
export interface MockCallControls {
  say(role: Role, text: string): void;
  tool(name: string, args?: Record<string, unknown>): Promise<unknown>;
  hangUp(): void; // the agent ends the call
  fail(message: string): void; // the connection drops with an error
}

declare global {
  interface Window {
    __agnt911VoiceMock?: boolean;
    __agnt911Voice?: MockCallControls;
  }
}

/** True when a test asked for the mock conversation. */
export const MOCK_VOICE = import.meta.env.DEV && typeof window !== "undefined" && Boolean(window.__agnt911VoiceMock);

/** Same startSession/endSession shape as useConversation; callbacks and client tools fire on the test's cue. */
export function useMockConversation(options: UseConversationOptions = {}) {
  const opts = useRef(options);
  opts.current = options;
  const live = useRef(false);

  const endSession = useCallback(() => {
    if (!live.current) return;
    live.current = false;
    delete window.__agnt911Voice;
    opts.current.onDisconnect?.({ reason: "user" });
  }, []);

  const startSession = useCallback(() => {
    live.current = true;
    window.setTimeout(() => {
      if (!live.current) return;
      window.__agnt911Voice = {
        say: (role, text) => opts.current.onMessage?.({ message: text, role, source: role === "agent" ? "ai" : "user", event_id: Date.now() }),
        tool: async (name, args = {}) => {
          const fn = opts.current.clientTools?.[name];
          if (!fn) throw new Error(`No client tool ${name}`);
          const out = await fn(args);
          return typeof out === "string" ? JSON.parse(out) : out;
        },
        hangUp: () => {
          live.current = false;
          delete window.__agnt911Voice;
          opts.current.onDisconnect?.({ reason: "agent" });
        },
        fail: (message) => opts.current.onError?.(message),
      };
      opts.current.onConnect?.({ conversationId: "mock" });
    }, 150);
  }, []);

  return { startSession, endSession };
}
