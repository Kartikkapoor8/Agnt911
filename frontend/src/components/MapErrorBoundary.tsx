import { Component, type ReactNode } from "react";

interface State {
  message: string | null;
}

/** Keep a map crash inside the map column instead of blanking the whole page. */
export class MapErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { message: null };

  /** Record the error so the fallback can show it. */
  static getDerivedStateFromError(error: unknown): State {
    return { message: error instanceof Error ? error.message : String(error) };
  }

  render() {
    if (this.state.message) {
      return (
        <div className="map-missing">
          <strong>Map failed to render.</strong>
          <span>{this.state.message}</span>
        </div>
      );
    }
    return this.props.children;
  }
}
