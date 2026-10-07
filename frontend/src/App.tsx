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
import { MoreScreen } from "./screens/MoreScreen";
import { ChartsScreen } from "./screens/ChartsScreen";
import { CommunityScreen } from "./screens/CommunityScreen";
import { NewsScreen } from "./screens/NewsScreen";
import { AdminScreen } from "./screens/AdminScreen";
import { PositionsScreen } from "./screens/PositionsScreen";
import { ExecutionScreen } from "./screens/ExecutionScreen";
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
        Reconnecting to ForexMind services...
      </div>
    </div>
  );
}

export default function App() {
  const [booted, setBooted] = useState(false);
  // An invite link (?invite=...) must ALWAYS land on the signup form -
  // never auto-enter a previously saved account (2026-10-01: a logged-in
  // device opening an invite link jumped straight into the old account).
  const HAS_INVITE = (() => {
    try { return !!new URLSearchParams(window.location.search).get("invite"); }
    catch { return false; }
  })();
  const [authed, setAuthed] = useState(!!getToken() && !HAS_INVITE);
  const location = useLocation();

  useEffect(() => {
    const t = setTimeout(() => setBooted(true), 1500);
    return () => clearTimeout(t);
  }, []);

  // verify stored token on boot - only clear it on a definitive 401.
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

  // P19 polish: scroll-reveal engine - [data-reveal] elements slide in the first
  // time they enter the viewport; rescans on DOM changes (routes, polling).
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add("in");
            io.unobserve(e.target);
          }
        }
      },
      { threshold: 0.05, rootMargin: "0px 0px -4% 0px" }
    );
    let timer: number | undefined;
    const scan = () => {
      timer = undefined;
      document.querySelectorAll("[data-reveal]:not(.js-reveal)").forEach((el) => {
        el.classList.add("js-reveal");
        io.observe(el);
      });
    };
    const mo = new MutationObserver(() => {
      if (timer === undefined) timer = window.setTimeout(scan, 120);
    });
    scan();
    mo.observe(document.body, { childList: true, subtree: true });
    return () => {
      mo.disconnect();
      io.disconnect();
      if (timer !== undefined) clearTimeout(timer);
    };
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
          <main className="min-h-screen min-w-0 flex-1 pb-32 lg:pb-12">
            <div className="mx-auto w-full max-w-[560px] px-5 pt-6 lg:max-w-none lg:px-10 lg:pt-10">
              <Routes>
                <Route path="/" element={<HomeScreen />} />
                <Route path="/signals" element={<SignalsScreen />} />
                <Route path="/signals/:id" element={<SignalDetailScreen />} />
                <Route path="/agent" element={<AgentScreen />} />
                <Route path="/positions" element={<PositionsScreen />} />
                <Route path="/execution" element={<ExecutionScreen />} />
                <Route path="/learning" element={<LearningLabScreen />} />
                <Route path="/strategies" element={<StrategiesScreen />} />
                <Route path="/strategies/:id" element={<StrategiesScreen />} />
                <Route path="/journal" element={<JournalScreen />} />
                <Route path="/analytics" element={<AnalyticsScreen />} />
                <Route path="/notifications" element={<NotificationsScreen />} />
                <Route path="/settings" element={<SettingsScreen />} />
                <Route path="/more" element={<MoreScreen />} />
                <Route path="/charts" element={<ChartsScreen />} />
                <Route path="/community" element={<CommunityScreen />} />
                <Route path="/news" element={<NewsScreen />} />
                <Route path="/admin" element={<AdminScreen />} />
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
