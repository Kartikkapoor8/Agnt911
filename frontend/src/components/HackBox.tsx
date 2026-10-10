import { useState } from "react";
import type { InjectRequest } from "../api";
import type { Preset } from "../types";

interface Props {
  selectedId: string | null;
  presets: Preset[];
  onInject: (req: InjectRequest) => Promise<void>;
}

type Result = { ok: boolean; text: string } | null;

const RAW = "__raw__"; // the free-text option, sent as text (the backend stores it as a raw sensor note)

/** Hijack box: corrupt the selected vehicle's sensor feed with a preset or a raw note, or place a fake call. */
export function HackBox({ selectedId, presets, onInject }: Props) {
  const vehiclePresets = presets.filter((p) => p.preset.kind !== "fake_call");
  const callPresets = presets.filter((p) => p.preset.kind === "fake_call");

  const [choice, setChoice] = useState<string>(vehiclePresets[0]?.id ?? RAW);
  const [text, setText] = useState("");
  const [sending, setSending] = useState<string | null>(null); // which button is in flight
  const [result, setResult] = useState<Result>(null);

  async function send(key: string, req: InjectRequest, done: string) {
    setSending(key);
    setResult(null);
    try {
      await onInject(req);
      setResult({ ok: true, text: done });
    } catch (err) {
      setResult({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setSending(null);
    }
  }

  function injectVehicle() {
    if (!selectedId) return;
    if (choice === RAW) {
      if (!text.trim()) return;
      send("vehicle", { vehicleId: selectedId, text }, `Accepted: ${selectedId}'s sensors now carry your note.`);
    } else {
      const label = presets.find((p) => p.id === choice)?.label ?? choice;
      send("vehicle", { vehicleId: selectedId, presetId: choice }, `Accepted: ${selectedId} hacked ("${label}").`);
    }
  }

  const canInject = Boolean(selectedId) && (choice !== RAW || text.trim().length > 0) && sending === null;

  return (
    <section className="panel hack-box">
      <h2>Hack {selectedId ?? "a vehicle"}</h2>
      {!selectedId && <p className="hint">Click a vehicle in the fleet list or on the map.</p>}
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
        {sending === "vehicle" ? "Injecting…" : "Inject"}
      </button>

      {callPresets.length > 0 && (
        <div className="call-presets">
          <p className="hint">Or fake a 911 call. No vehicle needed; the first police car on scene exposes it.</p>
          {callPresets.map((p) => (
            <button
              key={p.id}
              className="inject secondary"
              disabled={sending !== null}
              onClick={() => send(p.id, { vehicleId: null, presetId: p.id }, `Accepted: fake call placed by ${String(p.preset.source_id ?? "caller")}.`)}
            >
              {sending === p.id ? "Placing…" : p.label}
            </button>
          ))}
        </div>
      )}

      {result && <p className={result.ok ? "ok-text" : "error-text"}>{result.text}</p>}
    </section>
  );
}
