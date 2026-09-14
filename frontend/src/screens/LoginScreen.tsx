import { useState } from "react";
import { api, endpoints, setToken } from "../lib/api";

export function LoginScreen({ onAuthed }: { onAuthed: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const res =
        mode === "login"
          ? await api.post(endpoints.login, { email, password })
          : await api.post(endpoints.register, { email, password, display_name: name || email.split("@")[0] });
      setToken(res.token);
      onAuthed();
    } catch (e: any) {
      setError(
        e?.status
          ? e.message || "Login failed"
          : "Can't reach the server right now - it may be waking up. Try again in a few seconds."
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center px-6">
      <div className="ambient" />
      <div className="noise" />
      <div className="w-full max-w-[380px] animate-fadeUp">
        {/* hero */}
        <div className="mb-10 flex flex-col items-center text-center">
          <div className="relative mb-7">
            <div
              className="absolute -inset-9 rounded-full blur-3xl opacity-35 animate-breathe"
              style={{ background: "conic-gradient(from 200deg, #4d7cfe, #8e7bff, #33d6f6, #4d7cfe)" }}
            />
            <svg width="64" height="64" viewBox="0 0 48 48" fill="none">
              <defs>
                <linearGradient id="llg" x1="0" y1="0" x2="48" y2="48">
                  <stop stopColor="#33d6f6" />
                  <stop offset="1" stopColor="#4d7cfe" />
                </linearGradient>
              </defs>
              <circle cx="24" cy="24" r="21" stroke="url(#llg)" strokeWidth="1.8" />
              <path d="M17 34v-4.6c-2.5-1.8-4-4.7-4-7.9C13 16 17.6 11.5 23.5 11.5c5.6 0 10.2 4 10.8 9.4l2.7 2.6-2.7 1v2.9c0 1.7-1.4 3.1-3.1 3.1H28V34" stroke="url(#llg)" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M17.5 26.5l4.6-4.6 3 3 5.4-5.4" stroke="#2fd98a" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M30.5 19.5h-4.4M30.5 19.5v4.4" stroke="#2fd98a" strokeWidth="1.9" strokeLinecap="round" />
            </svg>
          </div>
          <h1 className="text-[24px] font-semibold tracking-tight">
            ForexMind <span className="text-acc">AI</span>
          </h1>
          <p className="mt-2 text-[12.5px] leading-relaxed text-txt-low">Your personal AI trading research agent.</p>
        </div>

        <div className="glass p-6">
          <div className="mb-5 flex rounded-full border border-white/[0.06] bg-white/[0.02] p-1">
            {(["login", "register"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`flex-1 rounded-full py-2 text-[12px] font-medium transition-all duration-300 ${
                  mode === m ? "bg-white/[0.09] text-txt-hi shadow-soft" : "text-txt-low"
                }`}
              >
                {m === "login" ? "Sign in" : "Create account"}
              </button>
            ))}
          </div>

          <div className="space-y-3">
            {mode === "register" && <input className="input" placeholder="Display name" value={name} onChange={(e) => setName(e.target.value)} />}
            <input className="input" placeholder="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            <input className="input" placeholder="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>

          {error && <div className="mt-4 rounded-2xl border border-neg/20 bg-neg/[0.06] px-4 py-2.5 text-[11.5px] text-neg">{error}</div>}

          <button className="btn-primary mt-5 w-full" disabled={busy} onClick={submit}>
            {busy ? "Connecting..." : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </div>

        <p className="mt-8 text-center text-[10px] leading-relaxed text-txt-faint">
          Research & signals only - ForexMind AI never executes trades.
          <br />
          You stay in control of every decision.
        </p>
      </div>
    </div>
  );
}
