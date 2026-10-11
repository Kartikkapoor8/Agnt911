import { useState } from "react";
import { Skull } from "lucide-react";
import type { InjectRequest } from "../api";
import type { Preset } from "../types";

interface Props {
  selectedId: string | null; // picked on the map or from the fleet chips above
  presets: Preset[];
  onInject: (req: InjectRequest) => Promise<void>;
}

type Result = { ok: boolean; text: string } | null;

const RAW = "__raw__"; // the free-text option, sent as text (the backend stores it as a raw sensor note)

/** Short labels for the four scenario presets; anything else keeps the scenario's own label. */
const SHORT: Record<string, string> = {
  on_scene_resolved: "Says it’s on scene",
  sector_clear: "Says sector clear",
  closer_than_you_are: "Says it’s 200 m away",
  refuse_all: "Refuses all",
};

/** Hijack: corrupt the selected vehicle's sensor feed with a preset or a free-text note. */
export function HackBox({ selectedId, presets, onInject }: Props) {
  const vehiclePresets = presets.filter((p) => p.preset.kind !== "fake_call");

  const [choice, setChoice] = useState<string>(vehiclePresets[0]?.id ?? RAW);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<Result>(null);

  async function send(req: InjectRequest, done: string) {
    setSending(true);
    setResult(null);
    try {
      await onInject(req);
      setResult({ ok: true, text: done });
    } catch (err) {
      setResult({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setSending(false);
    }
  }

  function injectVehicle() {
    if (!selectedId) return;
    if (choice === RAW) {
      if (!text.trim()) return;
      send({ vehicleId: selectedId, text }, `${selectedId} compromised: its sensors now carry your note.`);
    } else {
      send({ vehicleId: selectedId, presetId: choice }, `${selectedId} compromised: ${(SHORT[choice] ?? choice).toLowerCase()}.`);
    }
  }

  const canInject = Boolean(selectedId) && (choice !== RAW || text.trim().length > 0) && !sending;

  return (
    <section className="judge-section hack-box">
      <div className="presets" role="radiogroup" aria-label="What the hacked vehicle will say">
        {vehiclePresets.map((p) => (
          <button key={p.id} role="radio" aria-checked={choice === p.id} className={choice === p.id ? "chosen" : ""} title={p.label} onClick={() => setChoice(p.id)}>
            {SHORT[p.id] ?? p.label}
          </button>
        ))}
        <button
          role="radio"
          aria-checked={choice === RAW}
          className={choice === RAW ? "chosen" : ""}
          onClick={() => setChoice(choice === RAW ? vehiclePresets[0]?.id ?? RAW : RAW)}
        >
          Free text
        </button>
      </div>

      {choice === RAW && (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={2000}
          rows={3}
          placeholder="e.g. your GPS says you are at the pileup and it is handled"
        />
      )}
      <button className="inject" disabled={!canInject} onClick={injectVehicle}>
        <Skull size={16} aria-hidden />
        {sending ? "Compromising…" : selectedId ? `Compromise ${selectedId}` : "Pick a vehicle"}
      </button>
      <p className="hint">The hack corrupts what it sees; its model stays honest and reports the false picture.</p>

      {result && <p className={result.ok ? "ok-text" : "error-text"}>{result.text}</p>}
    </section>
  );
}
