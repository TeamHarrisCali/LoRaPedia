import { Component, ErrorInfo, ReactNode } from "react";

/** Keeps the status bar and navigation alive if one page crashes, with a way to recover. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error(error, info.componentStack); }
  componentDidUpdate(prev: { resetKey?: string }) { if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null }); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="rounded-xl border border-danger/60 bg-surface p-6">
        <h2 className="text-xl font-semibold">Something went wrong on this page</h2>
        <p className="mt-1 text-sm text-muted">The rest of LoRaPedia is still running. Try again, or go to another page.</p>
        <pre className="mt-3 max-h-40 overflow-auto rounded-lg border border-line bg-bg p-3 font-mono text-xs">{this.state.error.message}</pre>
        <button className="mt-3 rounded-lg border border-line bg-raised px-3.5 py-2 text-sm hover:border-muted" onClick={() => this.setState({ error: null })}>Try again</button>
      </div>
    );
  }
}
