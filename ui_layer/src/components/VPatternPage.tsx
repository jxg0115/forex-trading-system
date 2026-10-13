import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import {
  Activity,
  ArrowLeft,
  BarChart3,
  CircleAlert,
  LineChart,
  Play,
  RefreshCw,
  Save,
  ShieldCheck,
  Square,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { api } from "../api";

interface Props {
  onNotify?: (type: "success" | "error", message: string) => void;
  /** 返回主界面（点「返回」按钮或浏览器后退） */
  onBack?: () => void;
}

const RISK_FIELDS: Array<{ key: string; label: string; step: string; hint: string }> = [
  { key: "risk_pct", label: "每笔风险 (%)", step: "0.5", hint: "按账户权益的百分比作为单笔风险预算" },
  { key: "max_positions", label: "最大同时持仓", step: "1", hint: "不叠单，默认 1 笔" },
  { key: "loss_streak_halve", label: "连亏减半阈值", step: "1", hint: "连亏达 N 笔后仓位减半（每再亏 N 笔再减半，最低 1/8）" },
  { key: "halt_daily_loss_pct", label: "当日停手线 (%)", step: "1", hint: "当日亏损达此比例即停手，等下一个交易日自动恢复" },
  { key: "cooldown_minutes", label: "两次开仓间隔 (分)", step: "5", hint: "防止连续开仓" },
];

const S: Record<string, CSSProperties> = {
  page: { minHeight: "100vh", background: "var(--bg, #f5f6f8)", padding: "16px 20px 40px" },
  topbar: { display: "flex", alignItems: "center", gap: 12, marginBottom: 14, flexWrap: "wrap" },
  title: { fontSize: 18, fontWeight: 700, display: "flex", alignItems: "center", gap: 8, whiteSpace: "nowrap" },
  topRight: { display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginLeft: "auto" },
  back: {
    display: "flex", gap: 6, alignItems: "center", padding: "6px 12px",
    border: "1px solid var(--border, #e3e3e8)", borderRadius: 8, cursor: "pointer",
    fontSize: 13, background: "var(--panel, #fff)",
  },
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 14, alignItems: "start" },
  card: { border: "1px solid var(--border, #e3e3e8)", borderRadius: 12, padding: 16, background: "var(--panel, #fff)" },
  cardTitle: { fontSize: 14, fontWeight: 600, marginBottom: 12, display: "flex", gap: 6, alignItems: "center" },
  metricGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))", gap: 10 },
  metric: { padding: "10px 12px", border: "1px solid var(--border, #eceef2)", borderRadius: 10, background: "var(--bg-soft, #fafbfc)" },
  metricLabel: { fontSize: 11, color: "var(--muted, #8a8f99)" },
  metricValue: { fontSize: 18, fontWeight: 600, marginTop: 4, wordBreak: "break-all" },
  fieldRow: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10, marginBottom: 10 },
  label: { fontSize: 12, color: "var(--muted, #8a8f99)", marginBottom: 4 },
  input: { width: "100%", boxSizing: "border-box", padding: "7px 9px", border: "1px solid #d8d8de", borderRadius: 7, fontSize: 13 },
  row: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" },
  btn: {
    padding: "7px 14px", border: "1px solid #d8d8de", borderRadius: 8, background: "#f6f6f8",
    cursor: "pointer", fontSize: 13, display: "flex", gap: 6, alignItems: "center",
  },
  primary: { background: "#e8f0fe", borderColor: "#9dbcf0", color: "#1b4b8f" },
  danger: { background: "#fdeaea", borderColor: "#e6a5a5", color: "#8a2020" },
  hint: { color: "var(--muted, #8a8f99)", fontSize: 12, lineHeight: 1.7 },
  badge: { padding: "2px 9px", borderRadius: 999, fontSize: 12, border: "1px solid #d8d8de" },
  table: { width: "100%", borderCollapse: "collapse", fontSize: 12 },
  th: { textAlign: "left", padding: "7px 8px", borderBottom: "1px solid var(--border, #e3e3e8)", color: "var(--muted, #8a8f99)", fontWeight: 500, whiteSpace: "nowrap" },
  td: { padding: "7px 8px", borderBottom: "1px solid var(--border-soft, #f1f2f5)", whiteSpace: "nowrap" },
  empty: { color: "var(--muted, #8a8f99)", fontSize: 12, padding: "14px 0", textAlign: "center" },
};

function num(v: unknown, fb = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fb;
}

function fmt(v: unknown, digits = 2): string {
  if (v === null || v === undefined || v === "") return "--";
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(digits) : "--";
}

/** 盈亏配色：红涨绿跌（国内习惯）。 */
function pnlColor(v: unknown): string {
  const n = num(v);
  if (n > 0) return "var(--red, #d9342b)";
  if (n < 0) return "var(--green, #12a150)";
  return "var(--muted, #8a8f99)";
}

export function VPatternPage({ onNotify, onBack }: Props) {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [conf, setConf] = useState<Record<string, unknown>>({});
  const [ledger, setLedger] = useState<{ trades: Array<Record<string, unknown>>; stats: Record<string, unknown>; daily: Array<Record<string, unknown>> }>({
    trades: [],
    stats: {},
    daily: [],
  });
  const [busy, setBusy] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const load = useCallback(async () => {
    try {
      const res = (await api.vpatternState()) as Record<string, unknown>;
      setData(res);
      setConf((res.config ?? {}) as Record<string, unknown>);
    } catch (err) {
      onNotify?.("error", String(err instanceof Error ? err.message : err));
    }
  }, [onNotify]);

  const loadLedger = useCallback(async () => {
    try {
      const res = await api.vpatternTrades();
      setLedger({
        trades: (res.trades ?? []) as Array<Record<string, unknown>>,
        stats: (res.stats ?? {}) as Record<string, unknown>,
        daily: (res.daily ?? []) as Array<Record<string, unknown>>,
      });
    } catch {
      /* 账本读取失败不打断主流程 */
    }
  }, []);

  useEffect(() => {
    load();
    loadLedger();
  }, [load, loadLedger]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => {
      load();
      loadLedger();
    }, 10000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, load, loadLedger]);

  // 浏览器后退键 = 返回主界面
  useEffect(() => {
    const onPop = () => onBack?.();
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [onBack]);

  const toggle = async () => {
    setBusy(true);
    try {
      const next = !Boolean(data?.enabled);
      const res = await api.vpatternToggle(next);
      onNotify?.(res.success ? "success" : "error", res.message);
      await load();
    } catch (err) {
      onNotify?.("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    try {
      const payload: Record<string, unknown> = {};
      for (const f of RISK_FIELDS) {
        if (f.key in conf) payload[f.key] = num(conf[f.key]);
      }
      const res = await api.vpatternConfig(payload);
      onNotify?.(res.success ? "success" : "error", res.message);
      await load();
    } catch (err) {
      onNotify?.("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  };

  const goBack = () => {
    if (onBack) onBack();
    else window.history.back();
  };

  const available = Boolean(data?.available ?? data?.enabled !== undefined);
  const enabled = Boolean(data?.enabled);
  const st = (data?.stats ?? ledger.stats ?? {}) as Record<string, unknown>;
  const scan = (data?.last_scan ?? {}) as Record<string, unknown>;
  const signals = ((data?.recent_signals ?? []) as Array<Record<string, unknown>>).slice(0, 20);
  const orders = ((data?.recent_orders ?? []) as Array<Record<string, unknown>>).slice(0, 20);
  const lastError = String(data?.last_error ?? "");

  const m15Active = ((scan.m15_active ?? []) as string[]).join(" / ") || "无";
  const m30Active = ((scan.m30_active ?? []) as string[]).join(" / ") || "无";

  const dailyMax = useMemo(() => {
    const vals = ledger.daily.map((d) => Math.abs(num(d.r)));
    return Math.max(1, ...vals);
  }, [ledger.daily]);

  return (
    <div style={S.page}>
      <div style={S.topbar}>
        <button style={S.back} onClick={goBack}>
          <ArrowLeft size={14} /> 返回主界面
        </button>
        <div style={{ ...S.title, marginLeft: 4 }}>
          <LineChart size={20} /> K 线形态独立交易
        </div>
        <span
          style={{
            ...S.badge,
            color: enabled ? "var(--red, #d9342b)" : "var(--muted, #8a8f99)",
            borderColor: enabled ? "var(--red, #d9342b)" : "#d8d8de",
            fontWeight: 600,
          }}
        >
          {enabled ? "● 运行中" : "○ 已停止"}
        </span>
        <div style={{ flex: 1, minWidth: 12 }} />
        <div style={S.topRight}>
          <label style={{ ...S.hint, display: "flex", gap: 6, alignItems: "center", cursor: "pointer", whiteSpace: "nowrap" }}>
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
            自动刷新（10 秒）
          </label>
          <button style={{ ...S.btn, whiteSpace: "nowrap" }} onClick={() => { load(); loadLedger(); }}>
            <RefreshCw size={13} /> 刷新
          </button>
        </div>
      </div>

      <div style={{ ...S.hint, marginBottom: 14 }}>
        策略：M15 与 M30 <strong>各自</strong>走完 V 字底（倒 V 则做空），且 H1 最新一根收在同向半边 → 进场。
        本模块自带仓位与风控，<strong>完全不受镜像交易开关影响</strong>，也不占用主系统的信号通道。
      </div>

      {!available && (
        <div style={{ ...S.card, borderColor: "#e6a5a5", background: "#fdeaea", marginBottom: 14, color: "#8a2020" }}>
          <CircleAlert size={14} /> 模块未初始化（需 MT5 连接）。请确认后端已连上 MT5 终端。
        </div>
      )}
      {lastError && (
        <div style={{ ...S.card, borderColor: "#f0d9a0", background: "#fff8e6", marginBottom: 14 }}>
          <CircleAlert size={14} /> 最近异常：{lastError}
        </div>
      )}

      <div style={S.grid}>
        {/* ===== 开关与说明 ===== */}
        <div style={S.card}>
          <div style={S.cardTitle}><ShieldCheck size={14} /> 总开关</div>
          <div style={S.row}>
            <button
              style={{ ...S.btn, ...(enabled ? S.danger : S.primary) }}
              disabled={busy || !available}
              onClick={toggle}
            >
              {enabled ? <Square size={13} /> : <Play size={13} />}
              {enabled ? "关闭自动交易" : "开启自动交易"}
            </button>
          </div>
          <div style={{ ...S.hint, marginTop: 10 }}>
            {enabled
              ? "模块正在每 60 秒检查一次 K 线形态。满足条件会自动开仓（带止损止盈）。"
              : "当前不会自动开仓。开启后模块按 M15/M30/H1 三重条件自行判断。"}
          </div>
        </div>

        {/* ===== 实时判断 ===== */}
        <div style={S.card}>
          <div style={S.cardTitle}><Activity size={14} /> 实时判断</div>
          <div style={S.metricGrid}>
            <div style={S.metric}>
              <div style={S.metricLabel}>K线时刻</div>
              <div style={{ ...S.metricValue, fontSize: 14 }}>{String(scan.time ?? "--").slice(5, 16)}</div>
            </div>
            <div style={S.metric}>
              <div style={S.metricLabel}>价格</div>
              <div style={{ ...S.metricValue, fontSize: 14 }}>{fmt(scan.price)}</div>
            </div>
            <div style={S.metric}>
              <div style={S.metricLabel}>ATR(14)</div>
              <div style={{ ...S.metricValue, fontSize: 14 }}>{fmt(scan.atr15)}</div>
            </div>
            <div style={S.metric}>
              <div style={S.metricLabel}>H1 佐证</div>
              <div style={{ ...S.metricValue, fontSize: 14 }}>{String(scan.h1_half ?? "--")}</div>
            </div>
          </div>
          <div style={{ marginTop: 12, display: "grid", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
              <span style={S.hint}>M15 状态</span>
              <strong>{m15Active}</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
              <span style={S.hint}>M30 状态</span>
              <strong>{m30Active}</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
              <span style={S.hint}>本次方向</span>
              <strong>{String(scan.side ?? "无（未同时满足）")}</strong>
            </div>
          </div>
        </div>

        {/* ===== 独立统计 ===== */}
        <div style={S.card}>
          <div style={S.cardTitle}><TrendingUp size={14} /> 独立统计（本模块自己的账）</div>
          <div style={S.metricGrid}>
            <div style={S.metric}><div style={S.metricLabel}>已平仓</div><div style={S.metricValue}>{num(st.n)}</div></div>
            <div style={S.metric}><div style={S.metricLabel}>持仓中</div><div style={S.metricValue}>{num(st.open)}</div></div>
            <div style={S.metric}>
              <div style={S.metricLabel}>胜率</div>
              <div style={S.metricValue}>{st.win_rate == null ? "--" : `${num(st.win_rate).toFixed(1)}%`}</div>
            </div>
            <div style={S.metric}>
              <div style={S.metricLabel}>盈亏比</div>
              <div style={S.metricValue}>{st.profit_factor == null ? "--" : num(st.profit_factor).toFixed(2)}</div>
            </div>
            <div style={S.metric}>
              <div style={S.metricLabel}>累计 R</div>
              <div style={{ ...S.metricValue, color: pnlColor(st.total_r) }}>{num(st.total_r).toFixed(1)}</div>
            </div>
            <div style={S.metric}>
              <div style={S.metricLabel}>累计盈亏</div>
              <div style={{ ...S.metricValue, color: pnlColor(st.total_pnl) }}>{num(st.total_pnl).toFixed(2)}</div>
            </div>
            <div style={S.metric}><div style={S.metricLabel}>最大回撤 R</div><div style={S.metricValue}>{num(st.max_dd_r).toFixed(1)}</div></div>
            <div style={S.metric}>
              <div style={S.metricLabel}>多 / 空</div>
              <div style={{ ...S.metricValue, fontSize: 14 }}>{num(st.n_long)} / {num(st.n_short)}</div>
            </div>
          </div>
        </div>

        {/* ===== 仓位与风控 ===== */}
        <div style={S.card}>
          <div style={S.cardTitle}><TrendingDown size={14} /> 仓位与风控</div>
          <div style={S.metricGrid}>
            <div style={S.metric}><div style={S.metricLabel}>当前连亏</div><div style={S.metricValue}>{num(conf.consec_loss)}</div></div>
            <div style={S.metric}>
              <div style={S.metricLabel}>当日盈亏</div>
              <div style={{ ...S.metricValue, color: pnlColor(conf.day_pnl) }}>{num(conf.day_pnl).toFixed(2)}</div>
            </div>
            <div style={S.metric}>
              <div style={S.metricLabel}>当日停手</div>
              <div style={S.metricValue}>{conf.day_halted ? "是" : "否"}</div>
            </div>
          </div>
          <div style={{ ...S.fieldRow, marginTop: 14 }}>
            {RISK_FIELDS.map((f) => (
              <div key={f.key}>
                <div style={S.label}>{f.label}</div>
                <input
                  style={S.input}
                  type="number"
                  step={f.step}
                  min="0"
                  value={String(conf[f.key] ?? "")}
                  onChange={(e) => setConf((prev) => ({ ...prev, [f.key]: e.target.value }))}
                />
                <div style={{ ...S.hint, fontSize: 11, marginTop: 4 }}>{f.hint}</div>
              </div>
            ))}
          </div>
          <button style={{ ...S.btn, ...S.primary }} disabled={busy} onClick={save}>
            <Save size={13} /> 保存风控参数
          </button>
        </div>
      </div>

      {/* ===== 按日明细 ===== */}
      <div style={{ ...S.card, marginTop: 14 }}>
        <div style={S.cardTitle}><BarChart3 size={14} /> 按日表现</div>
        {ledger.daily.length === 0 ? (
          <div style={S.empty}>暂无数据。模块开仓并平仓后自动汇总（今天是休市日）。</div>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {ledger.daily.map((d) => {
              const r = num(d.r);
              const w = Math.round((Math.abs(r) / dailyMax) * 100);
              return (
                <div key={String(d.date)} style={{ display: "grid", gridTemplateColumns: "92px 1fr 150px", gap: 10, alignItems: "center" }}>
                  <span style={{ fontSize: 12 }}>{String(d.date).slice(5)}</span>
                  <div style={{ height: 16, background: "var(--bg-soft, #fafbfc)", borderRadius: 4, position: "relative", overflow: "hidden" }}>
                    <div
                      style={{
                        position: "absolute", top: 0, bottom: 0,
                        [r >= 0 ? "left" : "right"]: "50%",
                        width: `${w / 2}%`,
                        background: r >= 0 ? "var(--red, #d9342b)" : "var(--green, #12a150)",
                        opacity: 0.75,
                      }}
                    />
                    <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: "var(--border, #e3e3e8)" }} />
                  </div>
                  <span style={{ fontSize: 12, textAlign: "right" }}>
                    {num(d.n)} 笔 胜率 {d.win_rate == null ? "--" : `${num(d.win_rate).toFixed(0)}%`} ·{" "}
                    <strong style={{ color: pnlColor(r) }}>{r > 0 ? "+" : ""}{r.toFixed(1)}R</strong>
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ===== 账本明细 ===== */}
      <div style={{ ...S.card, marginTop: 14 }}>
        <div style={S.cardTitle}><Activity size={14} /> 交易账本</div>
        {ledger.trades.length === 0 ? (
          <div style={S.empty}>暂无成交记录。</div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={S.table}>
              <thead>
                <tr>
                  <th style={S.th}>开仓时间</th>
                  <th style={S.th}>方向</th>
                  <th style={S.th}>手数</th>
                  <th style={S.th}>入场</th>
                  <th style={S.th}>止损</th>
                  <th style={S.th}>止盈</th>
                  <th style={S.th}>平仓价</th>
                  <th style={S.th}>R</th>
                  <th style={S.th}>盈亏</th>
                  <th style={S.th}>出场原因</th>
                  <th style={S.th}>状态</th>
                </tr>
              </thead>
              <tbody>
                {ledger.trades.map((t, i) => (
                  <tr key={i}>
                    <td style={S.td}>{String(t.open_time ?? t.signal_time ?? "").slice(5, 16)}</td>
                    <td style={{ ...S.td, color: t.side === "long" ? "var(--red, #d9342b)" : "var(--green, #12a150)" }}>
                      {t.side === "long" ? "做多" : "做空"}
                    </td>
                    <td style={S.td}>{fmt(t.lots, 2)}</td>
                    <td style={S.td}>{fmt(t.entry_price)}</td>
                    <td style={S.td}>{fmt(t.sl)}</td>
                    <td style={S.td}>{fmt(t.tp)}</td>
                    <td style={S.td}>{fmt(t.close_price)}</td>
                    <td style={{ ...S.td, color: pnlColor(t.r_multiple) }}>
                      {t.r_multiple == null ? "--" : `${num(t.r_multiple) > 0 ? "+" : ""}${num(t.r_multiple).toFixed(2)}`}
                    </td>
                    <td style={{ ...S.td, color: pnlColor(t.pnl) }}>{t.pnl == null ? "--" : `${num(t.pnl) > 0 ? "+" : ""}${num(t.pnl).toFixed(2)}`}</td>
                    <td style={S.td}>{String(t.exit_reason ?? "--")}</td>
                    <td style={S.td}>{t.status === "open" ? "持仓中" : "已平仓"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ===== 信号 / 开仓日志 ===== */}
      <div style={{ ...S.grid, marginTop: 14 }}>
        <div style={S.card}>
          <div style={S.cardTitle}>最近信号</div>
          {signals.length === 0 ? (
            <div style={S.empty}>暂无信号记录。</div>
          ) : (
            signals.map((s, i) => (
              <div key={i} style={{ ...S.hint, fontSize: 12, padding: "4px 0", borderBottom: "1px solid var(--border-soft, #f1f2f5)" }}>
                {String(s.time ?? "").slice(5, 16)} · {String(s.side)} · {String(s.action)}
                {s.action === "skip" ? ` — ${String(s.reason ?? "")}` : ` @ ${fmt(s.entry)}`}
              </div>
            ))
          )}
        </div>
        <div style={S.card}>
          <div style={S.cardTitle}>最近开仓</div>
          {orders.length === 0 ? (
            <div style={S.empty}>暂无开仓记录。</div>
          ) : (
            orders.map((o, i) => (
              <div key={i} style={{ ...S.hint, fontSize: 12, padding: "4px 0", borderBottom: "1px solid var(--border-soft, #f1f2f5)" }}>
                {String(o.time ?? "").slice(5, 16)} · {String(o.side)} {fmt(o.lots)} 手 @ {fmt(o.entry)} ·
                SL {fmt(o.sl)} / TP {fmt(o.tp)}（#{String(o.ticket ?? "")}）
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
