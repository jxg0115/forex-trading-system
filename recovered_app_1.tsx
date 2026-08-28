import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  Ban,
  Bell,
  Bot,
  CheckCircle2,
  Eye,
  FileText,
  Layers,
  Play,
  RefreshCw,
  Save,
  Shield,
  Sparkles,
  Square,
  StopCircle,
  TestTube2,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { KLineChart, type ChartMode } from "./components/KLineChart";
import { api, type Alert, type BacktestResult, type Bar, type Factor, type FactorDraft, type MarkerPoint, type MatcherCandidate, type Selection, type SystemState } from "./api";

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

type Tab = "factor" | "backtest" | "library" | "trading" | "replay";

const SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"];
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
  const [timeframe, setTimeframe] = useState("M15");
  const [bars, setBars] = useState<Bar[]>([]);
  const [snapshot, setSnapshot] = useState<{ current_price: number; change_pct_24h: number; atr: number; atr_pct: number } | null>(null);
  const [mode, setMode] = useState<ChartMode>("select");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [entryPoints, setEntryPoints] = useState<MarkerPoint[]>([]);
  const [exitPoints, setExitPoints] = useState<MarkerPoint[]>([]);
  const [draft, setDraft] = useState<FactorDraft | null>(null);
  const [backtest, setBacktest] = useState<BacktestResult | null>(null);
  const [factors, setFactors] = useState<Factor[]>([]);
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [paperJobs, setPaperJobs] = useState<Array<Record<string, unknown>>>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [systemState, setSystemState] = useState<SystemState | null>(null);
  const [replay, setReplay] = useState<{ markdown: string; score: number; factor_name: string } | null>(null);
  const [tab, setTab] = useState<Tab>("factor");
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedFactor, setSelectedFactor] = useState<Factor | null>(null);

  const regionStats = useMemo(() => (selection ? computeRegionStats(bars, selection) : null), [bars, selection]);

  const notify = useCallback((type: "success" | "error", message: string) => {
    setToast({ type, message });
    window.setTimeout(() => setToast(null), 4200);
  }, []);

  const loadBars = useCallback(async () => {
    try {
      const data = await api.bars(symbol, timeframe, 500);
      setBars(data.bars);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [symbol, timeframe, notify]);

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
    loadFactors();
    loadAlerts();
    loadPaper();
    loadSystem();
  }, [loadBars, loadSnapshot, loadFactors, loadAlerts, loadPaper, loadSystem]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      loadBars();
      loadSnapshot();
      loadSystem();
      loadPaper();
    }, 6000);
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
    setDraft(null);
    setBacktest(null);
    setMode("select");
    notify("success", "已重置框选与标注");
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
      };
      const result = await api.generate(region, chartImage());
      setDraft(result);
      setBacktest(null);
      notify("success", `AI 因子生成完成：${result.name}`);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [selection, symbol, timeframe, entryPoints, exitPoints, chartImage, notify]);

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
        prompt_snapshot: { selection: selection ?? null, entry_points: entryPoints, exit_points: exitPoints },
        generated_region: selection ?? {},
        backtest_stats: backtest?.metrics ?? null,
      });
      setSelectedFactor(saved);
      await loadFactors();
      notify("success", `因子已存入特征库：${saved.name}`);
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }, [draft, symbol, timeframe, regionStats, selection, entryPoints, exitPoints, backtest, notify, loadFactors]);

  const viewFactor = useCallback((factor: Factor) => {
    setSelectedFactor(factor);
    setDraft(factor);
    setTab("factor");
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

  const handleMatcherToggle = useCallback(async () => {
    try {
      const running = systemState?.matcher_running;
      const result = running ? await api.stopMatcher() : await api.startMatcher();
      notify("success", result.message);
      await loadSystem();
    } catch (err) {
      notify("error", String(err instanceof Error ? err.message : err));
    }
  }, [systemState, notify, loadSystem]);

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
        <div className="select-wrap">
          <TrendingUp size={13} />
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="select-wrap">
          <Activity size={13} />
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
            {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div className="topbar-spacer" />
        <span className="status-pill"><span className={`dot ${systemState?.matcher_running ? "on" : ""}`} /> 实盘匹配 {systemState?.matcher_running ? "运行中" : "已暂停"}</span>
        <span className="status-pill"><span className={`dot ${systemState?.active_positions ? "warn" : ""}`} /> 持仓 {systemState?.active_positions ?? 0}</span>
        <span className="status-pill"><Wallet size={13} /> 账户权益 ${(systemState?.account_equity ?? 10000).toLocaleString()}</span>
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
            <button className={`btn ${mode === "entry" ? "active" : ""}`} onClick={() => setMode("entry")}>
              <Sparkles size={14} /> 标记入场点
            </button>
            <button className={`btn ${mode === "exit" ? "active" : ""}`} onClick={() => setMode("exit")}>
              <Ban size={14} /> 标记出场点
            </button>
            <button className="btn primary" disabled={busy} onClick={handleGenerate}>
              <Bot size={14} /> 提取并生成 AI 因子
            </button>
            <button className="btn" onClick={resetSelection}>
              <RefreshCw size={14} /> 重置框选
            </button>
            <button className="btn" disabled={busy || !draft} onClick={handleBacktest}>
              <TestTube2 size={14} /> 运行历史回测
            </button>
            <button className="btn" disabled={busy || !draft} onClick={handleSave}>
              <Save size={14} /> 存入因子特征库
            </button>
            <button className="btn" disabled={!selectedFactor} onClick={() => selectedFactor && viewFactor(selectedFactor)}>
              <Eye size={14} /> 查看因子逻辑
            </button>
            <button className="btn" disabled={!selectedFactor} onClick={() => selectedFactor && disableFactor(selectedFactor)}>
              <StopCircle size={14} /> 禁用此因子
            </button>
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
            mode={mode}
            regionStats={regionStats}
            onSelection={setSelection}
            onPoint={(p) => (mode === "entry" ? setEntryPoints((prev) => [...prev, p]) : setExitPoints((prev) => [...prev, p]))}
            onMode={setMode}
          />
        </section>

        <aside className="sidebar">
          <div className="tabs">
            <button className={`tab ${tab === "factor" ? "active" : ""}`} onClick={() => setTab("factor")}><Bot size={14} /> 因子生成</button>
            <button className={`tab ${tab === "backtest" ? "active" : ""}`} onClick={() => setTab("backtest")}><TestTube2 size={14} /> 回测结果</button>
            <button className={`tab ${tab === "library" ? "active" : ""}`} onClick={() => setTab("library")}><Layers size={14} /> 因子库</button>
            <button className={`tab ${tab === "trading" ? "active" : ""}`} onClick={() => setTab("trading")}><Activity size={14} /> 交易状态</button>
            <button className={`tab ${tab === "replay" ? "active" : ""}`} onClick={() => setTab("replay")}><FileText size={14} /> 复盘报告</button>
          </div>

          <div className="panel-body">
            {tab === "factor" && (
              <>
                {!draft ? (
                  <div className="empty">
                    在左侧 K 线图上拖拽框选形态，点击[标记入场点]/[标记出场点]标注入场与离场位置，然后点击[提取并生成 AI 因子]。
                  </div>
                ) : (
                  <div className="tool-block">
                    <h3><Sparkles size={13} /> 逆向因子草稿</h3>
                    <p className="factor-desc">{draft.description}</p>
                    <div className="metric-grid" style={{ marginBottom: 10 }}>
                      <div className="metric"><div className="label">沙盒检查</div><div className={`value ${draft.sandbox?.ok ? "good" : "bad"}`}>{draft.sandbox?.ok ? "通过" : "未通过"}</div></div>
                      <div className="metric"><div className="label">入场信号</div><div className="value">{draft.execution?.entry_count ?? 0}</div></div>
                      <div className="metric"><div className="label">生成模型</div><div className="value" style={{ fontSize: 11 }}>{draft.model}</div></div>
                    </div>
                    <code className="code-block">{draft.code}</code>
                  </div>
                )}
              </>
            )}

            {tab === "backtest" && (
              <>
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

            {tab === "library" && (
              <>
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
                        {f.status === "active" ? (
                          <button className="mini-btn" onClick={() => disableFactor(f)}><StopCircle size={12} /> 禁用此因子</button>
                        ) : (
                          <button className="mini-btn primary" onClick={async () => { await api.enableFactor(f.id); await loadFactors(); notify("success", "因子已启用"); }}><CheckCircle2 size={12} /> 启用因子</button>
                        )}
                        <button className="mini-btn primary" onClick={() => { setSelectedFactor(f); setTab("trading"); }}><Activity size={12} /> 模拟盘</button>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}

            {tab === "trading" && (
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
    </div>
  );
}
