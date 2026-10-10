import { useState } from "react";
import type { InjectRequest } from "../api";
import type { Preset, Vehicle } from "../types";

interface Props {
  selectedId: string | null;
  vehicles: Record<string, Vehicle>;
  onSelect: (vehicleId: string) => void;
  presets: Preset[];
  onInject: (req: InjectRequest) => Promise<void>;
}

type Result = { ok: boolean; text: string } | null;

const RAW = "__raw__"; // the free-text option, sent as text (the backend stores it as a raw sensor note)

/** Hijack box: corrupt the selected vehicle's sensor feed with a preset or a raw note. */
export function HackBox({ selectedId, vehicles, onSelect, presets, onInject }: Props) {
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
      send({ vehicleId: selectedId, text }, `Accepted: ${selectedId}'s sensors now carry your note.`);
    } else {
      const label = presets.find((p) => p.id === choice)?.label ?? choice;
      send({ vehicleId: selectedId, presetId: choice }, `Accepted: ${selectedId} hacked ("${label}").`);
    }
  }

  const canInject = Boolean(selectedId) && (choice !== RAW || text.trim().length > 0) && !sending;

  return (
    <section className="judge-section hack-box">
      <h3>Hijack</h3>
      <div className="hack-target">
        <select value={selectedId ?? ""} onChange={(e) => e.target.value && onSelect(e.target.value)} aria-label="Vehicle to hijack">
          <option value="" disabled>Pick a vehicle, or click one on the map</option>
          {Object.values(vehicles).map((v) => (
            <option key={v.id} value={v.id}>
              {v.id} · {v.type}{v.untrusted ? " · untrusted" : ""}
            </option>
          ))}
        </select>
      </div>
      <p className="hint">A hack corrupts what the vehicle sees. Its model stays honest and reports the false picture.</p>

      <div className="presets" role="radiogroup" aria-label="Attack">
        {vehiclePresets.map((p) => (
          <button key={p.id} role="radio" aria-checked={choice === p.id} className={choice === p.id ? "chosen" : ""} onClick={() => setChoice(p.id)}>
            {p.label}
          </button>
        ))}
        <button role="radio" aria-checked={choice === RAW} className={choice === RAW ? "chosen" : ""} onClick={() => setChoice(RAW)}>
          Free text
        </button>
      </div>

      {choice === RAW && (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={2000}
          rows={3}
          placeholder="Shown to this vehicle as a sensor note"
        />
      )}
      <button className="inject" disabled={!canInject} onClick={injectVehicle}>
        {sending ? "Injecting…" : selectedId ? `Inject ${selectedId}` : "Inject"}
      </button>

      {result && <p className={result.ok ? "ok-text" : "error-text"}>{result.text}</p>}
    </section>
  );
}
