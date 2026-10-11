import { Play } from "lucide-react";

interface Props {
  leaving: boolean; // fading out (400 ms) into the live map
  starting: boolean; // the fresh run is on its way
  onStart: () => void;
}

/** Landing: a looping clip of a hack being caught, dimmed and blurred, with the wordmark, one line and Start demo. */
export function Landing({ leaving, starting, onStart }: Props) {
  return (
    <div className={`landing${leaving ? " leaving" : ""}`}>
      <video className="landing-video" src="/landing.mp4" autoPlay muted loop playsInline aria-hidden />
      <div className="landing-veil" />
      <div className="landing-center">
        <div className="wordmark landing-wordmark">Agnt<span>911</span></div>
        <p className="landing-line">Emergency dispatch with no dispatcher. Try to break it.</p>
        <button className="start-demo" onClick={onStart} disabled={starting || leaving}>
          <Play size={20} aria-hidden />
          {starting ? "Starting…" : "Start demo"}
        </button>
      </div>
    </div>
  );
}
