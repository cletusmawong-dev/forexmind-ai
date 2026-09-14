import React from "react";

/** Catches any render crash and shows a recoverable glass card instead of a
 *  silent black screen. */
export class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: any }> {
  state = { error: null as any };

  static getDerivedStateFromError(error: any) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="relative flex min-h-screen items-center justify-center px-6">
          <div className="ambient" />
          <div className="noise" />
          <div className="glass w-full max-w-[380px] p-7 text-center animate-fadeUp">
            <div className="mx-auto mb-5 flex h-10 w-10 items-center justify-center rounded-full border border-warn/30 bg-warn/[0.08] text-[15px] text-warn">!</div>
            <h1 className="text-[17px] font-semibold tracking-tight">Something went wrong</h1>
            <p className="mt-2 text-[12px] leading-relaxed text-txt-low">
              The interface hit an unexpected error. Your data is safe - the server keeps researching while you reload.
            </p>
            <pre className="mt-4 max-h-24 overflow-auto rounded-2xl border border-white/[0.06] bg-white/[0.03] p-3 text-left text-[10px] leading-relaxed text-txt-faint">
              {String(this.state.error?.message || this.state.error || "Unknown error")}
            </pre>
            <button className="btn-primary mt-5 w-full" onClick={() => window.location.reload()}>
              Reload ForexMind
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
