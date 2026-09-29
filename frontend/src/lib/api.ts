const TOKEN_KEY = "fm_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string) {
  localStorage.setItem(TOKEN_KEY, t);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(path, { ...options, headers });
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    /* no body */
  }
  if (!res.ok) {
    const detail = data?.detail || data?.message || res.statusText;
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data as T;
}

export const api = {
  get: <T = any>(p: string) => request<T>(p),
  post: <T = any>(p: string, body?: any) =>
    request<T>(p, { method: "POST", body: JSON.stringify(body ?? {}) }),
  patch: <T = any>(p: string, body?: any) =>
    request<T>(p, { method: "PATCH", body: JSON.stringify(body ?? {}) }),
};

export const endpoints = {
  login: "/api/auth/login",
  register: "/api/auth/register",
  me: "/api/auth/me",
  markets: "/api/markets",
  candles: (m: string, tf: string, limit = 160) => `/api/markets/${m}/candles?tf=${tf}&limit=${limit}`,
  signals: "/api/signals",
  signal: (id: string) => `/api/signals/${id}`,
  action: (id: string) => `/api/signals/${id}/action`,
  analyze: "/api/signals/analyze",
  agentStatus: "/api/agent/status",
  agentActivity: "/api/agent/activity",
  agentScan: "/api/agent/scan",
  chat: "/api/chat",
  lessons: "/api/lessons",
  autopsies: "/api/autopsies",
  autopsyDecide: (id: string) => `/api/autopsies/${id}/decide`,
  hypotheses: "/api/hypotheses",
  approve: (id: string) => `/api/hypotheses/${id}/approve`,
  reject: (id: string) => `/api/hypotheses/${id}/reject`,
  experiments: "/api/experiments",
  runExperiment: "/api/experiments",
  observations: "/api/learning/observations",
  research: "/api/learning/research",
  researchDiscover: "/api/learning/research/discover",
  researchDesign: (id: string) => `/api/learning/research/${id}/design`,
  signalDna: (id: string) => `/api/signals/${id}/dna`,
  signalForensics: (id: string) => `/api/signals/${id}/forensics`,
  signalReplay: (id: string) => `/api/signals/${id}/replay`,
  strategies: "/api/strategies",
  strategy: (id: string) => `/api/strategies/${id}`,
  strategyVersions: (id: string) => `/api/strategies/${id}/versions`,
  rollback: (id: string) => `/api/strategies/${id}/rollback`,
  compare: (id: string, a: string, b: string) => `/api/strategies/${id}/compare?a=${a}&b=${b}`,
  backtest: (id: string) => `/api/strategies/${id}/backtest`,
  journal: "/api/journal/stats",
  analytics: "/api/analytics/summary",
  notifications: "/api/notifications",
  notificationsRead: "/api/notifications/read",
  goals: "/api/goals",
  settings: "/api/settings",
  systemInfo: "/api/system/info",
  notificationTest: "/api/notifications/test",
  daily: "/api/daily",
  aimanager: "/api/aimanager/status",
  adminCommandCenter: "/api/admin/command-center",
  adminUsers: "/api/admin/users",
  adminStatus: (id: string) => `/api/admin/users/${id}/status`,
  adminTrading: (id: string) => `/api/admin/users/${id}/trading`,
  adminEStop: (id: string) => `/api/admin/users/${id}/emergency-stop`,
  telegramLinkToken: "/api/telegram/link-token",
  executions: "/api/aimanager/executions",
  tpAudit: "/api/aimanager/executions/tp-audit",
  positionsLive: "/api/positions/live",
  learningMatrix: "/api/learning/matrix",
  journalEntries: "/api/journal/entries",
  evidence: "/api/evidence",
  evidenceGenerate: "/api/evidence/generate",
  strategyHealth: (id: string) => `/api/learning/strategy-health/${id}`,
  strategyDna: (id: string) => `/api/learning/strategy-dna/${id}`,
  fingerprint: (m: string) => `/api/research/fingerprint/${m}`,
  tca: "/api/risk/tca",
  exposure: "/api/risk/exposure",
  researchSummary: "/api/research/summary",
  montecarlo: "/api/research/montecarlo",
  stress: "/api/research/stress",
  calibration: "/api/research/calibration",
  shadow: "/api/research/shadow",
  shadowRun: "/api/research/shadow/run",
  blocked: "/api/research/blocked",
  ask: "/api/research/ask",
  knowledgeGraph: "/api/knowledge/graph",
  incidents: "/api/incidents",
  security: "/api/admin/security",
  killswitch: "/api/admin/killswitch",
  killswitchCloseAll: "/api/admin/killswitch/close-all",
  replay: (id: string) => `/api/research/replay/${id}`,
  learningRecommendations: "/api/learning/recommendations",
  learningRecsGenerate: "/api/learning/recommendations/generate",
  manualResult: (id: string) => `/api/signals/${id}/manual-result`,
};
