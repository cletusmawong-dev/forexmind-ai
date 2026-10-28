import logoMark from "../assets/logo-mark.png";
import { useState } from "react";
import { api, endpoints, setToken } from "../lib/api";
import { BUILD_STAMP } from "../lib/version";

export function LoginScreen({ onAuthed }: { onAuthed: () => void }) {
  const invite = (() => {
    try {
      const u = new URLSearchParams(window.location.search).get("invite") || "";
      if (u) sessionStorage.setItem("fm_invite", u);
      return u || sessionStorage.getItem("fm_invite") || "";
    } catch { return ""; }
  })();
  const [mode, setMode] = useState<"login" | "register">(invite ? "register" : "login");
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
          : await api.post(endpoints.register, { email, password, display_name: name || email.split("@")[0], invite });
      setToken(res.token);
      try { sessionStorage.removeItem("fm_invite"); } catch {}
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
          <img src={logoMark} alt="ForexMind AI" className="mb-7 h-[72px] w-[72px] rounded-[20px] object-cover"
               style={{ boxShadow: "0 2px 10px rgba(16,24,40,0.18)" }} />
          <h1 className="text-[24px] font-extrabold tracking-tight text-[var(--text-primary)]">
            FOREXMIND <span className="font-black text-[var(--accent-gold)]">AI</span>
          </h1>
          <p className="mt-2 text-[12.5px] leading-relaxed text-txt-low">Your personal AI trading research agent.</p>
        </div>

        <div className="glass p-6">
          <div className="mb-5 flex rounded-full border border-[rgba(var(--warm-rgb),0.07)] bg-[rgba(var(--warm-rgb),0.035)] p-1">
            {(["login", "register"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`flex-1 rounded-full py-2 text-[12px] font-medium transition-all duration-300 ${
                  mode === m ? "bg-[rgba(var(--warm-rgb),0.10)] text-txt-hi shadow-soft" : "text-txt-low"
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

          {mode === "register" && (
            <div className={`mt-4 rounded-2xl border px-4 py-2.5 text-[11.5px] ${invite ? "border-pos/20 bg-pos/[0.06] text-pos" : "border-warn/20 bg-warn/[0.06] text-warn"}`}>
              {invite
                ? "You've been invited - create your account below (signals access; execution stays with the owner)."
                : "Registration is by invite only - ask the owner for a link."}
            </div>
          )}
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
        <p className="mt-3 font-mono text-[9.5px] uppercase tracking-[0.14em] text-txt-faint">{BUILD_STAMP}</p>
      </div>
    </div>
  );
}
