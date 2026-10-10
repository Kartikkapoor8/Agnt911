import { useState } from "react";
import type { InjectRequest } from "../api";
import type { Preset } from "../types";

interface Props {
  presets: Preset[];
  onInject: (req: InjectRequest) => Promise<void>;
}

type Result = { ok: boolean; text: string } | null;

/** Fake call: an outside caller reports an emergency that isn't there (POST /inject with the fake_call preset). */
export function FakeCallBox({ presets, onInject }: Props) {
  const preset = presets.find((p) => p.preset.kind === "fake_call");
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<Result>(null);

  if (!preset) return null;

  async function place() {
    if (!preset) return;
    setSending(true);
    setResult(null);
    try {
      // /inject takes preset_id or text, never both, and the backend builds the call from the preset,
      // so the caller's words stay on this screen.
      await onInject({ vehicleId: null, presetId: preset.id });
      setResult({ ok: true, text: `Accepted: ${String(preset.preset.source_id ?? "a caller")} reported a ${String(preset.preset.type ?? "call")}.` });
      setNote("");
    } catch (err) {
      setResult({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="judge-section fake-call">
      <h3>Fake call</h3>
      <p className="hint">No vehicle needed. The first police car on scene finds nothing and the caller loses credibility.</p>
      <input
        type="text"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        maxLength={200}
        placeholder={`Caller says… (${preset.label.toLowerCase()})`}
        aria-label="What the caller says (not sent: the backend builds the call from the preset)"
      />
      <button className="inject secondary" disabled={sending} onClick={place}>
        {sending ? "Placing…" : preset.label}
      </button>
      {result && <p className={result.ok ? "ok-text" : "error-text"}>{result.text}</p>}
    </section>
  );
}
