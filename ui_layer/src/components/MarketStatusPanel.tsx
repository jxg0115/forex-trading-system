import { useEffect, useState, type CSSProperties } from "react";
import { Ban, CircleDot, Clock, Filter } from "lucide-react";
import { api, type MarketStatusPayload } from "../api";

const WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function fmtDur(diff: number): string {
  const d = Math.floor(diff / 86400);
  const h = Math.floor((diff % 86400) / 3600);
  const m = Math.floor((diff % 3600) / 60);
  const s = Math.floor(diff % 60);
  return `${d}天 ${pad(h)}:${pad(m)}:${pad(s)}`;
}

/** 以服务器返回的市场时间（UTC ISO）为基准，按本地秒差推进，tzOffsetMs 为目标时区偏移 */
function fmtClock(baseMs: number, driftMs: number, tzOffsetMs: number): string {
  const t = new Date(baseMs + driftMs + tzOffsetMs);
  return `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())} ${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}:${pad(t.getUTCSeconds())}`;
}

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
};
const LABEL: CSSProperties = { fontSize: 12, color: "#8a94a6", marginTop: 4 };

export function MarketStatusPanel() {
  const [st, setSt] = useState<MarketStatusPayload | null>(null);
  const [err, setErr] = useState<string>("");
  const [now, setNow] = useState<number>(() => Math.floor(Date.now() / 1000));

  // 3s 拉取市场状态（含过滤配置与最近拦截）
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await api.marketStatus();
        if (!alive) return;
        setSt(r);
        setErr("");
      } catch (e) {
        if (!alive) return;
        setErr(e instanceof Error ? e.message : String(e));
      }
    };
    tick();
    const iv = setInterval(tick, 3000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, []);

  // 本地时钟每秒推进（倒计时/时间显示实时）
  useEffect(() => {
    const iv = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(iv);
  }, []);

  if (err && !st) {
    return (
      <div style={CARD}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, marginBottom: 10 }}>
          <Clock size={14} color="#8a94a6" /> 市场状态
        </div>
        <div style={{ color: "#ff9a9a", fontSize: 13 }}>获取失败：{err}</div>
      </div>
    );
  }
  if (!st) {
    return (
      <div style={CARD}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, marginBottom: 10 }}>
          <Clock size={14} color="#8a94a6" /> 市场状态
        </div>
        <div style={{ color: "#8a94a6", fontSize: 13 }}>加载中…</div>
      </div>
    );
  }

  const m = st.market;
  const sessionLabel =
    m.session === "open" ? "开市中" : m.session === "daily_closed" ? "每日休市窗口" : "周末闭市";
  const badgeBg = m.is_open ? "#1a7f37" : m.session === "daily_closed" ? "#b5811e" : "#b5473b";

  const target = m.is_open ? m.next_close_ts : m.next_open_ts;
  const diff = target !== null ? Math.max(0, target - now) : 0;

  const baseMs = new Date(m.market_time_iso).getTime();
  const driftMs = (now - m.now_ts) * 1000;
  const marketClock = fmtClock(baseMs, driftMs, 0);
  const beijingClock = fmtClock(baseMs, driftMs, 8 * 3600 * 1000);

  const fc = st.filter_config ?? {};
  const skip = st.last_market_skip;

  return (
    <div style={CARD}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, marginBottom: 12 }}>
        <Clock size={14} color="#8a94a6" /> 市场状态 · 交易时段与行情过滤
      </div>

      {/* 开市状态徽标 */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "3px 10px", borderRadius: 999, fontSize: 12, fontWeight: 600, color: "#fff", background: badgeBg }}>
          <CircleDot size={12} /> {sessionLabel}
        </span>
        <span style={{ fontSize: 13, color: "#8a94a6" }}>
          {WEEKDAY_NAMES[m.weekday] ?? "?"} · {m.weekly_hours} · {m.daily_close_window}
        </span>
      </div>

      {/* 市场时间 / 北京时间（秒级实时） */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 10 }}>
        <div>
          <div style={{ fontSize: 24, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{marketClock}</div>
          <div style={LABEL}>市场时间（UTC，秒级实时）</div>
        </div>
        <div>
          <div style={{ fontSize: 24, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{beijingClock}</div>
          <div style={LABEL}>北京时间（UTC+8，秒级实时）</div>
        </div>
      </div>

      {/* 开市/收市倒计时 */}
      <div style={{ marginBottom: 10 }}>
        <div style={{ fontSize: 20, fontWeight: 700, fontVariantNumeric: "tabular-nums", color: m.is_open ? "#4ade80" : "#f0a0a0" }}>
          {m.is_open ? `距收市 ${fmtDur(diff)}` : `距开市 ${fmtDur(diff)}`}
        </div>
        <div style={LABEL}>
          {m.is_open
            ? "距下一次收市（每日窗口 21:59 UTC 或周末收市，取先到者）"
            : m.session === "daily_closed"
              ? "每日休市窗口期间：本窗口 23:00 UTC 结束即恢复开市"
              : "周末闭市：下周一 00:00 UTC 开市"}
        </div>
        {m.session === "daily_closed" && (
          <div style={{ marginTop: 6, fontSize: 12, color: "#e8b64c", background: "rgba(181,129,30,0.12)", border: "1px solid rgba(181,129,30,0.35)", borderRadius: 6, padding: "4px 8px" }}>
            ⚠ 当前处于每日休市窗口 —— 行情过滤处于拦截态，不扫描开仓（窗口结束自动恢复）
          </div>
        )}
      </div>

      {/* 行情过滤配置（当前生效，来自运行时配置） */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "12px 0 6px" }}>
        <Filter size={13} color="#8a94a6" />
        <span style={{ fontWeight: 600, fontSize: 13 }}>行情过滤（当前生效）</span>
        {fc.enabled ? (
          <span style={{ fontSize: 12, color: "#4ade80", background: "rgba(26,127,55,0.15)", border: "1px solid rgba(26,127,55,0.4)", borderRadius: 999, padding: "1px 8px" }}>已启用</span>
        ) : (
          <span style={{ fontSize: 12, color: "#f0a0a0", background: "rgba(181,71,59,0.15)", border: "1px solid rgba(181,71,59,0.4)", borderRadius: 999, padding: "1px 8px" }}>未启用（不限行情）</span>
        )}
      </div>
      <div style={{ marginBottom: 8 }}>
        <div style={ROW}><span>总开关</span><span>{fc.enabled ? "启用：以下勾选维度必须同时满足才开仓" : "关闭：全部维度不限制"}</span></div>
        <div style={ROW}>
          <span>行情方向</span>
          <span>
            {fc.trends?.length
              ? `勾选 ${fc.trends.map((t) => (t === "up" ? "上行" : t === "down" ? "下行" : "震荡")).join("、")}${fc.trends.includes("range") ? "（含震荡——不推荐）" : "（未勾震荡=过滤横盘绞肉行情）"}`
              : "未勾选（不限方向）"}
          </span>
        </div>
        <div style={ROW}>
          <span>波动水平</span>
          <span>
            {fc.volatilities?.length
              ? `勾选 ${fc.volatilities.join("、")}${fc.volatilities.includes("低波动") ? "（含低波动——不推荐）" : "（未勾低波动=避开死水时段）"}`
              : "未勾选（不限波动）"}
          </span>
        </div>
        <div style={ROW}>
          <span>量能状态</span>
          <span>
            {fc.volume_states?.length
              ? `勾选 ${fc.volume_states.join("、")}${fc.volume_states.includes("缩量") ? "（含缩量——不推荐）" : "（未勾缩量=过滤无量空涨/空跌）"}`
              : "未勾选（不限量能）"}
          </span>
        </div>
        <div style={ROW}>
          <span>大周期共振（H4 / D1）</span>
          <span>
            {fc.mtf_directions
              ? Object.entries(fc.mtf_directions)
                  .map(([k, v]) => `${k.toUpperCase()} 勾 ${(v ?? []).map((d) => (d === "up" ? "上行" : "下行")).join("/")}`)
                  .join(" · ") || "未配置（不限）"
              : "未配置（不限）"}
          </span>
        </div>
        <div style={ROW}>
          <span>宏观方向</span>
          <span>{fc.macro_directions?.length ? `勾选 ${fc.macro_directions.map((d) => (d === "up" ? "上行" : "下行")).join("、")}` : "未勾选（不限）"}</span>
        </div>
        <div style={ROW}>
          <span>环境评分下限</span>
          <span>{Number(fc.min_environment_score ?? 0) > 0 ? `${fc.min_environment_score} 分起开仓` : "0（不设限）"}</span>
        </div>
      </div>
      <div style={{ fontSize: 12, color: "#8a94a6", marginBottom: 12 }}>
        未勾选的维度不作限制；已勾选维度必须同时满足才允许开仓。
      </div>

      {/* 最近一次过滤拦截 / 放行 */}
      {skip ? (
        <div style={{ fontSize: 12, color: "#e8b64c", background: "rgba(181,129,30,0.12)", border: "1px solid rgba(181,129,30,0.35)", borderRadius: 6, padding: "6px 8px" }}>
          <Ban size={12} style={{ verticalAlign: -2, marginRight: 4 }} />
          最近一次被行情过滤拦截：{skip.time?.slice(0, 19) ?? "--"} · {skip.symbol}/{skip.timeframe} · 环境：{skip.label ?? "--"}（趋势 {skip.trend_direction ?? "--"} / 波动 {skip.volatility ?? "--"} / 量能 {skip.volume_state ?? "--"} / 评分 {skip.environment_score ?? "--"}）
        </div>
      ) : (
        <div style={{ fontSize: 12, color: "#4ade80", background: "rgba(26,127,55,0.10)", border: "1px solid rgba(26,127,55,0.3)", borderRadius: 6, padding: "6px 8px" }}>
          最近一次扫描通过行情过滤（未被拦截）—— 当前环境符合已勾选维度
        </div>
      )}
    </div>
  );
}