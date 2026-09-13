import { useEffect, useRef, useState } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import { api, endpoints, getToken, setToken } from "./lib/api";
import { LoginScreen } from "./screens/LoginScreen";
import { HomeScreen } from "./screens/HomeScreen";
import { SignalsScreen } from "./screens/SignalsScreen";
import { SignalDetailScreen } from "./screens/SignalDetailScreen";
import { AgentScreen } from "./screens/AgentScreen";
import { LearningLabScreen } from "./screens/LearningLabScreen";
import { StrategiesScreen } from "./screens/StrategiesScreen";
import { JournalScreen } from "./screens/JournalScreen";
import { AnalyticsScreen } from "./screens/AnalyticsScreen";
import { NotificationsScreen } from "./screens/NotificationsScreen";
import { SettingsScreen } from "./screens/SettingsScreen";
import { BottomNav } from "./components/BottomNav";
import { Sidebar } from "./components/Sidebar";
import { Splash } from "./screens/SplashScreen";
import { ErrorBoundary } from "./components/ErrorBoundary";

/** Subtle floating pill that appears only while the backend is unreachable. */
function ConnBanner() {
  const [down, setDown] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    let timer: number | undefined;
    const ping = async () => {
      try {
        await fetch("/api/health");
        if (alive.current) setDown(false);
      } catch {
        if (alive.current) setDown(true);
      } finally {
        if (alive.current) timer = window.setTimeout(ping, 5000);
      }
    };
    ping();
    return () => {
      alive.current = false;
      if (timer) clearTimeout(timer);
    };
  }, []);
  if (!down) return null;
  return (
    <div className="pointer-events-none fixed left-1/2 top-4 z-[60] -translate-x-1/2 animate-fadeUp px-4">
      <div className="glass-float flex items-center gap-2.5 whitespace-nowrap px-4 py-2.5 text-[11px] font-medium text-warn">
        <span className="h-1.5 w-1.5 animate-pulseSoft rounded-full bg-warn" />
        Reconnecting to ForexMind services…
      </div>
    </div>
  );
}

export default function App() {
  const [booted, setBooted] = useState(false);
  const [authed, setAuthed] = useState(!!getToken());
  const location = useLocation();

  useEffect(() => {
    const t = setTimeout(() => setBooted(true), 1500);
    return () => clearTimeout(t);
  }, []);

  // verify stored token on boot — only clear it on a definitive 401.
  // A network failure (backend waking up) keeps the session and the screens
  // degrade gracefully into "reconnecting" states.
  useEffect(() => {
    if (!getToken()) return;
    api.get(endpoints.me).catch((e: any) => {
      if (e?.status === 401) {
        setToken("");
        localStorage.removeItem("fm_token");
        setAuthed(false);
      }
    });
  }, []);

  if (!booted) return <Splash />;
  if (!authed) return <LoginScreen onAuthed={() => setAuthed(true)} />;

  return (
    <div className="min-h-screen">
      <div className="ambient" />
      <div className="noise" />
      <ConnBanner />
      <ErrorBoundary>
        <div className="mx-auto flex w-full max-w-[1180px]">
          <Sidebar />
          <main className="min-h-screen flex-1 pb-32 lg:pb-12">
            <div className="mx-auto w-full max-w-[560px] px-5 pt-5 lg:max-w-none lg:px-10 lg:pt-10">
              <Routes>
                <Route path="/" element={<HomeScreen />} />
                <Route path="/signals" element={<SignalsScreen />} />
                <Route path="/signals/:id" element={<SignalDetailScreen />} />
                <Route path="/agent" element={<AgentScreen />} />
                <Route path="/learning" element={<LearningLabScreen />} />
                <Route path="/strategies" element={<StrategiesScreen />} />
                <Route path="/strategies/:id" element={<StrategiesScreen />} />
                <Route path="/journal" element={<JournalScreen />} />
                <Route path="/analytics" element={<AnalyticsScreen />} />
                <Route path="/notifications" element={<NotificationsScreen />} />
                <Route path="/settings" element={<SettingsScreen />} />
                <Route path="*" element={<HomeScreen />} />
              </Routes>
            </div>
          </main>
        </div>
        <BottomNav />
      </ErrorBoundary>
    </div>
  );
}
