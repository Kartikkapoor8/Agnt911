import { useState, type CSSProperties } from "react";
import { useFeed, type FeedLine } from "../feed";
import type { Message } from "../types";
import type { MapTarget } from "./MapView";
import { Panel } from "./Panel";
import { TranscriptPanel } from "./TranscriptPanel";

interface Props {
  runId: string;
  messages: Message[];
  onPick: (target: MapTarget) => void; // highlight the line's vehicle (or its call) on the map
}

const MARK: Partial<Record<string, string>> = { good: "✓", bad: "✗", unknown: "?" };

function mark(line: FeedLine): string | null {
  if (line.group === "check") return MARK[line.tone] ?? "?";
  if (line.group === "trust") return line.tone === "bad" ? "▼" : "▲";
  return null;
}

/** Left panel: the typed event feed, newest first, one line per event; Matthew's message transcript is the second tab. */
export function FeedPanel({ runId, messages, onPick }: Props) {
  const lines = useFeed();
  const [tab, setTab] = useState<"events" | "transcript">("events");

  const tabs = (
    <div className="feed-tabs" role="tablist">
      <button role="tab" aria-selected={tab === "events"} className={tab === "events" ? "on" : ""} onClick={() => setTab("events")}>
        Events <span className="count">{lines.length}</span>
      </button>
      <button role="tab" aria-selected={tab === "transcript"} className={tab === "transcript" ? "on" : ""} onClick={() => setTab("transcript")}>
        Transcript
      </button>
    </div>
  );

  return (
    <Panel className="feed-panel" title="Feed" actions={tabs} collapsible>
      {tab === "events" ? (
        <ol className="feed" aria-live="off">
          {lines.length === 0 && <li className="hint">Waiting for the first event…</li>}
          {lines.map((l) => {
            const isVehicle = l.vehicle !== null && l.who === l.vehicle;
            const target: MapTarget | null = l.vehicle ? { kind: "vehicle", id: l.vehicle } : l.incident ? { kind: "incident", id: l.incident } : null;
            const m = mark(l);
            return (
              <li
                key={l.key}
                className={`feed-line group-${l.group} tone-${l.tone} type-${l.type}${target ? " pickable" : ""}`}
                style={{ "--who": isVehicle ? `var(--veh-${l.who}, var(--veh-default))` : "var(--who-incident)" } as CSSProperties}
                onClick={() => target && onPick(target)}
                title={target ? `Show ${target.id} on the map` : undefined}
              >
                <span className="fl-tick">t{l.tick}</span>
                <b className={`fl-who ${isVehicle ? "is-vehicle" : l.who === "Human" ? "is-human" : "is-incident"}`}>{l.who}</b>
                {m && <span className="fl-mark" aria-hidden>{m}</span>}
                <span className="fl-text">{l.text}</span>
              </li>
            );
          })}
        </ol>
      ) : (
        <TranscriptPanel runId={runId} messages={messages} />
      )}
    </Panel>
  );
}
