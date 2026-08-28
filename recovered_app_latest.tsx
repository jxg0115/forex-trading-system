import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  Ban,
  Bell,
  BoxSelect,
  Bot,
  Cable,
  CheckCircle2,
  Eye,
  FileText,
  FileClock,
  Layers,
  Play,
  Pencil,
  RefreshCw,
  Save,
  Shield,
  ShieldCheck,
  Sparkles,
  Square,
  StopCircle,
  TestTube2,
  Trash2,
  Undo2,
  Wallet,
  X,
} from "lucide-react";
import { KLineChart, type ChartMode } from "./components/KLineChart";
import { FactorEditModal } from "./components/FactorEditModal";
import { Mt5Panel } from "./components/Mt5Panel";
import { OrderLogPanel } from "./components/OrderLogPanel";
import { SymbolSearchSelect } from "./components/SymbolSearchSelect";
import { api, type Alert, type BacktestResult, type Bar, type Factor, type FactorDraft, type FactorPayload, type IndicatorData, type MarkerPoint, type MatcherCandidate, type OptimizeResult, type Selection, type SystemState } from "./api";

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

type MainTab = "factors" | "trading" | "replay";
type SubTab = "mark" | "backtest" | "manage" | "signals" | "mt5" | "replay" | "orders";

const TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];

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
  const [timeframe, setTimeframe] = useState("M15");
  const [bars, setBars] = useState<Bar[]>([]);
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
  const [paperJobs, setPaperJobs] = useState<Array<Record<string, unknown>>>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [systemState, setSystemState] = useState<SystemState | null>(null);
  const [replay, setReplay] = useState<{ markdown: string; score: number; factor_name: string } | null>(null);
  const [tab, setTab] = useState<MainTab>("factors");
  const [subTab, setSubTab] = useState<SubTab>("mark");
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedFactor, setSelectedFactor] = useState<Factor | null>(null);
  const [editingFactor, setEditingFactor] = useState<Factor | null>(null);
  const [sandboxResults, setSandboxResults] = useState<Record<string, { summary: string; staticOk: boolean; runtimeOk: boolean; message: string; suggestions: string[] }>>({});
  const [matcherDialog, setMatcherDialog] = useState(false);
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
    trailing_activation_pct: "0.5",
    trailing_atr_mult: "2.0",
    trailing_lock_distance: "1.0",
    trailing_take_atr_mult: "1.0",
    trailing_lock_buffer_pct: "0.2",
    delay_ms: "0",
    auto_close_enabled: true,
    alert_enabled: true,
    max_daily_loss_pct: "3",
    max_drawdown_pct: "20",
    pattern_min_similarity: "0.85",
    pattern_min_samples: "0",
  });
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

  useEffect(() => {
    loadBars();
    loadSnapshot();
    loadSymbols();
    loadFactors();
    loadAlerts();
    loadPaper();
    loadSystem();
  }, [loadBars, loadSnapshot, loadSymbols, loadFactors, loadAlerts, loadPaper, loadSystem]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(`ws://${window.location.host}/api/mt5/ws/tick?symbol=${symbol}`);
    } catch {
      return;
    }
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        const tick = msg?.data;
        if (!tick || !Number.isFinite(tick.bid)) return;
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
    const timer = window.setInterval(() => {
      loadBars();
      loadSnapshot();
      loadSystem();
      loadPaper();
    }, 4000);
    return () => window.clearInterval(timer);
  }, [loadBars, loadSnapshot, loadSystem, loadPaper]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      loadAlerts();
    }, 10000);
    return () => window.clearInterval(timer);
  }, [loadAlerts]);

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
      setTab("backtest");
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
      setTab("library");
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
      setTab("backtest");
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
        complexity_penalty: Number(optSettings.complexityPenalty) || 0,
        param_ranges: paramRanges,
      });
      setOptimizeResult(result);
      setTab("backtest");
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
      setTab("backtest");
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
    setMatcherForm((v) => ({ ...v, symbol, timeframe }));
    setMatcherDialog(true);
  }, [systemState, symbol, timeframe, notify, loadSystem]);

  const handleMatcherStart = useCallback(async () => {
    try {
      const result = await api.startMatcher({
        symbol: matcherForm.symbol,
        timeframe: matcherForm.timeframe,
        min_confidence: Number(matcherForm.min_confidence),
        direction: matcherForm.direction,
        lot_mode: matcherForm.lot_mode,
        fixed_lots: Number(matcherForm.fixed_lots),
        risk_percent: Number(matcherForm.risk_percent),
        max_positions: Number(matcherForm.max_positions),
        stop_method: matcherForm.stop_method,
        take_method: matcherForm.take_method,
        stop_atr_mult: Number(matcherForm.stop_atr_mult),
        take_atr_mult: Number(matcherForm.take_atr_mult),
        stop_points: Number(matcherForm.stop_points),
        take_points: Number(matcherForm.take_points),
        level_buffer_pct: Number(matcherForm.level_buffer_pct),
        take_level_buffer_pct: Number(matcherForm.take_level_buffer_pct),
        trailing_enabled: matcherForm.trailing_enabled,
        trailing_activation_pct: Number(matcherForm.trailing_activation_pct),
        trailing_atr_mult: Number(matcherForm.trailing_atr_mult),
        trailing_lock_distance: Number(matcherForm.trailing_lock_distance),
        trailing_take_atr_mult: Number(matcherForm.trailing_take_atr_mult),
        trailing_lock_buffer_pct: Number(matcherForm.trailing_lock_buffer_pct),
        delay_ms: Number(matcherForm.delay_ms),
        auto_close_enabled: matcherForm.auto_close_enabled,
        alert_enabled: matcherForm.alert_enabled,
        max_daily_loss_pct: Number(matcherForm.max_daily_loss_pct),
        max_drawdown_pct: Number(matcherForm.max_drawdown_pct),
        pattern_min_similarity: Number(matcherForm.pattern_min_similarity),
        pattern_min_samples: Number(matcherForm.pattern_min_samples),
      });
      setMatcherDialog(false);
      notify("success", result.message);
      await loadSystem();
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [systemState, symbol, timeframe, notify, loadSystem]);

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
        <span className="status-pill"><span className="dot on" /> 行情实时</span>
        <span className="status-pill"><Wallet size={13} /> MT5 账户 {systemState?.account_login ?? "--"} 权益 ${(systemState?.account_equity ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
        <button className={`btn ${systemState?.matcher_running ? "danger" : "primary"}`} onClick={handleMatcherToggle}>
          {systemState?.matcher_running ? <StopCircle size={14} /> : <Play size={14} />}
          {systemState?.matcher_running ? "暂停系统交易" : "启动实盘匹配"}
        </button>
        <button className="btn danger" onClick={handleCloseAll}>
          <Square size={14} /> 一键平仓
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

            {tab === "trading" && subTab === "mt5" && <Mt5Panel symbol={symbol} timeframe={timeframe} />}
            {tab === "replay" && subTab === "orders" && <OrderLogPanel />}
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
          />
        </section>

        <aside className="sidebar">
          <div className="tabs">
            <button className={`tab ${tab === "factors" ? "active" : ""}`} onClick={() => { setTab("factors"); setSubTab("mark"); }}><Bot size={14} /> 因子工作台</button>
            <button className={`tab ${tab === "trading" ? "active" : ""}`} onClick={() => { setTab("trading"); setSubTab("signals"); }}><Activity size={14} /> 交易中心</button>
            <button className={`tab ${tab === "replay" ? "active" : ""}`} onClick={() => { setTab("replay"); setSubTab("replay"); }}><FileText size={14} /> 复盘与日志</button>
          </div>

          <div className="panel-body">
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
                        <label className="field-label">最低交易数</label>
                        <input className="form-input" type="number" min="0" value={optSettings.minTrades} onChange={(e) => setOptSettings((v) => ({ ...v, minTrades: e.target.value }))} />
                      </div>
                      <div>
                        <label className="field-label">参数复杂度惩罚（每参数）</label>
                        <input className="form-input" type="number" min="0" step="0.001" value={optSettings.complexityPenalty} onChange={(e) => setOptSettings((v) => ({ ...v, complexityPenalty: e.target.value }))} />
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
                        <p className="factor-desc">
                          目标：胜率 ≥{optimizeResult.targets.win_rate_pct || "不限"}% | 收益 ≥{optimizeResult.targets.total_return_pct || "不限"}% | 盈亏比 ≥{optimizeResult.targets.profit_factor || "不限"} | 回撤 ≤{optimizeResult.targets.max_drawdown_pct}% | 达标组数 {optimizeResult.satisfied_trials}
                        </p>
                      <code className="code-block">最佳参数：{JSON.stringify(optimizeResult.best_params, null, 2)}</code>
                        <div className="metric-grid">
                          <div className="metric"><div className="label">胜率</div><div className="value">{optimizeResult.best_metrics.win_rate_pct.toFixed(1)}%</div></div>
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
      {matcherDialog && (
        <div className="modal-overlay">
          <div className="modal" style={{ width: 720 }}>
            <h3>启动实盘匹配</h3>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <div>
                <label className="field-label">交易品种</label>
                <SymbolSearchSelect symbols={symbols} value={matcherForm.symbol} onChange={(s) => setMatcherForm((v) => ({ ...v, symbol: s }))} />
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
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <label className="field-label" style={{ margin: 0 }}>移动止损</label>
                <input type="checkbox" checked={matcherForm.trailing_enabled} onChange={(e) => setMatcherForm((v) => ({ ...v, trailing_enabled: e.target.checked }))} />
              </div>
              {matcherForm.trailing_enabled && (
                <>
                  <div>
                    <label className="field-label">移动止损激活盈利 %</label>
                    <input className="form-input" type="number" step="0.1" min="0" value={matcherForm.trailing_activation_pct} onChange={(e) => setMatcherForm((v) => ({ ...v, trailing_activation_pct: e.target.value }))} />
                  </div>
                  <div>
                    <label className="field-label">移动止损追踪 ATR 倍数</label>
                    <input className="form-input" type="number" step="0.1" min="0" value={matcherForm.trailing_atr_mult} onChange={(e) => setMatcherForm((v) => ({ ...v, trailing_atr_mult: e.target.value }))} />
                  </div>
                  <div style={{ gridColumn: "1 / -1" }}>
                    <div className="factor-desc" style={{ margin: 0 }}>
                      盈利达到“激活盈利 %”后启动（即使未到止盈价）：做多先抬到保本价或最近支撑位上方，做空先压到保本价或最近压力位下方，再随实时最高/最低价按 ATR 倍数移动，止损只朝有利方向，不会回退。
                    </div>
                  </div>
                </>
              )}
              <div>
                <label className="field-label">最大持仓数量</label>
                <input className="form-input" type="number" min="1" value={matcherForm.max_positions} onChange={(e) => setMatcherForm((v) => ({ ...v, max_positions: e.target.value }))} />
              </div>
              <div>
                <label className="field-label">止损方式</label>
                <select className="form-input" value={matcherForm.stop_method} onChange={(e) => setMatcherForm((v) => ({ ...v, stop_method: e.target.value }))}>
                  <option value="atr">ATR 倍数</option>
                  <option value="points">固定点数</option>
                  <option value="levels">支撑/压力位</option>
                </select>
              </div>
              {matcherForm.stop_method === "atr" ? (
                <>
                  <div>
                    <label className="field-label">ATR 止损倍数</label>
                    <input className="form-input" type="number" step="0.1" value={matcherForm.stop_atr_mult} onChange={(e) => setMatcherForm((v) => ({ ...v, stop_atr_mult: e.target.value }))} />
                  </div>
                  <div>
                    <label className="field-label">ATR 止盈倍数</label>
                    <input className="form-input" type="number" step="0.1" value={matcherForm.take_atr_mult} onChange={(e) => setMatcherForm((v) => ({ ...v, take_atr_mult: e.target.value }))} />
                  </div>
                </>
              ) : matcherForm.stop_method === "levels" ? (
                <>
                  <div>
                    <label className="field-label">支撑/压力位缓冲 %</label>
                    <input className="form-input" type="number" step="0.01" min="0" value={matcherForm.level_buffer_pct} onChange={(e) => setMatcherForm((v) => ({ ...v, level_buffer_pct: e.target.value }))} />
                  </div>
                  <div style={{ alignSelf: "center" }}>
                    <div className="factor-desc" style={{ margin: 0 }}>做多：止损=支撑位下方、止盈=压力位下方；做空：止损=压力位上方、止盈=支撑位上方</div>
                  </div>
                </>
              ) : (
                <>
                  <div>
                    <label className="field-label">止损点数</label>
                    <input className="form-input" type="number" value={matcherForm.stop_points} onChange={(e) => setMatcherForm((v) => ({ ...v, stop_points: e.target.value }))} />
                  </div>
                  <div>
                    <label className="field-label">止盈点数</label>
                    <input className="form-input" type="number" value={matcherForm.take_points} onChange={(e) => setMatcherForm((v) => ({ ...v, take_points: e.target.value }))} />
                  </div>
                </>
              )}
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
            <div className="row-actions" style={{ marginTop: 10 }}>
              <button className="mini-btn primary" onClick={handleMatcherStart}>确认启动</button>
              <button className="mini-btn" onClick={() => setMatcherDialog(false)}>取消</button>
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
                          <div className="badge active">#{ex.order_id.slice(0, 6)}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
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

            {tab === "replay" && (
              <div className="sub-tabs">
                <button className={`sub-tab ${subTab === "replay" ? "active" : ""}`} onClick={() => setSubTab("replay")}>复盘报告</button>
                <button className={`sub-tab ${subTab === "orders" ? "active" : ""}`} onClick={() => setSubTab("orders")}>订单日志</button>
              </div>
            )}
            {tab === "replay" && subTab === "replay" && (
              <>
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
    </div>
  );
}

