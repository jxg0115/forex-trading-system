import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { Calendar, ChevronDown, ChevronUp, RefreshCw } from "lucide-react";
import { api, type CalendarPayload } from "../api";

const CARD: CSSProperties = {
  background: "var(--card-bg, #151a24)",
  border: "1px solid var(--border, #2a3342)",
  borderRadius: 10,
  padding: 16,
  color: "var(--text, #d7e0ea)",
  fontFamily: "inherit",
};
const ROW: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  padding: "6px 0",
  borderBottom: "1px solid #222b3a",
  fontSize: 13,
  gap: 8,
  alignItems: "center",
};
const LABEL: CSSProperties = { fontSize: 12, color: "#8a94a6" };

function fmtDate(ts: number): string {
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}
function pnlColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return "#8a94a6";
  return v > 0 ? "#4ade80" : v < 0 ? "#ff8f8f" : "#8a94a6";
}
function fmtPnl(v: number | null | undefined): string {
  if (v === null || v === undefined) return "--";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}`;
}
function todayStr(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`;
}
function shiftDay(day: string, delta: number): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + delta);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`;
}

type RangeKey = "today" | "7d" | "30d" | "custom";

export function CalendarPanel() {
  const [range, setRange] = useState<RangeKey>("7d");
  const [from, setFrom] = useState<string>(() => shiftDay(todayStr(), -6));
  const [to, setTo] = useState<string>(() => todayStr());
  const [data, setData] = useState<CalendarPayload | null>(null);
  const [err, setErr] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [openDay, setOpenDay] = useState<string>("");

  const load = useCallback(async (f: string, t: string) => {
    setLoading(true);
    try {
      const r = await api.calendar(f, t);
      setData(r);
      setErr("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(from, to);
  }, [load, from, to]);

  const applyRange = (k: RangeKey) => {
    setRange(k);
    const t = todayStr();
    if (k === "today") {
      setFrom(t);
      setTo(t);
    } else if (k === "7d") {
      setFrom(shiftDay(t, -6));
      setTo(t);
    } else if (k === "30d") {
      setFrom(shiftDay(t, -29));
      setTo(t);
    }
    // custom：保留用户已选 from/to
  };

  const btn = (k: RangeKey, label: string): CSSProperties => ({
    padding: "3px 10px",
    borderRadius: 6,
    border: "1px solid " + (range === k ? "#4a8fe7" : "#2a3342"),
    background: range === k ? "rgba(74,143,231,0.15)" : "transparent",
    color: range === k ? "#9cc3ff" : "#8a94a6",
    fontSize: 12,
    cursor: "pointer",
  });

  return (
    <div style={CARD}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, marginBottom: 12, flexWrap: "wrap" }}>
        <Calendar size={14} color="#8a94a6" /> 收益日历
        <button style={btn("today", "今天")} onClick={() => applyRange("today")}>今天</button>
        <button style={btn("7d", "近7天")} onClick={() => applyRange("7d")}>近7天</button>
        <button style={btn("30d", "近30天")} onClick={() => applyRange("30d")}>近30天</button>
        <button style={btn("custom", "自定义")} onClick={() => setRange("custom")}>自定义</button>
        <input type="date" value={from} onChange={(e) => { setRange("custom"); setFrom(e.target.value); }} style={{ background: "#1a2130", color: "#d7e0ea", border: "1px solid #2a3342", borderRadius: 6, padding: "2px 6px", fontSize: 12 }} />
        <span style={{ color: "#8a94a6", fontSize: 12 }}>~</span>
        <input type="date" value={to} onChange={(e) => { setRange("custom"); setTo(e.target.value); }} style={{ background: "#1a2130", color: "#d7e0ea", border: "1px solid #2a3342", borderRadius: 6, padding: "2px 6px", fontSize: 12 }} />
        <button style={{ ...btn("today", ""), background: "transparent" }} onClick={() => load(from, to)}><RefreshCw size={12} /> 刷新</button>
        {loading && <span style={LABEL}>加载中…</span>}
      </div>

      {err ? (
        <div style={{ color: "#ff9a9a", fontSize: 13 }}>获取失败：{err}</div>
      ) : !data ? (
        <div style={{ color: "#8a94a6", fontSize: 13 }}>加载中…</div>
      ) : (
        <>
          {/* 区间汇总 */}
          <div style={{ display: "flex", gap: 24, marginBottom: 12, flexWrap: "wrap", alignItems: "baseline" }}>
            <div>
              <div style={{ fontSize: 30, fontWeight: 700, fontVariantNumeric: "tabular-nums", color: pnlColor(data.total) }}>
                {fmtPnl(data.total)}
              </div>
              <div style={LABEL}>区间总收益（{data.date_from} ~ {data.date_to}）</div>
            </div>
            <div>
              <div style={{ fontSize: 18, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: pnlColor(data.floating_now) }}>
                {fmtPnl(data.floating_now)}
              </div>
              <div style={LABEL}>当前持仓浮动（盘中快照）</div>
            </div>
            <div>
              <div style={{ fontSize: 18, fontWeight: 600 }}>{data.days.length} 天有成交</div>
              <div style={LABEL}>实现盈亏按平仓日归属 · 时区 UTC</div>
            </div>
          </div>

          {/* 日卡片列表 */}
          <div>
            {data.days.length === 0 ? (
              <div style={{ color: "#8a94a6", fontSize: 13 }}>区间内无已平仓成交记录。</div>
            ) : (
              data.days.map((d) => (
                <div key={d.date} style={{ border: "1px solid #222b3a", borderRadius: 8, marginBottom: 8, background: "#171c27" }}>
                  <div style={{ ...ROW, padding: "8px 12px", cursor: "pointer", borderBottom: "1px solid #1d2634" }} onClick={() => setOpenDay(openDay === d.date ? "" : d.date)}>
                    <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      {openDay === d.date ? <ChevronUp size={13} color="#8a94a6" /> : <ChevronDown size={13} color="#8a94a6" />}
                      <span style={{ fontSize: 13, fontWeight: 600 }}>{d.date}</span>
                      {d.floating_pnl !== 0 && <span style={{ ...LABEL, color: "#e8b64c" }}>含浮动</span>}
                    </span>
                    <span style={{ fontSize: 16, fontWeight: 700, fontVariantNumeric: "tabular-nums", color: pnlColor(d.total_pnl) }}>
                      {fmtPnl(d.total_pnl)}
                    </span>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 14, padding: "6px 12px" }}>
                    <span style={LABEL}>实现 {fmtPnl(d.realized_pnl)}</span>
                    <span style={LABEL}>浮动 {fmtPnl(d.floating_pnl)}</span>
                    <span style={LABEL}>平仓 {d.closed_count} 笔（赢 {d.win_count}/亏 {d.loss_count}）</span>
                    <span style={LABEL}>胜率 {d.win_rate === null ? "--" : `${d.win_rate}%`}</span>
                    <span style={LABEL}>盈亏比 {d.profit_factor === null ? "--" : d.profit_factor}</span>
                    <span style={LABEL}>最大单盈 {fmtPnl(d.max_win)}</span>
                    <span style={LABEL}>最大单亏 {fmtPnl(d.max_loss)}</span>
                  </div>
                  {openDay === d.date && (
                    <div style={{ padding: "8px 12px" }}>
                      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                        <thead>
                          <tr style={{ color: "#8a94a6", textAlign: "left" }}>
                            <th style={{ padding: "4px 6px" }}>时间(UTC)</th>
                            <th>符号</th>
                            <th>方向</th>
                            <th>手数</th>
                            <th>入场</th>
                            <th>出场</th>
                            <th>盈亏</th>
                            <th>平仓原因</th>
                          </tr>
                        </thead>
                        <tbody>
                          {d.details.map((x) => (
                            <tr key={x.ticket} style={{ borderTop: "1px solid #1d2634" }}>
                              <td style={{ padding: "4px 6px", color: "#8a94a6", fontVariantNumeric: "tabular-nums" }}>{fmtDate(x.entry_time)} → {fmtDate(x.exit_time)}</td>
                              <td>{x.symbol}</td>
                              <td style={{ color: x.side === "long" ? "#7fb3ff" : "#ffb36b" }}>{x.side === "long" ? "多" : "空"}</td>
                              <td>{x.volume}</td>
                              <td style={{ fontVariantNumeric: "tabular-nums" }}>{x.entry_price}</td>
                              <td style={{ fontVariantNumeric: "tabular-nums" }}>{x.exit_price}</td>
                              <td style={{ fontWeight: 600, color: pnlColor(x.pnl), fontVariantNumeric: "tabular-nums" }}>{fmtPnl(x.pnl)}</td>
                              <td style={{ color: "#8a94a6" }}>{x.reason || "--"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}