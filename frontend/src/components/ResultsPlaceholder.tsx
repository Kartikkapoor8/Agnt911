interface Props {
  onBack: () => void;
}

/** Empty stand-in for the results page (fleet consensus vs single dispatcher) until it is built. */
export function ResultsPlaceholder({ onBack }: Props) {
  return (
    <div className="placeholder">
      <div className="wordmark">Agnt<span>911</span></div>
      <p>Results view goes here.</p>
      <button className="start secondary" onClick={onBack}>Back to live</button>
    </div>
  );
}
