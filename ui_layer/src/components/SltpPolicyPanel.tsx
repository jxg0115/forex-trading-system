import { useCallback, useEffect, useState } from "react";
import { Activity, Eye, RefreshCw, Save, Shield, Wand2 } from "lucide-react";
import { api } from "../api";

interface SltpPolicyPanelProps {
  onNotify?: (type: "success" | "error", msg: string) => void;
}

const NUM_FIELDS = [
  "scan_interval_seconds",
  "modify_cooldown_seconds",
  "risk_mult",
  "take_r_mult",
  "hard_stop_atr",
  "pattern_max_take_pct",
  "breakeven_trigger_r",
  "breakeven_buffer_atr",
  "trailing_activation_r",
  "trailing_stop_atr",
  "trailing_take_atr",
  "trailing_take_buffer_atr",
  "hw_activation_r",
  "hw_retrace_atr",
  "time_stop_bars",
  "time_stop_min_profit_r",
  "rsi_ob",
  "rsi_os",
  "adx_strong",
  "swing_bars",
  "ai_min_confidence",
];

const TIER_PATHS: Array<{ key: string; fields: string[] }> = [
  { key: "ladder_tiers", fields: ["trigger_r", "raise_to_r"] },
  { key: "partial_close_tiers", fields: ["trigger_r", "close_pct"] },
];

function num(value: unknown, fallback: number): number {
  const v = Number(value);
  return Number.isFinite(v) ? v : fallback;
}

function sanitize(cfg: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...cfg };
  for (const k of NUM_FIELDS) {
    if (k in out) out[k] = num(out[k], 0);
  }
  for (const t of TIER_PATHS) {
    const arr = ((out[t.key] ?? []) as Array<Record<string, unknown>>) ?? [];
    out[t.key] = arr.map((row) => {
      const clean: Record<string, unknown> = { ...row };
      for (const f of t.fields) {
        if (f in clean) clean[f] = num(clean[f], 0);
      }
      return clean;
    });
  }
  return out;
}

function NumInput({ value, onChange, step = "0.1", min = "0", max }: { value: unknown; onChange: (v: string) => void; step?: string; min?: string; max?: string }) {
  return (
    <input
      className="form-input"
      type="number"
      step={step}
      min={min}
      max={max}
      value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function SltpPolicyPanel({ onNotify }: SltpPolicyPanelProps) {
  const [conf, setConf] = useState<Record<string, unknown> | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [scanInterval, setScanInterval] = useState(5);
  const [updates, setUpdates] = useState<Array<Record<string, unknown>>>([]);
  const [positions, setPositions] = useState<Array<Record<string, unknown>>>([]);
  const [busy, setBusy] = useState(false);
  const [busyRec, setBusyRec] = useState(false);
  const [recMarket, setRecMarket] = useState<Record<string, unknown>>({});
  const [recReason, setRecReason] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await api.sltpPolicy();
      setConf((res.config ?? {}) as Record<string, unknown>);
      setEnabled(Boolean(res.enabled));
      setScanInterval(num(res.scan_interval, 5));
      setUpdates((res.last_updates ?? []) as Array<Record<string, unknown>>);
    } catch (err) {
      onNotify?.("error", String(err instanceof Error ? err.message : err));
    }
  }, [onNotify]);

  useEffect(() => {
    load();
  }, [load]);

  const loadMonitor = useCallback(async () => {
    try {
      const res = await api.sltpState();
      setPositions((res.positions ?? []) as Array<Record<string, unknown>>);
      setUpdates((res.last_updates ?? []) as Array<Record<string, unknown>>);
    } catch (err) {
      // 监控刷新失败不打断操作，仅保留旧数据
      void err;
    }
  }, []);

  useEffect(() => {
    loadMonitor();
    const timer = window.setInterval(loadMonitor, 10000);
    return () => window.clearInterval(timer);
  }, [loadMonitor]);

  const setV = (key: string, value: unknown) => setConf((prev) => ({ ...(prev ?? {}), [key]: value }));

  const toggleBool = (key: string) => setV(key, !Boolean(conf?.[key]));

  const setTier = (key: string, idx: number, field: string, value: unknown) => {
    setConf((prev) => {
      const arr = [...(((prev ?? {})[key] as Array<Record<string, unknown>>) ?? [])];
      arr[idx] = { ...(arr[idx] ?? {}), [field]: value };
      return { ...(prev ?? {}), [key]: arr };
    });
  };

  const save = async () => {
    if (!conf) return;
    setBusy(true);
    try {
      const res = await api.saveSltpPolicy(sanitize(conf));
      onNotify?.("success", res.message);
      await load();
    } catch (err) {
      onNotify?.("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  };

  const c = conf ?? {};

  const recommend = useCallback(async () => {
    setBusyRec(true);
    try {
      const symbol = String(c?.symbol ?? "EURUSD").trim() || "EURUSD";
      const timeframe = String(c?.timeframe ?? "M15").trim() || "M15";
      const res = await api.sltpRecommend(symbol, timeframe);
      setConf(res.config as Record<string, unknown>);
      setRecMarket(res.market ?? {});
      setRecReason(res.reason ?? "");
      onNotify?.("success", `推荐值已按 ${symbol} ${timeframe} 生成并填入，可再调整后保存`);
    } catch (err) {
      onNotify?.("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusyRec(false);
    }
  }, [c, onNotify]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="tool-block">
        <h4>
          <Shield size={14} /> 多维融合止损止盈政策（R/ATR 单位）
        </h4>
        <div className="metric-grid" style={{ marginBottom: 8 }}>
          <div className="metric">
            <div className="label">引擎状态</div>
            <div className={`value ${enabled ? "good" : "bad"}`}>{enabled ? "运行中" : "已停用"}</div>
          </div>
          <div className="metric">
            <div className="label">当前扫描周期</div>
            <div className="value">{scanInterval}s（波动自适应）</div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <label className="field-label" style={{ margin: 0 }}>
            启用引擎
          </label>
          <input type="checkbox" checked={Boolean(c.enabled)} onChange={() => toggleBool("enabled")} />
          <label className="field-label" style={{ margin: 0 }}>
            参考品种
          </label>
          <input
            className="form-input"
            style={{ width: 110 }}
            value={String(c.symbol ?? "EURUSD")}
            onChange={(e) => setV("symbol", e.target.value.toUpperCase())}
          />
          <label className="field-label" style={{ margin: 0 }}>
            周期
          </label>
          <select className="form-input" style={{ width: 90 }} value={String(c.timeframe ?? "M15")} onChange={(e) => setV("timeframe", e.target.value)}>
            {["M1", "M5", "M15", "M30", "H1", "H4", "D1"].map((tf) => (
              <option key={tf} value={tf}>
                {tf}
              </option>
            ))}
          </select>
          <label className="field-label" style={{ margin: 0 }}>
            基础心跳(s)
          </label>
          <NumInput value={c.scan_interval_seconds} onChange={(v) => setV("scan_interval_seconds", v)} step="1" />
          <label className="field-label" style={{ margin: 0 }}>
            自适应
          </label>
          <input type="checkbox" checked={Boolean(c.scan_adaptive)} onChange={() => toggleBool("scan_adaptive")} />
          <label className="field-label" style={{ margin: 0 }}>
            调整冷却(s)
          </label>
          <NumInput value={c.modify_cooldown_seconds} onChange={(v) => setV("modify_cooldown_seconds", v)} step="1" />
          <button className="mini-btn primary" disabled={busyRec} onClick={recommend}>
            <Wand2 size={12} /> {busyRec ? "生成中..." : "按品种+周期生成推荐"}
          </button>
          <button className="mini-btn primary" disabled={busy} onClick={save}>
            <Save size={12} /> {busy ? "保存中..." : "保存政策"}
          </button>
          <button className="mini-btn" onClick={load}>
            <RefreshCw size={12} /> 刷新
          </button>
        </div>
        {(recReason || Object.keys(recMarket).length > 0) && (
          <div style={{ marginTop: 8, borderTop: "1px dashed #444", paddingTop: 8 }}>
            <div className="metric-grid">
              <div className="metric">
                <div className="label">趋势方向</div>
                <div className="value">{String(recMarket.trend_direction ?? "-")}</div>
              </div>
              <div className="metric">
                <div className="label">波动等级</div>
                <div className="value">{String(recMarket.volatility ?? "-")}</div>
              </div>
              <div className="metric">
                <div className="label">ADX</div>
                <div className="value">{String(recMarket.adx ?? "-")}</div>
              </div>
              <div className="metric">
                <div className="label">环境评分</div>
                <div className="value">{String(recMarket.environment_score ?? "-")}</div>
              </div>
            </div>
            {recReason && <p className="factor-desc" style={{ margin: "6px 0 0" }}>推荐依据：{recReason}</p>}
          </div>
        )}
      </div>

      <div className="tool-block">
        <h4>初始止损止盈（L1）</h4>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <div>
            <label className="field-label">初始止盈来源</label>
            <select className="form-input" value={String(c.initial_tp_source ?? "r")} onChange={(e) => setV("initial_tp_source", e.target.value)}>
              <option value="r">R 倍数（R × take_r_mult）</option>
              <option value="structure">结构目标（swing/压力位）</option>
              <option value="pattern">形态建议（仅作上限参考）</option>
            </select>
          </div>
          <div>
            <label className="field-label">R（风险预算 ATR 倍数）</label>
            <NumInput value={c.risk_mult} onChange={(v) => setV("risk_mult", v)} />
          </div>
          <div>
            <label className="field-label">止盈 R 倍数（take_r_mult）</label>
            <NumInput value={c.take_r_mult} onChange={(v) => setV("take_r_mult", v)} />
          </div>
          <div>
            <label className="field-label">形态止盈上限（%）</label>
            <NumInput value={c.pattern_max_take_pct} onChange={(v) => setV("pattern_max_take_pct", v)} />
          </div>
          <div>
            <label className="field-label">硬止损（ATR）</label>
            <NumInput value={c.hard_stop_atr} onChange={(v) => setV("hard_stop_atr", v)} />
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <div className="tool-block">
          <h4>① 保本单元</h4>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={Boolean(c.breakeven_enabled)} onChange={() => toggleBool("breakeven_enabled")} />
            <label className="field-label" style={{ margin: 0 }}>启用</label>
          </div>
          <div style={{ marginTop: 6 }}>
            <label className="field-label">触发盈利（R）</label>
            <NumInput value={c.breakeven_trigger_r} onChange={(v) => setV("breakeven_trigger_r", v)} />
          </div>
          <div style={{ marginTop: 6 }}>
            <label className="field-label">保本缓冲（ATR）</label>
            <NumInput value={c.breakeven_buffer_atr} onChange={(v) => setV("breakeven_buffer_atr", v)} />
          </div>
        </div>

        <div className="tool-block">
          <h4>② 移动止损止盈单元</h4>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={Boolean(c.trailing_enabled)} onChange={() => toggleBool("trailing_enabled")} />
            <label className="field-label" style={{ margin: 0 }}>启用</label>
          </div>
          <div style={{ marginTop: 6 }}>
            <label className="field-label">激活盈利（R）</label>
            <NumInput value={c.trailing_activation_r} onChange={(v) => setV("trailing_activation_r", v)} />
          </div>
          <div style={{ marginTop: 6 }}>
            <label className="field-label">追踪间距（ATR）</label>
            <NumInput value={c.trailing_stop_atr} onChange={(v) => setV("trailing_stop_atr", v)} />
          </div>
          <div style={{ marginTop: 6 }}>
            <label className="field-label">触 TP 后上移间距（ATR）</label>
            <NumInput value={c.trailing_take_atr} onChange={(v) => setV("trailing_take_atr", v)} />
          </div>
          <div style={{ marginTop: 6 }}>
            <label className="field-label">上移缓冲（ATR）</label>
            <NumInput value={c.trailing_take_buffer_atr} onChange={(v) => setV("trailing_take_buffer_atr", v)} />
          </div>
        </div>

        <div className="tool-block">
          <h4>③ 阶梯止盈单元（档位底线）</h4>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={Boolean(c.ladder_enabled)} onChange={() => toggleBool("ladder_enabled")} />
            <label className="field-label" style={{ margin: 0 }}>启用</label>
          </div>
          {(((c.ladder_tiers as Array<Record<string, unknown>>) ?? [])).map((tier, idx) => (
            <div key={idx} style={{ display: "flex", gap: 6, marginTop: 6, alignItems: "center" }}>
              <span className="label" style={{ minWidth: 40 }}>
                档{idx + 1}
              </span>
              <label className="field-label" style={{ margin: 0 }}>触发R</label>
              <NumInput value={tier.trigger_r} onChange={(v) => setTier("ladder_tiers", idx, "trigger_r", v)} />
              <label className="field-label" style={{ margin: 0 }}>抬底R</label>
              <NumInput value={tier.raise_to_r} onChange={(v) => setTier("ladder_tiers", idx, "raise_to_r", v)} />
            </div>
          ))}
          <p className="factor-desc" style={{ margin: "6px 0 0" }}>盈利达触发 R 且行情 Gate 通过时，SL 抬到「抬底R」利润处。</p>
        </div>

        <div className="tool-block">
          <h4>④ 分批落袋单元（分档兑现）</h4>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={Boolean(c.partial_close_enabled)} onChange={() => toggleBool("partial_close_enabled")} />
            <label className="field-label" style={{ margin: 0 }}>启用</label>
          </div>
          {(((c.partial_close_tiers as Array<Record<string, unknown>>) ?? [])).map((tier, idx) => (
            <div key={idx} style={{ display: "flex", gap: 6, marginTop: 6, alignItems: "center" }}>
              <span className="label" style={{ minWidth: 40 }}>
                档{idx + 1}
              </span>
              <label className="field-label" style={{ margin: 0 }}>触发R</label>
              <NumInput value={tier.trigger_r} onChange={(v) => setTier("partial_close_tiers", idx, "trigger_r", v)} />
              <label className="field-label" style={{ margin: 0 }}>平仓%</label>
              <NumInput value={tier.close_pct} onChange={(v) => setTier("partial_close_tiers", idx, "close_pct", v)} />
            </div>
          ))}
          <p className="factor-desc" style={{ margin: "6px 0 0" }}>盈利达触发 R 时平掉剩余仓位的比例，每档只触发一次。</p>
        </div>

        <div className="tool-block">
          <h4>⑤ 高水位离场单元</h4>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={Boolean(c.high_watermark_enabled)} onChange={() => toggleBool("high_watermark_enabled")} />
            <label className="field-label" style={{ margin: 0 }}>启用</label>
          </div>
          <div style={{ marginTop: 6 }}>
            <label className="field-label">激活盈利（R）</label>
            <NumInput value={c.hw_activation_r} onChange={(v) => setV("hw_activation_r", v)} />
          </div>
          <div style={{ marginTop: 6 }}>
            <label className="field-label">回落容忍（ATR）</label>
            <NumInput value={c.hw_retrace_atr} onChange={(v) => setV("hw_retrace_atr", v)} />
          </div>
        </div>

        <div className="tool-block">
          <h4>⑥ 时间止损单元</h4>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={Boolean(c.time_stop_enabled)} onChange={() => toggleBool("time_stop_enabled")} />
            <label className="field-label" style={{ margin: 0 }}>启用</label>
          </div>
          <div style={{ marginTop: 6 }}>
            <label className="field-label">最大持仓根数</label>
            <NumInput value={c.time_stop_bars} onChange={(v) => setV("time_stop_bars", v)} step="1" />
          </div>
          <div style={{ marginTop: 6 }}>
            <label className="field-label">未达标阈值（R）</label>
            <NumInput value={c.time_stop_min_profit_r} onChange={(v) => setV("time_stop_min_profit_r", v)} />
          </div>
          <div style={{ marginTop: 6 }}>
            <label className="field-label">超时动作</label>
            <select className="form-input" value={String(c.time_stop_action ?? "close")} onChange={(e) => setV("time_stop_action", e.target.value)}>
              <option value="close">离场</option>
              <option value="tighten">收紧到保本</option>
            </select>
          </div>
        </div>
      </div>

      <div className="tool-block">
        <h4>指标阈值（Gate 参数）</h4>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
          <div>
            <label className="field-label">RSI 超买</label>
            <NumInput value={c.rsi_ob} onChange={(v) => setV("rsi_ob", v)} step="1" />
          </div>
          <div>
            <label className="field-label">RSI 超卖</label>
            <NumInput value={c.rsi_os} onChange={(v) => setV("rsi_os", v)} step="1" />
          </div>
          <div>
            <label className="field-label">强趋势 ADX 门槛</label>
            <NumInput value={c.adx_strong} onChange={(v) => setV("adx_strong", v)} step="1" />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={Boolean(c.boll_touch_enabled)} onChange={() => toggleBool("boll_touch_enabled")} />
            <label className="field-label" style={{ margin: 0 }}>布林触碰强制锁利</label>
          </div>
          <div>
            <label className="field-label">Swing 结构窗口（根）</label>
            <NumInput value={c.swing_bars} onChange={(v) => setV("swing_bars", v)} step="1" />
          </div>
        </div>
      </div>

      <div className="tool-block">
        <h4>AI 辅助层（L6，默认关闭）</h4>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input type="checkbox" checked={Boolean(c.ai_enabled)} onChange={() => toggleBool("ai_enabled")} />
          <label className="field-label" style={{ margin: 0 }}>启用 AI 止盈/移损建议</label>
        </div>
        {Boolean(c.ai_enabled) && (
          <div style={{ marginTop: 6 }}>
            <label className="field-label">最低置信度</label>
            <NumInput value={c.ai_min_confidence} onChange={(v) => setV("ai_min_confidence", v)} step="0.05" min="0" max="1" />
          </div>
        )}
      </div>

      <div className="tool-block">
        <h4>
          <Eye size={14} /> 持仓监控（实时 10s 刷新）
        </h4>
        {positions.length === 0 ? (
          <p className="factor-desc">当前无 MT5 持仓（或引擎未装配）。持有订单后将在此显示每笔持仓的维度状态与预判动作。</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {positions.map((p, i) => {
              const dims = (p.dims ?? {}) as Record<string, unknown>;
              const units = (p.active_units ?? []) as string[];
              return (
                <div key={i} style={{ border: "1px solid #333", borderRadius: 6, padding: "6px 8px", fontSize: 12 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
                    <b>#{String(p.ticket)}</b>
                    <span>
                      {String(p.symbol)} {String(p.side)}
                    </span>
                    <span>现价 {Number(p.current).toFixed(5)}</span>
                    <span>
                      SL {Number(p.sl).toFixed(5)} / TP {Number(p.tp).toFixed(5)}
                    </span>
                    <span style={{ color: "#9be" }}>
                      目标 SL {Number(p.target_sl || 0).toFixed(5)} / TP {Number(p.target_tp || 0).toFixed(5)}
                    </span>
                    <span>
                      盈亏 <b>{String(dims.profit_r)}R</b>
                    </span>
                    <span>峰值 {String(dims.peak_r)}R</span>
                    <span>持仓 {String(dims.bars_held)} 根</span>
                    <span>{String(dims.session)}</span>
                    <span>{String(dims.regime)}</span>
                    <span>RSI {String(dims.rsi ?? "-")}</span>
                    <span>ADX {String(dims.adx ?? "-")}</span>
                    <span>{String(dims.volatility)}</span>
                  </div>
                  <div style={{ marginTop: 4, display: "flex", gap: 8, flexWrap: "wrap", color: "#aaa" }}>
                    <span>激活：{units.length ? units.join("、") : "无"}</span>
                    <span>
                      预判：<b style={{ color: "#9be" }}>{String(p.plan_action)}</b>
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="tool-block">
        <h4>
          <Activity size={14} /> 最近动作时间线
        </h4>
        {updates.length === 0 ? (
          <p className="factor-desc">尚无动作记录（引擎启用并持有 MT5 持仓后产生）。</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {updates.slice(0, 10).map((u, i) => (
              <div key={i} style={{ fontSize: 12, display: "flex", gap: 8, alignItems: "baseline" }}>
                <span style={{ color: "#888", minWidth: 80 }}>{String(u.time ?? "")}</span>
                <span>
                  #{String(u.ticket ?? "")} {String(u.symbol ?? "")} {String(u.side ?? "")} ·{" "}
                  <b>{String(u.action ?? "")}</b>
                </span>
                <span className="factor-desc">{[...( (u.units as string[]) ?? []), String(u.reason ?? "")].join("；")}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}