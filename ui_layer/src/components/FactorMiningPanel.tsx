import { useCallback, useEffect, useMemo, useState } from "react";
import { BarChart3, Play, Radar, RefreshCw, Save, Search, X } from "lucide-react";
import { api } from "../api";

interface FactorMiningPanelProps {
  onNotify?: (type: "success" | "error", msg: string) => void;
}

const TIMEFRAMES = ["M5", "M15", "M30", "H1", "H4", "D1"];
const CAND_SIZES = [200, 500, 1000, 2000];
// 品种下拉兜底：MT5 已连接时会把账户可交易品种合并进来（可手动输入任意品种）
const COMMON_SYMBOLS = [
  "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
  "EURJPY", "GBPJPY", "AUDJPY", "EURGBP", "EURAUD", "XAUUSD", "XAGUSD",
  "US30", "NAS100", "SP500", "BTCUSD",
];

export function FactorMiningPanel({ onNotify }: FactorMiningPanelProps) {
  const [symbol, setSymbol] = useState("EURUSD");
  const [timeframe, setTimeframe] = useState("M15");
  const [maxCand, setMaxCand] = useState(2000);
  const [includeStructures, setIncludeStructures] = useState(true);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [status, setStatus] = useState<Record<string, unknown>>({});
  const [candidates, setCandidates] = useState<Array<Record<string, unknown>>>([]);
  const [busy, setBusy] = useState(false);
  const [kindFilter, setKindFilter] = useState("all");
  const [coreOnly, setCoreOnly] = useState(false);
  const [sortBy, setSortBy] = useState("score");
  const [minWin, setMinWin] = useState("");
  const [minPf, setMinPf] = useState("");
  const [searchText, setSearchText] = useState("");
  const [keepUngated, setKeepUngated] = useState(false);
  const [spreadPoints, setSpreadPoints] = useState("10");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [symbolOptions, setSymbolOptions] = useState<string[]>(COMMON_SYMBOLS);

  const refresh = useCallback(async () => {
    try {
      const s = await api.miningStatus();
      setStatus(s);
      const list = await api.miningCandidates("pending", symbol, undefined, 50);
      setCandidates(list.items ?? []);
    } catch (err) {
      void err;
    }
  }, [symbol]);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 10000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  // 品种下拉：合并 MT5 账户可交易品种与常见兜底列表（MT5 未连接时静默，仅用兜底）
  useEffect(() => {
    let mounted = true;
    api
      .mt5Symbols()
      .then((res) => {
        if (!mounted) return;
        const list = Array.isArray(res.symbols) ? res.symbols : [];
        setSymbolOptions(Array.from(new Set([...COMMON_SYMBOLS, ...list])).slice(0, 800));
      })
      .catch(() => {
        if (mounted) setSymbolOptions(COMMON_SYMBOLS);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const start = async () => {
    setBusy(true);
    try {
      const res = await api.miningRun({
        symbol,
        timeframe,
        max_candidates: maxCand,
        include_structures: includeStructures,
        date_from: dateFrom || null,
        date_to: dateTo || null,
        keep_ungated: keepUngated,
        spread_points: Number(spreadPoints) || 0,
      });
      onNotify?.(res.ok ? "success" : "error", res.message ?? "挖掘任务启动失败");
    } catch (err) {
      onNotify?.("error", "挖掘任务启动失败");
      void err;
    }
    setBusy(false);
    refresh();
  };

  const pauseOrResume = async () => {
    try {
      const paused = Boolean(status.paused);
      const res = paused ? await api.miningResume() : await api.miningPause();
      onNotify?.(res.ok ? "success" : "error", res.message ?? "操作失败");
    } catch (err) {
      onNotify?.("error", "操作失败");
      void err;
    }
    refresh();
  };

  const stopMining = async () => {
    try {
      const res = await api.miningStop();
      onNotify?.(res.ok ? "success" : "error", res.message ?? "停止失败");
    } catch (err) {
      onNotify?.("error", "停止失败");
      void err;
    }
    refresh();
  };

  const accept = async (id: string) => {
    try {
      const res = await api.miningAccept(id);
      onNotify?.(res.ok ? "success" : "error", res.message ?? "保存失败");
    } catch (err) {
      onNotify?.("error", "保存失败");
      void err;
    }
    refresh();
  };

  const ignore = async (id: string) => {
    try {
      const res = await api.miningIgnore(id);
      onNotify?.(res.ok ? "success" : "error", res.message ?? "忽略失败");
    } catch (err) {
      onNotify?.("error", "忽略失败");
      void err;
    }
    refresh();
  };

  const filtered = useMemo(() => {
    const btOf = (c: Record<string, unknown>) => (c.backtest ?? {}) as Record<string, unknown>;
    let list = candidates.filter((c) => {
      if (kindFilter !== "all" && c.kind !== kindFilter) return false;
      if (coreOnly) {
        // 核心候选 = 收益门槛通过（gate=passed：PF≥1.2 · 样本外收益>0 · 笔数≥30 · 一致性非「不稳」 · |t|≥2）
        if (String(c.gate ?? "") !== "passed") return false;
      }
      if (searchText.trim() && !String(c.name ?? "").includes(searchText.trim())) return false;
      if (minWin !== "") {
        const v = Number(btOf(c).win_rate_pct ?? 0);
        if (!(v >= Number(minWin))) return false;
      }
      if (minPf !== "") {
        const v = Number(btOf(c).profit_factor ?? 0);
        if (!(v >= Number(minPf))) return false;
      }
      return true;
    });
    const field = (c: Record<string, unknown>) => {
      if (sortBy === "win_rate") return Number(btOf(c).win_rate_pct ?? 0);
      if (sortBy === "profit_factor") return Number(btOf(c).profit_factor ?? 0);
      if (sortBy === "total_return") return Number(btOf(c).total_return_pct ?? -9999);
      return Number(c.score ?? -9999);
    };
    return list.slice().sort((a, b) => field(b) - field(a));
  }, [candidates, coreOnly, kindFilter, minWin, minPf, searchText, sortBy]);

  const running = Boolean(status.running);

  return (
    <>
      <div className="tool-block">
        <h3>
          <Radar size={13} /> 因子挖掘（自动搜索候选因子）
        </h3>
        <p className="factor-desc">
          从历史行情自动生成入场 / 出场 / 结构位（支撑压力）候选。评估：前 70% 样本分 4 段做 walk-forward 一致性
          （IC / 命中率 / 行情分组标签）+ 后 30% 样本外回测（与实盘一致的 R/ATR 止损止盈口径）。可填「起始 / 结束时间」
          只挖指定历史区间（留空 = 最近 3000 根）。挖掘结果不自动入库 —— 候选经你审阅后逐一「保存」为正式因子。
          ⚠ 多重检验提示：一次挖上千候选，纯随机数据里也必然出现若干「看似优秀」的假阳性 —— 请结合样本外表现、
          4 段一致性、交易笔数与 IC 的 t 值综合判断（|t|≥2 且笔数≥30 才谈得上有统计意义），再决定是否保存。
        </p>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
          <label style={{ fontSize: 12 }}>
            品种
            <input
              className="input"
              list="mining-symbols"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              style={{ width: 100, marginLeft: 4 }}
            />
            <datalist id="mining-symbols">
              {symbolOptions.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
          </label>
          <label style={{ fontSize: 12 }}>
            周期
            <select className="input" value={timeframe} onChange={(e) => setTimeframe(e.target.value)} style={{ marginLeft: 4 }}>
              {TIMEFRAMES.map((tf) => (
                <option key={tf} value={tf}>
                  {tf}
                </option>
              ))}
            </select>
          </label>
          <label style={{ fontSize: 12 }}>
            候选上限
            <select className="input" value={maxCand} onChange={(e) => setMaxCand(Number(e.target.value))} style={{ marginLeft: 4 }}>
              {CAND_SIZES.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
            <input type="checkbox" checked={includeStructures} onChange={(e) => setIncludeStructures(e.target.checked)} />
            含结构位（支撑/压力）
          </label>
          <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
            <input type="checkbox" checked={keepUngated} onChange={(e) => setKeepUngated(e.target.checked)} />
            保留未达门槛候选
            <span
              style={{ color: "#888", cursor: "help" }}
              title="不勾选时，盈亏比<1.2 / 样本外收益≤0 / 笔数<30 等未达门槛候选被直接拦截，候选池只留达到收益门槛的候选"
            >
              ⓘ
            </span>
          </label>
          <label style={{ fontSize: 12 }}>
            点差(点)
            <input
              className="input"
              type="number"
              value={spreadPoints}
              onChange={(e) => setSpreadPoints(e.target.value)}
              style={{ width: 56, marginLeft: 4 }}
              title="回测每笔计入的点差成本（10 点 = 1 pip；0 = 不计点差）"
            />
          </label>
          <label style={{ fontSize: 12 }}>
            起始时间
            <input className="input" type="datetime-local" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} style={{ marginLeft: 4 }} />
          </label>
          <label style={{ fontSize: 12 }}>
            结束时间
            <input className="input" type="datetime-local" value={dateTo} onChange={(e) => setDateTo(e.target.value)} style={{ marginLeft: 4 }} />
          </label>
          <button className="mini-btn primary" disabled={busy || running} onClick={start}>
            <Play size={12} /> {running ? "挖掘进行中…" : "开始挖掘"}
          </button>
          <button className="mini-btn" onClick={pauseOrResume} disabled={!running} style={{ marginLeft: 4 }}>
            {Boolean(status.paused) ? "▶ 继续" : "⏸ 暂停"}
          </button>
          <button className="mini-btn" onClick={stopMining} disabled={!running} style={{ marginLeft: 4 }}>
            ✕ 停止
          </button>
          <button className="mini-btn" onClick={refresh} disabled={running}>
            <RefreshCw size={12} /> 刷新
          </button>
        </div>
        <div className="metric-grid" style={{ marginBottom: 8 }}>
          <div className="metric">
            <span className="metric-label">任务状态</span>
            <span className="metric-value">{running ? (Boolean(status.paused) ? "已暂停" : "运行中") : String(status.message ?? "未开始")}</span>
          </div>
          <div className="metric">
            <span className="metric-label">候选总数</span>
            <span className="metric-value">{Number(status.total ?? 0)}</span>
          </div>
          <div className="metric">
            <span className="metric-label">已评估</span>
            <span className="metric-value">{Number(status.done ?? 0)}</span>
          </div>
          <div className="metric">
            <span className="metric-label">通过评估</span>
            <span className="metric-value">{Number(status.ok ?? 0)}</span>
          </div>
          <div className="metric">
            <span className="metric-label">待审阅候选</span>
            <span className="metric-value">{Number(status.pending_count ?? 0)}</span>
          </div>
          <div className="metric">
            <span className="metric-label">达门槛/未达</span>
            <span className="metric-value">{Number(status.gated ?? 0)} / {Number(status.ungated ?? 0)}</span>
          </div>
        </div>
        {running && (
          <div style={{ marginBottom: 8 }}>
            <div style={{ height: 6, borderRadius: 3, background: "#222", overflow: "hidden" }}>
              <div
                style={{
                  width: `${Number(status.percent ?? 0)}%`,
                  height: "100%",
                  background: "#48f",
                  transition: "width .3s",
                }}
              />
            </div>
            <p className="factor-desc" style={{ color: "#9be", marginTop: 4 }}>
              进度 {Number(status.percent ?? 0).toFixed(1)}% · 已评估 {Number(status.done ?? 0)}/{Number(status.total ?? 0)} · 当前：
              {String(status.current ?? "-")}（沙盒逐个执行：前 70% 评估 + 后 30% 样本外回测）
            </p>
          </div>
        )}
      </div>

      <div className="tool-block">
        <h4>
          <Search size={13} /> 候选清单（待审阅，不自动入库）
        </h4>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
          <label style={{ fontSize: 12 }}>
            类型
            <select className="input" value={kindFilter} onChange={(e) => setKindFilter(e.target.value)} style={{ marginLeft: 4 }}>
              <option value="all">全部</option>
              <option value="entry">入场</option>
              <option value="exit">出场</option>
            </select>
          </label>
          <button
            className={coreOnly ? "mini-btn primary" : "mini-btn"}
            onClick={() => setCoreOnly((v) => !v)}
            style={{ marginLeft: 4 }}
            title="核心候选 = 4段一致性(稳定/一般) + |IC t|≥2 + 样本外≥30 笔"
          >
            {coreOnly ? "核心候选 ✓" : "核心候选"}
          </button>
          <label style={{ fontSize: 12 }}>
            排序
            <select className="input" value={sortBy} onChange={(e) => setSortBy(e.target.value)} style={{ marginLeft: 4 }}>
              <option value="score">评分</option>
              <option value="win_rate">胜率</option>
              <option value="profit_factor">盈亏比</option>
              <option value="total_return">总收益</option>
            </select>
          </label>
          <label style={{ fontSize: 12 }}>
            最低胜率%
            <input
              className="input"
              type="number"
              value={minWin}
              onChange={(e) => setMinWin(e.target.value)}
              style={{ width: 64, marginLeft: 4 }}
            />
          </label>
          <label style={{ fontSize: 12 }}>
            最低盈亏比
            <input
              className="input"
              type="number"
              value={minPf}
              onChange={(e) => setMinPf(e.target.value)}
              style={{ width: 64, marginLeft: 4 }}
            />
          </label>
          <input
            className="input"
            placeholder="搜索名称…"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            style={{ width: 140 }}
          />
          <span style={{ fontSize: 12, color: "#888" }}>共 {filtered.length} 条</span>
        </div>
        {candidates.length === 0 ? (
          <p className="factor-desc">
            暂无候选。点击「开始挖掘」生成第一批候选（默认上限 2000，按参数网格实际约生成 1000+ 个：动量 / 均线 / 通道 /
            结构位 / 出场类模板 × 参数 × 趋势滤波与延迟确认变体）。
          </p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {filtered.map((c) => {
              const bt = (c.backtest ?? {}) as Record<string, unknown>;
              const groups = (c.groups ?? {}) as Record<string, Record<string, unknown>>;
              const tags = Object.keys(groups);
              const wf = (c.wf ?? {}) as Record<string, unknown>;
              const wfIcs = (wf.ic ?? []) as Array<number>;
              const wfLevel = String(wf.level ?? "-");
              return (
                <div key={String(c.id)} style={{ border: "1px solid #333", borderRadius: 6, padding: "8px 10px" }}>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
                    <b>{String(c.name)}</b>
                    <span style={{ color: "#9be", fontSize: 12 }}>
                      {String(c.kind)} / {String(c.variant)}
                    </span>
                    <span style={{ color: "#888", fontSize: 12 }}>信号 {String(c.signal_bars ?? 0)} 根</span>
                    <span style={{ color: "#888", fontSize: 12 }}>
                      {String(c.symbol)} {String(c.timeframe)}
                    </span>
                    <span style={{ color: "#888", fontSize: 12 }}>
                      评估 {String(c.eval_bars ?? 0)} 根 + 样本外回测 {String(c.bt_bars ?? 0)} 根
                    </span>
                  </div>
                  <div style={{ marginTop: 4, fontSize: 12, color: "#bbb", display: "flex", gap: 10, flexWrap: "wrap" }}>
                    <span>
                      IC <b>{Number(c.ic ?? 0).toFixed(3)}</b>{" "}
                      <span style={{ color: "#888" }}>t={Number(c.ic_tstat ?? 0).toFixed(2)}</span>
                    </span>
                    <span>
                      ICIR <b>{Number(c.icir ?? 0).toFixed(2)}</b>
                    </span>
                    <span>
                      4段一致性{" "}
                      <b
                        style={{
                          color: wfLevel === "稳定" ? "#8e8" : wfLevel === "一般" ? "#ee8" : "#e88",
                        }}
                      >
                        {wfLevel}
                      </b>
                      <span style={{ color: "#888" }}>
                        （各段IC：{wfIcs.length ? wfIcs.map((x) => Number(x).toFixed(3)).join(" / ") : "-"}）
                      </span>
                    </span>
                    <span>
                      过拟合概率(DSR){" "}
                      <b style={{ color: Number(c.dsr ?? 0) >= 0.95 ? "#8e8" : "#ee8" }}>
                        {Math.round(Number(c.dsr ?? 0) * 100)}%
                      </b>{" "}
                      <span style={{ color: Number(c.t_adj ?? 0) >= 1 ? "#8e8" : "#888" }}>
                        t修正比值 {Number(c.t_adj ?? 0).toFixed(2)}
                        {Number(c.t_adj ?? 0) >= 1 ? " ✓" : `（需≥${Number(c.t_required ?? 2).toFixed(1)}，哈维阈值）`}
                      </span>
                    </span>
                    <span>
                      命中 <b>{Number((Number(c.hit_rate ?? 0) * 100).toFixed(1))}%</b>
                    </span>
                    <span>
                      评分 <b>{Number(c.score ?? 0).toFixed(3)}</b>
                    </span>
                    <span>行情标签：{tags.length ? tags.join("、") : "无"}</span>
                  </div>
                  {c.gate === "passed" ? (
                    <div
                      style={{
                        marginTop: 4,
                        fontSize: 12,
                        color: "#8e8",
                        padding: "2px 6px",
                        background: "#233",
                        borderRadius: 4,
                      }}
                    >
                      ✓ 达收益门槛（PF≥1.2 · 样本外收益{"&gt;"}0 · 笔数≥30 · 一致性非「不稳」 · |t|≥2）
                    </div>
                  ) : (
                    <div
                      style={{
                        marginTop: 4,
                        fontSize: 12,
                        color: "#999",
                        padding: "2px 6px",
                        background: "#222",
                        borderRadius: 4,
                      }}
                    >
                      未达门槛：{String(c.gate_reason ?? "")}
                    </div>
                  )}
                  {Boolean(c.sample_note) && (
                    <div
                      style={{
                        marginTop: 4,
                        fontSize: 12,
                        color: "#ee8",
                        padding: "2px 6px",
                        background: "#332",
                        borderRadius: 4,
                      }}
                    >
                      ⚠ {String(c.sample_note)}
                    </div>
                  )}
                  <div style={{ marginTop: 4, fontSize: 12, color: "#888" }}>
                    {c.kind === "exit" ? (
                      "出场因子：仅评估出口正确性（出场后价格反向），独立回测需与入场因子搭配"
                    ) : (
                      <>
                        回测：胜率 {Number(bt.win_rate_pct ?? 0).toFixed(1)}% · 盈亏比 {Number(bt.profit_factor ?? 0).toFixed(2)} ·
                        总收益 {Number(bt.total_return_pct ?? 0).toFixed(1)}% · 最大回撤 {Number(bt.max_drawdown_pct ?? 0).toFixed(1)}% ·{" "}
                        {String(bt.num_trades ?? 0)} 笔
                      </>
                    )}
                  </div>
                  <div style={{ marginTop: 6, display: "flex", gap: 6 }}>
                    <button className="mini-btn primary" onClick={() => accept(String(c.id))}>
                      <Save size={12} /> 保存为因子
                    </button>
                    <button className="mini-btn" onClick={() => ignore(String(c.id))}>
                      <X size={12} /> 忽略
                    </button>
                    <button
                      className="mini-btn"
                      onClick={() => setExpandedId(expandedId === String(c.id) ? null : String(c.id))}
                    >
                      <BarChart3 size={12} /> {expandedId === String(c.id) ? "收起回测" : "查看回测数据"}
                    </button>
                  </div>
                  {expandedId === String(c.id) && (
                    <div style={{ marginTop: 6, borderTop: "1px dashed #333", paddingTop: 6, fontSize: 12 }}>
                      <div className="metric-grid" style={{ marginBottom: 6 }}>
                        <div className="metric">
                          <span className="metric-label">胜率</span>
                          <span className="metric-value">{Number(bt.win_rate_pct ?? 0).toFixed(1)}%</span>
                        </div>
                        <div className="metric">
                          <span className="metric-label">盈亏比</span>
                          <span className="metric-value">{Number(bt.profit_factor ?? 0).toFixed(2)}</span>
                        </div>
                        <div className="metric">
                          <span className="metric-label">总收益</span>
                          <span className="metric-value">{Number(bt.total_return_pct ?? 0).toFixed(1)}%</span>
                        </div>
                        <div className="metric">
                          <span className="metric-label">年化</span>
                          <span className="metric-value">{Number(bt.annual_return_pct ?? 0).toFixed(1)}%</span>
                        </div>
                        <div className="metric">
                          <span className="metric-label">夏普</span>
                          <span className="metric-value">{Number(bt.sharpe ?? 0).toFixed(2)}</span>
                        </div>
                        <div className="metric">
                          <span className="metric-label">最大回撤</span>
                          <span className="metric-value">{Number(bt.max_drawdown_pct ?? 0).toFixed(1)}%</span>
                        </div>
                        <div className="metric">
                          <span className="metric-label">平均每笔</span>
                          <span className="metric-value">{Number(bt.avg_trade_pct ?? 0).toFixed(3)}%</span>
                        </div>
                        <div className="metric">
                          <span className="metric-label">样本外分段收益</span>
                          <span className="metric-value">
                            {(() => {
                              const segs = (bt.out_segments ?? []) as Array<number>;
                              return segs.length
                                ? segs.map((s) => `${Number(s).toFixed(1)}%`).join(" / ")
                                : "（段过短未切）";
                            })()}
                          </span>
                        </div>
                      </div>
                      {(() => {
                        const trades = (bt.trades ?? []) as Array<Record<string, unknown>>;
                        if (!trades.length)
                          return <span style={{ color: "#888" }}>（样本外回测段无交易，仅评估段有数据）</span>;
                        return (
                          <>
                            <div style={{ color: "#aaa", marginBottom: 4 }}>
                              逐笔明细（样本外回测段，前 {Math.min(trades.length, 30)}/{trades.length} 笔）
                            </div>
                            <table style={{ width: "100%", borderCollapse: "collapse" }}>
                              <thead>
                                <tr style={{ color: "#888" }}>
                                  <th style={{ textAlign: "left", padding: "2px 6px" }}>方向</th>
                                  <th style={{ textAlign: "left", padding: "2px 6px" }}>入场时间</th>
                                  <th style={{ textAlign: "left", padding: "2px 6px" }}>入场价</th>
                                  <th style={{ textAlign: "left", padding: "2px 6px" }}>出场时间</th>
                                  <th style={{ textAlign: "left", padding: "2px 6px" }}>出场价</th>
                                  <th style={{ textAlign: "left", padding: "2px 6px" }}>盈亏%</th>
                                  <th style={{ textAlign: "left", padding: "2px 6px" }}>持仓K线</th>
                                  <th style={{ textAlign: "left", padding: "2px 6px" }}>离场原因</th>
                                </tr>
                              </thead>
                              <tbody>
                                {trades.slice(0, 30).map((t, i) => (
                                  <tr key={i} style={{ borderTop: "1px solid #222" }}>
                                    <td style={{ padding: "2px 6px", color: t.side === "long" ? "#8e8" : "#e88" }}>
                                      {String(t.side)}
                                    </td>
                                    <td style={{ padding: "2px 6px" }}>
                                      {String(t.entry_time ?? "").replace("T", " ").slice(0, 16)}
                                    </td>
                                    <td style={{ padding: "2px 6px" }}>{Number(t.entry_price ?? 0).toFixed(5)}</td>
                                    <td style={{ padding: "2px 6px" }}>
                                      {String(t.exit_time ?? "").replace("T", " ").slice(0, 16)}
                                    </td>
                                    <td style={{ padding: "2px 6px" }}>{Number(t.exit_price ?? 0).toFixed(5)}</td>
                                    <td style={{ padding: "2px 6px", color: Number(t.pnl_pct ?? 0) >= 0 ? "#8e8" : "#e88" }}>
                                      {Number(t.pnl_pct ?? 0).toFixed(3)}%
                                    </td>
                                    <td style={{ padding: "2px 6px" }}>{Number(t.bars_held ?? 0)}</td>
                                    <td style={{ padding: "2px 6px" }}>{String(t.exit_reason ?? "")}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </>
                        );
                      })()}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}