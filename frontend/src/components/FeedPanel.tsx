import { useState } from "react";
import {
  Ban, Check, ChevronDown, CircleCheck, CircleHelp, Siren, Skull, TriangleAlert, TrendingDown, UserCheck, X, type LucideIcon,
} from "lucide-react";
import {
  checkSentence, decisionLine, decisionSentence, involvedIn, type DecisionCard, type NoteCard, type RawItem, type StoryItem, useStory,
} from "../story";
import type { Message, Preset, Vehicle } from "../types";
import type { Highlight } from "./MapView";
import { Panel } from "./Panel";
import { TranscriptPanel } from "./TranscriptPanel";

interface Props {
  runId: string;
  messages: Message[];
  vehicles: Record<string, Vehicle>;
  labels: Record<string, string>;
  presets: Preset[];
  onPick: (target: Omit<Highlight, "nonce">) => void; // pan to the card's call and highlight who was involved
}

const MARK: Record<string, LucideIcon> = { confirmed: Check, contradicted: X, unverifiable: CircleHelp };
const NOTE_ICON: Record<string, LucideIcon> = {
  untrusted: Ban, trusted: UserCheck, trust: TrendingDown, hack: Skull, human: UserCheck, resolved: CircleCheck, false_report: X, call_escalated: TriangleAlert,
};
const HACK_WORDS: Record<string, string> = { raw: "a free-text note" };

/** Where a click on this item takes the map: its call if it has one, else its vehicle. */
function targetOf(item: StoryItem): Omit<Highlight, "nonce"> | null {
  const involved = involvedIn(item);
  switch (item.kind) {
    case "decision": return { kind: "incident", id: item.incident, involved };
    case "check": return { kind: "vehicle", id: item.vehicle, involved };
    case "raw": {
      const l = item.line;
      return l.incident ? { kind: "incident", id: l.incident, involved } : l.vehicle ? { kind: "vehicle", id: l.vehicle, involved } : null;
    }
    default: return item.incident ? { kind: "incident", id: item.incident, involved } : item.vehicle ? { kind: "vehicle", id: item.vehicle, involved } : null;
  }
}

/** Left panel: the story as one-line cards (newest first, colour edge by type); expand a card for detail. */
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
          All events
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
                return <DecisionView key={item.key} card={item} vehicles={vehicles} labels={labels} onPick={pick} />;
              case "check":
                return <Line key={item.key} kind="card-check" icon={X} tick={item.tick} text={checkSentence(item)} onPick={pick} />;
              case "raw":
                return <RawView key={item.key} item={item} onPick={pick} />;
              default:
                return <Line key={item.key} kind={`note-${item.kind}`} icon={NOTE_ICON[item.kind] ?? CircleCheck} tick={item.tick} text={noteText(item, presets, labels)} onPick={pick} />;
            }
          })}
        </ol>
      ) : (
        <TranscriptPanel runId={runId} messages={messages} />
      )}
    </Panel>
  );
}

function noteText(card: NoteCard, presets: Preset[], labels: Record<string, string>): string {
  if (card.kind === "hack") {
    const preset = presets.find((p) => p.preset.kind === card.text || p.id === card.text);
    return `Judge compromised ${card.vehicle}: ${preset ? preset.label.toLowerCase() : HACK_WORDS[card.text] ?? card.text.replace(/_/g, " ")}`;
  }
  if (card.kind === "resolved" && card.incident) return `${card.incident}${labels[card.incident] ? ` · ${labels[card.incident]}` : ""} resolved`;
  return card.text;
}

/** One line: icon, text (truncated), tick. */
function Line({ kind, icon: Icon, tick, text, onPick }: { kind: string; icon: LucideIcon; tick: number; text: string; onPick: () => void }) {
  return (
    <li className={`story-card ${kind}`} onClick={onPick} title={text}>
      <div className="sc-head">
        <span className="sc-mark" aria-hidden><Icon size={16} /></span>
        <span className="sc-text">{text}</span>
        <span className="sc-tick">t{tick}</span>
      </div>
    </li>
  );
}

/** One incident decision: one line (call and outcome); the chevron opens the sentence and the bids and votes. */
function DecisionView({ card, vehicles, labels, onPick }: { card: DecisionCard; vehicles: Record<string, Vehicle>; labels: Record<string, string>; onPick: () => void }) {
  const [open, setOpen] = useState(false);
  const label = card.label ?? labels[card.incident]; // the label when this decision was made
  const rows = [...new Set([...card.bids.map((b) => b.vehicle), ...card.votes.map((x) => x.voter)])];
  return (
    <li className={`story-card card-decision status-${card.status}${open ? " open" : ""}`} onClick={onPick} title={decisionSentence(card, vehicles)}>
      <div className="sc-head">
        <span className="sc-mark" aria-hidden><Siren size={16} /></span>
        <span className="sc-title">
          <b>{card.incident}</b> <span className="sc-label">{label ?? ""}</span>
        </span>
        <span className="sc-outcome">{decisionLine(card)}</span>
        <button
          className="sc-expand"
          aria-expanded={open}
          aria-label={open ? "Hide details" : "Show details"}
          onClick={(e) => {
            e.stopPropagation();
            setOpen(!open);
          }}
        >
          <ChevronDown size={16} />
        </button>
      </div>
      {open && (
        <div className="sc-detail">
          <p className="sc-body">
            {card.severity !== null && <span className="sc-sev">sev {card.severity}{card.revote ? " · re-vote" : ""} · t{card.tick}</span>}
            {decisionSentence(card, vehicles)}
          </p>
          {rows.length > 0 && (
            <table className="sc-table">
              <thead>
                <tr><th>Unit</th><th>Bid</th><th>Check</th><th>Vote</th></tr>
              </thead>
              <tbody>
                {rows.map((id) => {
                  const bid = card.bids.find((b) => b.vehicle === id);
                  const votes = card.votes.filter((x) => x.voter === id);
                  const Mark = bid?.check ? MARK[bid.check.result] ?? CircleHelp : null;
                  return (
                    <tr key={id} className={card.assigned.includes(id) ? "chosen" : ""}>
                      <td className="sc-unit">{id}</td>
                      <td>{bid ? (bid.withdrawn ? "withdrew" : bid.eta !== null ? `${bid.eta} s` : "–") : "–"}</td>
                      <td className={bid?.check ? `result-${bid.check.result}` : ""}>
                        {Mark && bid?.check ? <span className="result-mark"><Mark size={12} /> {bid.check.verifier ?? "nobody"}</span> : "–"}
                      </td>
                      <td className={votes.some((x) => !x.counted) ? "not-counted" : ""}>
                        {votes.length ? votes.map((x) => x.choice ?? "?").join(" · ") : "–"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}
    </li>
  );
}

/** A routine event, shown only with "All events". */
function RawView({ item, onPick }: { item: RawItem; onPick: () => void }) {
  const l = item.line;
  return (
    <li className={`raw-line group-${l.group} tone-${l.tone}`} onClick={onPick}>
      <span className="fl-tick">t{l.tick}</span>
      <b className="fl-who">{l.who}</b>
      <span className="fl-text">{l.text}</span>
    </li>
  );
}
