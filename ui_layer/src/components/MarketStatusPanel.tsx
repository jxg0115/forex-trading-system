import { useEffect, useState, type CSSProperties } from "react";
import { Clock, CircleDot } from "lucide-react";
import { api } from "../api";

interface MarketStatus {
  is_open: boolean;
  session: "open" | "closed";
  now_ts: number;
  next_open_ts: number;
  next_close_ts: number | null;
  weekday: number;
}

const WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

function fmtDays(diff: number): { d: number; h: number; m: number; s: number } {
  const d = Math.floor(diff / 86400);
  const h = Math.floor((diff % 86400) / 3600);
  const m = Math.floor((diff % 3600) / 60);
  const s = Math.floor(diff % 60);
  return { d, h, m, s };
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

export function MarketStatusPanel() {
  const [ms, setMs] = useState<MarketStatus | null>(null);
  const [err, setErr] = useState<string>("");
  const [now, setNow] = useState<number>(() => Math.floor(Date.now() / 1000));

  // 时间戳 3s 轮询（后端 now_ts 校准本地时钟漂移）；倒计时本身用本地时钟每秒刷新
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await api.marketStatus("XAUUSD", "M15");
        if (!alive) return;
        setMs(r.market);
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

  // 本地时钟每秒刷新（倒计时秒级实时）
  useEffect(() => {
    const iv = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(iv);
  }, []);

  const isOpen = ms?.is_open ?? false;
  const target = isOpen ? (ms?.next_close_ts ?? null) : (ms?.next_open_ts ?? null);
  const diff = target !== null && ms ? Math.max(0, target - now) : 0;
  const { d, h, m, s } = fmtDays(diff);

  const cardStyle: CSSProperties = {
    background: "var(--card-bg, #151a24)",
    border: "1px solid var(--border, #2a3342)",
    borderRadius: 10,
    padding: 16,
    color: "var(--text, #d7e0ea)",
    fontFamily: "inherit",
  };
  const badgeStyle: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    padding: "3px 10px",
    borderRadius: 999,
    fontSize: 12,
    fontWeight: 600,
    color: "#fff",
    background: isOpen ? "#1a7f37" : "#b5473b",
  };
  const big: CSSProperties = {
    fontSize: 34,
    fontWeight: 700,
    letterSpacing: 1,
    fontVariantNumeric: "tabular-nums",
    color: isOpen ? "#4ade80" : "#f0a0a0",
  };
  const label: CSSProperties = {
    fontSize: 12,
    color: "#8a94a6",
    marginTop: 4,
  };
  const row: CSSProperties = {
    display: "flex",
    justifyContent: "space-between",
    padding: "6px 0",
    borderBottom: "1px solid #222b3a",
    fontSize: 13,
  };

  return (
    <div style={cardStyle}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <Clock size={16} color="#8a94a6" />
        <span style={{ fontWeight: 600 }}>市场状态 · 开市/收市倒计时</span>
      </div>

      {err ? (
        <div style={{ color: "#ff9a9a", fontSize: 13 }}>
          获取市场状态失败：{err}（后端 /api/market/status 不可用？）
        </div>
      ) : !ms ? (
        <div style={{ color: "#8a94a6", fontSize: 13 }}>加载中…</div>
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
            <span style={badgeStyle}>
              <CircleDot size={12} />
              {isOpen ? "开市中" : "闭市"}
            </span>
            <span style={{ fontSize: 13, color: "#8a94a6" }}>
              今日 {WEEKDAY_NAMES[ms.weekday] ?? "?"}（UTC）
            </span>
          </div>

          {target !== null ? (
            <div>
              <div style={big}>
                {d}
                <span style={{ fontSize: 16, margin: "0 4px" }}>天</span>
                {pad(h)}
                <span style={{ fontSize: 16, margin: "0 4px" }}>:</span>
                {pad(m)}
                <span style={{ fontSize: 16, margin: "0 4px" }}>:</span>
                {pad(s)}
              </div>
              <div style={label}>
                {isOpen ? "距本周收市" : "距下次开市"}（本地时钟实时刷新，UTC 时间戳由后端提供）
              </div>
            </div>
          ) : (
            <div style={{ color: "#8a94a6", fontSize: 13 }}>
              {isOpen ? "本周收市时间不可用" : "下次开市时间不可用"}
            </div>
          )}

          <div style={{ marginTop: 12 }}>
            <div style={row}>
              <span>会话模式</span>
              <span>{ms.session === "open" ? "24/5 连续交易" : "周六/周日休市"}</span>
            </div>
            <div style={row}>
              <span>规则</span>
              <span>周一 00:00 UTC 开市，周五结束（周六 00:00 收市）</span>
            </div>
            <div style={row}>
              <span>品种/周期</span>
              <span>XAUUSD / M15</span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}