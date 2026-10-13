import { useCallback, useEffect, useState } from "react";
import { Activity, BarChart3, RefreshCw, Save, TrendingDown, TrendingUp } from "lucide-react";
import { api } from "../api";

interface VPatternPanelProps {
  onNotify?: (type: "success" | "error", msg: string) => void;
}

const RISK_FIELDS: Array<{ key: string; label: string; step: string; hint: string }> = [
  { key: "risk_pct", label: "每笔风险 (%)", step: "0.5", hint: "按账户权益的百分比作为单笔风险预算" },
  { key: "max_positions", label: "最大同时持仓", step: "1", hint: "不叠单，默认 1 笔" },
  { key: "loss_streak_halve", label: "连亏减半阈值", step: "1", hint: "连亏达 N 笔后仓位减半" },
  { key: "halt_daily_loss_pct", label: "当日停手线 (%)", step: "1", hint: "当日亏损达此比例即停手" },
  { key: "cooldown_minutes", label: "两次开仓间隔 (分)", step: "5", hint: "防止连续开仓" },
];

function num(v: unknown, fb = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fb;
}

export function VPatternPanel({ onNotify }: VPatternPanelProps) {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [conf, setConf] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = (await api.vpatternState()) as Record<string, unknown>;
      setData(res);
      setConf((res.config ?? {}) as Record<string, unknown>);
    } catch (err) {
      onNotify?.("error", String(err instanceof Error ? err.message : err));
    }
  }, [onNotify]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 10000);
    return () => window.clearInterval(timer);
  }, [load]);

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

  const available = Boolean(data?.available ?? data?.enabled !== undefined);
  const enabled = Boolean(data?.enabled);
  const stats = (data?.stats ?? {}) as Record<string, unknown>;
  const scan = (data?.last_scan ?? {}) as Record<string, unknown>;
  const signals = ((data?.recent_signals ?? []) as Array<Record<string, unknown>>).slice(0, 12);
  const orders = ((data?.recent_orders ?? []) as Array<Record<string, unknown>>).slice(0, 8);
  const lastError = String(data?.last_error ?? "");

  return (
    <>
      <div className="tool-block">
        <h3>
          <Activity size={13} /> K 线形态独立交易
          <span
            style={{
              marginLeft: 8,
              fontSize: 11,
              padding: "1px 6px",
              borderRadius: 4,
              color: enabled ? "var(--green)" : "var(--muted)",
              border: `1px solid ${enabled ? "var(--green)" : "var(--muted)"}`,
            }}
          >
            {enabled ? "运行中" : "已停止"}
          </span>
        </h3>
        <p className="factor-desc">
          M15 与 M30 各自走完 V 字底（倒 V 做空）+ H1 最新一根收在同向半边 → 进场。
          自带仓位与风控，<strong>不受镜像交易开关影响</strong>。
        </p>
        {!available && <p className="factor-desc" style={{ color: "var(--red)" }}>模块未初始化（需 MT5 连接）。</p>}
        <div className="row-actions">
          <button className="mini-btn primary" disabled={busy || !available} onClick={toggle}>
            {enabled ? "关闭自动交易" : "开启自动交易"}
          </button>
          <button className="mini-btn" onClick={load}>
            <RefreshCw size={12} /> 刷新
          </button>
        </div>
        {lastError && <p className="factor-desc" style={{ color: "var(--red)" }}>最近异常：{lastError}</p>}
      </div>

      <div className="tool-block">
        <h3><BarChart3 size={13} /> 实时判断</h3>
        <div className="metric-grid">
          <div className="metric"><div className="label">K线时刻</div><div className="value" style={{ fontSize: 11 }}>{String(scan.time ?? "--").slice(5, 16)}</div></div>
          <div className="metric"><div className="label">价格</div><div className="value">{scan.price != null ? num(scan.price).toFixed(2) : "--"}</div></div>
          <div className="metric"><div className="label">ATR(14)</div><div className="value">{scan.atr15 != null ? num(scan.atr15).toFixed(2) : "--"}</div></div>
          <div className="metric"><div className="label">M15 状态</div><div className="value" style={{ fontSize: 12 }}>{((scan.m15_active ?? []) as string[]).join("/") || "无"}</div></div>
          <div className="metric"><div className="label">M30 状态</div><div className="value" style={{ fontSize: 12 }}>{((scan.m30_active ?? []) as string[]).join("/") || "无"}</div></div>
          <div className="metric"><div className="label">H1 佐证</div><div className="value" style={{ fontSize: 12 }}>{String(scan.h1_half ?? "--")}</div></div>
        </div>
        <p className="factor-desc">本次方向：{String(scan.side ?? "无（未同时满足）")}</p>
      </div>

      <div className="tool-block">
        <h3><TrendingUp size={13} /> 独立统计（本模块自己的账）</h3>
        <div className="metric-grid">
          <div className="metric"><div className="label">已平仓</div><div className="value">{num(stats.n)}</div></div>
          <div className="metric"><div className="label">持仓中</div><div className="value">{num(stats.open)}</div></div>
          <div className="metric"><div className="label">胜率</div><div className="value">{stats.win_rate == null ? "--" : `${num(stats.win_rate).toFixed(1)}%`}</div></div>
          <div className="metric"><div className="label">盈亏比</div><div className="value">{stats.profit_factor == null ? "--" : num(stats.profit_factor).toFixed(2)}</div></div>
          <div className="metric"><div className="label">累计 R</div><div className="value">{num(stats.total_r).toFixed(1)}</div></div>
          <div className="metric"><div className="label">最大回撤 R</div><div className="value">{num(stats.max_dd_r).toFixed(1)}</div></div>
        </div>
        <p className="factor-desc">多 {num(stats.n_long)} / 空 {num(stats.n_short)}</p>
      </div>

      <div className="tool-block">
        <h3><TrendingDown size={13} /> 仓位与风控</h3>
        <div className="metric-grid">
          <div className="metric"><div className="label">当前连亏</div><div className="value">{num(conf.consec_loss)}</div></div>
          <div className="metric"><div className="label">当日盈亏</div><div className="value">{num(conf.day_pnl).toFixed(2)}</div></div>
          <div className="metric"><div className="label">当日停手</div><div className="value">{conf.day_halted ? "是" : "否"}</div></div>
        </div>
        {RISK_FIELDS.map((f) => (
          <div key={f.key} style={{ marginTop: 8 }}>
            <div className="label" style={{ fontSize: 11, color: "var(--muted)" }}>{f.label}</div>
            <input
              className="form-input"
              type="number"
              step={f.step}
              min="0"
              value={String(conf[f.key] ?? "")}
              onChange={(e) => setConf((prev) => ({ ...prev, [f.key]: e.target.value }))}
            />
            <div className="factor-desc" style={{ fontSize: 11 }}>{f.hint}</div>
          </div>
        ))}
        <div className="row-actions">
          <button className="mini-btn" disabled={busy} onClick={save}><Save size={12} /> 保存风控参数</button>
        </div>
      </div>

      {orders.length > 0 && (
        <div className="tool-block">
          <h3>最近开仓</h3>
          {orders.map((o, i) => (
            <div key={i} className="factor-desc" style={{ fontSize: 11 }}>
              {String(o.time ?? "").slice(5, 16)} {String(o.side)} {num(o.lots).toFixed(2)} 手 @ {num(o.entry).toFixed(2)}
              {" "}SL {num(o.sl).toFixed(2)} / TP {num(o.tp).toFixed(2)}（#{String(o.ticket ?? "")}）
            </div>
          ))}
        </div>
      )}

      {signals.length > 0 && (
        <div className="tool-block">
          <h3>最近信号</h3>
          {signals.map((s, i) => (
            <div key={i} className="factor-desc" style={{ fontSize: 11 }}>
              {String(s.time ?? "").slice(5, 16)} {String(s.side)} {String(s.action)}{" "}
              {s.action === "skip" ? `— ${String(s.reason ?? "")}` : `@ ${num(s.entry).toFixed(2)}`}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
