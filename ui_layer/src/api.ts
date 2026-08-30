export interface Bar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface MarkerPoint {
  time: string;
  price: number;
  label?: string;
  reason?: string;
}

export interface Selection {
  timeStart: string;
  timeEnd: string;
  priceTop: number;
  priceBottom: number;
}

export interface SandboxCheck {
  ok: boolean;
  errors: string[];
  warnings: string[];
  node_count: number;
  max_depth: number;
}

export interface FactorExecution {
  ok: boolean;
  entry_count: number;
  exit_count: number;
  entry_sample: number[];
  exit_sample: number[];
  message: string;
  execution_ms: number;
}

export interface FactorDraft {
  name: string;
  description: string;
  code: string;
  model: string;
  source: string;
  params: Record<string, unknown>;
  tags: string[];
  sandbox: SandboxCheck | null;
  execution: FactorExecution | null;
}

export type FactorPayload = Partial<FactorDraft> & {
  symbol: string;
  timeframe: string;
  chart_stats?: object;
  prompt_snapshot?: object;
  generated_region?: object;
  backtest_stats?: object | null;
  sl_tp_strategy?: object;
  market_adapt?: Record<string, unknown>;
};

export interface Factor extends FactorDraft {
  id: string;
  symbol: string;
  timeframe: string;
  chart_stats: Record<string, unknown>;
  prompt_snapshot: Record<string, unknown>;
  generated_region: Record<string, unknown>;
  sl_tp_strategy: Record<string, unknown>;
  market_adapt?: Record<string, unknown>;
  status: "active" | "disabled";
  version: number;
  created_at: string;
  updated_at: string;
  backtest_stats: Record<string, unknown> | null;
}

export interface LearnTradeResult {
  ok: boolean;
  trade_summary: Record<string, unknown>;
  factor_draft: FactorDraft;
  sl_tp_strategy: Record<string, unknown>;
  backtest: BacktestResult | null;
  out_of_sample_backtest: BacktestResult | null;
  cross_validation: Array<{ symbol: string; timeframe: string; backtest: BacktestResult; disjoint: boolean }>;
  validation_stats: {
    ok: boolean;
    warnings: string[];
    pass_gate: boolean;
    gate_reasons: string[];
    benchmark: { buy_hold_return_pct: number; random_baseline_return_pct: number } | null;
    cost_assumptions: { commission_pct: number; slippage: number; execution_delay_bars: number } | null;
    validation_gross: BacktestResult | null;
    statistics: {
      num_trades: number;
      samples: number;
      win_rate_ci: number[];
      profit_factor_ci: number[];
      insufficient: boolean;
    } | null;
    learning_end: string;
    validation_bars: number;
    out_of_sample_bars: number;
  } | null;
  ai_model: string | null;
  warning: string;
  symbol: string;
  timeframe: string;
}

export interface TradeStatsResult {
  count: number;
  accounts?: string[];
  filters: Record<string, unknown>;
  normalized_to_one_lot: boolean;
  win_count: number;
  win_rate_pct: number;
  profit_factor: number | null;
  total_pnl: number;
  avg_pnl: number;
  avg_peak_pnl: number;
  avg_trough_pnl: number;
  avg_hold_minutes: number;
  by_lots: Array<{ lots: string; count: number; win_rate_pct: number; profit_factor: number | null; total_pnl: number }>;
}

export interface BacktestMetrics {
  total_return_pct: number;
  annual_return_pct: number;
  sharpe: number;
  max_drawdown_pct: number;
  win_rate_pct: number;
  profit_factor: number;
  num_trades: number;
  avg_trade_pct: number;
  max_consecutive_losses: number;
}

export interface TradeDetail {
  entry_time: string;
  entry_price: number;
  side: "long" | "short";
  exit_time: string | null;
  exit_price: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  bars_held: number | null;
  exit_reason: string;
}

export interface BacktestResult {
  ok: boolean;
  message: string;
  metrics: BacktestMetrics | null;
  trades: TradeDetail[];
  equity_curve: Array<{ time: string; equity: number; position: boolean }>;
}

export interface OptimizeTrial {
  params: Record<string, unknown>;
  score: number;
  metrics: BacktestMetrics;
  trades: number;
}

export interface OptimizeResult {
  ok: boolean;
    message: string;
    best_params: Record<string, unknown>;
    best_metrics: BacktestMetrics;
  auto_expanded?: Array<{ timeframe: string; bars: number }>;
  auto_expand_message?: string;
  train_metrics?: BacktestMetrics;
  test_metrics?: BacktestMetrics | null;
  walk_forward_metrics?: BacktestMetrics[];
  best_trades: number;
  score: number;
  fidelity?: number;
  stop_reason: string;
  trials: OptimizeTrial[];
  objective: string;
  targets: {
    win_rate_pct: number;
    total_return_pct: number;
    profit_factor: number;
    max_drawdown_pct: number;
  };
  targets_reached: boolean;
  satisfied_trials: number;
}

export interface MatcherCandidate {
  factor_id: string;
  factor_name: string;
  signal: "long" | "short" | "none";
  confidence: number;
  regime: string;
  reasons: string[];
  support_levels?: number[];
  resistance_levels?: number[];
}

export interface PatternLearned {
  success: boolean;
  symbol: string;
    timeframe: string;
    fingerprint: Record<string, unknown>;
    fingerprints?: Record<string, unknown>;
    features?: Record<string, unknown>;
    ohlcv?: unknown[];
    pattern_type?: string;
    pattern_subtype?: string;
    recognition_confidence?: number;
    region_stats: Record<string, unknown>;
  learned: Record<string, unknown>;
  bar_count: number;
}

export interface PatternMatch {
  case_id: string;
  factor_id: string;
  factor_name: string;
  similarity: number;
  base_similarity?: number;
  sample_count?: number;
    win_rate?: number;
    avg_pnl?: number;
    expected_value?: number;
    pattern_type?: string;
    pattern_subtype?: string;
    recognition_confidence?: number;
    human_confirmed?: boolean;
    learned: Record<string, unknown>;
  region_stats: Record<string, unknown>;
}

export interface Alert {
  id: string;
  level: "info" | "warning" | "error" | "success";
  title: string;
  message: string;
  created_at: string;
  delivered_to: string[];
}

export interface AiConfig {
  id: string;
  name: string;
  provider: string;
  model: string;
  api_key_masked: string;
  base_url: string;
  roles: string[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AiStatus {
  configured: boolean;
  enabled_count: number;
  configs: AiConfig[];
  roles: Record<string, { label: string; configs: string[] }>;
}

export interface SystemState {
  market_live: boolean;
  matcher_running: boolean;
  matcher_symbol: string;
  matcher_timeframe: string;
  paper_jobs: number;
  active_positions: number;
  account_equity: number;
  account_balance: number | null;
  account_profit: number | null;
  account_currency: string;
  account_login: number | null;
  max_positions: number;
    last_scan: { regime: string; candidates: MatcherCandidate[]; pattern_matches?: PatternMatch[] } | null;
  mt5_connected: boolean;
  executor_enabled: boolean;
  executor_config?: Record<string, unknown>;
  last_executions: Array<{
    time: string;
    factor_id: string;
    factor_name: string;
    symbol: string;
    side: string;
    lots: number;
    stop: number;
    take: number;
    confidence: number;
    order_id: string;
  }>;
  executor_failures: Array<{
    time: string;
    factor_name: string;
    symbol: string;
    side: string;
    error: string;
    }>;
    last_spread: number;
    risk_tripped?: boolean;
    risk_reasons?: string[];
  }

export interface Mt5Account {
  balance: number;
  equity: number;
  profit: number;
  margin: number;
  free_margin: number;
  leverage: number;
  currency: string;
  login: number;
  server: string;
  trade_allowed: boolean;
}

export interface Mt5SymbolInfo {
  symbol: string;
  bid: number;
  ask: number;
  spread_points: number;
  digits: number;
  point: number;
  min_lot: number;
  max_lot: number;
  lot_step: number;
  contract_size: number;
  pip_value: number;
}

export interface Mt5Position {
  ticket: number;
  symbol: string;
  type: "BUY" | "SELL";
  volume: number;
  price_open: number;
  price_current: number;
  sl: number;
  tp: number;
  profit: number;
  comment: string;
}

export interface Mt5Order {
  ticket: number;
  symbol: string;
  type: string;
  volume_initial: number;
  price_open: number;
  sl: number;
  tp: number;
  comment: string;
}

export interface OrderLog {
  id: string;
  mt5_ticket: string;
  symbol: string;
  side: string;
  order_type: string;
  volume: number;
  price: number | null;
  stoplimit_price: number | null;
  sl: number | null;
  tp: number | null;
  status: string;
  action: string;
  factor_name: string;
    reason: string;
    message: string;
    request_payload: Record<string, unknown>;
    response_data: Record<string, unknown>;
    created_at: string;
  updated_at: string;
}

export interface Mt5Status {
    broker_mode: string;
    connected: boolean;
    account: Mt5Account | null;
    algo_trading_enabled?: boolean;
    algo_trading_hint?: string;
    message: string;
  }

export interface Mt5Tick {
  symbol: string;
  bid: number;
  ask: number;
  last: number;
  volume: number;
  spread_points: number;
  time: number;
}

export interface IndicatorPoint {
  time: string;
  value: number | null;
}

export interface IndicatorData {
  success: boolean;
  symbol: string;
  timeframe: string;
  count: number;
  latest: Record<string, number | null>;
  series: Record<string, IndicatorPoint[]>;
  registry: string[];
}

const JSON_HEADERS = { "Content-Type": "application/json" };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = `请求失败（${res.status}）`;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // 保持默认错误信息
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  bars(symbol: string, timeframe: string, limit = 500): Promise<{ bars: Bar[] }> {
    return request(`/api/market/bars?symbol=${symbol}&timeframe=${timeframe}&limit=${limit}`);
  },
  marketAnalysis(symbol: string, timeframe: string): Promise<Record<string, unknown>> {
    return request(`/api/market/analysis?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`);
  },
  snapshot(symbol: string, timeframe: string): Promise<{ current_price: number; change_pct_24h: number; atr: number; atr_pct: number }> {
    return request(`/api/market/snapshot?symbol=${symbol}&timeframe=${timeframe}`);
  },
  generate(region: { symbol: string; timeframe: string; time_start: string; time_end: string; price_top?: number; price_bottom?: number; entry_points: MarkerPoint[]; exit_points: MarkerPoint[] }, chartImage?: string): Promise<FactorDraft> {
    return request("/api/ai/generate", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ region, chart_image_data_url: chartImage }),
    });
  },
  backtest(payload: {
    code: string;
    symbol: string;
    timeframe: string;
    bars: number;
    params: Record<string, unknown>;
    start_time?: string;
    end_time?: string;
    initial_equity?: number;
    leverage?: number;
    delay_ms?: number;
    slippage_points?: number;
  }): Promise<BacktestResult> {
    return request("/api/backtest/run", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  optimizeBacktest(payload: {
    code: string;
    symbol: string;
    timeframe: string;
    bars: number;
    params: Record<string, unknown>;
    start_time?: string;
    end_time?: string;
    initial_equity?: number;
    leverage?: number;
    delay_ms?: number;
    slippage_points?: number;
    objective?: string;
    target_win_rate_pct?: number;
    target_total_return_pct?: number;
    target_profit_factor?: number;
    target_max_drawdown_pct?: number;
    max_iterations?: number;
    early_stop_rounds?: number;
    walk_forward?: boolean;
    walk_forward_folds?: number;
    min_trades?: number;
    auto_expand?: boolean;
    complexity_penalty?: number;
    max_deviation_pct?: number;
    fidelity_weight?: number;
    beam_width?: number;
    search_rounds?: number;
    param_ranges?: Record<string, Record<string, number>>;
  }): Promise<OptimizeResult> {
    return request("/api/backtest/optimize", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  saveFactor(payload: FactorPayload): Promise<Factor> {
    return request("/api/factors", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  factors(): Promise<{ factors: Factor[] }> {
    return request("/api/factors");
  },
  disableFactor(id: string): Promise<{ message: string }> {
    return request(`/api/factors/${id}/disable`, { method: "POST" });
  },
  enableFactor(id: string): Promise<{ message: string }> {
    return request(`/api/factors/${id}/enable`, { method: "POST" });
  },
  updateFactor(id: string, payload: FactorPayload): Promise<{ message: string; factor: Factor }> {
    return request(`/api/factors/${id}`, {
      method: "PUT",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  deleteFactor(id: string): Promise<{ message: string }> {
    return request(`/api/factors/${id}`, { method: "DELETE" });
  },
  clearFactors(): Promise<{ message: string; deleted: number }> {
    return request("/api/factors/clear", { method: "POST" });
  },
  aiConfigs(): Promise<{ configs: AiConfig[] }> {
    return request("/api/ai/configs");
  },
  aiCreateConfig(payload: Record<string, unknown>): Promise<{ message: string; config: AiConfig }> {
    return request("/api/ai/configs", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  aiUpdateConfig(id: string, payload: Record<string, unknown>): Promise<{ message: string; config: AiConfig }> {
    return request(`/api/ai/configs/${id}`, {
      method: "PUT",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  aiDeleteConfig(id: string): Promise<{ message: string }> {
    return request(`/api/ai/configs/${id}`, { method: "DELETE" });
  },
  aiTestConfig(id: string): Promise<{ success: boolean; message: string; latency_ms?: number; model?: string }> {
    return request(`/api/ai/configs/${id}/test`, { method: "POST" });
  },
  aiLearnTrades(payload: {
    trade_ids: string[];
    symbol?: string;
    timeframe?: string;
    period_mode?: string;
    start_time?: string;
    end_time?: string;
    recent_months?: number;
    recent_bars?: number;
    min_validation_trades?: number;
    min_win_rate_pct?: number;
    min_profit_factor?: number;
    min_oos_trades?: number;
  }): Promise<LearnTradeResult> {
    return request("/api/ai/learn-trades", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  aiChat(payload: {
    message: string;
    image_data_url?: string | null;
    metadata: { symbol?: string; timeframe?: string; start_time?: string; end_time?: string; direction?: string };
  }): Promise<{
    ok: boolean;
    reply: string;
    has_image: boolean;
    factor_draft?: FactorDraft | null;
    sl_tp_strategy?: Record<string, unknown>;
    backtest?: BacktestResult | null;
    ai_model?: string | null;
  }> {
    return request("/api/ai/chat", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  aiOrderChat(payload: {
    trade_id: string;
    message?: string;
    mirror?: boolean;
    timeframe?: string;
  }): Promise<{
    ok: boolean;
    reply: string;
    warning: string;
    factor_draft?: FactorDraft | null;
    sl_tp_strategy?: Record<string, unknown>;
    backtest?: BacktestResult | null;
    mirrored: boolean;
    trade: Record<string, unknown>;
  }> {
    return request("/api/ai/order-chat", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  factorStats(payload: { factor_id?: string; symbol?: string; timeframe?: string; status?: string }): Promise<{
    overall: Record<string, unknown>;
    factors: Array<Record<string, unknown>>;
    detail: Record<string, unknown> | null;
  }> {
    return request("/api/replay/factor-stats", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  sltpLearn(tradeIds: string[]): Promise<{ cases: Array<Record<string, unknown>>; saved: number }> {
    return request("/api/replay/sltp-learn", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ trade_ids: tradeIds }),
    });
  },
  sltpCases(): Promise<{ cases: Array<Record<string, unknown>> }> {
    return request("/api/replay/sltp-cases");
  },
  sltpStrategies(symbol?: string, side?: string): Promise<{ strategies: Array<Record<string, unknown>> }> {
    const qs = new URLSearchParams();
    if (symbol) qs.set("symbol", symbol);
    if (side) qs.set("side", side);
    const query = qs.toString();
    return request(`/api/sltp/strategies${query ? `?${query}` : ""}`);
  },
  sltpAutoLearn(): Promise<{ created: number; updated: number; strategies: number }> {
    return request("/api/sltp/learn", { method: "POST" });
  },
  sltpTrain(): Promise<{ trained: number; model_version: string; trained_at: string }> {
    return request("/api/sltp/train", { method: "POST" });
  },
  modelPredict(name: string, symbol: string, timeframe: string, count = 200): Promise<{ model: string; trained: boolean; signal: string; confidence: number; probability?: number }> {
    return request(`/api/models/${name}/predict`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ symbol, timeframe, count }),
    });
  },
  sltpModel(): Promise<{ model_version: string; trained_at: string; strategy_count: number; avg_confidence: number }> {
    return request("/api/sltp/model");
  },
  saveLearnedFactor(payload: {
    factor: FactorPayload;
    trade_ids: string[];
    validation_stats: LearnTradeResult["validation_stats"];
  }): Promise<Factor> {
    return request("/api/ai/learn-trades/save", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  aiStatus(): Promise<AiStatus> {
    return request("/api/ai/status");
  },
  aiModels(apiKey: string, baseUrl: string): Promise<{ models: string[] }> {
    return request("/api/ai/models", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ api_key: apiKey, base_url: baseUrl }),
    });
  },
  scanMatcher(symbol: string, timeframe: string): Promise<{ regime: string; regime_detail: string; candidates: MatcherCandidate[]; errors: string[]; scanned: number }> {
    return request(`/api/matcher/scan?symbol=${symbol}&timeframe=${timeframe}`, { method: "POST" });
  },
  startMatcher(payload: Record<string, unknown>): Promise<{ message: string; running: boolean; symbol: string; timeframe: string; config?: Record<string, unknown> }> {
    return request("/api/matcher/start", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  recommendMatcher(symbol: string): Promise<Record<string, unknown>> {
    return request(`/api/matcher/recommend?symbol=${encodeURIComponent(symbol)}`);
  },
  stopMatcher(): Promise<{ message: string }> {
    return request("/api/matcher/stop", { method: "POST" });
  },
  tradingConfig(): Promise<{ config: Record<string, unknown>; matcher_running: boolean; matcher_symbol: string; matcher_timeframe: string }> {
    return request("/api/trading/config");
  },
  saveTradingConfig(payload: Record<string, unknown>): Promise<{ message: string; config: Record<string, unknown> }> {
    return request("/api/trading/config", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  smartStopConfig(): Promise<{ config: Record<string, unknown>; enabled: boolean; last_updates: Array<Record<string, unknown>> }> {
    return request("/api/smart-stop/config");
  },
  saveSmartStopConfig(config: Record<string, unknown>): Promise<{ message: string; config: Record<string, unknown> }> {
    return request("/api/smart-stop/config", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ config }),
    });
  },
  sltpRecommend(symbol: string, timeframe: string): Promise<{ symbol: string; timeframe: string; market: Record<string, unknown>; config: Record<string, unknown>; reason: string }> {
    return request(`/api/sltp/recommend?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`);
  },
  sltpPolicy(): Promise<{ config: Record<string, unknown>; enabled: boolean; scan_interval: number; last_updates: Array<Record<string, unknown>> }> {
    return request("/api/sltp/policy");
  },
  saveSltpPolicy(payload: Record<string, unknown>): Promise<{ message: string; config: Record<string, unknown>; enabled: boolean }> {
    return request("/api/sltp/policy", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ config: payload }),
    });
  },
  sltpState(): Promise<{ enabled: boolean; policy: Record<string, unknown>; manager: boolean; positions: Array<Record<string, unknown>>; last_updates: Array<Record<string, unknown>> }> {
    return request("/api/sltp/state");
  },
  miningRun(params: { symbol: string; timeframe: string; max_candidates?: number; include_structures?: boolean; date_from?: string | null; date_to?: string | null; keep_ungated?: boolean; spread_points?: number }): Promise<{ ok: boolean; message: string; job?: Record<string, unknown> }> {
    return request("/api/mining/run", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(params),
    });
  },
  portfolioBacktestRun(params: { symbol: string; timeframe: string; date_from?: string | null; date_to?: string | null; market_filter?: Record<string, unknown>; max_factors?: number }): Promise<{ ok: boolean; message: string }> {
    return request("/api/backtest/portfolio", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(params),
    });
  },
  portfolioBacktestStatus(): Promise<{ ok: boolean; running: boolean; message: string; result?: Record<string, unknown> }> {
    return request("/api/backtest/portfolio/status");
  },
  miningStatus(): Promise<{ running: boolean; paused?: boolean; cancelled?: boolean; message: string; total?: number; done?: number; ok?: number; gated?: number; ungated?: number; pending_count?: number }> {
    return request("/api/mining/status");
  },
  miningCandidates(status = "pending", symbol?: string, kind?: string, limit = 50): Promise<{ count: number; items: Array<Record<string, unknown>> }> {
    const query = [`status=${encodeURIComponent(status)}`, `limit=${limit}`];
    if (symbol) query.push(`symbol=${encodeURIComponent(symbol)}`);
    if (kind) query.push(`kind=${encodeURIComponent(kind)}`);
    return request(`/api/mining/candidates?${query.join("&")}`);
  },
  miningAccept(candidateId: string): Promise<{ ok: boolean; message: string; factor_id?: string }> {
    return request("/api/mining/accept", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ candidate_id: candidateId }),
    });
  },
  miningIgnore(candidateId: string): Promise<{ ok: boolean; message: string }> {
    return request("/api/mining/ignore", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ candidate_id: candidateId }),
    });
  },
  miningPause(): Promise<{ ok: boolean; message: string }> {
    return request("/api/mining/pause", { method: "POST" });
  },
  miningResume(): Promise<{ ok: boolean; message: string }> {
    return request("/api/mining/resume", { method: "POST" });
  },
  miningStop(): Promise<{ ok: boolean; message: string }> {
    return request("/api/mining/stop", { method: "POST" });
  },
  startPaper(payload: { factor_id: string; symbol: string; timeframe: string; equity: number }): Promise<{ message: string; job_id: string }> {
    return request("/api/matcher/paper/start", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  stopPaper(payload?: { job_id?: string }): Promise<{ message: string }> {
    return request("/api/matcher/paper/stop", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload ?? {}),
    });
  },
  paperState(): Promise<{ jobs: Array<Record<string, unknown>> }> {
    return request("/api/matcher/paper/state");
  },
  closeAll(): Promise<{ message: string }> {
    return request("/api/trading/close-all", { method: "POST" });
  },
  restartSystem(): Promise<{ message: string; ok: boolean }> {
    return request("/api/system/restart", { method: "POST" });
  },
  replayExport(tradeId?: string): Promise<{ markdown: string; score: number; factor_name: string }> {
    return request("/api/replay/export", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ trade_id: tradeId ?? "" }),
    });
  },
  replayTrades(limit = 200): Promise<{ trades: Array<Record<string, unknown>> }> {
    return request(`/api/replay/trades?limit=${limit}`);
  },
  setTradeQuality(tradeId: string, quality: "ideal" | "general" | "poor"): Promise<{ message: string }> {
    return request("/api/replay/trade-quality", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ trade_id: tradeId, quality }),
    });
  },
  optimizeTrade(tradeId: string, timeframe = "M15"): Promise<Record<string, unknown>> {
    return request("/api/ai/optimize-trade", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ trade_id: tradeId, timeframe }),
    });
  },
  tradeStats(payload: {
    symbol?: string;
    account_id?: string;
    side?: string;
    quality?: string;
    fixed_lots?: number;
    normalize_to_one_lot?: boolean;
  }): Promise<TradeStatsResult> {
    return request("/api/replay/trade-stats", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  deleteTrades(tradeIds: string[]): Promise<{ message: string; deleted: number }> {
    return request("/api/replay/delete-trades", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ trade_ids: tradeIds }),
    });
  },
  syncMt5Trades(days = 30): Promise<{ message: string; imported: number; skipped: number }> {
    return request("/api/replay/sync-mt5-trades", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ days }),
    });
  },
  migrateAccount(): Promise<{ message: string; updated: number; account_id: string }> {
    return request("/api/replay/migrate-account", { method: "POST" });
  },
  backfillExtremes(): Promise<{ message: string; updated: number }> {
    return request("/api/replay/backfill-extremes", { method: "POST" });
  },
  alerts(): Promise<{ alerts: Alert[] }> {
    return request("/api/observability/alerts");
  },
  systemState(): Promise<SystemState> {
    return request("/api/system/state");
  },
  mt5Status(): Promise<Mt5Status> {
    return request("/api/mt5/status");
  },
  mt5Setup(): Promise<{ mt5_path: string; login: string; server: string; broker: string; connected: boolean; account: Record<string, unknown> | null }> {
    return request("/api/mt5/setup");
  },
  saveMt5Setup(payload: { mt5_path?: string; login?: string; password?: string; server?: string }): Promise<{ success: boolean; message: string; connected: boolean; account: Record<string, unknown> | null; symbols: string[] }> {
    return request("/api/mt5/setup", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  mt5Account(): Promise<{ success: boolean; data: Mt5Account }> {
    return request("/api/mt5/account");
  },
  mt5Symbols(): Promise<{ success: boolean; symbols: string[] }> {
    return request("/api/mt5/symbols");
  },
  mt5SymbolInfo(symbol: string): Promise<{ success: boolean; data: Mt5SymbolInfo }> {
    return request(`/api/mt5/symbol_info?symbol=${symbol}`);
  },
  mt5Tick(symbol: string): Promise<{ success: boolean; data: Mt5Tick }> {
    return request(`/api/mt5/tick?symbol=${symbol}`);
  },
  mt5Klines(symbol: string, timeframe: string, count = 500): Promise<{ success: boolean; bars: Bar[] }> {
    return request(`/api/mt5/klines?symbol=${symbol}&timeframe=${timeframe}&count=${count}`);
  },
  indicators(symbol: string, timeframe: string, count = 500): Promise<IndicatorData> {
    return request(`/api/indicators?symbol=${symbol}&timeframe=${timeframe}&count=${count}`);
  },
  mt5SendOrder(payload: {
    symbol: string;
    action_type: string;
    volume: number;
    price?: number;
    sl?: number;
    tp?: number;
    expiration_bars?: number;
    comment?: string;
    stoplimit_price?: number;
  }): Promise<{ success: boolean; data: Record<string, unknown> }> {
    return request("/api/mt5/order", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  mt5Positions(): Promise<{ success: boolean; positions: Mt5Position[] }> {
    return request("/api/mt5/positions");
  },
  mt5Orders(): Promise<{ success: boolean; orders: Mt5Order[] }> {
    return request("/api/mt5/orders");
  },
  mt5Close(ticket: number): Promise<{ success: boolean }> {
    return request("/api/mt5/close", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ ticket }),
    });
  },
  mt5Cancel(ticket: number): Promise<{ success: boolean }> {
    return request("/api/mt5/cancel", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ ticket }),
    });
  },
  mt5CloseAll(): Promise<{ success: boolean; closed: Array<Record<string, unknown>>; total: number }> {
    return request("/api/mt5/close_all", { method: "POST" });
  },
  mt5CalcLot(symbol: string, riskPercent: number, slPips: number): Promise<{ success: boolean; calculated_lot: number; risk_amount: number; pip_value_used: number }> {
    return request(`/api/mt5/risk/calc_lot?symbol=${symbol}&risk_percent=${riskPercent}&sl_pips=${slPips}`);
  },
  orderLog(): Promise<{ logs: OrderLog[] }> {
    return request("/api/orders/log");
  },
  executorState(): Promise<{ enabled: boolean; min_confidence: number; max_positions: number; symbol: string; timeframe: string; last_executions: SystemState["last_executions"] }> {
    return request("/api/executor/state");
  },
  executorToggle(enabled: boolean): Promise<{ message: string; enabled: boolean }> {
    return request("/api/executor/toggle", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ enabled }),
    });
  },
  executorScanRun(): Promise<{ message: string; scan: { regime: string; candidates: MatcherCandidate[] }; executed: SystemState["last_executions"] }> {
    return request("/api/executor/scan-run", { method: "POST" });
  },
  learnPattern(payload: {
    symbol: string;
    timeframe: string;
    time_start: string;
    time_end: string;
    price_top: number;
    price_bottom: number;
    entry_points: MarkerPoint[];
    exit_points: MarkerPoint[];
    support_levels?: Array<{ price: number; label?: string; touched?: number }>;
    resistance_levels?: Array<{ price: number; label?: string; touched?: number }>;
  }): Promise<PatternLearned> {
    return request("/api/patterns/learn", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  savePattern(payload: Record<string, unknown>): Promise<{ success: boolean; case_id: string }> {
    return request("/api/patterns", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  matchPatterns(payload: { symbol: string; timeframe: string; window_bars?: number; min_similarity?: number; top_k?: number }): Promise<{ success: boolean; matches: PatternMatch[] }> {
    return request("/api/patterns/match", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  checkSandbox(code: string): Promise<{ ok: boolean; errors: string[]; warnings: string[]; node_count: number; max_depth: number }> {
    return request("/api/ai/check", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ code }),
    });
  },
  sandboxDiagnose(payload: { code: string; symbol?: string; timeframe?: string; bars_count?: number }): Promise<{
    success: boolean;
    summary: string;
    static: { ok: boolean; errors: string[]; warnings: string[]; node_count: number; max_depth: number };
    execution: { ok: boolean; message: string; entry_count: number; exit_count: number; entry_sample: number[]; exit_sample: number[]; execution_ms: number };
    data_source: string;
    bars_used: number;
    suggestions: string[];
  }> {
    return request("/api/ai/sandbox-diagnose", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    });
  },
  marketStatus(symbol = "XAUUSD", timeframe = "M15"): Promise<{
    symbol: string;
    timeframe: string;
    market: {
      is_open: boolean;
      session: "open" | "closed";
      now_ts: number;
      next_open_ts: number;
      next_close_ts: number | null;
      weekday: number;
    };
  }> {
    return request(`/api/market/status?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`);
  },
  bridgeStatus(): Promise<{ running: boolean; status: Record<string, unknown>; settings: BridgeSettings; bridge_dir: string; fresh_s: number }> {
    return request("/api/bridge/status");
  },
  bridgeStart(): Promise<{ ok: boolean; already_running?: boolean; pid?: number; error?: string }> {
    return request("/api/bridge/start", { method: "POST" });
  },
  bridgeStop(): Promise<{ ok: boolean; stopped_pid?: number | null; error?: string }> {
    return request("/api/bridge/stop", { method: "POST" });
  },
  bridgeClean(): Promise<{ ok: boolean; cleared: string[]; failed: Array<{ file: string; error: string }>; settings_preserved: boolean; status_file_preserved: boolean }> {
    return request("/api/bridge/clean", { method: "POST" });
  },
  bridgeGetConfig(): Promise<{ settings: BridgeSettings; defaults: BridgeSettings }> {
    return request("/api/bridge/config");
  },
  bridgeSetConfig(settings: Partial<BridgeSettings>): Promise<{ ok: boolean; settings: BridgeSettings; error?: string }> {
    return request("/api/bridge/config", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(settings),
    });
  },
};

export interface BridgeSettings {
  factors: boolean;
  sltp: boolean;
  smart_stop: boolean;
  events: boolean;
  risk: boolean;
  patterns: boolean;
  ai: boolean;
}
