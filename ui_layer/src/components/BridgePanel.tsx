import { useCallback, useEffect, useRef, useState } from "react";
import { Cable, Power, RefreshCw, Save, StopCircle, Trash2 } from "lucide-react";
import { api, type BridgeSettings } from "../api";

const MODULE_LABELS: Record<keyof BridgeSettings, string> = {
  factors: "因子库匹配（候选+入库因子）",
  sltp: "SLTP 止损止盈（读因子配置）",
  smart_stop: "智能止损（移动止损）",
  events: "事件过滤（非农等窗口）",
  risk: "风控（仓位计算）",
  patterns: "形态匹配",
  ai: "AI/ML 模型",
};

export function BridgePanel({ onNotify }: { onNotify: (type: "success" | "error", message: string) => void }) {
  const [running, setRunning] = useState<boolean | null>(null);
  const [status, setStatus] = useState<Record<string, unknown>>({});
  const [settings, setSettings] = useState<BridgeSettings>({
    factors: true,
    sltp: true,
    smart_stop: true,
    events: true,
    risk: true,
    patterns: false,
    ai: false,
  });
  const [busy, setBusy] = useState(false);
  const [bridgeDir, setBridgeDir] = useState("");
  const timer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await api.bridgeStatus();
      setRunning(res.running);
      setStatus(res.status);
      setBridgeDir(res.bridge_dir ?? "");
      if (res.settings) setSettings(res.settings);
    } catch {
      setRunning(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    timer.current = window.setInterval(refresh, 3000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [refresh]);

  const start = async () => {
    setBusy(true);
    try {
      const res = await api.bridgeStart();
      onNotify(res.ok ? "success" : "error", res.already_running ? "桥已在运行" : `桥已启动（pid ${res.pid}）`);
      await refresh();
    } catch (e) {
      onNotify("error", `启动失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const clean = async () => {
    if (!window.confirm("将清空桥记录的数据文件：bars.csv / cmds.csv / trades.csv / stats.txt / equity.csv / bridge_config.csv。\n保留功能勾选（bridge_settings.json）与桥运行状态。确认清理？")) {
      return;
    }
    setBusy(true);
    try {
      const res = await api.bridgeClean();
      const msg = res.ok
        ? `已清理 ${res.cleared.length} 个数据文件：${res.cleared.join("、")}`
        : `清理部分失败：${(res.failed ?? []).map((f) => `${f.file}: ${f.error}`).join("；") || "未知错误"}`;
      onNotify(res.ok ? "success" : "error", msg);
      await refresh();
    } catch (e) {
      onNotify("error", `清理失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    try {
      const res = await api.bridgeStop();
      onNotify(res.ok ? "success" : "error", res.ok ? "桥已停止" : "停止失败");
      await new Promise((r) => setTimeout(r, 1500));
      await refresh();
    } catch (e) {
      onNotify("error", `停止失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (key: keyof BridgeSettings, value: boolean) => {
    const next = { ...settings, [key]: value };
    setSettings(next);
    try {
      await api.bridgeSetConfig(next);
      onNotify("success", `功能「${MODULE_LABELS[key]}」已${value ? "勾选" : "取消"}（桥下个决策实时生效）`);
    } catch (e) {
      onNotify("error", `保存失败：${e instanceof Error ? e.message : String(e)}`);
      refresh();
    }
  };

  const st = status as Record<string, unknown>;
  const progress = typeof st.progress_pct === "number" ? st.progress_pct : 0;
  const nTrades = typeof st.n_trades === "number" ? st.n_trades : 0;
  const cumPct = typeof st.cum_pct === "number" ? st.cum_pct : 0;
  const source = typeof st.source === "string" ? st.source : "idle";
  const ts = typeof st.ts === "number" ? new Date(st.ts * 1000).toLocaleTimeString("zh-CN") : "-";

  return (
    <div className="panel">
      <div className="panel-header">
        <Cable size={16} /> MT5 策略测试器桥（整体功能测试）
        <span className="spacer" />
        <button className={running ? "btn green" : "btn"}>
          {running === null ? "…" : running ? "桥运行中（心跳实时）" : "桥已停止 / 未启动"}
        </button>
      </div>

      <div className="form-row" style={{ flexWrap: "wrap", gap: 8 }}>
        <button className="btn primary" onClick={start} disabled={busy}>
          <Power size={14} /> 启动桥
        </button>
        <button className="btn danger" onClick={stop} disabled={busy || !running}>
          <StopCircle size={14} /> 停止桥
        </button>
        <button className="btn" onClick={refresh}>
          <RefreshCw size={14} /> 刷新
        </button>
        <button className="btn danger" onClick={clean} disabled={busy}>
          <Trash2 size={14} /> 清理数据
        </button>
      </div>

      <h4>功能参与勾选（保存后桥下个决策实时生效）</h4>
      <div className="form-row" style={{ flexWrap: "wrap", gap: 8 }}>
        {(Object.keys(MODULE_LABELS) as (keyof BridgeSettings)[]).map((k) => (
          <label key={k} className="check-row" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={Boolean(settings[k])} onChange={(e) => toggle(k, e.target.checked)} />
            {MODULE_LABELS[k]}
          </label>
        ))}
      </div>

      <h4>测试实时状态</h4>
      <table className="detail-table">
        <tbody>
          <tr>
            <td>桥运行</td>
            <td>{running ? "正常（实时）" : "已停止 / 离线"}</td>
          </tr>
          <tr>
            <td>测试品种 / 周期</td>
            <td>
              {String(st.symbol ?? "-")} / {String(st.period ?? "-")}
            </td>
          </tr>
          <tr>
            <td>进度</td>
            <td>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <div style={{ flex: 1, height: 10, background: "var(--border)", borderRadius: 5, overflow: "hidden" }}>
                  <div
                    style={{ width: `${progress}%`, height: "100%", background: "var(--accent)", transition: "width .5s" }}
                  />
                </div>
                <b>{progress.toFixed(1)}%</b>
              </div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>
                已回放 {String(st.bars ?? 0)} 根 bar，当前到 {String(st.current_time ?? "-")}
              </div>
            </td>
          </tr>
          <tr>
            <td>最近动作</td>
            <td>
              {source} {st.holding ? "（持仓中）" : "（空仓）"}
            </td>
          </tr>
          <tr>
            <td>成交笔数 / 累计</td>
            <td>
              已配对 {nTrades} 笔，累计 {cumPct >= 0 ? "+" : ""}
              {cumPct.toFixed(3)}%（价格%）
            </td>
          </tr>
          <tr>
            <td>心跳 / PID</td>
            <td>
              {ts} / {String(st.pid ?? "-")}
            </td>
          </tr>
        </tbody>
      </table>

      <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 8 }}>
        桥目录（与 EA 共用，Tester 沙箱唯一可写）：{""}
        <code>{bridgeDir || "Common\\Files\\dsb"}</code>
      </div>
    </div>
  );
}