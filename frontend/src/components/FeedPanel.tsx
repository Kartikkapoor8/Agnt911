import { useState, type CSSProperties } from "react";
import { checkSentence, decisionSentence, type CheckCard, type DecisionCard, type NoteCard, type RawItem, type StoryItem, useStory } from "../story";
import type { Message, Preset, Vehicle } from "../types";
import type { MapTarget } from "./MapView";
import { Panel } from "./Panel";
import { TranscriptPanel } from "./TranscriptPanel";

interface Props {
  runId: string;
  messages: Message[];
  vehicles: Record<string, Vehicle>;
  labels: Record<string, string>;
  presets: Preset[];
  onPick: (target: MapTarget) => void; // highlight the card's vehicle (or its call) on the map
}

const MARK: Record<string, string> = { confirmed: "✓", contradicted: "✗", unverifiable: "?" };
const vehicleColor = (id: string) => ({ "--who": `var(--veh-${id}, var(--veh-default))` }) as CSSProperties;

/** Where a click on this item takes the map. */
function targetOf(item: StoryItem): MapTarget | null {
  switch (item.kind) {
    case "decision": return { kind: "incident", id: item.incident };
    case "check": return { kind: "vehicle", id: item.vehicle };
    case "raw": return item.line.vehicle ? { kind: "vehicle", id: item.line.vehicle } : item.line.incident ? { kind: "incident", id: item.line.incident } : null;
    default: return item.vehicle ? { kind: "vehicle", id: item.vehicle } : item.incident ? { kind: "incident", id: item.incident } : null;
  }
}

/** Left panel: the story of the run as cards, newest first. "Show all" adds the raw log lines; the transcript tab has every message. */
export function FeedPanel({ runId, messages, vehicles, labels, presets, onPick }: Props) {
  const story = useStory();
  const [tab, setTab] = useState<"story" | "transcript">("story");
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? story : story.filter((x) => x.kind !== "raw");

  const actions = (
    <>
      {tab === "story" && (
        <label className="show-all" title="Also show bids, votes, passing checks, and arrivals">
          <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
          Show all
        </label>
      )}
      <div className="feed-tabs" role="tablist">
        <button role="tab" aria-selected={tab === "story"} className={tab === "story" ? "on" : ""} onClick={() => setTab("story")}>
          Story
        </button>
        <button role="tab" aria-selected={tab === "transcript"} className={tab === "transcript" ? "on" : ""} onClick={() => setTab("transcript")}>
          Transcript
        </button>
      </div>
    </>
  );

  return (
    <Panel className="feed-panel" title="Feed" actions={actions} collapsible>
      {tab === "story" ? (
        <ol className="story" aria-live="off">
          {shown.length === 0 && <li className="hint">Waiting for the first call…</li>}
          {shown.map((item) => {
            const target = targetOf(item);
            const pick = () => target && onPick(target);
            switch (item.kind) {
              case "decision":
                return <DecisionView key={item.key} card={item} vehicles={vehicles} labels={labels} expandedByDefault={showAll} onPick={pick} />;
              case "check":
                return <CheckView key={item.key} card={item} onPick={pick} />;
              case "raw":
                return <RawView key={item.key} item={item} onPick={pick} />;
              default:
                return <NoteView key={item.key} card={item} presets={presets} labels={labels} onPick={pick} />;
            }
          })}
        </ol>
      ) : (
        <TranscriptPanel runId={runId} messages={messages} />
      )}
    </Panel>
  );
}

/** One incident decision: header, one sentence, and the bids and votes behind an expand arrow. */
function DecisionView({ card, vehicles, labels, expandedByDefault, onPick }: { card: DecisionCard; vehicles: Record<string, Vehicle>; labels: Record<string, string>; expandedByDefault: boolean; onPick: () => void }) {
  const [open, setOpen] = useState<boolean | null>(null); // null = follow "show all"
  const expanded = open ?? expandedByDefault;
  const label = card.label ?? labels[card.incident]; // the label when this decision was made
  const rows = [...new Set([...card.bids.map((b) => b.vehicle), ...card.votes.map((x) => x.voter)])];
  return (
    <li className={`story-card card-decision status-${card.status}`} onClick={onPick}>
      <div className="sc-head">
        <span className="sc-emoji" aria-hidden>🚨</span>
        <span className="sc-title">
          <b>{card.incident}</b>
          {label ? ` · ${label}` : ""}
          {card.severity !== null ? ` · sev ${card.severity}` : ""}
          {card.revote && <span className="sc-tag">re-vote</span>}
        </span>
        <span className="sc-tick">t{card.tick}</span>
      </div>
      <p className="sc-body">{decisionSentence(card, vehicles)}</p>
      {rows.length > 0 && (
        <>
          <button
            className="sc-expand"
            aria-expanded={expanded}
            onClick={(e) => {
              e.stopPropagation();
              setOpen(!expanded);
            }}
          >
            <span className="chevron" aria-hidden /> {expanded ? "Hide" : "Bids and votes"}
          </button>
          {expanded && (
            <table className="sc-table">
              <thead>
                <tr><th>Unit</th><th>Bid</th><th>Check</th><th>Vote</th></tr>
              </thead>
              <tbody>
                {rows.map((id) => {
                  const bid = card.bids.find((b) => b.vehicle === id);
                  const votes = card.votes.filter((x) => x.voter === id);
                  const chosen = card.assigned.includes(id);
                  return (
                    <tr key={id} className={chosen ? "chosen" : ""}>
                      <td style={vehicleColor(id)} className="sc-unit">{id}</td>
                      <td>{bid ? (bid.withdrawn ? "withdrew" : bid.eta !== null ? `${bid.eta} s` : "–") : "–"}</td>
                      <td className={bid?.check ? `result-${bid.check.result}` : ""}>
                        {bid?.check ? `${MARK[bid.check.result] ?? "?"} ${bid.check.verifier ?? "nobody"}` : "–"}
                      </td>
                      <td className={votes.some((x) => !x.counted) ? "not-counted" : ""}>
                        {votes.length ? votes.map((x) => x.choice ?? "?").join(" · ") : "–"}
                        {votes.some((x) => !x.counted) && " (not counted)"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </>
      )}
    </li>
  );
}

/** A failed check: who checked whom, what was false, and the trust it cost. */
function CheckView({ card, onPick }: { card: CheckCard; onPick: () => void }) {
  return (
    <li className="story-card card-check" style={vehicleColor(card.vehicle)} onClick={onPick}>
      <div className="sc-head">
        <span className="sc-mark" aria-hidden>✗</span>
        <span className="sc-text">{checkSentence(card)}</span>
        <span className="sc-tick">t{card.tick}</span>
      </div>
    </li>
  );
}

const HACK_WORDS: Record<string, string> = { raw: "a free-text note" };

/** Untrusted, trusted again, hack, human, resolved, false report, escalated call. */
function NoteView({ card, presets, labels, onPick }: { card: NoteCard; presets: Preset[]; labels: Record<string, string>; onPick: () => void }) {
  let text = card.text;
  if (card.kind === "hack") {
    const preset = presets.find((p) => p.preset.kind === card.text || p.id === card.text);
    text = `Judge hacked ${card.vehicle}: ${preset ? preset.label.toLowerCase() : HACK_WORDS[card.text] ?? card.text.replace(/_/g, " ")}.`;
  }
  if (card.kind === "resolved" && card.incident && labels[card.incident]) text = `${card.incident} · ${labels[card.incident]} · resolved`;
  const icon: Record<string, string> = { untrusted: "⛔", trusted: "✓", trust: "▲", hack: "☠", human: "👤", resolved: "✓", false_report: "✗", call_escalated: "⚠" };
  return (
    <li className={`story-card note note-${card.kind}`} style={card.vehicle ? vehicleColor(card.vehicle) : undefined} onClick={onPick}>
      <div className="sc-head">
        <span className="sc-mark" aria-hidden>{icon[card.kind] ?? "•"}</span>
        <span className="sc-text">{text}</span>
        <span className="sc-tick">t{card.tick}</span>
      </div>
    </li>
  );
}

/** A routine event, shown only with "show all". */
function RawView({ item, onPick }: { item: RawItem; onPick: () => void }) {
  const l = item.line;
  const isVehicle = l.vehicle !== null && l.who === l.vehicle;
  return (
    <li className={`raw-line group-${l.group} tone-${l.tone}`} style={isVehicle ? vehicleColor(l.who) : undefined} onClick={onPick}>
      <span className="fl-tick">t{l.tick}</span>
      <b className={`fl-who ${isVehicle ? "is-vehicle" : "is-incident"}`}>{l.who}</b>
      <span className="fl-text">{l.text}</span>
    </li>
  );
}
