import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Ban,
  BarChart3,
  Bell,
  BoxSelect,
  Bot,
  Cable,
  CheckCircle2,
  Cpu,
  Eye,
  FileText,
  FileClock,
  Layers,
  Play,
  Pencil,
  Power,
  Radar,
  RefreshCw,
  Save,
  Shield,
  ShieldCheck,
  Sparkles,
  Square,
  StopCircle,
  TestTube2,
  FlaskConical,
  Trash2,
  Undo2,
  Wallet,
  Wand2,
  X,
} from "lucide-react";
import { KLineChart, type ChartMode, type ModelSignal } from "./components/KLineChart";
import { FactorEditModal } from "./components/FactorEditModal";
import { Mt5Panel } from "./components/Mt5Panel";
import { OrderLogPanel } from "./components/OrderLogPanel";
import { FactorMiningPanel } from "./components/FactorMiningPanel";
import { BridgePanel } from "./components/BridgePanel";
import { SltpPolicyPanel } from "./components/SltpPolicyPanel";
import { SymbolSearchSelect } from "./components/SymbolSearchSelect";
import { api, type AiConfig, type AiStatus, type Alert, type BacktestResult, type Bar, type Factor, type FactorDraft, type FactorPayload, type IndicatorData, type LearnTradeResult, type MarkerPoint, type MatcherCandidate, type Mt5Tick, type OptimizeResult, type Selection, type SystemState, type TradeStatsResult } from "./api";

interface RegionStats {
  bar_count: number;
  return_pct: number;
  trend: string;
  volatility: string;
}

interface ScanResult {
  regime: string;
  regime_detail: string;
  candidates: MatcherCandidate[];
  errors: string[];
  scanned: number;
}

type MainTab = "factors" | "trading" | "replay" | "ai" | "sltp" | "mining" | "bridge";
type SubTab = "mark" | "backtest" | "manage" | "signals" | "mt5" | "replay" | "orders" | "stats" | "optimize" | "sltp";

const TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];
const MATCHER_LABELS: Record<string, string> = {
  symbol: "交易品种",
  timeframe: "K线周期",
  min_confidence: "最小置信度",
  direction: "交易方向",
  lot_mode: "仓位模式",
  fixed_lots: "固定手数",
  risk_percent: "单笔风险 %",
  max_positions: "最大持仓",
  initial_sltp_source: "止损止盈来源",
  stop_method: "止损方式",
  take_method: "止盈方式",
  stop_atr_mult: "ATR 止损倍数",
  take_atr_mult: "ATR 止盈倍数",
  stop_points: "止损点数",
  take_points: "止盈点数",
  level_buffer_pct: "关键位缓冲 %",
  take_level_buffer_pct: "止盈关键位缓冲 %",
  trailing_unit: "移动单位",
  trailing_activation_pct: "移动激活 %",
  trailing_retrace_pct: "移动回撤 %",
  trailing_take_retrace_pct: "移动止盈回撤 %",
  trailing_take_buffer_pct: "止盈锁仓缓冲 %",
  trailing_activation_atr: "移动激活 ATR",
  trailing_stop_atr: "移动止损 ATR",
  trailing_take_atr: "移动止盈 ATR",
  trailing_take_buffer_atr: "止盈锁仓 ATR",
  sl_tp_strategies: "止损止盈策略",
  hw_activation_profit: "高水位激活盈利",
  hw_max_retrace_pct: "高水位最大回撤 %",
  pc_tier1_profit: "第一档落袋盈利",
  pc_tier1_close_pct: "第一档平仓比例 %",
  pc_tier2_profit: "第二档落袋盈利",
  pc_tier2_close_pct: "第二档平仓比例 %",
  pc_breakeven_buffer: "保本缓冲",
  delay_ms: "下单延迟(毫秒)",
  auto_close_enabled: "自动平仓",
  alert_enabled: "推送告警",
  max_daily_loss_pct: "最大日亏损 %",
  max_drawdown_pct: "最大回撤 %",
};

const MATCHER_HIDDEN_KEYS = new Set([
  "initial_sltp_source",
  "stop_method",
  "take_method",
  "stop_atr_mult",
  "take_atr_mult",
  "stop_points",
  "take_points",
  "level_buffer_pct",
  "take_level_buffer_pct",
  "trailing_enabled",
  "trailing_unit",
  "trailing_activation_pct",
  "trailing_retrace_pct",
  "trailing_take_retrace_pct",
  "trailing_take_buffer_pct",
  "trailing_activation_atr",
  "trailing_stop_atr",
  "trailing_take_atr",
  "trailing_take_buffer_atr",
  "sl_tp_strategies",
  "hw_activation_profit",
  "hw_max_retrace_pct",
  "pc_tier1_profit",
  "pc_tier1_close_pct",
  "pc_tier2_profit",
  "pc_tier2_close_pct",
  "pc_breakeven_buffer",
]);
const AI_ROLE_OPTIONS: Array<[string, string]> = [
  ["factor_learning", "K线框选学习"],
  ["market_monitor", "实时形态监控"],
  ["code_review", "因子代码审查"],
  ["backtest_analysis", "回测分析"],
  ["trade_replay", "交易复盘"],
  ["market_regime", "市场环境研判"],
  ["alert_writing", "告警文案"],
  ["health_diagnosis", "系统健康诊断"],
  ["stop_optimizer", "智能止损辅助"],
  ["chat_assistant", "AI 交易助手"],
];

function toTime(iso: string): number {
  return new Date(iso).getTime();
}

function computeRegionStats(bars: Bar[], selection: Selection): RegionStats {
  const start = toTime(selection.timeStart);
  const end = toTime(selection.timeEnd);
  const sliced = bars.filter((b) => toTime(b.time) >= start && toTime(b.time) <= end);
  if (sliced.length < 2) return { bar_count: sliced.length, return_pct: 0, trend: "震荡", volatility: "中等波动" };
  const first = sliced[0].close;
  const last = sliced[sliced.length - 1].close;
  const ret = (last / first - 1) * 100;
  const diffs = sliced.slice(1).map((b, i) => Math.abs(b.close - sliced[i].close) / sliced[i].close);
  const std = Math.sqrt(diffs.reduce((s, v) => s + v * v, 0) / diffs.length);
  return {
    bar_count: sliced.length,
    return_pct: ret,
    trend: ret > 0.5 ? "上升趋势" : ret < -0.5 ? "下降趋势" : "震荡",
    volatility: std >= 0.0012 ? "高波动" : std >= 0.0006 ? "中等波动" : "低波动",
  };
}

export default function App() {
  const [symbol, setSymbol] = useState("EURUSD");
  const [symbols, setSymbols] = useState<string[]>(["EURUSD"]);
  // 顶部行情显示：MT5 tick 实时推送（符号切换时重置涨跌基线）
  const [topTick, setTopTick] = useState<Mt5Tick | null>(null);
  const [tickDir, setTickDir] = useState<"up" | "down" | "flat">("flat");
  const prevTickRef = useRef<number | null>(null);
  const [timeframe, setTimeframe] = useState("M15");
  const [bars, setBars] = useState<Bar[]>([]);
  const [modelSignal, setModelSignal] = useState<ModelSignal | null>(null);
  const [snapshot, setSnapshot] = useState<{ current_price: number; change_pct_24h: number; atr: number; atr_pct: number } | null>(null);
  const [indicators, setIndicators] = useState<IndicatorData | null>(null);
  const [showIndicators, setShowIndicators] = useState(true);
  const [mode, setMode] = useState<ChartMode>("pan");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [entryPoints, setEntryPoints] = useState<MarkerPoint[]>([]);
  const [exitPoints, setExitPoints] = useState<MarkerPoint[]>([]);
  const [supportLevels, setSupportLevels] = useState<Array<{ price: number; label?: string; touched?: number }>>([]);
  const [resistanceLevels, setResistanceLevels] = useState<Array<{ price: number; label?: string; touched?: number }>>([]);
  const [markerHistory, setMarkerHistory] = useState<Array<{ type: "entry" | "exit"; point: MarkerPoint }>>([]);
  const [levelHistory, setLevelHistory] = useState<Array<{ type: "support" | "resistance"; price: number }>>([]);
  const [pendingExit, setPendingExit] = useState<MarkerPoint | null>(null);
  const [draft, setDraft] = useState<FactorDraft | null>(null);
  const [backtest, setBacktest] = useState<BacktestResult | null>(null);
  const [factors, setFactors] = useState<Factor[]>([]);
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [marketAnalysis, setMarketAnalysis] = useState<Record<string, unknown> | null>(null);
  const [portfolioJob, setPortfolioJob] = useState<Record<string, unknown> | null>(null);
  const [paperJobs, setPaperJobs] = useState<Array<Record<string, unknown>>>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [systemState, setSystemState] = useState<SystemState | null>(null);
  const [aiStatus, setAiStatus] = useState<AiStatus | null>(null);
  const [aiDialog, setAiDialog] = useState(false);
  const [aiEditingId, setAiEditingId] = useState<string | null>(null);
  const [aiTestingId, setAiTestingId] = useState<string | null>(null);
  const [aiModels, setAiModels] = useState<string[]>([]);
  const [aiModelsLoading, setAiModelsLoading] = useState(false);
  const [aiModelError, setAiModelError] = useState("");
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState<Array<{ role: string; content: string; factor?: FactorDraft | null; backtest?: BacktestResult | null; sl_tp?: Record<string, unknown> }>>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatImage, setChatImage] = useState<string | null>(null);
  const [chatBusy, setChatBusy] = useState(false);
  const [chatMeta, setChatMeta] = useState({ symbol: "", timeframe: "M15", start_time: "", end_time: "", direction: "" });
  const [aiForm, setAiForm] = useState({
    name: "",
    provider: "openai",
    model: "qwen3.8-max",
    api_key: "",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    roles: [] as string[],
    enabled: true,
  });
  const [replay, setReplay] = useState<{ markdown: string; score: number; factor_name: string } | null>(null);
  const [tradeRecords, setTradeRecords] = useState<Array<Record<string, unknown>>>([]);
  const [backfilling, setBackfilling] = useState(false);
  const [syncingTrades, setSyncingTrades] = useState(false);
  const [migratingAccount, setMigratingAccount] = useState(false);
  const [selectedTradeIds, setSelectedTradeIds] = useState<string[]>([]);
  const [tradeOpts, setTradeOpts] = useState<Record<string, Record<string, unknown>>>({});
  const [learning, setLearning] = useState(false);
  const [learnDialog, setLearnDialog] = useState<LearnTradeResult | null>(null);
  const [factorStats, setFactorStats] = useState<{ overall: Record<string, unknown>; factors: Array<Record<string, unknown>>; detail: Record<string, unknown> | null } | null>(null);
  const [factorStatFilter, setFactorStatFilter] = useState({ factor_id: "", symbol: "", timeframe: "", status: "" });
  const [orderChatOpen, setOrderChatOpen] = useState(false);
  const [orderChatTrade, setOrderChatTrade] = useState<Record<string, unknown> | null>(null);
  const [orderChatResult, setOrderChatResult] = useState<{ reply: string; warning: string; factor?: FactorDraft | null; backtest?: BacktestResult | null; sl_tp?: Record<string, unknown>; mirrored: boolean } | null>(null);
  const [orderChatBusy, setOrderChatBusy] = useState(false);
  const [orderChatBacktesting, setOrderChatBacktesting] = useState(false);
  const [orderChatChoiceOpen, setOrderChatChoiceOpen] = useState(false);
  const [tradeFilter, setTradeFilter] = useState({
    symbol: "",
    account_id: "",
    side: "",
    quality: "",
    fixed_lots: "",
    normalizeToOneLot: false,
  });
  const [learnSettings, setLearnSettings] = useState({
    symbol: "",
    timeframe: "M15",
    period_mode: "all",
    start_time: "",
    end_time: "",
    recent_months: "6",
    recent_bars: "500",
    min_validation_trades: "10",
    min_win_rate_pct: "40",
    min_profit_factor: "1",
    min_oos_trades: "0",
  });
  const [sltpAtr, setSltpAtr] = useState("5");
  const [sltpCases, setSltpCases] = useState<Array<Record<string, unknown>>>([]);
  const [sltpStrategies, setSltpStrategies] = useState<Array<Record<string, unknown>>>([]);
  const [sltpModel, setSltpModel] = useState<Record<string, unknown> | null>(null);
  const [sltpLearnBusy, setSltpLearnBusy] = useState(false);
  const [sltpTrainBusy, setSltpTrainBusy] = useState(false);
  const [sltpBusy, setSltpBusy] = useState(false);
  const [tradeStats, setTradeStats] = useState<TradeStatsResult | null>(null);
  const [tab, setTab] = useState<MainTab>("factors");
  const [subTab, setSubTab] = useState<SubTab>("mark");
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedFactor, setSelectedFactor] = useState<Factor | null>(null);
  const [editingFactor, setEditingFactor] = useState<Factor | null>(null);
  const [sandboxResults, setSandboxResults] = useState<Record<string, { summary: string; staticOk: boolean; runtimeOk: boolean; message: string; suggestions: string[] }>>({});
  const [matcherDialog, setMatcherDialog] = useState(false);
  const [matcherConfirm, setMatcherConfirm] = useState(false);
  const [matcherViewOpen, setMatcherViewOpen] = useState(false);
  const [matcherRecommendBusy, setMatcherRecommendBusy] = useState(false);
  const [matcherRecommendReasons, setMatcherRecommendReasons] = useState<string[]>([]);
  const [trailingLoaded, setTrailingLoaded] = useState(false);
  const [smartStopForm, setSmartStopForm] = useState<Record<string, unknown>>({
    enabled: false,
    timeframe: "M15",
    manage_manual: true,
    use_history_profile: true,
    use_pattern_match: true,
    pattern_match_min_similarity: "0.85",
    pattern_refresh_seconds: "60",
    partial_close_enabled: false,
    ladder_take_enabled: true,
    soft_stop_enabled: true,
    soft_stop_atr: "0.5",
    soft_stop_bars: "3",
    breakeven_enabled: true,
    breakeven_atr: "0.5",
    trailing_enabled: true,
    trailing_activation_atr: "0.8",
    trailing_stop_atr: "1.0",
    dynamic_level_enabled: true,
    time_stop_enabled: true,
    max_hold_bars: "120",
    lock_profit_enabled: true,
    lock_activation_atr: "1.0",
    lock_retrace_atr: "0.5",
    hard_stop_atr: "1.5",
    max_single_loss_usd: "1500",
    cooldown_seconds: "60",
    ai_enabled: false,
    ai_take_profit_enabled: true,
    ai_min_interval_seconds: "60",
    ai_timeout_seconds: "8",
    ai_min_confidence: "0.6",
    ai_triggers: ["soft_stop", "time_stop", "near_hard", "profit_lock"],
    ai_periodic_enabled: false,
    ai_periodic_interval_seconds: "60",
  });
  const [trailingForm, setTrailingForm] = useState({
    trailing_enabled: false,
    trailing_unit: "atr",
    initial_sltp_source: "pattern",
    sl_tp_strategies: ["high_watermark"] as string[],
    trailing_activation_pct: "0.3",
    trailing_retrace_pct: "0.3",
    trailing_take_retrace_pct: "0.3",
    trailing_take_buffer_pct: "0.2",
    trailing_activation_atr: "0.5",
    trailing_stop_atr: "1.0",
    trailing_take_atr: "0.5",
    trailing_take_buffer_atr: "0.5",
    hw_activation_profit: "200",
    hw_max_retrace_pct: "20",
    pc_tier1_profit: "400",
    pc_tier1_close_pct: "50",
    pc_tier2_profit: "1000",
    pc_tier2_close_pct: "30",
    pc_breakeven_buffer: "0.5",
  });
  const [matcherForm, setMatcherForm] = useState({
    symbol,
    timeframe,
    min_confidence: "0.55",
    direction: "both",
    lot_mode: "risk",
    fixed_lots: "0.01",
    risk_percent: "1.0",
    max_positions: "5",
    stop_method: "atr",
    take_method: "atr",
    stop_atr_mult: "2.0",
    take_atr_mult: "3.0",
    stop_points: "0",
    take_points: "0",
    level_buffer_pct: "0.1",
    take_level_buffer_pct: "0.1",
    trailing_enabled: false,
    trailing_unit: "atr",
    initial_sltp_source: "pattern",
    sl_tp_strategies: ["high_watermark"] as string[],
    trailing_activation_pct: "0.3",
    trailing_retrace_pct: "0.3",
    trailing_take_retrace_pct: "0.3",
    trailing_take_buffer_pct: "0.2",
    trailing_activation_atr: "0.5",
    trailing_stop_atr: "1.0",
    trailing_take_atr: "0.5",
    trailing_take_buffer_atr: "0.5",
    hw_activation_profit: "200",
    hw_max_retrace_pct: "20",
    pc_tier1_profit: "400",
    pc_tier1_close_pct: "50",
    pc_tier2_profit: "1000",
    pc_tier2_close_pct: "30",
    pc_breakeven_buffer: "0.5",
    delay_ms: "0",
    auto_close_enabled: true,
    alert_enabled: true,
    max_daily_loss_pct: "3",
    max_drawdown_pct: "20",
    pattern_min_similarity: "0.85",
    pattern_min_samples: "0",
    market_filter_enabled: false,
    market_filter_trends: [] as string[],
    market_filter_volatilities: [] as string[],
    market_filter_volume_states: [] as string[],
    market_filter_macro_directions: [] as string[],
    market_filter_d1_directions: [] as string[],
    market_filter_min_score: "0",
  });
  const [matcherMarketAnalysis, setMatcherMarketAnalysis] = useState<Record<string, unknown> | null>(null);
  const [backtestSettings, setBacktestSettings] = useState({
    symbol,
    timeframe,
    bars: "500",
    startTime: "",
    endTime: "",
    initialEquity: "10000",
    leverage: "30",
    delayMs: "0",
    slippage: "0",
  });
  const [optSettings, setOptSettings] = useState({
    objective: "composite",
    maxIterations: "30",
    earlyStop: "5",
    walkForward: "1",
    walkForwardFolds: "3",
    minTrades: "0",
    complexityPenalty: "0.01",
    maxDeviation: "30",
    fidelityWeight: "40",
    beamWidth: "10",
    searchRounds: "5",
    paramRanges: "",
    targetWinRate: "",
    targetReturn: "",
    targetProfitFactor: "",
    targetMaxDrawdown: "",
  });
  const [optimizing, setOptimizing] = useState(false);
  const [optimizeResult, setOptimizeResult] = useState<OptimizeResult | null>(null);

  const regionStats = useMemo(() => (selection ? computeRegionStats(bars, selection) : null), [bars, selection]);

  const notify = useCallback((type: "success" | "error", message: string) => {
    setToast({ type, message });
    window.setTimeout(() => setToast(null), 4200);
  }, []);

  const loadBars = useCallback(async () => {
    try {
      const data = await api.mt5Klines(symbol, timeframe, 500);
      setBars(data.bars);
      api.indicators(symbol, timeframe, 500).then(setIndicators).catch(() => {
        // 指标加载失败不影响主图
      });
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [symbol, timeframe, notify]);

  const loadSymbols = useCallback(async () => {
    try {
      const res = await api.mt5Symbols();
      setSymbols(res.symbols.length ? res.symbols : ["EURUSD"]);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [notify]);

  const loadMarketAnalysis = useCallback(async () => {
    try {
      setMarketAnalysis(await api.marketAnalysis(symbol, timeframe));
    } catch {
      // 行情分析失败不打断主流程
    }
  }, [symbol, timeframe]);

  const runPortfolioBacktest = useCallback(async () => {
    setPortfolioJob({ running: true, message: "启动组合历史回测…" });
    try {
      await api.portfolioBacktestRun({
        symbol,
        timeframe,
        date_from: new Date(Date.now() - 365 * 86400000).toISOString().slice(0, 10),
        date_to: null,
        market_filter: {
          trends: matcherForm.market_filter_trends,
          macro_directions: matcherForm.market_filter_macro_directions,
          mtf_directions: matcherForm.market_filter_d1_directions.length
            ? { d1: matcherForm.market_filter_d1_directions }
            : undefined,
        },
        max_factors: 200,
      });
      let st = await api.portfolioBacktestStatus();
      for (let i = 0; i < 40 && st.running; i++) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        st = await api.portfolioBacktestStatus();
      }
      setPortfolioJob(st as unknown as Record<string, unknown>);
    } catch {
      setPortfolioJob({ running: false, message: "组合回测请求失败" });
    }
  }, [symbol, timeframe, matcherForm]);

  const loadSnapshot = useCallback(async () => {
    try {
      setSnapshot(await api.snapshot(symbol, timeframe));
    } catch {
      // 快照轮询失败不打断主流程
    }
  }, [symbol, timeframe]);

  const loadFactors = useCallback(async () => {
    try {
      const data = await api.factors();
      setFactors(data.factors);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [notify]);

  const loadSystem = useCallback(async () => {
    try {
      setSystemState(await api.systemState());
    } catch {
      // 忽略
    }
  }, []);

  const loadAiStatus = useCallback(async () => {
    try {
      setAiStatus(await api.aiStatus());
    } catch {
      // 忽略
    }
  }, []);

  const openAiDialog = useCallback((cfg?: AiConfig) => {
    if (cfg) {
      setAiEditingId(cfg.id);
      setAiForm({
        name: cfg.name,
        provider: cfg.provider,
        model: cfg.model,
        api_key: "",
        base_url: cfg.base_url,
        roles: cfg.roles,
        enabled: cfg.enabled,
      });
    } else {
      setAiEditingId(null);
      setAiForm({
        name: "",
        provider: "openai",
        model: "qwen3.8-max",
        api_key: "",
        base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
        roles: [],
        enabled: true,
      });
    }
    setAiDialog(true);
  }, []);

  const handleAiSave = useCallback(async () => {
    if (!aiForm.name.trim()) {
      notify("error", "请填写 AI 名称备注");
      return;
    }
    if (!aiEditingId && !aiForm.api_key.trim()) {
      notify("error", "请填写 API Key");
      return;
    }
    setBusy(true);
    try {
      const payload = { ...aiForm };
      if (aiEditingId) {
        await api.aiUpdateConfig(aiEditingId, payload);
      } else {
        await api.aiCreateConfig(payload);
      }
      setAiDialog(false);
      await loadAiStatus();
      notify("success", aiEditingId ? "AI 配置已更新" : "AI 配置已创建");
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [aiForm, aiEditingId, loadAiStatus, notify]);

  const handleAiTest = useCallback(async (id: string) => {
    setAiTestingId(id);
    try {
      const result = await api.aiTestConfig(id);
      notify(
        result.success ? "success" : "error",
        result.success
          ? `AI 连接成功：${result.model}，延迟 ${result.latency_ms}ms`
          : `AI 连接失败：${result.message}`
      );
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setAiTestingId(null);
    }
  }, [notify]);

  const handleAiDelete = useCallback(async (cfg: AiConfig) => {
    if (!window.confirm(`确认删除 AI 配置「${cfg.name}」？`)) return;
    try {
      await api.aiDeleteConfig(cfg.id);
      await loadAiStatus();
      notify("success", "AI 配置已删除");
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [loadAiStatus, notify]);

  const loadAiModels = useCallback(async () => {
    if (!aiForm.api_key.trim()) {
      notify("error", "请先填写 API Key 再获取可用模型");
      return;
    }
    setAiModelsLoading(true);
    setAiModelError("");
    try {
      const res = await api.aiModels(aiForm.api_key.trim(), aiForm.base_url);
      setAiModels(res.models || []);
      notify("success", `获取到 ${(res.models || []).length} 个可用模型`);
    } catch (err) {
      const msg = String(err instanceof Error ? err.message : err);
      setAiModelError(msg);
      notify("error", msg);
    } finally {
      setAiModelsLoading(false);
    }
  }, [aiForm.api_key, aiForm.base_url, notify]);

  const loadAlerts = useCallback(async () => {
    try {
      const data = await api.alerts();
      setAlerts(data.alerts);
    } catch {
      // 忽略
    }
  }, []);

  const loadPaper = useCallback(async () => {
    try {
      const data = await api.paperState();
      setPaperJobs(data.jobs);
    } catch {
      // 忽略
    }
  }, []);


  const resetSelection = useCallback(() => {
    setSelection(null);
    setEntryPoints([]);
    setExitPoints([]);
    setSupportLevels([]);
    setResistanceLevels([]);
    setMarkerHistory([]);
    setLevelHistory([]);
    setPendingExit(null);
    setDraft(null);
    setBacktest(null);
    setMode("pan");
    notify("success", "已重置框选与标注");
  }, [notify]);

  const handleAddPoint = useCallback((point: MarkerPoint) => {
    const type = mode === "entry" ? "entry" : "exit";
    if (mode === "exit") {
      setPendingExit(point);
      return;
    }
    if (type === "entry") {
      setEntryPoints((prev) => [...prev, point]);
    } else {
      setExitPoints((prev) => [...prev, point]);
    }
    setMarkerHistory((prev) => [...prev, { type, point }]);
  }, [mode]);

  const confirmExitReason = useCallback((reason: string) => {
    if (!pendingExit) return;
    const point = { ...pendingExit, label: `出场点（${reason}）`, reason };
    setExitPoints((prev) => [...prev, point]);
    setMarkerHistory((prev) => [...prev, { type: "exit", point }]);
    setPendingExit(null);
  }, [pendingExit]);

  const handleAddLevel = useCallback((type: "support" | "resistance", price: number) => {
    if (type === "support") {
      setSupportLevels((prev) => [...prev, { price, label: "支撑位" }]);
    } else {
      setResistanceLevels((prev) => [...prev, { price, label: "压力位" }]);
    }
    setLevelHistory((prev) => [...prev, { type, price }]);
  }, []);

  const handleSuggestPoints = useCallback(() => {
    if (!selection || supportLevels.length === 0 || resistanceLevels.length === 0) {
      notify("error", "请先框选形态并标记支撑位/压力位");
      return;
    }
    const range = selection.priceTop - selection.priceBottom;
    if (range <= 0) return;
    const nearestSupport = Math.max(...supportLevels.map((l) => l.price));
    const nearestResistance = Math.min(...resistanceLevels.map((l) => l.price));
    const entryPrice = nearestSupport + range * 0.1;
    const exitPrice = nearestResistance - range * 0.1;
    const entryPoint = { time: selection.timeStart, price: entryPrice, label: "回踩支撑入场", reason: "回踩支撑" };
    const exitPoint = { time: selection.timeEnd, price: exitPrice, label: "出场点（到压力位）", reason: "到压力位" };
    setEntryPoints([entryPoint]);
    setExitPoints([exitPoint]);
    setMarkerHistory([{ type: "entry", point: entryPoint }, { type: "exit", point: exitPoint }]);
    setPendingExit(null);
    notify("success", "已自动建议入场/出场点位");
  }, [selection, supportLevels, resistanceLevels, notify]);

  const handleUndoMarker = useCallback(() => {
    const lastLevel = levelHistory[levelHistory.length - 1];
    if (lastLevel) {
      if (lastLevel.type === "support") {
        setSupportLevels((prev) => prev.filter((l) => l.price !== lastLevel.price));
      } else {
        setResistanceLevels((prev) => prev.filter((l) => l.price !== lastLevel.price));
      }
      setLevelHistory((prev) => prev.slice(0, -1));
      return;
    }
    if (supportLevels.length > 0) {
      setSupportLevels((prev) => prev.slice(0, -1));
      return;
    }
    if (resistanceLevels.length > 0) {
      setResistanceLevels((prev) => prev.slice(0, -1));
      return;
    }
    const last = markerHistory[markerHistory.length - 1];
    if (!last) return;
    if (last.type === "entry") {
      setEntryPoints((prev) => prev.filter((p) => !(p.time === last.point.time && p.price === last.point.price)));
    } else {
      setExitPoints((prev) => prev.filter((p) => !(p.time === last.point.time && p.price === last.point.price)));
    }
    setMarkerHistory((prev) => prev.slice(0, -1));
  }, [levelHistory, supportLevels, resistanceLevels, markerHistory]);

  const handleClearMarkers = useCallback(() => {
    setEntryPoints([]);
    setExitPoints([]);
    setSupportLevels([]);
    setResistanceLevels([]);
    setMarkerHistory([]);
    setLevelHistory([]);
    setPendingExit(null);
    notify("success", "已清除全部入场/出场标注");
  }, [notify]);

  const handleClearSelection = useCallback(() => {
    setSelection(null);
    notify("success", "已取消框选");
  }, [notify]);

  const chartImage = useCallback((): string | undefined => {
    const canvas = document.querySelector(".chart-wrap canvas") as HTMLCanvasElement | null;
    return canvas ? canvas.toDataURL("image/png") : undefined;
  }, []);

  const handleGenerate = useCallback(async () => {
    if (!selection) {
      notify("error", "请先在 K 线图上拖拽框选形态");
      return;
    }
    setBusy(true);
    try {
      const region = {
        symbol,
        timeframe,
        time_start: selection.timeStart,
        time_end: selection.timeEnd,
        price_top: selection.priceTop,
        price_bottom: selection.priceBottom,
        entry_points: entryPoints,
        exit_points: exitPoints,
        support_levels: supportLevels,
        resistance_levels: resistanceLevels,
      };
      const result = await api.generate(region, chartImage());
      setDraft(result);
      setBacktest(null);
      setTab("factors");
      setSubTab("mark");
      notify("success", `AI 因子生成完成：${result.name}`);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [selection, symbol, timeframe, entryPoints, exitPoints, supportLevels, resistanceLevels, chartImage, notify]);

  const handleBacktest = useCallback(async () => {
    if (!draft) {
      notify("error", "请先生成 AI 因子");
      return;
    }
    setBusy(true);
    try {
      const result = await api.backtest({
        code: draft.code,
        symbol,
        timeframe,
        bars: 500,
        params: draft.params,
      });
      setBacktest(result);
      setTab("factors");
      setSubTab("backtest");
      notify("success", result.message);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [draft, symbol, timeframe, notify]);

  const handleSave = useCallback(async () => {
    if (!draft) {
      notify("error", "请先生成 AI 因子");
      return;
    }
    setBusy(true);
    try {
      const saved = await api.saveFactor({
        name: draft.name,
        description: draft.description,
        code: draft.code,
        symbol,
        timeframe,
        source: draft.source,
        model: draft.model,
        params: draft.params,
        tags: draft.tags,
        chart_stats: regionStats ?? {},
        prompt_snapshot: {
          selection: selection ?? null,
          entry_points: entryPoints,
          exit_points: exitPoints,
          support_levels: supportLevels,
          resistance_levels: resistanceLevels,
        },
        generated_region: selection ?? {},
        backtest_stats: backtest?.metrics ?? null,
      });
      setSelectedFactor(saved);
      setTab("factors");
      setSubTab("manage");
      await loadFactors();
      if (selection) {
        try {
          const learned = await api.learnPattern({
            symbol,
            timeframe,
            time_start: selection.timeStart,
            time_end: selection.timeEnd,
            price_top: selection.priceTop,
            price_bottom: selection.priceBottom,
            entry_points: entryPoints,
            exit_points: exitPoints,
            support_levels: supportLevels,
            resistance_levels: resistanceLevels,
          });
          await api.savePattern({
            ...learned,
            factor_id: saved.id,
            symbol,
            timeframe,
            time_start: selection.timeStart,
            time_end: selection.timeEnd,
            price_top: selection.priceTop,
            price_bottom: selection.priceBottom,
            entry_points: entryPoints,
            exit_points: exitPoints,
            support_levels: supportLevels,
            resistance_levels: resistanceLevels,
          });
        } catch {
          // 形态学习失败不影响因子入库
        }
      }
      notify("success", `因子已存入特征库：${saved.name}`);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [draft, symbol, timeframe, regionStats, selection, entryPoints, exitPoints, supportLevels, resistanceLevels, backtest, notify, loadFactors]);

  const handleUpdateFactor = useCallback(async (payload: FactorPayload) => {
    if (!editingFactor) return;
    try {
      const result = await api.updateFactor(editingFactor.id, payload);
      setEditingFactor(null);
      await loadFactors();
      notify("success", result.message);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [editingFactor, loadFactors, notify]);

  const handleDeleteFactor = useCallback(async (factor: Factor) => {
    if (!window.confirm(`确认删除因子「${factor.name}」？`)) return;
    try {
      const result = await api.deleteFactor(factor.id);
      await loadFactors();
      notify("success", result.message);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [loadFactors, notify]);

  const handleSandboxTest = useCallback(async (factor: Factor) => {
    setSandboxResults((prev) => ({
      ...prev,
      [factor.id]: { summary: "沙盒测试中...", staticOk: false, runtimeOk: false, message: "", suggestions: [] },
    }));
    try {
      const res = await api.sandboxDiagnose({
        code: factor.code,
        symbol: factor.symbol,
        timeframe: factor.timeframe,
        bars_count: 300,
      });
      const summary = res.summary;
      const message = res.execution.ok ? "" : res.execution.message;
      setSandboxResults((prev) => ({
        ...prev,
        [factor.id]: {
          summary,
          staticOk: res.static.ok,
          runtimeOk: res.execution.ok,
          message,
          suggestions: res.suggestions,
        },
      }));
      notify(res.execution.ok && res.static.ok ? "success" : "error", summary);
    } catch (err) {
      const text = String(err instanceof Error ? err.message : err);
      setSandboxResults((prev) => ({
        ...prev,
        [factor.id]: { summary: `沙盒测试失败：${text}`, staticOk: false, runtimeOk: false, message: text, suggestions: [] },
      }));
      notify("error", text);
    }
  }, [notify]);

  const handleClearFactors = useCallback(async () => {
    if (!window.confirm("确认清空整个因子库？此操作不可恢复。")) return;
    try {
      const result = await api.clearFactors();
      setSelectedFactor(null);
      await loadFactors();
      notify("success", result.message);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [loadFactors, notify]);

  const handleCustomBacktest = useCallback(async () => {
    if (!draft) {
      notify("error", "请先生成 AI 因子");
      return;
    }
    setBusy(true);
    try {
      const result = await api.backtest({
        code: draft.code,
        symbol: backtestSettings.symbol,
        timeframe: backtestSettings.timeframe,
        bars: Number(backtestSettings.bars) || 500,
        params: draft.params,
        start_time: backtestSettings.startTime || undefined,
        end_time: backtestSettings.endTime || undefined,
        initial_equity: Number(backtestSettings.initialEquity) || 10000,
        leverage: Number(backtestSettings.leverage) || 30,
        delay_ms: Number(backtestSettings.delayMs) || 0,
        slippage_points: Number(backtestSettings.slippage) || 0,
      });
      setBacktest(result);
      setTab("factors");
      setSubTab("backtest");
      notify("success", result.message);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [draft, backtestSettings, notify]);

  const handleOptimize = useCallback(async () => {
    if (!draft) {
      notify("error", "请先生成 AI 因子");
      return;
    }
    let paramRanges: Record<string, Record<string, number>> | undefined;
    if (optSettings.paramRanges.trim()) {
      try {
        paramRanges = JSON.parse(optSettings.paramRanges);
      } catch {
        notify("error", "参数范围必须是合法 JSON，例如 {\"lookback\":{\"min\":10,\"max\":40,\"step\":5}}");
        return;
      }
    }
    setOptimizing(true);
    setOptimizeResult(null);
    try {
      const result = await api.optimizeBacktest({
        code: draft.code,
        symbol: backtestSettings.symbol,
        timeframe: backtestSettings.timeframe,
        bars: Number(backtestSettings.bars) || 500,
        params: draft.params,
        start_time: backtestSettings.startTime || undefined,
        end_time: backtestSettings.endTime || undefined,
        initial_equity: Number(backtestSettings.initialEquity) || 10000,
        leverage: Number(backtestSettings.leverage) || 30,
        delay_ms: Number(backtestSettings.delayMs) || 0,
        slippage_points: Number(backtestSettings.slippage) || 0,
        objective: optSettings.objective,
        ...(optSettings.targetWinRate !== "" ? { target_win_rate_pct: Number(optSettings.targetWinRate) } : {}),
        ...(optSettings.targetReturn !== "" ? { target_total_return_pct: Number(optSettings.targetReturn) } : {}),
        ...(optSettings.targetProfitFactor !== "" ? { target_profit_factor: Number(optSettings.targetProfitFactor) } : {}),
        ...(optSettings.targetMaxDrawdown !== "" ? { target_max_drawdown_pct: Number(optSettings.targetMaxDrawdown) } : {}),
        max_iterations: Number(optSettings.maxIterations) || 30,
        early_stop_rounds: Number(optSettings.earlyStop) || 5,
        walk_forward: optSettings.walkForward === "1",
        walk_forward_folds: Number(optSettings.walkForwardFolds) || 3,
        min_trades: Number(optSettings.minTrades) || 0,
        auto_expand: true,
        complexity_penalty: Number(optSettings.complexityPenalty) || 0,
        param_ranges: paramRanges,
      });
      setOptimizeResult(result);
      setTab("factors");
      setSubTab("backtest");
      notify("success", result.message);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setOptimizing(false);
    }
  }, [draft, backtestSettings, optSettings, notify]);

  const handleApplyBestParams = useCallback(async () => {
    if (!draft || !optimizeResult) return;
    setBusy(true);
    try {
      setDraft({ ...draft, params: optimizeResult.best_params });
      const result = await api.backtest({
        code: draft.code,
        symbol: backtestSettings.symbol,
        timeframe: backtestSettings.timeframe,
        bars: Number(backtestSettings.bars) || 500,
        params: optimizeResult.best_params,
        start_time: backtestSettings.startTime || undefined,
        end_time: backtestSettings.endTime || undefined,
        initial_equity: Number(backtestSettings.initialEquity) || 10000,
        leverage: Number(backtestSettings.leverage) || 30,
        delay_ms: Number(backtestSettings.delayMs) || 0,
        slippage_points: Number(backtestSettings.slippage) || 0,
      });
      setBacktest(result);
      setTab("factors");
      setSubTab("backtest");
      notify("success", "已应用最佳参数并完成回测");
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [draft, optimizeResult, backtestSettings, notify]);

  const viewFactor = useCallback(async (factor: Factor) => {
    setSelectedFactor(factor);
    setDraft(factor);
    setTab("factors");
    setSubTab("mark");
    try {
      const res = await api.sandboxDiagnose({
        code: factor.code,
        symbol: factor.symbol,
        timeframe: factor.timeframe,
        bars_count: 300,
      });
      const checked = {
        summary: res.summary,
        staticOk: res.static.ok,
        runtimeOk: res.execution.ok,
        message: res.execution.ok ? "" : res.execution.message,
        suggestions: res.suggestions,
      };
      setSandboxResults((prev) => ({ ...prev, [factor.id]: checked }));
      setDraft((prev) =>
        prev
          ? {
              ...prev,
              sandbox: {
                ok: res.static.ok,
                errors: res.static.errors,
                warnings: res.static.warnings,
                node_count: res.static.node_count,
                max_depth: res.static.max_depth,
              },
              execution: {
                ok: res.execution.ok,
                entry_count: res.execution.entry_count,
                exit_count: res.execution.exit_count,
                entry_sample: res.execution.entry_sample,
                exit_sample: res.execution.exit_sample,
                message: res.execution.message,
                execution_ms: res.execution.execution_ms,
              },
            }
          : prev
      );
    } catch {
      // 实时检测失败时保留原有草稿
    }
    notify("success", `正在查看因子逻辑：${factor.name}`);
  }, [notify]);

  const disableFactor = useCallback(async (factor: Factor) => {
    try {
      const result = await api.disableFactor(factor.id);
      await loadFactors();
      notify("success", result.message);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [loadFactors, notify]);

  const handleScan = useCallback(async () => {
    setBusy(true);
    try {
      const result = await api.scanMatcher(symbol, timeframe);
      setScan(result);
      notify("success", `扫描完成：共 ${result.scanned} 个因子，${result.candidates.length} 个候选信号`);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [symbol, timeframe, notify]);

  const handleExecutorToggle = useCallback(async () => {
    try {
      const result = await api.executorToggle(!systemState?.executor_enabled);
      notify("success", result.message);
      await loadSystem();
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [matcherForm, notify, loadSystem]);

  const handleExecutorScan = useCallback(async () => {
    setBusy(true);
    try {
      const result = await api.executorScanRun();
      notify("success", result.message);
      await loadSystem();
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [notify, loadSystem]);

  const toggleIn = (arr: string[], val: string) =>
    arr.includes(val) ? arr.filter((x) => x !== val) : [...arr, val];

  const loadMatcherMarketAnalysis = useCallback(async (sym: string, tf: string) => {
    setMatcherMarketAnalysis(null);
    try {
      setMatcherMarketAnalysis(await api.marketAnalysis(sym, tf));
    } catch {
      setMatcherMarketAnalysis(null);
    }
  }, []);

  const handleMatcherToggle = useCallback(async () => {
    if (systemState?.matcher_running) {
      try {
        const result = await api.stopMatcher();
        notify("success", result.message);
        await loadSystem();
      } catch (err) {
        notify("error", String(err instanceof Error ? err.message : err));
      }
      return;
    }
    const cfg = systemState?.executor_config as Record<string, unknown> | undefined;
    const mf = (cfg?.market_filter ?? {}) as Record<string, unknown>;
    setMatcherForm((v) => ({
      ...v,
      symbol,
      timeframe,
      ...(cfg
        ? {
            min_confidence: String(cfg.min_confidence ?? v.min_confidence),
            direction: String(cfg.direction || v.direction),
            lot_mode: String(cfg.lot_mode || v.lot_mode),
            fixed_lots: String(cfg.fixed_lots ?? v.fixed_lots),
            risk_percent: String(cfg.risk_percent ?? v.risk_percent),
            max_positions: String(cfg.max_positions ?? v.max_positions),
            stop_method: String(cfg.stop_method || v.stop_method),
            take_method: String(cfg.take_method || v.take_method),
            stop_atr_mult: String(cfg.stop_atr_mult ?? v.stop_atr_mult),
            take_atr_mult: String(cfg.take_atr_mult ?? v.take_atr_mult),
            stop_points: String(cfg.stop_points ?? v.stop_points),
            take_points: String(cfg.take_points ?? v.take_points),
            level_buffer_pct: String(cfg.level_buffer_pct ?? v.level_buffer_pct),
            take_level_buffer_pct: String(cfg.take_level_buffer_pct ?? v.take_level_buffer_pct),
            initial_sltp_source: String(cfg.initial_sltp_source || v.initial_sltp_source),
            trailing_enabled: Boolean(cfg.trailing_enabled ?? v.trailing_enabled),
            trailing_unit: String(cfg.trailing_unit || v.trailing_unit),
            trailing_activation_pct: String(cfg.trailing_activation_pct ?? v.trailing_activation_pct),
            trailing_retrace_pct: String(cfg.trailing_retrace_pct ?? v.trailing_retrace_pct),
            trailing_take_retrace_pct: String(cfg.trailing_take_retrace_pct ?? v.trailing_take_retrace_pct),
            trailing_take_buffer_pct: String(cfg.trailing_take_buffer_pct ?? v.trailing_take_buffer_pct),
            trailing_activation_atr: String(cfg.trailing_activation_atr ?? v.trailing_activation_atr),
            trailing_stop_atr: String(cfg.trailing_stop_atr ?? v.trailing_stop_atr),
            trailing_take_atr: String(cfg.trailing_take_atr ?? v.trailing_take_atr),
            trailing_take_buffer_atr: String(cfg.trailing_take_buffer_atr ?? v.trailing_take_buffer_atr),
            sl_tp_strategies: (cfg.sl_tp_strategies as string[] | undefined) ?? v.sl_tp_strategies,
            hw_activation_profit: String(cfg.hw_activation_profit ?? v.hw_activation_profit),
            hw_max_retrace_pct: String(cfg.hw_max_retrace_pct ?? v.hw_max_retrace_pct),
            pc_tier1_profit: String(cfg.pc_tier1_profit ?? v.pc_tier1_profit),
            pc_tier1_close_pct: String(cfg.pc_tier1_close_pct ?? v.pc_tier1_close_pct),
            pc_tier2_profit: String(cfg.pc_tier2_profit ?? v.pc_tier2_profit),
            pc_tier2_close_pct: String(cfg.pc_tier2_close_pct ?? v.pc_tier2_close_pct),
            pc_breakeven_buffer: String(cfg.pc_breakeven_buffer ?? v.pc_breakeven_buffer),
            delay_ms: String(cfg.delay_ms ?? v.delay_ms),
            auto_close_enabled: Boolean(cfg.auto_close_enabled ?? v.auto_close_enabled),
            alert_enabled: Boolean(cfg.alert_enabled ?? v.alert_enabled),
            max_daily_loss_pct: String(cfg.max_daily_loss_pct ?? v.max_daily_loss_pct),
            max_drawdown_pct: String(cfg.max_drawdown_pct ?? v.max_drawdown_pct),
            pattern_min_similarity: String(cfg.pattern_min_similarity ?? v.pattern_min_similarity),
            pattern_min_samples: String(cfg.pattern_min_samples ?? v.pattern_min_samples),
            market_filter_enabled: Boolean(mf.enabled ?? v.market_filter_enabled),
            market_filter_trends: (mf.trends as string[] | undefined) ?? v.market_filter_trends,
            market_filter_volatilities: (mf.volatilities as string[] | undefined) ?? v.market_filter_volatilities,
            market_filter_volume_states: (mf.volume_states as string[] | undefined) ?? v.market_filter_volume_states,
            market_filter_macro_directions: (mf.macro_directions as string[] | undefined) ?? v.market_filter_macro_directions,
            market_filter_d1_directions: (((mf as Record<string, unknown>).mtf_directions as { d1?: string[] } | undefined)?.d1 ?? []) as string[],
            market_filter_min_score: String(mf.min_environment_score ?? v.market_filter_min_score),
          }
        : {}),
    }));
    setMatcherConfirm(false);
    setMatcherDialog(true);
  }, [systemState, symbol, timeframe, notify, loadSystem]);

  const handleMatcherStart = useCallback(async () => {
    try {
      const toNum = (val: unknown, fallback = 0) => {
        const n = Number(val);
        return Number.isFinite(n) ? n : fallback;
      };
      const result = await api.startMatcher({
        symbol: matcherForm.symbol,
        timeframe: matcherForm.timeframe,
        min_confidence: toNum(matcherForm.min_confidence, 0.55),
        direction: matcherForm.direction,
        lot_mode: matcherForm.lot_mode,
        fixed_lots: toNum(matcherForm.fixed_lots, 0.01),
        risk_percent: toNum(matcherForm.risk_percent, 1),
        max_positions: toNum(matcherForm.max_positions, 5),
        stop_method: matcherForm.stop_method,
        take_method: matcherForm.take_method,
        stop_atr_mult: toNum(matcherForm.stop_atr_mult, 2),
        take_atr_mult: toNum(matcherForm.take_atr_mult, 3),
        stop_points: toNum(matcherForm.stop_points, 0),
        take_points: toNum(matcherForm.take_points, 0),
        level_buffer_pct: toNum(matcherForm.level_buffer_pct, 0.1),
        take_level_buffer_pct: toNum(matcherForm.take_level_buffer_pct, 0.1),
        initial_sltp_source: matcherForm.initial_sltp_source,
        sl_tp_strategies: matcherForm.sl_tp_strategies,
        trailing_enabled: matcherForm.trailing_enabled,
        trailing_unit: matcherForm.trailing_unit,
        trailing_activation_pct: toNum(matcherForm.trailing_activation_pct, 0.3),
        trailing_retrace_pct: toNum(matcherForm.trailing_retrace_pct, 0.3),
        trailing_take_retrace_pct: toNum(matcherForm.trailing_take_retrace_pct, 0.3),
        trailing_take_buffer_pct: toNum(matcherForm.trailing_take_buffer_pct, 0.2),
        trailing_activation_atr: toNum(matcherForm.trailing_activation_atr, 0.5),
        trailing_stop_atr: toNum(matcherForm.trailing_stop_atr, 1),
        trailing_take_atr: toNum(matcherForm.trailing_take_atr, 0.5),
        trailing_take_buffer_atr: toNum(matcherForm.trailing_take_buffer_atr, 0.5),
        hw_activation_profit: toNum(matcherForm.hw_activation_profit, 200),
        hw_max_retrace_pct: toNum(matcherForm.hw_max_retrace_pct, 20),
        pc_tier1_profit: toNum(matcherForm.pc_tier1_profit, 400),
        pc_tier1_close_pct: toNum(matcherForm.pc_tier1_close_pct, 50),
        pc_tier2_profit: toNum(matcherForm.pc_tier2_profit, 1000),
        pc_tier2_close_pct: toNum(matcherForm.pc_tier2_close_pct, 30),
        pc_breakeven_buffer: toNum(matcherForm.pc_breakeven_buffer, 0.5),
        delay_ms: toNum(matcherForm.delay_ms, 0),
        auto_close_enabled: matcherForm.auto_close_enabled,
        alert_enabled: matcherForm.alert_enabled,
        max_daily_loss_pct: toNum(matcherForm.max_daily_loss_pct, 3),
        max_drawdown_pct: toNum(matcherForm.max_drawdown_pct, 20),
        pattern_min_similarity: toNum(matcherForm.pattern_min_similarity, 0.85),
        pattern_min_samples: toNum(matcherForm.pattern_min_samples, 0),
        market_filter: {
          enabled: matcherForm.market_filter_enabled,
          trends: matcherForm.market_filter_trends,
          volatilities: matcherForm.market_filter_volatilities,
          volume_states: matcherForm.market_filter_volume_states,
          macro_directions: matcherForm.market_filter_macro_directions,
          mtf_directions: matcherForm.market_filter_d1_directions.length ? { d1: matcherForm.market_filter_d1_directions } : undefined,
          min_environment_score: toNum(matcherForm.market_filter_min_score, 0),
        },
      });
      setMatcherDialog(false);
      setMatcherConfirm(false);
      notify("success", result.message);
      await loadSystem();
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [matcherForm, smartStopForm, systemState, symbol, timeframe, notify, loadSystem]);

  const handleRecommendMatcher = useCallback(async (target?: string) => {
    const sym = (target || matcherForm.symbol || "").trim().toUpperCase();
    if (!sym) return;
    setMatcherRecommendBusy(true);
    try {
      const rec = await api.recommendMatcher(sym);
      setMatcherForm((v) => ({
        ...v,
        symbol: sym,
        timeframe: String(rec.timeframe || v.timeframe),
        min_confidence: String(rec.min_confidence ?? v.min_confidence),
        direction: String(rec.direction || v.direction),
        lot_mode: String(rec.lot_mode || v.lot_mode),
        fixed_lots: String(rec.fixed_lots ?? v.fixed_lots),
        risk_percent: String(rec.risk_percent ?? v.risk_percent),
        max_positions: String(rec.max_positions ?? v.max_positions),
        initial_sltp_source: String(rec.initial_sltp_source || v.initial_sltp_source),
        stop_method: String(rec.stop_method || v.stop_method),
        take_method: String(rec.take_method || v.take_method),
        stop_atr_mult: String(rec.stop_atr_mult ?? v.stop_atr_mult),
        take_atr_mult: String(rec.take_atr_mult ?? v.take_atr_mult),
        stop_points: String(rec.stop_points ?? v.stop_points),
        take_points: String(rec.take_points ?? v.take_points),
        level_buffer_pct: String(rec.level_buffer_pct ?? v.level_buffer_pct),
        take_level_buffer_pct: String(rec.take_level_buffer_pct ?? v.take_level_buffer_pct),
        delay_ms: String(rec.delay_ms ?? v.delay_ms),
        max_daily_loss_pct: String(rec.max_daily_loss_pct ?? v.max_daily_loss_pct),
        max_drawdown_pct: String(rec.max_drawdown_pct ?? v.max_drawdown_pct),
        pattern_min_similarity: String(rec.pattern_min_similarity ?? v.pattern_min_similarity),
        pattern_min_samples: String(rec.pattern_min_samples ?? v.pattern_min_samples),
        auto_close_enabled: Boolean(rec.auto_close_enabled ?? v.auto_close_enabled),
        alert_enabled: Boolean(rec.alert_enabled ?? v.alert_enabled),
      }));
      const sc = rec.smart_stop as Record<string, unknown> | undefined;
      if (sc) {
        setSmartStopForm((prev) => ({
          ...prev,
          enabled: Boolean(sc.enabled ?? prev.enabled),
          manage_manual: Boolean(sc.manage_manual ?? prev.manage_manual),
          use_history_profile: Boolean(sc.use_history_profile ?? prev.use_history_profile),
          use_pattern_match: Boolean(sc.use_pattern_match ?? prev.use_pattern_match),
          partial_close_enabled: Boolean(sc.partial_close_enabled ?? prev.partial_close_enabled),
          ladder_take_enabled: Boolean(sc.ladder_take_enabled ?? prev.ladder_take_enabled),
          hard_stop_atr: String(sc.hard_stop_atr ?? prev.hard_stop_atr),
          max_single_loss_usd: String(sc.max_single_loss_usd ?? prev.max_single_loss_usd),
        }));
      }
      setMatcherRecommendReasons(Array.isArray(rec.reasons) ? rec.reasons as string[] : []);
      notify("success", `已按 ${sym} 推荐参数`);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setMatcherRecommendBusy(false);
    }
  }, [matcherForm.symbol, notify]);

  const handleStartPaper = useCallback(async () => {
    if (!selectedFactor) {
      notify("error", "请先从因子库选择因子");
      return;
    }
    try {
      const result = await api.startPaper({
        factor_id: selectedFactor.id,
        symbol,
        timeframe,
        equity: systemState?.account_equity ?? 10000,
      });
      notify("success", result.message);
      await loadPaper();
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [selectedFactor, symbol, timeframe, systemState, notify, loadPaper]);

  const handleStopPaper = useCallback(async () => {
    try {
      const result = await api.stopPaper();
      notify("success", result.message);
      await loadPaper();
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [notify, loadPaper]);

  const handleCloseAll = useCallback(async () => {
    try {
      const result = await api.closeAll();
      notify("success", result.message);
      await loadAlerts();
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [notify, loadAlerts]);

  const handleRestartSystem = useCallback(async () => {
    if (!window.confirm("确定重启系统？将关闭所有后端和前端进程，约 10 秒后自动重启，请稍后刷新页面。")) return;
    setBusy(true);
    try {
      const result = await api.restartSystem();
      notify("success", result.message);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [notify]);

  const handleAiChatSend = useCallback(async () => {
    if (!chatInput.trim() && !chatImage) return;
    const userText = chatInput.trim() || "（上传了K线图片）";
    setChatMessages((prev) => [...prev, { role: "user", content: userText }]);
    setChatInput("");
    setChatBusy(true);
    try {
      const result = await api.aiChat({
        message: userText,
        image_data_url: chatImage,
        metadata: chatMeta,
      });
      setChatMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: result.reply,
          factor: result.factor_draft ?? null,
          backtest: result.backtest ?? null,
          sl_tp: result.sl_tp_strategy ?? {},
        },
      ]);
      setChatImage(null);
    } catch (err) {
      setChatMessages((prev) => [...prev, { role: "assistant", content: String(err instanceof Error ? err.message : err) }]);
    } finally {
      setChatBusy(false);
    }
  }, [chatInput, chatImage, chatMeta]);

  const handleSaveChatFactor = useCallback(async (factor: FactorDraft, sl_tp: Record<string, unknown>, backtest: BacktestResult | null) => {
    try {
      const saved = await api.saveFactor({
        name: factor.name,
        description: factor.description,
        code: factor.code,
        symbol: chatMeta.symbol || symbol,
        timeframe: chatMeta.timeframe || timeframe,
        source: factor.source,
        model: factor.model,
        params: factor.params,
        tags: factor.tags,
        sl_tp_strategy: sl_tp,
        chart_stats: { source: "ai_chat" },
        prompt_snapshot: { ai_chat: true },
        generated_region: {},
        backtest_stats: backtest?.metrics ?? null,
      });
      notify("success", `因子已存入因子库：${saved.name}`);
      await loadFactors();
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [chatMeta, symbol, timeframe, notify, loadFactors]);

  const handleReplay = useCallback(async () => {
    setBusy(true);
    try {
      const result = await api.replayExport();
      setReplay(result);
      notify("success", "中文复盘报告已生成");
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [notify]);

  const loadTrades = useCallback(async () => {
    try {
      const data = await api.replayTrades(200);
      setTradeRecords(data.trades || []);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [notify]);

  const handleBackfillExtremes = useCallback(async () => {
    setBackfilling(true);
    try {
      const result = await api.backfillExtremes();
      notify("success", result.message);
      await loadTrades();
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBackfilling(false);
    }
  }, [loadTrades, notify]);

  const handleSaveTrailing = useCallback(async () => {
    setBusy(true);
    try {
      const result = await api.saveTradingConfig({ ...trailingForm });
      notify("success", result.message);
      setTrailingLoaded(true);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [trailingForm, notify]);

  const loadSmartStop = useCallback(async () => {
    try {
      const data = await api.smartStopConfig();
      const cfg = data.config || {};
      setSmartStopForm({
        enabled: Boolean(cfg.enabled),
        timeframe: String(cfg.timeframe || "M15"),
        manage_manual: Boolean(cfg.manage_manual ?? true),
        use_history_profile: Boolean(cfg.use_history_profile ?? true),
        use_pattern_match: Boolean(cfg.use_pattern_match ?? true),
        pattern_match_min_similarity: String(cfg.pattern_match_min_similarity ?? "0.85"),
        pattern_refresh_seconds: String(cfg.pattern_refresh_seconds ?? "60"),
        partial_close_enabled: Boolean(cfg.partial_close_enabled),
        ladder_take_enabled: Boolean(cfg.ladder_take_enabled ?? true),
        soft_stop_enabled: Boolean(cfg.soft_stop_enabled ?? true),
        soft_stop_atr: String(cfg.soft_stop_atr ?? "0.5"),
        soft_stop_bars: String(cfg.soft_stop_bars ?? "3"),
        breakeven_enabled: Boolean(cfg.breakeven_enabled ?? true),
        breakeven_atr: String(cfg.breakeven_atr ?? "0.5"),
        trailing_enabled: Boolean(cfg.trailing_enabled ?? true),
        trailing_activation_atr: String(cfg.trailing_activation_atr ?? "0.8"),
        trailing_stop_atr: String(cfg.trailing_stop_atr ?? "1.0"),
        dynamic_level_enabled: Boolean(cfg.dynamic_level_enabled ?? true),
        time_stop_enabled: Boolean(cfg.time_stop_enabled ?? true),
        max_hold_bars: String(cfg.max_hold_bars ?? "120"),
        lock_profit_enabled: Boolean(cfg.lock_profit_enabled ?? true),
        lock_activation_atr: String(cfg.lock_activation_atr ?? "1.0"),
        lock_retrace_atr: String(cfg.lock_retrace_atr ?? "0.5"),
        hard_stop_atr: String(cfg.hard_stop_atr ?? "1.5"),
        max_single_loss_usd: String(cfg.max_single_loss_usd ?? "1500"),
        cooldown_seconds: String(cfg.cooldown_seconds ?? "60"),
        ai_enabled: Boolean(cfg.ai_enabled),
        ai_take_profit_enabled: Boolean(cfg.ai_take_profit_enabled ?? true),
        ai_min_interval_seconds: String(cfg.ai_min_interval_seconds ?? "60"),
        ai_timeout_seconds: String(cfg.ai_timeout_seconds ?? "8"),
        ai_min_confidence: String(cfg.ai_min_confidence ?? "0.6"),
        ai_triggers: Array.isArray(cfg.ai_triggers) ? cfg.ai_triggers as string[] : ["soft_stop", "time_stop", "near_hard", "profit_lock"],
        ai_periodic_enabled: Boolean(cfg.ai_periodic_enabled),
        ai_periodic_interval_seconds: String(cfg.ai_periodic_interval_seconds ?? "60"),
      });
    } catch {
      // 忽略加载失败
    }
  }, []);

  const handleSaveSmartStop = useCallback(async () => {
    setBusy(true);
    try {
      const result = await api.saveSmartStopConfig(smartStopForm);
      notify("success", result.message);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [smartStopForm, notify]);

  const handleLearnTrades = useCallback(async () => {
    if (selectedTradeIds.length < 3) {
      notify("error", "至少选择 3 笔订单才能学习");
      return;
    }
    setLearning(true);
    try {
      const result = await api.aiLearnTrades({
        trade_ids: selectedTradeIds,
        symbol: learnSettings.symbol || undefined,
        timeframe: learnSettings.timeframe,
        period_mode: learnSettings.period_mode,
        start_time: learnSettings.period_mode === "custom" ? learnSettings.start_time || undefined : undefined,
        end_time: learnSettings.period_mode === "custom" ? learnSettings.end_time || undefined : undefined,
        recent_months: learnSettings.period_mode === "recent_months" || learnSettings.period_mode === "recent_months_12"
          ? (learnSettings.period_mode === "recent_months_12" ? 12 : Number(learnSettings.recent_months))
          : undefined,
        recent_bars: learnSettings.period_mode === "recent_bars" ? Number(learnSettings.recent_bars) : undefined,
        min_validation_trades: Number(learnSettings.min_validation_trades) || 10,
        min_win_rate_pct: Number(learnSettings.min_win_rate_pct) || 40,
        min_profit_factor: Number(learnSettings.min_profit_factor) || 1,
        min_oos_trades: Number(learnSettings.min_oos_trades) || 1,
      });
      if (!result.ok) {
        notify("error", result.warning || "AI 学习失败");
        return;
      }
      setLearnDialog(result);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setLearning(false);
    }
  }, [selectedTradeIds, learnSettings, notify]);

  const handleSetTradeQuality = useCallback(async (id: string, quality: "ideal" | "general" | "poor") => {
    try {
      await api.setTradeQuality(id, quality);
      setTradeRecords((prev) => prev.map((t) => (String(t.id) === id ? { ...t, quality } : t)));
      notify("success", "质量标签已更新");
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [notify]);

  const handleOptimizeTrade = useCallback(async (id: string) => {
    try {
      const result = await api.optimizeTrade(id, "M15");
      setTradeOpts((prev) => ({ ...prev, [id]: result }));
      if (result.ok) {
        notify("success", `优化后预计盈亏 ${Number(result.optimized_pnl).toFixed(2)}`);
      } else {
        notify("error", String(result.message || "模拟优化失败"));
      }
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [notify]);

  const loadFactorStats = useCallback(async (factorId = "") => {
    try {
      const res = await api.factorStats({
        factor_id: factorId || factorStatFilter.factor_id || undefined,
        symbol: factorStatFilter.symbol || undefined,
        timeframe: factorStatFilter.timeframe || undefined,
        status: factorStatFilter.status || undefined,
      });
      setFactorStats(res);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [factorStatFilter, notify]);

  const loadSltpCases = useCallback(async () => {
    try {
      const res = await api.sltpCases();
      setSltpCases(res.cases || []);
    } catch {
      // 忽略加载失败
    }
  }, []);

  const handleSltpLearn = useCallback(async () => {
    if (selectedTradeIds.length === 0) {
      notify("error", "请先选择要学习的订单");
      return;
    }
    setSltpBusy(true);
    try {
      const res = await api.sltpLearn(selectedTradeIds);
      setSltpCases(res.cases || []);
      notify("success", `已保存 ${res.saved} 笔理想止损止盈案例`);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setSltpBusy(false);
    }
  }, [selectedTradeIds, notify]);

  const loadSltpStrategies = useCallback(async () => {
    try {
      const [res, model] = await Promise.all([api.sltpStrategies(), api.sltpModel()]);
      setSltpStrategies(res.strategies || []);
      setSltpModel(model);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [notify]);

  const handleSltpAutoLearn = useCallback(async () => {
    setSltpLearnBusy(true);
    try {
      const res = await api.sltpAutoLearn();
      notify("success", `自动学习完成：新增 ${res.created}，更新 ${res.updated}`);
      await loadSltpStrategies();
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setSltpLearnBusy(false);
    }
  }, [loadSltpStrategies, notify]);

  const handleSltpTrain = useCallback(async () => {
    setSltpTrainBusy(true);
    try {
      const res = await api.sltpTrain();
      notify("success", `模型已训练：${res.model_version}`);
      await loadSltpStrategies();
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setSltpTrainBusy(false);
    }
  }, [loadSltpStrategies, notify]);

  const handleOrderChat = useCallback(async (trade: Record<string, unknown>, mirror: boolean) => {
    setOrderChatTrade(trade);
    setOrderChatResult(null);
    setOrderChatOpen(true);
    setOrderChatBusy(true);
    try {
      const res = await api.aiOrderChat({
        trade_id: String(trade.id),
        mirror,
        timeframe: "M15",
      });
      setOrderChatResult({
        reply: res.reply,
        warning: res.warning,
        factor: res.factor_draft ?? null,
        backtest: res.backtest ?? null,
        sl_tp: res.sl_tp_strategy ?? {},
        mirrored: res.mirrored,
      });
    } catch (err) {
      setOrderChatResult({ reply: String(err instanceof Error ? err.message : err), warning: "", mirrored: true });
    } finally {
      setOrderChatBusy(false);
    }
  }, []);

  const openOrderChatChoice = useCallback((trade: Record<string, unknown>) => {
    setOrderChatTrade(trade);
    setOrderChatResult(null);
    setOrderChatChoiceOpen(true);
  }, []);

  const handleOrderChatBacktest = useCallback(async () => {
    if (!orderChatResult?.factor) {
      notify("error", "请先完成 AI 优化生成因子");
      return;
    }
    setOrderChatBacktesting(true);
    try {
      const result = await api.backtest({
        code: orderChatResult.factor.code,
        symbol: String(orderChatTrade?.symbol || symbol),
        timeframe: "M15",
        bars: 500,
        params: orderChatResult.factor.params || {},
      });
      setOrderChatResult((prev) => (prev ? { ...prev, backtest: result } : prev));
      notify("success", result.message || "回测完成");
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setOrderChatBacktesting(false);
    }
  }, [orderChatResult, orderChatTrade, symbol, notify]);

  const handleSaveOrderChatFactor = useCallback(async (factor: FactorDraft, sltp: Record<string, unknown>, backtest: BacktestResult | null) => {
    try {
      const saved = await api.saveFactor({
        name: factor.name,
        description: factor.description,
        code: factor.code,
        symbol: String(orderChatTrade?.symbol || symbol),
        timeframe: "M15",
        source: factor.source,
        model: factor.model,
        params: factor.params,
        tags: factor.tags,
        sl_tp_strategy: sltp,
        chart_stats: { source: "order_chat", trade_id: String(orderChatTrade?.id || "") },
        prompt_snapshot: { order_chat: true },
        generated_region: {},
        backtest_stats: backtest?.metrics ?? null,
      });
      notify("success", `因子已存入因子库：${saved.name}`);
      await loadFactors();
      setOrderChatOpen(false);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [orderChatTrade, symbol, notify, loadFactors]);

  const loadTradeStats = useCallback(async () => {
    try {
      const result = await api.tradeStats({
        symbol: tradeFilter.symbol || undefined,
        account_id: tradeFilter.account_id || undefined,
        side: tradeFilter.side || undefined,
        quality: tradeFilter.quality || undefined,
        fixed_lots: tradeFilter.fixed_lots ? Number(tradeFilter.fixed_lots) : undefined,
        normalize_to_one_lot: tradeFilter.normalizeToOneLot,
      });
      setTradeStats(result);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [tradeFilter, notify]);

  const handleDeleteSelectedTrades = useCallback(async () => {
    if (selectedTradeIds.length === 0) return;
    if (!window.confirm(`确定删除选中的 ${selectedTradeIds.length} 条交易记录？此操作不可恢复。`)) return;
    setBusy(true);
    try {
      const result = await api.deleteTrades(selectedTradeIds);
      setSelectedTradeIds([]);
      await loadTrades();
      await loadTradeStats();
      notify("success", result.message);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [selectedTradeIds, loadTrades, loadTradeStats, notify]);

  const handleSyncMt5Trades = useCallback(async () => {
    setSyncingTrades(true);
    try {
      const result = await api.syncMt5Trades(30);
      notify("success", result.message);
      await loadTrades();
      await loadTradeStats();
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setSyncingTrades(false);
    }
  }, [loadTrades, loadTradeStats, notify]);

  const handleMigrateAccount = useCallback(async () => {
    if (!window.confirm("确定将全部历史交易记录归属到当前 MT5 账户吗？")) return;
    setMigratingAccount(true);
    try {
      const result = await api.migrateAccount();
      notify("success", result.message);
      await loadTrades();
      await loadTradeStats();
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setMigratingAccount(false);
    }
  }, [loadTrades, loadTradeStats, notify]);

  const handleConfirmLearn = useCallback(async () => {
    if (!learnDialog) return;
    setBusy(true);
    try {
      const d = learnDialog.factor_draft;
      const factorPayload = {
        name: d.name,
        description: d.description,
        code: d.code,
        symbol: learnDialog.symbol,
        timeframe: learnDialog.timeframe,
        source: d.source,
        model: d.model,
        params: d.params,
        tags: d.tags,
        sl_tp_strategy: learnDialog.sl_tp_strategy,
        chart_stats: {
          trade_summary: learnDialog.trade_summary,
          ai_model: learnDialog.ai_model,
          warning: learnDialog.warning,
        },
        prompt_snapshot: {
          learn_trades: true,
          trade_ids: selectedTradeIds,
        },
        generated_region: {},
        backtest_stats: learnDialog.backtest?.metrics ?? null,
      };
      const saved = await api.saveLearnedFactor({
        factor: factorPayload,
        trade_ids: selectedTradeIds,
        validation_stats: learnDialog.validation_stats,
      });
      setSelectedTradeIds([]);
      setLearnDialog(null);
      await loadFactors();
      notify("success", `因子已存入因子库：${saved.name}`);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [learnDialog, selectedTradeIds, loadFactors, notify]);

  useEffect(() => {
    loadBars();
    loadSnapshot();
    loadSymbols();
    loadFactors();
    loadAlerts();
    loadPaper();
    loadSystem();
    loadAiStatus();
    loadTrades();
    loadSmartStop();
    loadSltpStrategies();
    loadMarketAnalysis();
  }, [loadBars, loadSnapshot, loadSymbols, loadFactors, loadAlerts, loadPaper, loadSystem, loadAiStatus, loadTrades, loadSmartStop, loadSltpStrategies, loadMarketAnalysis]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(`ws://${window.location.host}/api/mt5/ws/tick?symbol=${symbol}`);
    } catch {
      return;
    }
    prevTickRef.current = null; // 换品种后重置涨跌基线
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        const tick = msg?.data;
        if (!tick || !Number.isFinite(tick.bid)) return;
        setTopTick(tick);
        const prev = prevTickRef.current;
        setTickDir(prev != null ? (tick.bid > prev ? "up" : tick.bid < prev ? "down" : "flat") : "flat");
        prevTickRef.current = tick.bid;
        const price = tick.bid || tick.ask;
        setBars((prev) => {
          if (!prev.length) return prev;
          const last = prev[prev.length - 1];
          return [
            ...prev.slice(0, -1),
            {
              ...last,
              close: price,
              high: Math.max(last.high, price),
              low: Math.min(last.low, price),
            },
          ];
        });
      } catch {
        // 忽略异常帧
      }
    };
    return () => ws.close();
  }, [symbol]);

  useEffect(() => {
    if (selection) {
      setBacktestSettings((v) => ({ ...v, symbol, timeframe }));
    }
  }, [selection, symbol, timeframe]);

  useEffect(() => {
    setSelection(null);
  }, [symbol, timeframe]);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const r = await api.modelPredict("logistic_momentum", symbol, timeframe);
        if (!cancelled) setModelSignal(r.trained && r.signal !== "none" ? { model: r.model ?? "logistic_momentum", signal: r.signal, confidence: r.confidence, probability: r.probability } : null);
      } catch {
        if (!cancelled) setModelSignal(null);
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [symbol, timeframe]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      loadBars();
      loadSnapshot();
      loadSystem();
      loadPaper();
      loadMarketAnalysis();
    }, 4000);
    return () => window.clearInterval(timer);
  }, [loadBars, loadSnapshot, loadSystem, loadPaper, loadMarketAnalysis]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      loadAlerts();
      loadAiStatus();
      loadTrades();
    }, 10000);
    return () => window.clearInterval(timer);
  }, [loadAlerts, loadAiStatus, loadTrades]);

  useEffect(() => {
    loadTradeStats();
  }, [loadTradeStats]);

  useEffect(() => {
    if (tab === "replay" && subTab === "stats") {
      loadFactorStats();
    }
  }, [tab, subTab, loadFactorStats]);

  const metric = backtest?.metrics;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark"><Bot size={17} /></span>
          外汇 AI 量化交易系统
        </div>
        <SymbolSearchSelect symbols={symbols} value={symbol} onChange={setSymbol} />
        <div className="select-wrap">
          <Activity size={13} />
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
            {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div className="topbar-spacer" />
        <span className="status-pill"><span className={`dot ${systemState?.matcher_running ? "on" : ""}`} /> 实盘匹配 {systemState?.matcher_running ? "运行中" : "已暂停"}</span>
        <span className="status-pill"><span className={`dot ${systemState?.active_positions ? "warn" : ""}`} /> 持仓 {systemState?.active_positions ?? 0}</span>
        <span className="status-pill" title="实时行情：MT5 tick 推送（非轮询），Bid / Ask / 点差随每次 tick 更新">
          <span className={`dot ${topTick?.bid ? "on" : ""}`} /> 行情{" "}
          {topTick ? (
            <>
              <span style={{ color: tickDir === "up" ? "#8e8" : tickDir === "down" ? "#e88" : "#ccc" }}>
                {tickDir === "up" ? "▲ " : tickDir === "down" ? "▼ " : ""}
              </span>
              {topTick.bid.toFixed(topTick.bid >= 100 ? 2 : 5)} / {topTick.ask.toFixed(topTick.ask >= 100 ? 2 : 5)}
              <span style={{ color: "#888" }}> · {topTick.spread_points}点</span>
              {String((marketAnalysis?.multi_timeframe as Record<string, unknown> | undefined)?.consensus ?? "") && (
                <span style={{ color: "#9be", marginLeft: 6 }}>
                  {String((marketAnalysis?.multi_timeframe as Record<string, unknown> | undefined)?.consensus ?? "")}
                </span>
              )}
            </>
          ) : (
            "实时"
          )}
        </span>
        <span className={`status-pill ai-pill ${aiStatus?.enabled_count ? "ai-on" : aiStatus?.configured ? "ai-warn" : ""}`}>
          <span className={`dot ${aiStatus?.enabled_count ? "on" : aiStatus?.configured ? "warn" : ""}`} />
          AI {aiStatus?.enabled_count ? `已配置 ${aiStatus.configs.find((c) => c.enabled)?.model ?? ""}` : aiStatus?.configured ? "未启用" : "未配置"}
        </span>
        <span className="status-pill"><Wallet size={13} /> MT5 账户 {systemState?.account_login ?? "--"} 权益 ${(systemState?.account_equity ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
        <button className={`btn ${systemState?.matcher_running ? "danger" : "primary"}`} onClick={handleMatcherToggle}>
          {systemState?.matcher_running ? <StopCircle size={14} /> : <Play size={14} />}
          {systemState?.matcher_running ? "暂停系统交易" : "启动实盘匹配"}
        </button>
        <button className="btn" onClick={() => setMatcherViewOpen(true)}><Eye size={14} /> 查看匹配参数</button>
        <button className="btn danger" onClick={handleCloseAll}>
          <Square size={14} /> 一键平仓
        </button>
        <button className="btn" disabled={busy} onClick={handleRestartSystem}>
          <Power size={14} /> 重启系统
        </button>
        <button className="btn" onClick={() => setChatOpen(true)}>
          <Bot size={14} /> AI 助手
        </button>
      </header>

      <div className="main">
        <section className="chart-zone">
          <div className="action-bar">
            <button className={`btn ${mode === "select" ? "active" : ""}`} onClick={() => setMode("select")}>
              <BoxSelect size={14} /> 框选形态
            </button>
            <button className={`btn ${mode === "entry" ? "active" : ""}`} onClick={() => setMode("entry")}>
              <Sparkles size={14} /> 标记入场点
            </button>
            <button className={`btn ${mode === "exit" ? "active" : ""}`} onClick={() => setMode("exit")}>
              <Ban size={14} /> 标记出场点
            </button>
            <button className={`btn ${mode === "resistance" ? "active" : ""}`} onClick={() => setMode("resistance")}>
              <Ban size={14} /> 标记压力位
            </button>
            <button className={`btn ${mode === "support" ? "active" : ""}`} onClick={() => setMode("support")}>
              <Sparkles size={14} /> 标记支撑位
            </button>
            <button className="btn" disabled={!selection || supportLevels.length === 0 || resistanceLevels.length === 0} onClick={handleSuggestPoints}>
              <Sparkles size={14} /> 自动建议入场/出场
            </button>
            <button className="btn" onClick={resetSelection}>
              <RefreshCw size={14} /> 重置框选
            </button>
            <button className="btn" disabled={markerHistory.length === 0 && levelHistory.length === 0} onClick={handleUndoMarker}>
              <Undo2 size={14} /> 撤销标记
            </button>
            <button className="btn" disabled={entryPoints.length === 0 && exitPoints.length === 0 && supportLevels.length === 0 && resistanceLevels.length === 0} onClick={handleClearMarkers}>
              <X size={14} /> 清除标注
            </button>
            <button className="btn" disabled={!selection} onClick={handleClearSelection}>
              <X size={14} /> 取消框选
            </button>
            <span className="btn-sep" />
            <button className="btn primary" disabled={busy} onClick={handleGenerate}>
              <Bot size={14} /> 提取并生成 AI 因子
            </button>
            <button className="btn" disabled={busy || !draft} onClick={handleBacktest}>
              <TestTube2 size={14} /> 运行历史回测
            </button>
            <button className="btn" disabled={busy || !draft} onClick={handleSave}>
              <Save size={14} /> 存入因子特征库
            </button>
            <span className="btn-sep" />
            <button className={`btn ${showIndicators ? "active" : ""}`} onClick={() => setShowIndicators((v) => !v)}>
              <Activity size={14} /> 指标叠加
            </button>
            <button className="btn" disabled={!selectedFactor} onClick={() => selectedFactor && viewFactor(selectedFactor)}>
              <Eye size={14} /> 查看因子逻辑
            </button>
            <button className="btn" disabled={!selectedFactor} onClick={() => selectedFactor && disableFactor(selectedFactor)}>
              <StopCircle size={14} /> 禁用此因子
            </button>
            <span className="btn-sep" />
            {snapshot && (
              <span className="status-pill">
                最新价 <strong>{snapshot.current_price.toFixed(5)}</strong>
                <span className={snapshot.change_pct_24h >= 0 ? "pnl-pos" : "pnl-neg"}>{snapshot.change_pct_24h >= 0 ? "▲" : "▼"}{Math.abs(snapshot.change_pct_24h).toFixed(3)}%</span>
              </span>
            )}
          </div>
          <KLineChart
            bars={bars}
            selection={selection}
            entryPoints={entryPoints}
            exitPoints={exitPoints}
            supportLevels={supportLevels}
            resistanceLevels={resistanceLevels}
            mode={mode}
            regionStats={regionStats}
            indicators={indicators}
            showIndicators={showIndicators}
            onSelection={setSelection}
            onPoint={handleAddPoint}
            onLevel={handleAddLevel}
            onMode={setMode}
            modelSignal={modelSignal}
          />
        </section>

        <aside className="sidebar">
          <div className="tabs">
            <button className={`tab ${tab === "factors" ? "active" : ""}`} onClick={() => { setTab("factors"); setSubTab("mark"); }}><Bot size={14} /> 因子工作台</button>
            <button className={`tab ${tab === "mining" ? "active" : ""}`} onClick={() => setTab("mining")}><Radar size={14} /> 因子挖掘</button>
            <button className={`tab ${tab === "trading" ? "active" : ""}`} onClick={() => { setTab("trading"); setSubTab("signals"); }}><Activity size={14} /> 交易中心</button>
            <button className={`tab ${tab === "replay" ? "active" : ""}`} onClick={() => { setTab("replay"); setSubTab("replay"); }}><FileText size={14} /> 复盘与日志</button>
            <button className={`tab ${tab === "ai" ? "active" : ""}`} onClick={() => setTab("ai")}><Cpu size={14} /> AI 管理</button>
            <button className={`tab ${tab === "bridge" ? "active" : ""}`} onClick={() => setTab("bridge")}><Cable size={14} /> MT5 测试器桥</button>
          </div>

          <div className="panel-body">
            {tab === "mining" && <FactorMiningPanel onNotify={notify} />}
            {tab === "bridge" && <BridgePanel onNotify={notify} />}
            {tab === "factors" && (
              <div className="sub-tabs">
                <button className={`sub-tab ${subTab === "mark" ? "active" : ""}`} onClick={() => setSubTab("mark")}>形态标注</button>
                <button className={`sub-tab ${subTab === "backtest" ? "active" : ""}`} onClick={() => setSubTab("backtest")}>回测与优化</button>
                <button className={`sub-tab ${subTab === "manage" ? "active" : ""}`} onClick={() => setSubTab("manage")}>因子管理</button>
              </div>
            )}
            {tab === "factors" && subTab === "mark" && (
              <>
                {!draft ? (
                  <div className="empty">尚未生成因子</div>
                ) : (
                  <div className="tool-block">
                    <h3><Sparkles size={13} /> 逆向因子草稿</h3>
                    <p className="factor-desc">{draft.description}</p>
                    <div className="metric-grid" style={{ marginBottom: 10 }}>
                      <div className="metric"><div className="label">沙盒检查</div><div className={`value ${draft.sandbox?.ok ? "good" : draft.sandbox ? "bad" : ""}`}>{draft.sandbox?.ok ? "通过" : draft.sandbox ? "未通过" : "待校验"}</div></div>
                      <div className="metric"><div className="label">入场信号</div><div className="value">{draft.execution?.entry_count ?? 0}</div></div>
                      <div className="metric"><div className="label">生成模型</div><div className="value" style={{ fontSize: 11 }}>{draft.model}</div></div>
                    </div>
                    <code className="code-block">{draft.code}</code>
                  </div>
                )}
              </>
            )}

            {tab === "factors" && subTab === "backtest" && (
              <>
                <div className="tool-block">
                  <h3><TestTube2 size={13} /> 回测与参数优化</h3>
                    <div className="opt-section-title">数据与执行参数</div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                      <div>
                        <label className="field-label">交易品种（默认跟随框选）</label>
                        <SymbolSearchSelect symbols={symbols} value={backtestSettings.symbol} onChange={(s) => setBacktestSettings((v) => ({ ...v, symbol: s }))} />
                      </div>
                      <div>
                        <label className="field-label">K线周期</label>
                        <select className="form-input" value={backtestSettings.timeframe} onChange={(e) => setBacktestSettings((v) => ({ ...v, timeframe: e.target.value }))}>
                          {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
                        </select>
                      </div>
                      <div>
                        <label className="field-label">开始时间（可空）</label>
                        <input className="form-input" type="datetime-local" value={backtestSettings.startTime} onChange={(e) => setBacktestSettings((v) => ({ ...v, startTime: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">结束时间（可空，默认当前）</label>
                        <input className="form-input" type="datetime-local" value={backtestSettings.endTime} onChange={(e) => setBacktestSettings((v) => ({ ...v, endTime: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">测试K线数量（到当前）</label>
                        <input className="form-input" type="number" min="10" value={backtestSettings.bars} onChange={(e) => setBacktestSettings((v) => ({ ...v, bars: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">入金（初始权益）</label>
                        <input className="form-input" type="number" placeholder="入金" value={backtestSettings.initialEquity} onChange={(e) => setBacktestSettings((v) => ({ ...v, initialEquity: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">杠杆</label>
                        <input className="form-input" type="number" placeholder="杠杆" value={backtestSettings.leverage} onChange={(e) => setBacktestSettings((v) => ({ ...v, leverage: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">执行延迟（毫秒）</label>
                        <input className="form-input" type="number" placeholder="延迟(ms)" value={backtestSettings.delayMs} onChange={(e) => setBacktestSettings((v) => ({ ...v, delayMs: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">滑点（点）</label>
                        <input className="form-input" type="number" min="0" step="0.1" value={backtestSettings.slippage} onChange={(e) => setBacktestSettings((v) => ({ ...v, slippage: e.target.value }))} />
                      </div>
                    </div>
                    <div className="row-actions" style={{ marginTop: 8 }}>
                      <button className="mini-btn primary" disabled={busy || !draft} onClick={handleCustomBacktest}>运行回测</button>
                      <button className="mini-btn primary" disabled={optimizing || !draft} onClick={handleOptimize}>{optimizing ? "优化中..." : "运行优化并回测"}</button>
                    </div>
                  </div>
                  <div className="tool-block">
                      <h3><Sparkles size={13} /> 优化参数设置</h3>
                      <div className="opt-section-title">优化目标与搜索范围（与上方数据共用）</div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                      <div>
                        <label className="field-label">优化目标（评分方式）</label>
                        <select className="form-input" value={optSettings.objective} onChange={(e) => setOptSettings((v) => ({ ...v, objective: e.target.value }))}>
                          <option value="composite">综合评分</option>
                          <option value="win_rate">最高胜率</option>
                          <option value="profit">最高盈利</option>
                        </select>
                      </div>
                      <div>
                        <label className="field-label">最大迭代次数</label>
                        <input className="form-input" type="number" min="1" value={optSettings.maxIterations} onChange={(e) => setOptSettings((v) => ({ ...v, maxIterations: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">提前停止轮次（连续无改进）</label>
                        <input className="form-input" type="number" min="0" value={optSettings.earlyStop} onChange={(e) => setOptSettings((v) => ({ ...v, earlyStop: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">样本外验证（walk-forward）</label>
                        <select className="form-input" value={optSettings.walkForward} onChange={(e) => setOptSettings((v) => ({ ...v, walkForward: e.target.value }))}>
                          <option value="1">启用</option>
                          <option value="0">关闭</option>
                        </select>
                      </div>
                      <div>
                        <label className="field-label">滚动样本外段数</label>
                        <input className="form-input" type="number" min="1" value={optSettings.walkForwardFolds} onChange={(e) => setOptSettings((v) => ({ ...v, walkForwardFolds: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">最低交易数（不足自动扩周期/K线）</label>
                        <input className="form-input" type="number" min="0" value={optSettings.minTrades} onChange={(e) => setOptSettings((v) => ({ ...v, minTrades: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">参数复杂度惩罚（每参数）</label>
                        <input className="form-input" type="number" min="0" step="0.001" value={optSettings.complexityPenalty} onChange={(e) => setOptSettings((v) => ({ ...v, complexityPenalty: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">最大参数偏离 %</label>
                        <input className="form-input" type="number" min="0" max="100" value={optSettings.maxDeviation} onChange={(e) => setOptSettings((v) => ({ ...v, maxDeviation: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">形态保真度权重 %</label>
                        <input className="form-input" type="number" min="0" max="100" value={optSettings.fidelityWeight} onChange={(e) => setOptSettings((v) => ({ ...v, fidelityWeight: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">束搜索保留候选数</label>
                        <input className="form-input" type="number" min="1" value={optSettings.beamWidth} onChange={(e) => setOptSettings((v) => ({ ...v, beamWidth: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">组合探索轮数</label>
                        <input className="form-input" type="number" min="0" value={optSettings.searchRounds} onChange={(e) => setOptSettings((v) => ({ ...v, searchRounds: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">目标胜率 %（留空=不限）</label>
                        <input className="form-input" type="number" min="0" value={optSettings.targetWinRate} onChange={(e) => setOptSettings((v) => ({ ...v, targetWinRate: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">目标收益 %（留空=不限）</label>
                        <input className="form-input" type="number" value={optSettings.targetReturn} onChange={(e) => setOptSettings((v) => ({ ...v, targetReturn: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">目标盈亏比（留空=不限）</label>
                        <input className="form-input" type="number" min="0" step="0.1" value={optSettings.targetProfitFactor} onChange={(e) => setOptSettings((v) => ({ ...v, targetProfitFactor: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">最大回撤 %（留空=不限）</label>
                        <input className="form-input" type="number" min="0" value={optSettings.targetMaxDrawdown} onChange={(e) => setOptSettings((v) => ({ ...v, targetMaxDrawdown: e.target.value }))} />
                      </div>
                      <div style={{ gridColumn: "1 / -1" }}>
                        <label className="field-label">{'参数范围 JSON（留空=按当前参数自动生成，格式如 {"lookback":{"min":10,"max":40,"step":5}}）'}</label>
                        <textarea className="form-input modal-textarea" value={optSettings.paramRanges} onChange={(e) => setOptSettings((v) => ({ ...v, paramRanges: e.target.value }))} />
                      </div>
                    </div>
                    {optimizeResult && (
                      <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 8 }}>
                        <p className={`factor-desc ${optimizeResult.targets_reached ? "pnl-pos" : "pnl-neg"}`}>
                          {optimizeResult.targets_reached ? "目标已达成" : "未达到目标，已返回最接近参数"} | 最优评分 {optimizeResult.score.toFixed(4)} | {optimizeResult.stop_reason}
                        </p>
                        {optimizeResult.auto_expand_message && <p className="factor-desc">{optimizeResult.auto_expand_message}</p>}
                        <p className="factor-desc">
                          目标：胜率 ≥{optimizeResult.targets.win_rate_pct || "不限"}% | 收益 ≥{optimizeResult.targets.total_return_pct || "不限"}% | 盈亏比 ≥{optimizeResult.targets.profit_factor || "不限"} | 回撤 ≤{optimizeResult.targets.max_drawdown_pct}% | 达标组数 {optimizeResult.satisfied_trials}
                        </p>
                      <code className="code-block">最佳参数：{JSON.stringify(optimizeResult.best_params, null, 2)}</code>
                        <div className="metric-grid">
                          <div className="metric"><div className="label">胜率</div><div className="value">{optimizeResult.best_metrics.win_rate_pct.toFixed(1)}%</div></div>
                          <div className="metric"><div className="label">形态保真度</div><div className="value">{(optimizeResult.fidelity ?? 0) * 100 >= 0 ? `${((optimizeResult.fidelity ?? 0) * 100).toFixed(0)}%` : "--"}</div></div>
                        <div className="metric"><div className="label">总收益</div><div className={`value ${optimizeResult.best_metrics.total_return_pct >= 0 ? "good" : "bad"}`}>{optimizeResult.best_metrics.total_return_pct.toFixed(2)}%</div></div>
                        <div className="metric"><div className="label">盈亏比</div><div className="value">{optimizeResult.best_metrics.profit_factor.toFixed(2)}</div></div>
                          <div className="metric"><div className="label">最大回撤</div><div className="value bad">-{optimizeResult.best_metrics.max_drawdown_pct.toFixed(2)}%</div></div>
                        </div>
                        {optimizeResult.test_metrics && (
                          <div className="metric-grid">
                            <div className="metric"><div className="label">样本外胜率</div><div className="value">{optimizeResult.test_metrics.win_rate_pct.toFixed(1)}%</div></div>
                            <div className="metric"><div className="label">样本外收益</div><div className={`value ${optimizeResult.test_metrics.total_return_pct >= 0 ? "good" : "bad"}`}>{optimizeResult.test_metrics.total_return_pct.toFixed(2)}%</div></div>
                            <div className="metric"><div className="label">样本外盈亏比</div><div className="value">{optimizeResult.test_metrics.profit_factor.toFixed(2)}</div></div>
                            <div className="metric"><div className="label">样本外回撤</div><div className="value bad">-{optimizeResult.test_metrics.max_drawdown_pct.toFixed(2)}%</div></div>
                          </div>
                        )}
                        {optimizeResult.walk_forward_metrics && optimizeResult.walk_forward_metrics.length > 0 && (
                          <p className="factor-desc">
                            滚动样本外验证：{optimizeResult.walk_forward_metrics.length} 段 | 平均胜率 {(() => { const arr = optimizeResult.walk_forward_metrics ?? []; return arr.length ? (arr.reduce((s, m) => s + m.win_rate_pct, 0) / arr.length).toFixed(1) : "0"; })()}% | 平均收益 {(() => { const arr = optimizeResult.walk_forward_metrics ?? []; return arr.length ? (arr.reduce((s, m) => s + m.total_return_pct, 0) / arr.length).toFixed(2) : "0"; })()}%
                          </p>
                        )}
                      <div className="row-actions">
                        <button className="mini-btn primary" disabled={busy} onClick={handleApplyBestParams}>应用最佳参数并回测</button>
                      </div>
                      {optimizeResult.trials.length > 0 && (
                        <div className="factor-list">
                          {optimizeResult.trials.slice(0, 5).map((t, i) => (
                            <div className="trade-row" key={i}>
                              <div><div className="t-label">参数</div><div className="t-value">{JSON.stringify(t.params)}</div></div>
                              <div><div className="t-label">评分</div><div className="t-value">{t.score.toFixed(4)}</div></div>
                              <div className="badge active">胜率 {t.metrics.win_rate_pct.toFixed(0)}%</div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
                {!backtest || !metric ? (
                  <div className="empty">点击[运行历史回测]查看绩效指标与逐笔交易明细。</div>
                ) : (
                  <>
                    <div className="tool-block">
                      <h3><TestTube2 size={13} /> 回测绩效</h3>
                      <div className="metric-grid">
                        <div className="metric"><div className="label">总收益率</div><div className={`value ${metric.total_return_pct >= 0 ? "good" : "bad"}`}>{metric.total_return_pct.toFixed(2)}%</div></div>
                        <div className="metric"><div className="label">夏普比率</div><div className="value">{metric.sharpe.toFixed(2)}</div></div>
                        <div className="metric"><div className="label">最大回撤</div><div className="value bad">-{metric.max_drawdown_pct.toFixed(2)}%</div></div>
                        <div className="metric"><div className="label">胜率</div><div className="value">{metric.win_rate_pct.toFixed(1)}%</div></div>
                        <div className="metric"><div className="label">盈亏比</div><div className="value">{metric.profit_factor.toFixed(2)}</div></div>
                        <div className="metric"><div className="label">交易次数</div><div className="value">{metric.num_trades}</div></div>
                      </div>
                    </div>
                    <div className="tool-block">
                      <h3><Layers size={13} /> 逐笔交易</h3>
                      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        {backtest.trades.slice(-8).reverse().map((t, i) => (
                          <div key={i} className="trade-row">
                            <div><div className="t-label">{t.side === "long" ? "多头" : "空头"} 入场</div><div className="t-value">{new Date(t.entry_time).toLocaleString()}</div></div>
                            <div><div className="t-label">出场原因</div><div className="t-value">{t.exit_reason}</div></div>
                            <div className={t.pnl_pct && t.pnl_pct >= 0 ? "pnl-pos" : "pnl-neg"}>
                              <div className="t-label">盈亏</div>
                              <div className="t-value">{t.pnl?.toFixed(2)}</div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </>
                )}
              </>
            )}

            {tab === "factors" && subTab === "manage" && (
              <>
                <div className="row-actions" style={{ marginBottom: 8 }}>
                  <button className="mini-btn danger" onClick={handleClearFactors}><Trash2 size={12} /> 清空因子库</button>
                </div>
                <div className="factor-list">
                  {factors.length === 0 && <div className="empty">因子库为空，生成并存入首个因子后即可在这里管理。</div>}
                  {factors.map((f) => (
                    <div className="factor-card" key={f.id}>
                      <div className="factor-card-head">
                        <h4>{f.name}</h4>
                        <span className={`badge ${f.status}`}>{f.status === "active" ? "启用" : "禁用"}</span>
                      </div>
                      <p>{f.description}</p>
                      {f.sl_tp_strategy && Object.keys(f.sl_tp_strategy).length > 0 && (
                        <p className="factor-desc" style={{ marginTop: 4 }}>
                          策略：{String(f.sl_tp_strategy.stop_method ?? "atr")} 止损 {String(f.sl_tp_strategy.stop_atr_mult ?? "--")} / {String(f.sl_tp_strategy.take_method ?? "atr")} 止盈 {String(f.sl_tp_strategy.take_atr_mult ?? "--")}
                          {f.sl_tp_strategy.trailing_enabled ? " | 移动保护已开启" : ""}
                        </p>
                      )}
                      <div className="row-actions">
                        <button className="mini-btn" onClick={() => viewFactor(f)}><Eye size={12} /> 查看因子逻辑</button>
                        <button className="mini-btn" onClick={() => handleSandboxTest(f)}><ShieldCheck size={12} /> 沙盒测试</button>
                        <button className="mini-btn" onClick={() => setEditingFactor(f)}><Pencil size={12} /> 编辑</button>
                        <button className="mini-btn danger" onClick={() => handleDeleteFactor(f)}><Trash2 size={12} /> 删除</button>
                        {f.status === "active" ? (
                          <button className="mini-btn" onClick={() => disableFactor(f)}><StopCircle size={12} /> 禁用此因子</button>
                        ) : (
                          <button className="mini-btn primary" onClick={async () => { await api.enableFactor(f.id); await loadFactors(); notify("success", "因子已启用"); }}><CheckCircle2 size={12} /> 启用因子</button>
                        )}
                          <button className="mini-btn primary" onClick={() => { setSelectedFactor(f); setTab("trading"); setSubTab("signals"); }}><Activity size={12} /> 模拟盘</button>
                      </div>
                      {sandboxResults[f.id] && (
                        <p className={`factor-desc ${sandboxResults[f.id].summary.includes("通过") ? "pnl-pos" : "pnl-neg"}`}>
                          {sandboxResults[f.id].summary}
                          {sandboxResults[f.id].message && `：${sandboxResults[f.id].message}`}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}

            {tab === "trading" && (
              <div className="sub-tabs">
                <button className={`sub-tab ${subTab === "signals" ? "active" : ""}`} onClick={() => setSubTab("signals")}>信号与执行</button>
                <button className={`sub-tab ${subTab === "mt5" ? "active" : ""}`} onClick={() => setSubTab("mt5")}>实盘下单</button>
                <button className={`sub-tab ${subTab === "sltp" ? "active" : ""}`} onClick={() => setSubTab("sltp")}>止损止盈</button>
              </div>
            )}
            {tab === "trading" && subTab === "signals" && (
              <>
                <div className="tool-block">
                  <h3><Activity size={13} /> 市场环境</h3>
                  <div className="metric-grid">
                    <div className="metric"><div className="label">环境分类</div><div className="value" style={{ fontSize: 12 }}>{scan?.regime ?? "未扫描"}</div></div>
                    <div className="metric"><div className="label">扫描因子</div><div className="value">{scan?.scanned ?? 0}</div></div>
                    <div className="metric"><div className="label">候选信号</div><div className="value">{scan?.candidates.length ?? 0}</div></div>
                  </div>
                  {scan?.regime_detail && <p className="factor-desc">{scan.regime_detail}</p>}
                  <div className="row-actions">
                    <button className="mini-btn" onClick={handleScan}><RefreshCw size={12} /> 立即扫描</button>
                    <button className="mini-btn primary" disabled={!selectedFactor} onClick={handleStartPaper}><Play size={12} /> 启动模拟盘</button>
                    <button className="mini-btn" onClick={handleStopPaper}><StopCircle size={12} /> 暂停模拟盘</button>
                  </div>
                </div>
                {systemState?.risk_tripped && (
                  <div style={{ padding: 10, borderRadius: 6, border: "1px solid var(--red)", background: "rgba(239,98,98,0.12)", color: "var(--red)", fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}>
                    <Shield size={14} /> 风控熔断已触发，实盘匹配已自动暂停：{systemState.risk_reasons?.join("；") || "单日亏损或回撤超限"}
                  </div>
                )}
                {marketAnalysis && (
                  <div className="tool-block">
                    <h3><BarChart3 size={13} /> 行情分析</h3>
                    <div className="metric-grid">
                      <div className="metric"><div className="label">行情标签</div><div className="value" style={{ fontSize: 11 }}>{String(marketAnalysis.label ?? "--")}</div></div>
                      <div className="metric"><div className="label">趋势</div><div className="value">{String(marketAnalysis.trend ?? "--")}</div></div>
                      <div className="metric"><div className="label">波动</div><div className="value">{String(marketAnalysis.volatility ?? "--")}</div></div>
                      <div className="metric"><div className="label">ADX</div><div className="value">{String(marketAnalysis.adx ?? "--")}</div></div>
                      <div className="metric"><div className="label">ATR 分位</div><div className="value">{String(marketAnalysis.atr_percentile ?? "--")}%</div></div>
                      <div className="metric"><div className="label">量能</div><div className="value">{String(marketAnalysis.volume_state ?? "--")}</div></div>
                      <div className="metric"><div className="label">位置</div><div className="value">{String(marketAnalysis.position_state ?? "--")}</div></div>
                      <div className="metric"><div className="label">大周期</div><div className="value">{String(marketAnalysis.macro_trend ?? "--")}</div></div>
                      <div className="metric"><div className="label">多周期共识</div><div className="value">{String((marketAnalysis.multi_timeframe as Record<string, unknown> | undefined)?.consensus ?? "--")}</div></div>
                      <div className="metric"><div className="label">多周期阶梯</div><div className="value" style={{ fontSize: 11 }}>{String((marketAnalysis.multi_timeframe as Record<string, unknown> | undefined)?.summary ?? "--")}</div></div>
                      <div className="metric"><div className="label">环境评分</div><div className="value">{String(marketAnalysis.environment_score ?? "--")}</div></div>
                    </div>
                    <p className="factor-desc">{String(marketAnalysis.detail ?? "")}</p>
                  </div>
                )}
                <div className="tool-block">
                  <h3><Bot size={13} /> AI 自动执行</h3>
                  <div className="metric-grid">
                    <div className="metric"><div className="label">执行状态</div><div className={`value ${systemState?.executor_enabled ? "good" : "bad"}`}>{systemState?.executor_enabled ? "已开启" : "已关闭"}</div></div>
                    <div className="metric"><div className="label">匹配品种/周期</div><div className="value" style={{ fontSize: 11 }}>{systemState?.matcher_symbol ?? "--"} / {systemState?.matcher_timeframe ?? "--"}</div></div>
                    <div className="metric"><div className="label">最近执行</div><div className="value">{systemState?.last_executions.length ?? 0}</div></div>
                    <div className="metric"><div className="label">最近失败</div><div className={`value ${systemState && systemState.executor_failures.length ? "bad" : "good"}`}>{systemState?.executor_failures.length ?? 0}</div></div>
                  </div>
                  <div className="row-actions">
                    <button className="mini-btn primary" onClick={handleExecutorToggle}>{systemState?.executor_enabled ? "关闭 AI 自动执行" : "开启 AI 自动执行"}</button>
                    <button className="mini-btn" onClick={handleExecutorScan}><RefreshCw size={12} /> 立即扫描并执行</button>
                  </div>
                  {systemState && systemState.last_executions.length > 0 && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8 }}>
                      {systemState.last_executions.slice(0, 5).map((ex) => (
                        <div className="trade-row" key={ex.order_id}>
                          <div><div className="t-label">{ex.factor_name}</div><div className="t-value">{ex.symbol} {ex.side === "buy" ? "买入" : "卖出"} {ex.lots} 手</div></div>
                          <div><div className="t-label">置信度</div><div className="t-value">{(ex.confidence * 100).toFixed(0)}%</div></div>
                          <div><div className="t-label">止损/止盈</div><div className="t-value">{ex.stop ? ex.stop.toFixed(5) : "--"} / {ex.take ? ex.take.toFixed(5) : "--"}</div></div>
                          <div className="badge active">#{ex.order_id.slice(0, 6)}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                {systemState?.last_scan && systemState.last_scan.pattern_matches && systemState.last_scan.pattern_matches.length > 0 && (
                  <div className="tool-block">
                    <h3><Shield size={13} /> 相似形态</h3>
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      {systemState.last_scan.pattern_matches.slice(0, 5).map((m) => (
                        <div className="trade-row" key={m.case_id}>
                          <div>
                            <div className="t-label">{m.pattern_type || m.factor_name || "未关联因子"}</div>
                            <div className="t-value">相似度 {(m.similarity * 100).toFixed(0)}% | 置信度 {((m.recognition_confidence ?? 0) * 100).toFixed(0)}% | 样本 {m.sample_count ?? 0}</div>
                          </div>
                          <div>
                            <div className="t-label">建议止损/止盈</div>
                            <div className="t-value">
                              {m.learned.suggested_stop_pct ? `${(Number(m.learned.suggested_stop_pct) * 100).toFixed(2)}% / ${(Number(m.learned.suggested_take_pct) * 100).toFixed(2)}%` : "--"}
                            </div>
                          </div>
                          <div className="badge active">胜率 {m.win_rate ?? 0}% | 期望 {m.expected_value ?? 0} | 持仓 {Number(m.learned.avg_hold_bars || 0).toFixed(1)} 根</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                <div className="tool-block">
                  <h3><Shield size={13} /> 激活信号</h3>
                  {(!scan || scan.candidates.length === 0) ? (
                    <div className="empty">暂无激活信号，可先点击[立即扫描]。</div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      {scan.candidates.map((c) => (
                        <div className="trade-row" key={c.factor_id}>
                          <div><div className="t-label">{c.factor_name}</div><div className="t-value">{c.signal === "long" ? "多头" : c.signal === "short" ? "空头" : "无信号"}</div></div>
                          <div><div className="t-label">置信度</div><div className="t-value">{(c.confidence * 100).toFixed(1)}%</div></div>
                          <div className="badge active">{c.regime}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div className="tool-block">
                  <h3><Wallet size={13} /> 模拟盘任务</h3>
                  {paperJobs.length === 0 ? (
                    <div className="empty">暂无模拟盘任务。</div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      {paperJobs.map((j) => (
                        <div className="trade-row" key={String(j.id)}>
                          <div><div className="t-label">因子</div><div className="t-value">{String(j.factor_name)}</div></div>
                          <div><div className="t-label">状态</div><div className="t-value">{j.status === "running" ? "运行中" : "已暂停"}</div></div>
                          <div className={Number(j.equity) >= 10000 ? "pnl-pos" : "pnl-neg"}>{Number(j.equity).toFixed(2)}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}
            {tab === "trading" && subTab === "sltp" && <SltpPolicyPanel onNotify={notify} />}
            {tab === "trading" && subTab === "mt5" && (
              <Mt5Panel symbol={symbol} timeframe={timeframe} />
            )}
            {tab === "replay" && subTab === "replay" && (
              <>
                <div className="tool-block">
                  <h3><BarChart3 size={13} /> 订单胜率与盈亏比统计</h3>
                  <div className="row-actions" style={{ flexWrap: "wrap", gap: 6 }}>
                    <select className="form-input" style={{ width: 140 }} value={tradeFilter.account_id} onChange={(e) => setTradeFilter((v) => ({ ...v, account_id: e.target.value }))}>
                      <option value="">全部账户</option>
                      {Array.from(new Set([...(tradeStats?.accounts ?? []), ...(systemState?.account_login != null ? [String(systemState.account_login)] : [])])).map((acc) => <option key={acc} value={acc}>账户 {acc}</option>)}
                    </select>
                    <SymbolSearchSelect symbols={["", ...symbols]} value={tradeFilter.symbol} onChange={(s) => setTradeFilter((v) => ({ ...v, symbol: s }))} placeholder="搜索全部品种" />
                    <select className="form-input" style={{ width: 100 }} value={tradeFilter.side} onChange={(e) => setTradeFilter((v) => ({ ...v, side: e.target.value }))}>
                      <option value="">全部方向</option>
                      <option value="long">多头</option>
                      <option value="short">空头</option>
                    </select>
                    <select className="form-input" style={{ width: 110 }} value={tradeFilter.quality} onChange={(e) => setTradeFilter((v) => ({ ...v, quality: e.target.value }))}>
                      <option value="">全部质量</option>
                      <option value="ideal">理想</option>
                      <option value="general">一般</option>
                      <option value="poor">不理想</option>
                    </select>
                    <select className="form-input" style={{ width: 100 }} value={tradeFilter.fixed_lots} onChange={(e) => setTradeFilter((v) => ({ ...v, fixed_lots: e.target.value }))}>
                      <option value="">全部手数</option>
                      <option value="0.01">0.01 手</option>
                      <option value="0.1">0.1 手</option>
                      <option value="1">1 手</option>
                    </select>
                    <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted)" }}>
                      <input type="checkbox" checked={tradeFilter.normalizeToOneLot} onChange={(e) => setTradeFilter((v) => ({ ...v, normalizeToOneLot: e.target.checked }))} />
                      换算为 1 手
                    </label>
                    <button className="mini-btn primary" onClick={loadTradeStats}><RefreshCw size={12} /> 统计</button>
                  </div>
                  {tradeStats && (
                    <>
                      <div className="metric-grid">
                        <div className="metric"><div className="label">订单数</div><div className="value">{tradeStats.count}</div></div>
                        <div className="metric"><div className="label">胜率</div><div className="value">{tradeStats.win_rate_pct.toFixed(1)}%</div></div>
                        <div className="metric"><div className="label">盈亏比</div><div className="value">{tradeStats.profit_factor == null ? "--" : tradeStats.profit_factor.toFixed(2)}</div></div>
                        <div className="metric"><div className="label">总盈亏</div><div className={`value ${tradeStats.total_pnl >= 0 ? "good" : "bad"}`}>{tradeStats.total_pnl.toFixed(2)}</div></div>
                        <div className="metric"><div className="label">平均盈亏</div><div className={`value ${tradeStats.avg_pnl >= 0 ? "good" : "bad"}`}>{tradeStats.avg_pnl.toFixed(2)}</div></div>
                        <div className="metric"><div className="label">平均持仓</div><div className="value">{tradeStats.avg_hold_minutes.toFixed(0)} 分钟</div></div>
                      </div>
                      {tradeStats.by_lots.length > 0 && (
                        <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 8 }}>
                          {tradeStats.by_lots.map((b) => (
                            <div className="trade-row" key={b.lots} style={{ flexWrap: "wrap" }}>
                              <div><div className="t-label">手数</div><div className="t-value">{b.lots}</div></div>
                              <div><div className="t-label">订单数</div><div className="t-value">{b.count}</div></div>
                              <div><div className="t-label">胜率</div><div className="t-value">{b.win_rate_pct.toFixed(1)}%</div></div>
                              <div><div className="t-label">盈亏比</div><div className="t-value">{b.profit_factor == null ? "--" : b.profit_factor.toFixed(2)}</div></div>
                              <div><div className={`t-label ${b.total_pnl >= 0 ? "pnl-pos" : "pnl-neg"}`}>总盈亏</div><div className="t-value">{b.total_pnl.toFixed(2)}</div></div>
                            </div>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
                <div className="tool-block">
                  <h3><Sparkles size={13} /> AI 学习设置</h3>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                    <div>
                      <label className="field-label">交易品种</label>
                      <SymbolSearchSelect symbols={["", ...symbols]} value={learnSettings.symbol} onChange={(s) => setLearnSettings((v) => ({ ...v, symbol: s }))} placeholder="自动选择/搜索品种" />
                    </div>
                    <div>
                      <label className="field-label">K线周期</label>
                      <select className="form-input" value={learnSettings.timeframe} onChange={(e) => setLearnSettings((v) => ({ ...v, timeframe: e.target.value }))}>
                        {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="field-label">学习数据范围</label>
                      <select className="form-input" value={learnSettings.period_mode} onChange={(e) => setLearnSettings((v) => ({ ...v, period_mode: e.target.value }))}>
                        <option value="all">全部历史</option>
                        <option value="recent_months">近半年</option>
                        <option value="recent_months_12">近一年</option>
                        <option value="recent_bars">最近N根K线</option>
                        <option value="custom">自定义时间</option>
                      </select>
                    </div>
                    {learnSettings.period_mode === "recent_months_12" && (
                      <div>
                        <label className="field-label">近一年</label>
                        <input className="form-input" value="近一年" readOnly />
                      </div>
                    )}
                    {learnSettings.period_mode === "recent_months" && (
                      <div>
                        <label className="field-label">近半年</label>
                        <input className="form-input" value="近半年" readOnly />
                      </div>
                    )}
                    {learnSettings.period_mode === "recent_bars" && (
                      <div>
                        <label className="field-label">最近K线根数</label>
                        <input className="form-input" type="number" min="1" value={learnSettings.recent_bars} onChange={(e) => setLearnSettings((v) => ({ ...v, recent_bars: e.target.value }))} />
                      </div>
                    )}
                    {learnSettings.period_mode === "custom" && (
                      <>
                        <div>
                          <label className="field-label">开始时间</label>
                          <input className="form-input" type="datetime-local" value={learnSettings.start_time} onChange={(e) => setLearnSettings((v) => ({ ...v, start_time: e.target.value }))} />
                        </div>
                        <div>
                          <label className="field-label">结束时间</label>
                          <input className="form-input" type="datetime-local" value={learnSettings.end_time} onChange={(e) => setLearnSettings((v) => ({ ...v, end_time: e.target.value }))} />
                        </div>
                      </>
                    )}
                    <div>
                      <label className="field-label">最少验证交易笔数</label>
                      <input className="form-input" type="number" min="1" value={learnSettings.min_validation_trades} onChange={(e) => setLearnSettings((v) => ({ ...v, min_validation_trades: e.target.value }))} />
                    </div>
                    <div>
                      <label className="field-label">最低胜率 %</label>
                      <input className="form-input" type="number" min="0" max="100" value={learnSettings.min_win_rate_pct} onChange={(e) => setLearnSettings((v) => ({ ...v, min_win_rate_pct: e.target.value }))} />
                    </div>
                    <div>
                      <label className="field-label">最低盈亏比</label>
                      <input className="form-input" type="number" min="0" step="0.1" value={learnSettings.min_profit_factor} onChange={(e) => setLearnSettings((v) => ({ ...v, min_profit_factor: e.target.value }))} />
                    </div>
                    <div>
                      <label className="field-label">样本外最少笔数</label>
                      <input className="form-input" type="number" min="0" value={learnSettings.min_oos_trades} onChange={(e) => setLearnSettings((v) => ({ ...v, min_oos_trades: e.target.value }))} />
                    </div>
                  </div>
                </div>
                <div className="tool-block">
                  <h3><FileClock size={13} /> 交易记录（最高浮盈 / 最低浮亏）</h3>
                  <div className="row-actions">
                    <button className="mini-btn primary" disabled={backfilling} onClick={handleBackfillExtremes}>
                      {backfilling ? "回填中..." : "回填历史最高/最低盈亏"}
                    </button>
                    <button className="mini-btn primary" disabled={syncingTrades} onClick={handleSyncMt5Trades}>
                      {syncingTrades ? "同步中..." : "同步 MT5 历史成交"}
                    </button>
                    <button className="mini-btn" disabled={migratingAccount} onClick={handleMigrateAccount}>
                      {migratingAccount ? "迁移中..." : "历史订单迁移到当前账户"}
                    </button>
                    <button className="mini-btn" onClick={loadTrades}><RefreshCw size={12} /> 刷新</button>
                  </div>
                  <div className="row-actions" style={{ flexWrap: "wrap", gap: 6, marginTop: 6 }}>
                    <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted)" }}>
                      <input
                        type="checkbox"
                        checked={tradeRecords.slice(0, 50).length > 0 && tradeRecords.slice(0, 50).every((t) => selectedTradeIds.includes(String(t.id)))}
                        onChange={(e) => {
                          const visible = tradeRecords.slice(0, 50).map((t) => String(t.id));
                          setSelectedTradeIds(e.target.checked ? visible : selectedTradeIds.filter((id) => !visible.includes(id)));
                        }}
                      />
                      全选当前列表
                    </label>
                    <span className="factor-desc" style={{ margin: 0 }}>已选 {selectedTradeIds.length} 笔</span>
                    <button className="mini-btn primary" disabled={learning || selectedTradeIds.length < 3} onClick={handleLearnTrades}>
                      <Bot size={12} /> {learning ? "AI 学习中..." : "AI 学习生成因子"}
                    </button>
                    <button className="mini-btn danger" disabled={selectedTradeIds.length === 0 || busy} onClick={handleDeleteSelectedTrades}>
                      <Trash2 size={12} /> 删除所选
                    </button>
                  </div>
                  {tradeRecords.length === 0 ? (
                    <div className="empty">暂无交易记录</div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8 }}>
                      {tradeRecords.slice(0, 50).map((t) => {
                        const side = t.side === "long" || t.side === "buy" ? "多" : "空";
                        const peak = Number(t.peak_pnl ?? NaN);
                        const trough = Number(t.trough_pnl ?? NaN);
                        return (
                          <div className="trade-row" key={String(t.id)} style={{ flexWrap: "wrap" }}>
                            <div style={{ display: "flex", alignItems: "center" }}>
                              <input
                                type="checkbox"
                                checked={selectedTradeIds.includes(String(t.id))}
                                onChange={(e) => {
                                  const id = String(t.id);
                                  setSelectedTradeIds((prev) => e.target.checked ? [...prev, id] : prev.filter((x) => x !== id));
                                }}
                              />
                            </div>
                            <div>
                              <div className="t-label">{String(t.symbol)} {side} | {Number(t.lots).toFixed(2)} 手</div>
                              <div className="t-value">
                                入 {Number(t.entry_price).toFixed(5)} → 出 {t.exit_price ? Number(t.exit_price).toFixed(5) : "--"}
                              </div>
                            </div>
                            <div>
                              <div className="t-label">实际盈亏</div>
                              <div className={`t-value ${Number(t.pnl) >= 0 ? "pnl-pos" : "pnl-neg"}`}>
                                {Number(t.pnl).toFixed(2)} {t.exit_reason ? ` | ${t.exit_reason}` : ""}
                              </div>
                            </div>
                            <div>
                              <div className="t-label">最高浮盈</div>
                              <div className="t-value pnl-pos">
                                {Number.isFinite(peak) ? `${peak.toFixed(2)} @ ${Number(t.peak_price).toFixed(5)}` : "--"}
                              </div>
                            </div>
                            <div>
                              <div className="t-label">最低浮亏</div>
                              <div className="t-value pnl-neg">
                                {Number.isFinite(trough) ? `${trough.toFixed(2)} @ ${Number(t.trough_price).toFixed(5)}` : "--"}
                              </div>
                            </div>
                            <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0, gridColumn: "1 / -1" }}>
                              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                                <label style={{ fontSize: 11, color: "var(--muted)" }}>质量</label>
                                <select
                                  className="form-input"
                                  style={{ width: 96, height: 26 }}
                                  value={String(t.quality || "general")}
                                  onChange={(e) => handleSetTradeQuality(String(t.id), e.target.value as "ideal" | "general" | "poor")}
                                >
                                  <option value="ideal">理想</option>
                                  <option value="general">一般</option>
                                  <option value="poor">不理想</option>
                                </select>
                                <button className="mini-btn" onClick={() => openOrderChatChoice(t)}><Wand2 size={12} /> AI 优化</button>
                                <button className="mini-btn" onClick={() => handleOptimizeTrade(String(t.id))}><Activity size={12} /> 订单优化</button>
                              </div>
                              {(() => {
                                const o = tradeOpts[String(t.id)] || (t.optimization as Record<string, unknown> | undefined);
                                if (!o || !o.ok || !o.best_params) return null;
                                const bp = o.best_params as Record<string, unknown>;
                                return (
                                  <div className="factor-desc" style={{ margin: 0 }}>
                                    优化后 {Number(o.optimized_pnl).toFixed(2)}（原 {Number(o.current_pnl).toFixed(2)}）| 止损 {String(bp.stop_atr_mult)} ATR / 止盈 {String(bp.take_atr_mult)} ATR
                                  </div>
                                );
                              })()}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
                <div className="tool-block">
                  <h3><FileText size={13} /> AI 中文复盘</h3>
                  <div className="row-actions">
                    <button className="mini-btn primary" onClick={handleReplay}><FileText size={12} /> 导出中文复盘报告</button>
                  </div>
                  {replay && <p className="factor-desc">综合评分：{replay.score} / 100，因子：{replay.factor_name}</p>}
                </div>
                {replay && <pre className="replay-doc">{replay.markdown}</pre>}
              </>
            )}

            {tab === "replay" && subTab === "stats" && (
              <div className="tool-block">
                <h3><BarChart3 size={13} /> 因子统计</h3>
                <div className="row-actions" style={{ flexWrap: "wrap", gap: 6 }}>
                  <select className="form-input" style={{ width: 180 }} value={factorStatFilter.factor_id} onChange={(e) => setFactorStatFilter((v) => ({ ...v, factor_id: e.target.value }))}>
                    <option value="">全部因子</option>
                    {(factorStats?.factors || []).map((f) => <option key={String(f.factor_id)} value={String(f.factor_id)}>{String(f.factor_name)}</option>)}
                  </select>
                  <select className="form-input" style={{ width: 120 }} value={factorStatFilter.symbol} onChange={(e) => setFactorStatFilter((v) => ({ ...v, symbol: e.target.value }))}>
                    <option value="">全部品种</option>
                    {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                  <select className="form-input" style={{ width: 110 }} value={factorStatFilter.timeframe} onChange={(e) => setFactorStatFilter((v) => ({ ...v, timeframe: e.target.value }))}>
                    <option value="">全部周期</option>
                    {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <select className="form-input" style={{ width: 100 }} value={factorStatFilter.status} onChange={(e) => setFactorStatFilter((v) => ({ ...v, status: e.target.value }))}>
                    <option value="">全部状态</option>
                    <option value="active">启用</option>
                    <option value="disabled">禁用</option>
                  </select>
                  <button className="mini-btn primary" onClick={() => loadFactorStats()}><RefreshCw size={12} /> 统计</button>
                </div>
                {factorStats && (
                  <>
                    <div className="metric-grid">
                      <div className="metric"><div className="label">因子数量</div><div className="value">{String(factorStats.overall.count ?? 0)}</div></div>
                      <div className="metric"><div className="label">已启用</div><div className="value">{factorStats.factors.filter((f) => f.status === "active").length}</div></div>
                      <div className="metric"><div className="label">已禁用</div><div className="value">{factorStats.factors.filter((f) => f.status === "disabled").length}</div></div>
                    </div>
                    <div className="factor-list" style={{ marginTop: 8 }}>
                      {factorStats.factors.map((f) => {
                        const bt = (f.backtest_stats as Record<string, unknown>) || {};
                        const strategy = (f.sl_tp_strategy as Record<string, unknown>) || {};
                        return (
                          <div className="factor-card" key={String(f.factor_id)}>
                            <div className="factor-card-head"><h4>{String(f.factor_name)}</h4><span className="badge active">{String(f.symbol)} {String(f.timeframe)}</span></div>
                            <div className="metric-grid">
                              <div className="metric"><div className="label">回测胜率</div><div className="value">{bt.win_rate_pct == null ? "--" : `${Number(bt.win_rate_pct).toFixed(1)}%`}</div></div>
                              <div className="metric"><div className="label">盈亏比</div><div className="value">{bt.profit_factor == null ? "--" : Number(bt.profit_factor).toFixed(2)}</div></div>
                              <div className="metric"><div className="label">总收益</div><div className={`value ${Number(bt.total_return_pct ?? 0) >= 0 ? "good" : "bad"}`}>{bt.total_return_pct == null ? "--" : `${Number(bt.total_return_pct).toFixed(2)}%`}</div></div>
                              <div className="metric"><div className="label">最大回撤</div><div className="value">{bt.max_drawdown_pct == null ? "--" : `${Number(bt.max_drawdown_pct).toFixed(2)}%`}</div></div>
                            </div>
                            <p className="factor-desc" style={{ marginTop: 4 }}>策略：止损 {String(strategy.stop_atr_mult ?? "--")} ATR / 止盈 {String(strategy.take_atr_mult ?? "--")} ATR</p>
                            <div className="row-actions" style={{ marginTop: 6 }}>
                              <button className="mini-btn" onClick={() => loadFactorStats(String(f.factor_id))}><Eye size={12} /> 查看单个因子</button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    {factorStats.detail && (
                      <div className="tool-block" style={{ marginTop: 8 }}>
                        <h4>{String(factorStats.detail.factor_name ?? "因子")} 详情</h4>
                        <div className="metric-grid">
                          <div className="metric"><div className="label">品种/周期</div><div className="value">{String(factorStats.detail.symbol)} {String(factorStats.detail.timeframe)}</div></div>
                          <div className="metric"><div className="label">状态</div><div className="value">{String(factorStats.detail.status)}</div></div>
                          <div className="metric"><div className="label">来源</div><div className="value">{String(factorStats.detail.source)}</div></div>
                          <div className="metric"><div className="label">模型</div><div className="value">{String(factorStats.detail.model)}</div></div>
                        </div>
                        <p className="factor-desc">{String(factorStats.detail.description ?? "")}</p>
                        <pre className="replay-doc">参数：{JSON.stringify(factorStats.detail.params ?? {}, null, 2)}</pre>
                        <pre className="replay-doc">策略：{JSON.stringify(factorStats.detail.sl_tp_strategy ?? {}, null, 2)}</pre>
                        <pre className="replay-doc">{String(factorStats.detail.code ?? "")}</pre>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {tab === "replay" && subTab === "optimize" && (
              <div className="tool-block">
                <h3><Bot size={13} /> 订单优化（AI 卡片）</h3>
                {tradeRecords.length === 0 ? (
                  <div className="empty">暂无交易记录</div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {tradeRecords.slice(0, 50).map((t) => (
                      <div className="trade-row" key={String(t.id)} style={{ flexWrap: "wrap" }}>
                        <div><div className="t-label">{String(t.symbol)} {String(t.side)}</div><div className="t-value">入 {Number(t.entry_price).toFixed(5)} → 出 {t.exit_price ? Number(t.exit_price).toFixed(5) : "--"}</div></div>
                        <div><div className="t-label">盈亏</div><div className={`t-value ${Number(t.pnl) >= 0 ? "pnl-pos" : "pnl-neg"}`}>{String(t.pnl)}</div></div>
                        <button className="mini-btn primary" onClick={() => openOrderChatChoice(t)}><Bot size={12} /> AI 优化</button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {tab === "replay" && subTab === "orders" && <OrderLogPanel />}
            {tab === "ai" && (
              <>
                <div className="tool-block">
                  <h3><Cpu size={13} /> AI 连接状态</h3>
                  <div className="metric-grid">
                    <div className="metric"><div className="label">已配置 AI</div><div className="value">{aiStatus?.configs.length ?? 0}</div></div>
                    <div className="metric"><div className="label">已启用</div><div className="value">{aiStatus?.enabled_count ?? 0}</div></div>
                    <div className="metric"><div className="label">负责板块</div><div className="value">{Object.values(aiStatus?.roles ?? {}).filter((r) => r.configs.length).length} / 8</div></div>
                  </div>
                  <div className="row-actions">
                    <button className="mini-btn primary" onClick={() => openAiDialog()}><Bot size={12} /> 新增 AI 配置</button>
                    <button className="mini-btn" onClick={loadAiStatus}><RefreshCw size={12} /> 刷新状态</button>
                  </div>
                </div>
                <div className="tool-block">
                  <h3><Layers size={13} /> AI 配置列表</h3>
                  {!aiStatus || aiStatus.configs.length === 0 ? (
                    <div className="empty">尚未配置 AI，点击[新增 AI 配置]接入 qwen3.8-max 等模型。</div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {aiStatus.configs.map((cfg) => (
                        <div className="trade-row" key={cfg.id} style={{ flexWrap: "wrap" }}>
                          <div>
                            <div className="t-label">{cfg.name} <span className={`badge ${cfg.enabled ? "active" : ""}`}>{cfg.enabled ? "启用" : "停用"}</span></div>
                            <div className="t-value">{cfg.provider} / {cfg.model} | Key {cfg.api_key_masked || "未设置"}</div>
                          </div>
                          <div>
                            <div className="t-label">负责板块</div>
                            <div className="t-value">{cfg.roles.map((r) => AI_ROLE_OPTIONS.find(([k]) => k === r)?.[1] ?? r).join("、") || "未指派"}</div>
                          </div>
                          <div className="row-actions">
                            <button className="mini-btn" disabled={aiTestingId === cfg.id} onClick={() => handleAiTest(cfg.id)}>
                              <Cable size={12} /> {aiTestingId === cfg.id ? "测试中..." : "测试连接"}
                            </button>
                            <button className="mini-btn" onClick={() => openAiDialog(cfg)}><Pencil size={12} /> 编辑</button>
                            <button className="mini-btn danger" onClick={() => handleAiDelete(cfg)}><Trash2 size={12} /> 删除</button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div className="tool-block">
                  <h3><Layers size={13} /> 板块指派</h3>
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {AI_ROLE_OPTIONS.map(([key, label]) => (
                      <div className="trade-row" key={key}>
                        <div><div className="t-label">{label}</div></div>
                        <div className={`badge ${aiStatus?.roles[key]?.configs.length ? "active" : ""}`}>
                          {aiStatus?.roles[key]?.configs.length ? aiStatus.roles[key].configs.join("、") : "未指派"}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}
          </div>
        </aside>
      </div>

      <div className="status-strip">
        <Bell size={12} /> 最近告警：
        {alerts.slice(0, 3).map((a) => (
          <span key={a.id} style={{ color: a.level === "error" ? "var(--red)" : a.level === "warning" ? "var(--amber)" : "var(--muted)" }}>
            [{a.title}] {a.message}
          </span>
        ))}
        {alerts.length === 0 && <span>暂无告警</span>}
      </div>

      {toast && <div className={`toast ${toast.type}`}>{toast.type === "success" ? "✓ " : "✕ "}{toast.message}</div>}
      {editingFactor && (
        <FactorEditModal factor={editingFactor} onSave={handleUpdateFactor} onClose={() => setEditingFactor(null)} />
      )}
      {aiDialog && (
        <div className="modal-overlay">
          <div className="modal" style={{ width: 640 }}>
            <h3>{aiEditingId ? "编辑 AI 配置" : "新增 AI 配置"}</h3>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <div>
                <label className="field-label">名称备注</label>
                <input className="form-input" value={aiForm.name} onChange={(e) => setAiForm((v) => ({ ...v, name: e.target.value }))} placeholder="例如：主力-千问" />
              </div>
              <div>
                <label className="field-label">接入方式</label>
                <select className="form-input" value={aiForm.provider} onChange={(e) => setAiForm((v) => ({ ...v, provider: e.target.value }))}>
                  <option value="openai">OpenAI 兼容（千问/OpenAI）</option>
                  <option value="anthropic">Anthropic（Claude）</option>
                </select>
              </div>
              <div>
                <label className="field-label">模型名称</label>
                <input className="form-input" list="ai-model-options" value={aiForm.model} onChange={(e) => setAiForm((v) => ({ ...v, model: e.target.value }))} placeholder="qwen3.8-max / qwen-max" />
                <datalist id="ai-model-options">
                  {aiModels.map((m) => <option key={m} value={m} />)}
                </datalist>
                <div className="row-actions" style={{ marginTop: 6 }}>
                  <button className="mini-btn" disabled={aiModelsLoading} onClick={loadAiModels}>
                    {aiModelsLoading ? "获取中..." : "获取可用模型"}
                  </button>
                  {aiModels.length > 0 && <span className="factor-desc" style={{ margin: 0 }}>共 {aiModels.length} 个，可输入筛选</span>}
                </div>
                {aiModelError && <p className="factor-desc pnl-neg" style={{ margin: "4px 0 0" }}>{aiModelError}</p>}
              </div>
              <div>
                <label className="field-label">API Key</label>
                <input className="form-input" type="password" value={aiForm.api_key} onChange={(e) => setAiForm((v) => ({ ...v, api_key: e.target.value }))} placeholder={aiEditingId ? "留空则不修改" : "sk-..."} />
              </div>
              <div style={{ gridColumn: "1 / -1" }}>
                <label className="field-label">接口地址（OpenAI 兼容）</label>
                <input className="form-input" value={aiForm.base_url} onChange={(e) => setAiForm((v) => ({ ...v, base_url: e.target.value }))} placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
              </div>
              <div style={{ gridColumn: "1 / -1" }}>
                <label className="field-label">负责板块</label>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {AI_ROLE_OPTIONS.map(([key, label]) => (
                    <label key={key} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12 }}>
                      <input
                        type="checkbox"
                        checked={aiForm.roles.includes(key)}
                        onChange={(e) => setAiForm((v) => ({
                          ...v,
                          roles: e.target.checked ? [...v.roles, key] : v.roles.filter((r) => r !== key),
                        }))}
                      />
                      {label}
                    </label>
                  ))}
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <label className="field-label" style={{ margin: 0 }}>启用</label>
                <input type="checkbox" checked={aiForm.enabled} onChange={(e) => setAiForm((v) => ({ ...v, enabled: e.target.checked }))} />
              </div>
            </div>
            <div className="row-actions" style={{ marginTop: 10 }}>
              <button className="mini-btn primary" disabled={busy} onClick={handleAiSave}>{busy ? "保存中..." : "保存"}</button>
              <button className="mini-btn" onClick={() => setAiDialog(false)}>取消</button>
            </div>
          </div>
        </div>
      )}
      {matcherDialog && (
        <div className="modal-overlay">
          <div className="modal" style={{ width: 720 }}>
            <h3>启动实盘匹配</h3>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <div>
                <label className="field-label">交易品种</label>
                <SymbolSearchSelect symbols={symbols} value={matcherForm.symbol} onChange={(s) => { setMatcherForm((v) => ({ ...v, symbol: s })); handleRecommendMatcher(s); }} />
                <div className="row-actions" style={{ marginTop: 6 }}>
                  <button className="mini-btn" disabled={matcherRecommendBusy} onClick={() => handleRecommendMatcher()}>
                    <Sparkles size={12} /> {matcherRecommendBusy ? "推荐中..." : "推荐参数"}
                  </button>
                </div>
                {matcherRecommendReasons.length > 0 && (
                  <p className="factor-desc" style={{ margin: "4px 0 0" }}>{matcherRecommendReasons.join("；")}</p>
                )}
              </div>
              <div>
                <label className="field-label">K线周期</label>
                <select className="form-input" value={matcherForm.timeframe} onChange={(e) => setMatcherForm((v) => ({ ...v, timeframe: e.target.value }))}>
                  {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div>
                <label className="field-label">最小信号置信度</label>
                <input className="form-input" type="number" step="0.01" value={matcherForm.min_confidence} onChange={(e) => setMatcherForm((v) => ({ ...v, min_confidence: e.target.value }))} />
              </div>
              <div>
                <label className="field-label">交易方向</label>
                <select className="form-input" value={matcherForm.direction} onChange={(e) => setMatcherForm((v) => ({ ...v, direction: e.target.value }))}>
                  <option value="both">双向</option>
                  <option value="long">只做多</option>
                  <option value="short">只做空</option>
                </select>
              </div>
              <div>
                <label className="field-label">仓位模式</label>
                <select className="form-input" value={matcherForm.lot_mode} onChange={(e) => setMatcherForm((v) => ({ ...v, lot_mode: e.target.value }))}>
                  <option value="risk">按风险比例</option>
                  <option value="fixed">固定手数</option>
                </select>
              </div>
              {matcherForm.lot_mode === "risk" ? (
                <div>
                  <label className="field-label">单笔风险 %</label>
                  <input className="form-input" type="number" step="0.1" value={matcherForm.risk_percent} onChange={(e) => setMatcherForm((v) => ({ ...v, risk_percent: e.target.value }))} />
                </div>
              ) : (
                <div>
                  <label className="field-label">固定手数</label>
                  <input className="form-input" type="number" step="0.01" value={matcherForm.fixed_lots} onChange={(e) => setMatcherForm((v) => ({ ...v, fixed_lots: e.target.value }))} />
                </div>
              )}
              <div>
                <label className="field-label">最大持仓数量</label>
                <input className="form-input" type="number" min="1" value={matcherForm.max_positions} onChange={(e) => setMatcherForm((v) => ({ ...v, max_positions: e.target.value }))} />
              </div>
              <div>
                <label className="field-label">下单延迟（毫秒）</label>
                <input className="form-input" type="number" min="0" value={matcherForm.delay_ms} onChange={(e) => setMatcherForm((v) => ({ ...v, delay_ms: e.target.value }))} />
              </div>
              <div>
                <label className="field-label">最大日亏损 %</label>
                <input className="form-input" type="number" step="0.1" value={matcherForm.max_daily_loss_pct} onChange={(e) => setMatcherForm((v) => ({ ...v, max_daily_loss_pct: e.target.value }))} />
              </div>
              <div>
                <label className="field-label">最大回撤 %</label>
                <input className="form-input" type="number" step="0.1" value={matcherForm.max_drawdown_pct} onChange={(e) => setMatcherForm((v) => ({ ...v, max_drawdown_pct: e.target.value }))} />
              </div>
              <div>
                <label className="field-label">形态相似度门槛（0-1）</label>
                <input className="form-input" type="number" min="0" max="1" step="0.01" value={matcherForm.pattern_min_similarity} onChange={(e) => setMatcherForm((v) => ({ ...v, pattern_min_similarity: e.target.value }))} />
              </div>
              <div>
                <label className="field-label">最小形态样本数</label>
                <input className="form-input" type="number" min="0" value={matcherForm.pattern_min_samples} onChange={(e) => setMatcherForm((v) => ({ ...v, pattern_min_samples: e.target.value }))} />
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <label className="field-label" style={{ margin: 0 }}>自动平仓</label>
                <input type="checkbox" checked={matcherForm.auto_close_enabled} onChange={(e) => setMatcherForm((v) => ({ ...v, auto_close_enabled: e.target.checked }))} />
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <label className="field-label" style={{ margin: 0 }}>推送告警</label>
                <input type="checkbox" checked={matcherForm.alert_enabled} onChange={(e) => setMatcherForm((v) => ({ ...v, alert_enabled: e.target.checked }))} />
              </div>
            </div>
            <div className="tool-block" style={{ marginTop: 10 }}>
              <h4><Shield size={12} /> 止损止盈设置</h4>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                <div>
                  <label className="field-label">止损止盈依据</label>
                  <select className="form-input" value={matcherForm.initial_sltp_source} onChange={(e) => setMatcherForm((v) => ({ ...v, initial_sltp_source: e.target.value }))}>
                    <option value="pattern">形态建议</option>
                    <option value="manual">按下方规则</option>
                  </select>
                </div>
                <div>
                  <label className="field-label">止损方式</label>
                  <select className="form-input" value={matcherForm.stop_method} onChange={(e) => setMatcherForm((v) => ({ ...v, stop_method: e.target.value }))}>
                    <option value="atr">ATR 倍数</option>
                    <option value="points">固定点数</option>
                    <option value="levels">支撑/压力位</option>
                  </select>
                </div>
                <div>
                  <label className="field-label">止盈方式</label>
                  <select className="form-input" value={matcherForm.take_method} onChange={(e) => setMatcherForm((v) => ({ ...v, take_method: e.target.value }))}>
                    <option value="atr">ATR 倍数</option>
                    <option value="points">固定点数</option>
                    <option value="levels">支撑/压力位</option>
                  </select>
                </div>
                <div>
                  <label className="field-label">止损 ATR 倍数</label>
                  <input className="form-input" type="number" step="0.1" value={matcherForm.stop_atr_mult} onChange={(e) => setMatcherForm((v) => ({ ...v, stop_atr_mult: e.target.value }))} />
                </div>
                <div>
                  <label className="field-label">止盈 ATR 倍数</label>
                  <input className="form-input" type="number" step="0.1" value={matcherForm.take_atr_mult} onChange={(e) => setMatcherForm((v) => ({ ...v, take_atr_mult: e.target.value }))} />
                </div>
                <div>
                  <label className="field-label">止损点数</label>
                  <input className="form-input" type="number" step="0.1" value={matcherForm.stop_points} onChange={(e) => setMatcherForm((v) => ({ ...v, stop_points: e.target.value }))} />
                </div>
                <div>
                  <label className="field-label">止盈点数</label>
                  <input className="form-input" type="number" step="0.1" value={matcherForm.take_points} onChange={(e) => setMatcherForm((v) => ({ ...v, take_points: e.target.value }))} />
                </div>
                <div>
                  <label className="field-label">支撑位缓冲 %</label>
                  <input className="form-input" type="number" step="0.1" value={matcherForm.level_buffer_pct} onChange={(e) => setMatcherForm((v) => ({ ...v, level_buffer_pct: e.target.value }))} />
                </div>
                <div>
                  <label className="field-label">压力位缓冲 %</label>
                  <input className="form-input" type="number" step="0.1" value={matcherForm.take_level_buffer_pct} onChange={(e) => setMatcherForm((v) => ({ ...v, take_level_buffer_pct: e.target.value }))} />
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <label className="field-label" style={{ margin: 0 }}>移动止损（持仓保护）</label>
                  <input type="checkbox" checked={matcherForm.trailing_enabled} onChange={(e) => setMatcherForm((v) => ({ ...v, trailing_enabled: e.target.checked }))} />
                </div>
              </div>
            </div>
            <div className="tool-block" style={{ marginTop: 10 }}>
              <h4><Shield size={12} /> 行情过滤设置</h4>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <label className="field-label" style={{ margin: 0 }}>启用行情过滤（只允许以下行情开仓）</label>
                <input type="checkbox" checked={matcherForm.market_filter_enabled} onChange={(e) => setMatcherForm((v) => ({ ...v, market_filter_enabled: e.target.checked }))} />
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                <div>
                  <label className="field-label">行情方向</label>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {([["up", "上行"], ["down", "下行"], ["range", "震荡"]] as [string, string][]).map(([val, label]) => (
                      <label key={val} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>
                        <input type="checkbox" checked={matcherForm.market_filter_trends.includes(val)} onChange={(e) => setMatcherForm((v) => ({ ...v, market_filter_trends: toggleIn(v.market_filter_trends, val) }))} />
                        {label}
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="field-label">波动水平</label>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {([["高波动", "高波动"], ["中等波动", "中等波动"], ["低波动", "低波动"]] as [string, string][]).map(([val, label]) => (
                      <label key={val} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>
                        <input type="checkbox" checked={matcherForm.market_filter_volatilities.includes(val)} onChange={(e) => setMatcherForm((v) => ({ ...v, market_filter_volatilities: toggleIn(v.market_filter_volatilities, val) }))} />
                        {label}
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="field-label">量能状态</label>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {([["放量", "放量"], ["缩量", "缩量"], ["正常", "正常"]] as [string, string][]).map(([val, label]) => (
                      <label key={val} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>
                        <input type="checkbox" checked={matcherForm.market_filter_volume_states.includes(val)} onChange={(e) => setMatcherForm((v) => ({ ...v, market_filter_volume_states: toggleIn(v.market_filter_volume_states, val) }))} />
                        {label}
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="field-label">大周期方向（H4）</label>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {([["up", "上行"], ["down", "下行"]] as [string, string][]).map(([val, label]) => (
                      <label key={val} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>
                        <input type="checkbox" checked={matcherForm.market_filter_macro_directions.includes(val)} onChange={(e) => setMatcherForm((v) => ({ ...v, market_filter_macro_directions: toggleIn(v.market_filter_macro_directions, val) }))} />
                        {label}
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="field-label">日线方向（D1）</label>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {([["up", "上行"], ["down", "下行"]] as [string, string][]).map(([val, label]) => (
                      <label key={val} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>
                        <input type="checkbox" checked={matcherForm.market_filter_d1_directions.includes(val)} onChange={(e) => setMatcherForm((v) => ({ ...v, market_filter_d1_directions: toggleIn(v.market_filter_d1_directions, val) }))} />
                        {label}
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="field-label">环境评分下限（0-100）</label>
                  <input className="form-input" type="number" min="0" max="100" step="1" value={matcherForm.market_filter_min_score} onChange={(e) => setMatcherForm((v) => ({ ...v, market_filter_min_score: e.target.value }))} />
                </div>
              </div>
              <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <button className="mini-btn" onClick={() => loadMatcherMarketAnalysis(matcherForm.symbol, matcherForm.timeframe)}>
                  <Eye size={12} /> 查看当前行情
                </button>
                <button className="mini-btn" onClick={runPortfolioBacktest} disabled={Boolean((portfolioJob as Record<string, unknown> | null)?.running)}>
                  <FlaskConical size={12} /> 组合历史回测
                </button>
                {portfolioJob && (
                  <span className="field-label" style={{ margin: 0, fontSize: 12 }}>
                    {String((portfolioJob as Record<string, unknown>).message ?? "")}
                  </span>
                )}
                <button className="mini-btn" disabled={Boolean((portfolioJob as Record<string, unknown> | null)?.running)} onClick={runPortfolioBacktest}>
                  <TestTube2 size={12} /> 组合历史回测
                </button>
                {portfolioJob && (
                  <span className="field-label" style={{ margin: 0, fontSize: 12 }}>
                    {String((portfolioJob as Record<string, unknown>).message ?? "")}
                  </span>
                )}
                {((portfolioJob as Record<string, unknown> | null)?.result as Record<string, unknown> | undefined) && (() => {
                  const res = (portfolioJob as Record<string, unknown>).result as Record<string, unknown>;
                  const sum = (res.summary ?? {}) as Record<string, unknown>;
                  const byc = (res.by_consensus ?? {}) as Record<string, unknown>;
                  return (
                    <div style={{ marginTop: 6, fontSize: 12, color: "#bbb", background: "#1d1d1d", borderRadius: 4, padding: "6px 8px", width: "100%" }}>
                      <b>组合回测</b> {String(res.symbol ?? "--")} {String(res.timeframe ?? "--")} · {String(res.bars ?? 0)} 根 · {String(res.factors_used ?? 0)} 个因子
                      <br />
                      总收益 <b>{String(sum.total_return_pct ?? "--")}%</b> · 胜率 {String(sum.win_rate_pct ?? "--")}% · 盈亏比 {String(sum.profit_factor ?? "--")} · {String(sum.num_trades ?? 0)} 笔 · 最大回撤 {String(sum.max_drawdown_pct ?? "--")}%
                      <br />
                      按多周期共识：{Object.entries(byc).map(([k, v]) => `${k} ${(v as Record<string, unknown>).trades ?? 0}笔·{(v as Record<string, unknown>).return_pct ?? 0}%`).join(" · ") || "--"}
                    </div>
                  );
                })()}
                {matcherMarketAnalysis && (
                  <span className="factor-desc" style={{ fontSize: 12 }}>
                    当前：{String(matcherMarketAnalysis.label ?? "--")}（趋势 {String(matcherMarketAnalysis.trend_direction ?? "--")}，波动 {String(matcherMarketAnalysis.volatility ?? "--")}，量能 {String(matcherMarketAnalysis.volume_state ?? "--")}，评分 {String(matcherMarketAnalysis.environment_score ?? "--")}）
                  </span>
                )}
              </div>
              <p className="factor-desc" style={{ margin: "6px 0 0" }}>开启后，当前行情需满足所选条件才允许开仓；未勾选的维度不限。</p>
            </div>
            {matcherConfirm && (
              <div className="tool-block" style={{ marginTop: 10 }}>
                <h4><Shield size={12} /> 启动参数确认</h4>
                <div className="metric-grid">
                  <div className="metric"><div className="label">品种</div><div className="value">{matcherForm.symbol}</div></div>
                  <div className="metric"><div className="label">周期</div><div className="value">{matcherForm.timeframe}</div></div>
                  <div className="metric"><div className="label">方向</div><div className="value">{matcherForm.direction === "both" ? "双向" : matcherForm.direction === "long" ? "只做多" : "只做空"}</div></div>
                  <div className="metric"><div className="label">仓位</div><div className="value">{matcherForm.lot_mode === "fixed" ? `${matcherForm.fixed_lots} 手` : `风险 ${matcherForm.risk_percent}%`}</div></div>
                  <div className="metric"><div className="label">最大持仓</div><div className="value">{matcherForm.max_positions}</div></div>
                  <div className="metric"><div className="label">行情过滤</div><div className="value">{matcherForm.market_filter_enabled ? "已启用" : "未启用"}</div></div>
                </div>
              </div>
            )}
            <div className="row-actions" style={{ marginTop: 10 }}>
              {matcherConfirm ? (
                <>
                  <button className="mini-btn primary" onClick={handleMatcherStart}>确认启动</button>
                  <button className="mini-btn" onClick={() => setMatcherConfirm(false)}>返回修改</button>
                </>
              ) : (
                <button className="mini-btn primary" onClick={() => setMatcherConfirm(true)}>下一步：确认</button>
              )}
              <button className="mini-btn" onClick={() => setMatcherDialog(false)}>取消</button>
            </div>
          </div>
        </div>
      )}
      {matcherViewOpen && (
        <div className="modal-overlay">
          <div className="modal" style={{ width: 720, maxHeight: "86vh", overflow: "auto" }}>
            <h3><Eye size={14} /> 当前实盘匹配参数</h3>
            <div className="metric-grid">
              <div className="metric"><div className="label">运行状态</div><div className={`value ${systemState?.matcher_running ? "good" : "bad"}`}>{systemState?.matcher_running ? "运行中" : "已暂停"}</div></div>
              <div className="metric"><div className="label">品种</div><div className="value">{systemState?.matcher_symbol || "--"}</div></div>
              <div className="metric"><div className="label">周期</div><div className="value">{systemState?.matcher_timeframe || "--"}</div></div>
              <div className="metric"><div className="label">最大持仓</div><div className="value">{systemState?.max_positions ?? "--"}</div></div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 8 }}>
              {Object.entries(systemState?.executor_config ?? {}).filter(([key]) => !MATCHER_HIDDEN_KEYS.has(key)).map(([key, value]) => (
                <div className="trade-row" key={key} style={{ flexWrap: "wrap" }}>
                  <div className="t-label" style={{ minWidth: 180 }}>{MATCHER_LABELS[key] || key}</div>
                  <div className="t-value">{Array.isArray(value) ? value.join("、") : String(value)}</div>
                </div>
              ))}
            </div>
            <div className="row-actions" style={{ marginTop: 10 }}>
              <button className="mini-btn" onClick={() => setMatcherViewOpen(false)}>关闭</button>
            </div>
          </div>
        </div>
      )}
      {orderChatChoiceOpen && (
        <div className="modal-overlay">
          <div className="modal" style={{ width: 520 }}>
            <h3><Wand2 size={14} /> 选择优化方式</h3>
            {orderChatTrade && (
              <p className="factor-desc">
                {String(orderChatTrade.symbol)} {String(orderChatTrade.side)} | 盈亏 {String(orderChatTrade.pnl)}
              </p>
            )}
            <div className="row-actions" style={{ flexWrap: "wrap", gap: 8 }}>
              <button className="mini-btn" onClick={() => handleOrderChat(orderChatTrade!, false)}>正常优化</button>
              <button className="mini-btn primary" onClick={() => handleOrderChat(orderChatTrade!, true)}>镜像优化（方向完全反转）</button>
            </div>
            <div className="row-actions" style={{ marginTop: 10 }}>
              <button className="mini-btn" onClick={() => setOrderChatChoiceOpen(false)}>取消</button>
            </div>
          </div>
        </div>
      )}
      {orderChatOpen && (
        <div className="modal-overlay">
          <div className="modal" style={{ width: 860, maxHeight: "88vh", overflow: "auto" }}>
            <h3><Bot size={14} /> AI 订单优化</h3>
            {orderChatTrade && (
              <div className="tool-block">
                <h4>{String(orderChatTrade.symbol)} {String(orderChatTrade.side)} | {String(orderChatTrade.lots)} 手</h4>
                <div className="metric-grid">
                  <div className="metric"><div className="label">实际盈亏</div><div className={`value ${Number(orderChatTrade.pnl) >= 0 ? "good" : "bad"}`}>{String(orderChatTrade.pnl)}</div></div>
                  <div className="metric"><div className="label">最高浮盈</div><div className="value">{String(orderChatTrade.peak_pnl ?? "--")}</div></div>
                  <div className="metric"><div className="label">最低浮亏</div><div className="value">{String(orderChatTrade.trough_pnl ?? "--")}</div></div>
                </div>
              </div>
            )}
            {orderChatBusy && <p className="factor-desc">AI 分析中...</p>}
            {orderChatResult && (
              <div className="tool-block">
                {orderChatResult.warning && <p className="factor-desc pnl-neg">{orderChatResult.warning}</p>}
                <p className="factor-desc">{orderChatResult.reply}</p>
                {orderChatResult.factor && (
                  <>
                    <h4>{orderChatResult.mirrored ? "镜像因子草稿" : "优化因子草稿"}：{orderChatResult.factor.name}</h4>
                    <pre className="replay-doc">{orderChatResult.factor.code}</pre>
                    {orderChatResult.backtest?.metrics && (
                      <div className="metric-grid">
                        <div className="metric"><div className="label">胜率</div><div className="value">{orderChatResult.backtest.metrics.win_rate_pct.toFixed(1)}%</div></div>
                        <div className="metric"><div className="label">盈亏比</div><div className="value">{orderChatResult.backtest.metrics.profit_factor.toFixed(2)}</div></div>
                        <div className="metric"><div className="label">交易笔数</div><div className="value">{orderChatResult.backtest.metrics.num_trades}</div></div>
                      </div>
                    )}
                    {orderChatResult.backtest && !orderChatResult.backtest.metrics && (
                      <p className="factor-desc pnl-neg">{orderChatResult.backtest.message}</p>
                    )}
                    <div className="row-actions" style={{ marginTop: 8 }}>
                      <button className="mini-btn" disabled={orderChatBacktesting} onClick={handleOrderChatBacktest}><TestTube2 size={12} /> {orderChatBacktesting ? "回测中..." : "运行因子回测"}</button>
                      <button className="mini-btn primary" onClick={() => handleSaveOrderChatFactor(orderChatResult.factor!, orderChatResult.sl_tp ?? {}, orderChatResult.backtest ?? null)}><Save size={12} /> 保存因子</button>
                    </div>
                  </>
                )}
              </div>
            )}
            <div className="row-actions" style={{ marginTop: 10 }}>
              <button className="mini-btn" onClick={() => setOrderChatOpen(false)}>关闭</button>
            </div>
          </div>
        </div>
      )}
      {chatOpen && (
        <div className="modal-overlay">
          <div className="modal" style={{ width: 960, maxHeight: "88vh", overflow: "hidden", display: "flex", flexDirection: "column" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <h3><Bot size={14} /> AI 交易助手</h3>
              <button className="mini-btn" onClick={() => setChatOpen(false)}>关闭</button>
            </div>
            <div style={{ display: "flex", gap: 12, flex: 1, minHeight: 0 }}>
              <div style={{ width: 280, overflow: "auto", display: "flex", flexDirection: "column", gap: 8, paddingRight: 8 }}>
                <div>
                  <label className="field-label">品种</label>
                  <SymbolSearchSelect symbols={["", ...symbols]} value={chatMeta.symbol} onChange={(s) => setChatMeta((v) => ({ ...v, symbol: s }))} placeholder="搜索/未选择品种" />
                </div>
                <div>
                  <label className="field-label">K线周期</label>
                  <select className="form-input" value={chatMeta.timeframe} onChange={(e) => setChatMeta((v) => ({ ...v, timeframe: e.target.value }))}>
                    {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
                <div>
                  <label className="field-label">开始时间</label>
                  <input className="form-input" type="datetime-local" value={chatMeta.start_time} onChange={(e) => setChatMeta((v) => ({ ...v, start_time: e.target.value }))} />
                </div>
                <div>
                  <label className="field-label">结束时间</label>
                  <input className="form-input" type="datetime-local" value={chatMeta.end_time} onChange={(e) => setChatMeta((v) => ({ ...v, end_time: e.target.value }))} />
                </div>
                <div>
                  <label className="field-label">方向</label>
                  <select className="form-input" value={chatMeta.direction} onChange={(e) => setChatMeta((v) => ({ ...v, direction: e.target.value }))}>
                    <option value="">不确定</option>
                    <option value="long">做多</option>
                    <option value="short">做空</option>
                  </select>
                </div>
                <button className="mini-btn" onClick={() => setChatMeta((v) => ({ ...v, symbol, timeframe }))}>使用当前图表参数</button>
                <label className="mini-btn" style={{ textAlign: "center" }}>
                  上传K线图片
                  <input
                    type="file"
                    accept="image/*"
                    style={{ display: "none" }}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (!file) return;
                      const reader = new FileReader();
                      reader.onload = () => setChatImage(String(reader.result));
                      reader.readAsDataURL(file);
                    }}
                  />
                </label>
                {chatImage && (
                  <div>
                    <img src={chatImage} alt="K线图" style={{ width: "100%", borderRadius: 6, border: "1px solid var(--line)" }} />
                    <button className="mini-btn danger" style={{ width: "100%", marginTop: 4 }} onClick={() => setChatImage(null)}>移除图片</button>
                  </div>
                )}
              </div>
              <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
                <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column", gap: 8, paddingRight: 8 }}>
                  {chatMessages.length === 0 && <div className="empty">上传K线图片或输入问题，AI 会自动识别形态并尝试生成因子。</div>}
                  {chatMessages.map((m, idx) => (
                    <div key={idx} className={`trade-row ${m.role === "user" ? "" : "pnl-pos"}`} style={{ flexDirection: "column", alignItems: "stretch", gap: 4 }}>
                      <div className="t-label">{m.role === "user" ? "我" : "AI 助手"}</div>
                      <div className="t-value" style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{m.content}</div>
                      {m.factor && (
                        <div className="tool-block" style={{ marginTop: 4 }}>
                          <h4>{m.factor.name}</h4>
                          {m.backtest?.metrics && (
                            <div className="metric-grid">
                              <div className="metric"><div className="label">胜率</div><div className="value">{m.backtest.metrics.win_rate_pct.toFixed(1)}%</div></div>
                              <div className="metric"><div className="label">盈亏比</div><div className="value">{m.backtest.metrics.profit_factor.toFixed(2)}</div></div>
                              <div className="metric"><div className="label">交易笔数</div><div className="value">{m.backtest.metrics.num_trades}</div></div>
                            </div>
                          )}
                          <div className="row-actions" style={{ marginTop: 6 }}>
                            <button className="mini-btn primary" onClick={() => handleSaveChatFactor(m.factor!, m.sl_tp ?? {}, m.backtest ?? null)}><Save size={12} /> 保存因子</button>
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
                <div className="row-actions" style={{ marginTop: 8, alignItems: "flex-end" }}>
                  <textarea
                    className="form-input modal-textarea"
                    style={{ flex: 1, minHeight: 56 }}
                    placeholder="输入问题，例如：识别这张K线图并生成因子"
                    value={chatInput}
                    onChange={(e) => setChatInput(e.target.value)}
                  />
                  <button className="mini-btn primary" disabled={chatBusy || (!chatInput.trim() && !chatImage)} onClick={handleAiChatSend}>
                    {chatBusy ? "思考中..." : "发送"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
      {learnDialog && (
        <div className="modal-overlay">
          <div className="modal" style={{ width: 780, maxHeight: "86vh", overflow: "auto" }}>
            <h3><Bot size={14} /> AI 学习结果预览</h3>
            {learnDialog.warning && <p className="factor-desc pnl-neg">{learnDialog.warning}</p>}
            {(learnDialog.validation_stats?.warnings ?? []).length > 0 && (
              <div className="factor-desc pnl-neg" style={{ marginBottom: 8 }}>
                {(learnDialog.validation_stats?.warnings ?? []).map((w) => <div key={w}>提示：{w}</div>)}
              </div>
            )}
            <div className={`factor-desc ${learnDialog.validation_stats?.pass_gate ? "pnl-pos" : "pnl-neg"}`}>
              可信门槛：{learnDialog.validation_stats?.pass_gate ? "已通过" : "未通过"}
              {!learnDialog.validation_stats?.pass_gate && (learnDialog.validation_stats?.gate_reasons ?? []).length > 0 && (
                <span>（{(learnDialog.validation_stats?.gate_reasons ?? []).join("；")}）</span>
              )}
            </div>
            <p className="factor-desc">
              模型：{learnDialog.ai_model || "本地规则模板"} | 样本：{String(learnDialog.trade_summary.count ?? 0)} 笔 |
              胜率：{String(learnDialog.trade_summary.win_rate_pct ?? 0)}% | 平均持仓：{String(learnDialog.trade_summary.avg_hold_minutes ?? 0)} 分钟
            </p>
            <div className="tool-block">
              <h4><Sparkles size={12} /> {learnDialog.factor_draft.name}</h4>
              <p className="factor-desc">{learnDialog.factor_draft.description}</p>
              <pre className="replay-doc">{learnDialog.factor_draft.code}</pre>
            </div>
            <div className="tool-block">
              <h4><Shield size={12} /> 止损止盈策略</h4>
              <pre className="replay-doc">{JSON.stringify(learnDialog.sl_tp_strategy, null, 2)}</pre>
              {(() => {
                const stopMult = Number(learnDialog.sl_tp_strategy.stop_atr_mult || 0);
                const takeMult = Number(learnDialog.sl_tp_strategy.take_atr_mult || 0);
                const atr = Number(sltpAtr || 5);
                if (!stopMult && !takeMult) return null;
                return (
                  <p className="factor-desc">
                    估算（按 ATR {atr}，100 合约）：止损约 {Math.round(stopMult * atr * 100)} 美元/手，止盈约 {Math.round(takeMult * atr * 100)} 美元/手
                  </p>
                );
              })()}
            </div>
            <div className="tool-block">
              <h4><TestTube2 size={12} /> 可信回测验证</h4>
              <p className="factor-desc">
                学习期截止：{learnDialog.validation_stats?.learning_end ? String(learnDialog.validation_stats.learning_end).slice(0, 19).replace("T", " ") : "--"}
                {" "}| 验证期 {learnDialog.validation_stats?.validation_bars ?? 0} 根 | 样本外 {learnDialog.validation_stats?.out_of_sample_bars ?? 0} 根
              </p>
              {learnDialog.validation_stats?.statistics && (
                <div className="metric-grid">
                  <div className="metric"><div className="label">验证样本</div><div className="value">{learnDialog.validation_stats.statistics.num_trades} 笔</div></div>
                  <div className="metric"><div className="label">胜率 95% 区间</div><div className="value">{learnDialog.validation_stats.statistics.win_rate_ci[0]}% ~ {learnDialog.validation_stats.statistics.win_rate_ci[1]}%</div></div>
                  <div className="metric"><div className="label">盈亏比 95% 区间</div><div className="value">{learnDialog.validation_stats.statistics.profit_factor_ci[0]} ~ {learnDialog.validation_stats.statistics.profit_factor_ci[1]}</div></div>
                  <div className="metric"><div className="label">Bootstrap</div><div className="value">{learnDialog.validation_stats.statistics.samples} 次</div></div>
                </div>
              )}
              {learnDialog.validation_stats?.cost_assumptions && learnDialog.validation_stats.benchmark && (
                <div className="metric-grid" style={{ marginTop: 8 }}>
                  <div className="metric"><div className="label">手续费</div><div className="value">{Number(learnDialog.validation_stats.cost_assumptions.commission_pct * 100).toFixed(2)}%</div></div>
                  <div className="metric"><div className="label">滑点</div><div className="value">{Number(learnDialog.validation_stats.cost_assumptions.slippage).toFixed(6)}</div></div>
                  <div className="metric"><div className="label">买入持有基准</div><div className="value">{learnDialog.validation_stats.benchmark.buy_hold_return_pct.toFixed(2)}%</div></div>
                  <div className="metric"><div className="label">随机入场基准</div><div className="value">{learnDialog.validation_stats.benchmark.random_baseline_return_pct.toFixed(2)}%</div></div>
                </div>
              )}
              {learnDialog.validation_stats?.validation_gross?.metrics && learnDialog.backtest?.metrics && (
                <p className="factor-desc" style={{ margin: "6px 0 0" }}>
                  毛收益 {learnDialog.validation_stats.validation_gross.metrics.total_return_pct.toFixed(2)}% → 净收益（含成本）{learnDialog.backtest.metrics.total_return_pct.toFixed(2)}%
                </p>
              )}
            </div>
            {learnDialog.backtest?.metrics && (
              <div className="tool-block">
                <h4><TestTube2 size={12} /> 验证期回测（学习期之后）</h4>
                <div className="metric-grid">
                  <div className="metric"><div className="label">胜率</div><div className="value">{learnDialog.backtest.metrics.win_rate_pct.toFixed(1)}%</div></div>
                  <div className="metric"><div className="label">总收益</div><div className={`value ${learnDialog.backtest.metrics.total_return_pct >= 0 ? "good" : "bad"}`}>{learnDialog.backtest.metrics.total_return_pct.toFixed(2)}%</div></div>
                  <div className="metric"><div className="label">盈亏比</div><div className="value">{learnDialog.backtest.metrics.profit_factor.toFixed(2)}</div></div>
                  <div className="metric"><div className="label">最大回撤</div><div className="value bad">-{learnDialog.backtest.metrics.max_drawdown_pct.toFixed(2)}%</div></div>
                  <div className="metric"><div className="label">交易笔数</div><div className="value">{learnDialog.backtest.metrics.num_trades}</div></div>
                </div>
              </div>
            )}
            {learnDialog.out_of_sample_backtest?.metrics && learnDialog.out_of_sample_backtest.metrics.num_trades > 0 ? (
              <div className="tool-block">
                <h4><Shield size={12} /> 最终样本外回测</h4>
                <div className="metric-grid">
                  <div className="metric"><div className="label">胜率</div><div className="value">{learnDialog.out_of_sample_backtest.metrics.win_rate_pct.toFixed(1)}%</div></div>
                  <div className="metric"><div className="label">总收益</div><div className={`value ${learnDialog.out_of_sample_backtest.metrics.total_return_pct >= 0 ? "good" : "bad"}`}>{learnDialog.out_of_sample_backtest.metrics.total_return_pct.toFixed(2)}%</div></div>
                  <div className="metric"><div className="label">盈亏比</div><div className="value">{learnDialog.out_of_sample_backtest.metrics.profit_factor.toFixed(2)}</div></div>
                  <div className="metric"><div className="label">交易笔数</div><div className="value">{learnDialog.out_of_sample_backtest.metrics.num_trades}</div></div>
                </div>
              </div>
            ) : (
              learnDialog.out_of_sample_backtest && <p className="factor-desc pnl-neg">最终样本外区间未产生交易，无法验证泛化能力。</p>
            )}
            {learnDialog.cross_validation.length > 0 && (
              <div className="tool-block">
                <h4><Layers size={12} /> 多品种/多周期交叉验证</h4>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {learnDialog.cross_validation.map((c) => {
                    const m = c.backtest.metrics;
                    if (!m) return null;
                    return (
                      <div className="trade-row" key={`${c.symbol}_${c.timeframe}`} style={{ flexWrap: "wrap" }}>
                        <div><div className="t-label">{c.symbol} / {c.timeframe}</div><div className="t-value">{c.disjoint ? "学习期后数据" : "可能与学习期重叠"}</div></div>
                        <div><div className="t-label">交易笔数</div><div className="t-value">{m.num_trades}</div></div>
                        <div><div className="t-label">胜率</div><div className="t-value">{m.win_rate_pct.toFixed(1)}%</div></div>
                        <div><div className="t-label">盈亏比</div><div className="t-value">{m.profit_factor.toFixed(2)}</div></div>
                        <div><div className={`t-label ${m.total_return_pct >= 0 ? "pnl-pos" : "pnl-neg"}`}>收益</div><div className="t-value">{m.total_return_pct.toFixed(2)}%</div></div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            <div className="row-actions" style={{ marginTop: 12 }}>
              <button className="mini-btn primary" disabled={busy || !learnDialog.validation_stats?.pass_gate} onClick={handleConfirmLearn}><Save size={12} /> 确认存入因子库</button>
              <button className="mini-btn" onClick={() => setLearnDialog(null)}>取消</button>
            </div>
          </div>
        </div>
      )}
      {pendingExit && (
        <div className="modal-overlay">
          <div className="modal" style={{ width: 480 }}>
            <h3>选择出场原因</h3>
            <p className="factor-desc">出场价格：{pendingExit.price.toFixed(5)}</p>
            <div className="row-actions" style={{ flexWrap: "wrap", gap: 6 }}>
              {["到压力位", "破支撑", "止盈", "止损", "超时", "手动"].map((reason) => (
                <button key={reason} className="mini-btn" onClick={() => confirmExitReason(reason)}>{reason}</button>
              ))}
            </div>
            <div className="row-actions" style={{ marginTop: 8 }}>
              <button className="mini-btn" onClick={() => setPendingExit(null)}>取消</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
