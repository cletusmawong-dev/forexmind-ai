import logoMark from "../assets/logo-mark.png";
import { useState } from "react";
import { api, endpoints, setToken } from "../lib/api";

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
          : "Can't reach the server right now. It may be waking up."
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative flex min-h-screen flex-col px-6 pb-10 pt-[max(env(safe-area-inset-top),48px)]">
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(520px 280px at 50% -10%, rgba(76,141,255,0.28), transparent 60%), radial-gradient(400px 240px at 100% 20%, rgba(31,191,117,0.08), transparent 55%)",
        }}
      />
      <div className="relative">
        <img src={logoMark} alt="" className="h-14 w-14 rounded-2xl object-cover" />
        <h1 className="mt-8 text-[40px] font-semibold leading-[1.05] tracking-[-0.04em]">
          Markets,<br />explained.
        </h1>
        <p className="mt-3 max-w-[280px] text-[16px] leading-relaxed text-[var(--text-secondary)]">
          ForexMind watches your pairs and tells you when a setup is real.
        </p>
      </div>

      <div className="relative my-2 flex min-h-[180px] flex-1 items-center">
        <svg viewBox="0 0 360 200" className="h-full w-full" aria-hidden="true">
          <defs>
            <linearGradient id="loginFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#4c8dff" stopOpacity="0.32" />
              <stop offset="100%" stopColor="#4c8dff" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d="M0 130 C50 130 70 48 120 78 C170 108 190 168 240 112 C290 56 320 40 360 72 V200 H0 Z" fill="url(#loginFill)" />
          <path className="login-trace" d="M0 130 C50 130 70 48 120 78 C170 108 190 168 240 112 C290 56 320 40 360 72" fill="none" stroke="#9ec0ff" strokeWidth="2.25" strokeLinecap="round" />
        </svg>
      </div>

      <div className="relative">
        <div className="mb-6 flex gap-6 text-[15px] font-medium">
          {(["login", "register"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={mode === m ? "text-[var(--text-primary)]" : "text-[var(--text-faint)]"}
            >
              {m === "login" ? "Sign in" : "Create account"}
              {mode === m && <span className="mt-1 block h-0.5 w-6 rounded-full bg-[var(--accent)]" />}
            </button>
          ))}
        </div>
        <div className="space-y-3">
          {mode === "register" && (
            <input className="input" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
          )}
          <input className="input" placeholder="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          <input className="input" placeholder="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} />
        </div>
        {mode === "register" && (
          <p className={`mt-3 text-[13px] leading-relaxed ${invite ? "text-[var(--accent-green)]" : "text-[var(--text-muted)]"}`}>
            {invite ? "This invite gives you signal access. Execution stays with the owner." : "Accounts are invite only."}
          </p>
        )}
        {error && <p className="mt-3 text-[13px] text-[var(--accent-red)]">{error}</p>}
        <button className="btn-primary press mt-5 w-full" disabled={busy} onClick={submit}>
          {busy ? "Connecting…" : mode === "login" ? "Continue" : "Create account"}
        </button>
      </div>
    </div>
  );
}
