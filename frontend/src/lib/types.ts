export interface MarketCard {
  symbol: string;
  price: number | null;
  demo: boolean;
  data_status: string;
  bias: string;
  timeframe: string;
  status: string;
  mtf?: Record<string, number>;
  change_pct?: number;
}

export interface Check {
  label: string;
  ok: boolean;
  detail: string;
}

export interface Signal {
  id: string;
  signal_id: string;
  strategy_id: string;
  strategy_name: string;
  strategy_version: string;
  market: string;
  timeframe: string;
  direction: "BUY" | "SELL";
  entry: number;
  entry_zone: [number, number];
  sl: number;
  tp1: number | null;
  tp2: number | null;
  tp3: number | null;
  risk: number;
  rr_primary: number;
  score: number;
  score_components: Record<string, number>;
  reason: string;
  checks: Check[];
  mtf: Record<string, number> | null;
  market_conditions: { session?: string; volatility_regime?: string; price?: number };
  candle_time: string;
  status: string;
  tp_hits: number;
  r_multiple: number;
  outcome: string | null;
  completed: boolean;
  user_action: string | null;
  extra_signal?: boolean;
  execution_status?: string | null;
  mt5_ticket?: number | null;
  entry_blocked_reason?: string | null;
  daily_pl_at_signal?: number | null;
  user_manual?: { taken?: boolean; result?: string; pl?: number; entry_price?: number; sl?: number; tp?: number; exit_reason?: string } | null;
  user_entry_price?: number | null;
  user_notes?: string;
  result_analysis?: any;
  createdAt: string;
  adaptive?: AdaptiveEvaluation | null;
  dna?: DnaSnapshot | null;
  forensics?: ForensicsReport | null;
}

export interface DnaSnapshot {
  price?: number;
  ema9?: number;
  ema21?: number;
  ema21_slope_pct?: number;
  atr14?: number;
  atr_pct_of_price?: number;
  regime?: string;
  regime_confidence?: number;
  volatility?: string;
  volatility_rank?: number;
  momentum?: string;
  mtf?: Record<string, string>;
  mtf_alignment?: string;
  session?: string;
  news_proximity_min?: number | null;
  news_event?: string | null;
  data_source?: string;
  data_demo?: boolean;
}

export interface ForensicsFinding {
  label: string;
  kind: string;
  detail: string;
}

export interface ForensicsReport {
  findings: ForensicsFinding[];
  comparables: EvidenceSignal[];
  sample_size: number;
}

export interface EvidenceSignal {
  signal_id: string | null;
  market?: string;
  direction?: string;
  outcome: string | null;
  r: number | null;
  status: string | null;
  time: string | null;
}

export interface Observation {
  kind: string;
  text: string;
  sample_size: number;
  regime?: string;
  wins?: number;
  losses?: number;
  median_r?: number;
  evidence?: EvidenceSignal[];
}

export interface Lesson {
  id: string;
  lesson_no: number;
  strategy_id: string;
  strategy_name: string;
  observation: string;
  evidence: number;
  win_rate: number;
  baseline_win_rate: number;
  delta_pp: number;
  status: string;
}

export interface Hypothesis {
  id: string;
  hypothesis_id: string;
  strategy_id: string;
  strategy_name: string;
  variable: string;
  old_value: any;
  new_value: any;
  reason: string;
  expected_effect: string;
  status: string;
  result?: string;
  ai_conclusion?: string;
  old_metrics?: any;
  new_metrics?: any;
  experiment_id?: string;
  source_lesson?: string;
  user_decision?: string | null;
  applied_version?: string;
}

export interface Experiment {
  id: string;
  hypothesis_id: string;
  strategy_id: string;
  variable: string;
  old_value: any;
  new_value: any;
  market: string;
  timeframe: string;
  dataset: any;
  original_metrics: any;
  experimental_metrics: any;
  result: string;
  conclusion: string;
  recommend_approval: boolean;
  delta_win_rate?: number;
  delta_expectancy?: number;
  experiment_code?: string;
  status?: string;
  overfitting_risk?: boolean;
  small_sample_warning?: boolean;
  robustness?: Record<string, { base: any; exp: any }> | null;
  robustness_note?: string | null;
  signal_frequency_per_day?: number | null;
  split?: {
    train: { base: any; exp: any };
    validation: { base: any; exp: any };
  } | null;
  createdAt: string;
}

export interface StrategyVersion {
  id: string;
  strategy_id: string;
  version: string;
  params: Record<string, any>;
  changes: { variable: string; old_value: any; new_value: any }[];
  active: boolean;
  note: string;
  hypothesis_id?: string;
  createdAt: string;
}

export interface StrategyDoc {
  id: string;
  name: string;
  short_name: string;
  description: string;
  status: "ACTIVE" | "PAUSED" | "DISABLED";
  active_version: string;
  metadata?: any;
  active_params?: Record<string, any>;
  experiment_variables?: Record<string, any>;
}

export interface ActivityItem {
  id: string;
  kind: string;
  message: string;
  market?: string;
  ts_override?: string;
  createdAt: string;
}

export interface Notification {
  id: string;
  type: string;
  title: string;
  body: string;
  read: boolean;
  signal_id?: string;
  createdAt: string;
}

export interface AgentStatus {
  agent_status: string;
  current_objective: string;
  goals: { account_balance: number; daily_objective_pct: number; weekly_objective_pct: number };
  progress: {
    daily_pl_pct: number;
    weekly_pl_pct: number;
    objective_pct: number;
    objective_progress: string;
    risk_per_trade_pct: number;
  };
  current_task: string;
  strategies_active: number;
  signals_today: number;
  wins: number;
  losses: number;
  lessons: number;
  experiments: number;
  pending_approvals: number;
  strategy_versions: Record<string, string>;
  ai: { provider: string; configured: boolean; note: string };
  market_data: { provider: string; demo: boolean };
}

export interface ReplayData {
  signal: {
    id: string; signal_id: string; market: string; timeframe: string;
    strategy_name: string; direction: "BUY" | "SELL";
    entry: number; sl: number; tp1: number | null; tp2: number | null;
    tp3: number | null; candle_time: string; status: string;
    outcome: string | null; r_multiple: number; tp_hits: number;
  };
  markers: { entry: number; sl: number; tp1: number | null; tp2: number | null; tp3: number | null };
  candles: { ts: number; open: number; high: number; low: number; close: number }[];
  coverage: "full" | "partial" | "unavailable";
  note: string | null;
  dna: DnaSnapshot | null;
  forensics: ForensicsReport | null;
  demo: boolean;
}

export interface AdaptiveEvaluation {
  adaptive_version: string;
  weights: Record<string, number>;
  similarity_weights: Record<string, number>;
  similarity_threshold: number;
  min_sample: number;
  score: number;
  verdict: string;
  reliability: "HIGH" | "MEDIUM" | "LOW";
  components: Record<string, number>;
  missing_data: string[];
  historical: {
    status: string;
    similar_signals: number;
    wins?: number;
    losses?: number;
    win_rate?: number;
    total_r?: number | null;
    avg_r?: number | null;
    note?: string;
    comparables?: EvidenceSignal[];
  };
  regime_evidence?: any;
  session_evidence?: any;
  news?: { status: string; note?: string };
  checks: { label: string; detail?: string }[];
  informationality_only?: boolean;
  informational_only?: boolean;
  note?: string;
}

export interface ResearchHypothesis {
  id: string;
  strategy_id: string;
  market: string;
  segment_type: string;
  segment_label: string;
  claim: string;
  kind: string;
  status: string;
  sample_size: number;
  segment_win_rate: number;
  segment_avg_r?: number;
  segment_median_r?: number;
  overall?: { n: number; win_rate: number; avg_r?: number };
  divergence_pp: number;
  evidence?: EvidenceSignal[];
  note?: string;
  designed_hypothesis_id?: string | null;
  createdAt: string;
}

export interface Candle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}
