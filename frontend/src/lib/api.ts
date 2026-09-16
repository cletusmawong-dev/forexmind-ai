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
};
