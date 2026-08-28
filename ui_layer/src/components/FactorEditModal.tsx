import { useState } from "react";
import { ShieldCheck } from "lucide-react";
import { api } from "../api";
import type { Factor, FactorPayload } from "../api";

interface Props {
  factor: Factor;
  onSave: (payload: FactorPayload) => Promise<void>;
  onClose: () => void;
}

export function FactorEditModal({ factor, onSave, onClose }: Props) {
  const [name, setName] = useState(factor.name);
  const [description, setDescription] = useState(factor.description);
  const [code, setCode] = useState(factor.code);
  const [tags, setTags] = useState((factor.tags ?? []).join(", "));
  const [marketAdapt, setMarketAdapt] = useState<Record<string, unknown>>(factor.market_adapt ?? {});
  const [saving, setSaving] = useState(false);
  const [sandboxMsg, setSandboxMsg] = useState<string | null>(null);
  const [diag, setDiag] = useState<{
    summary: string;
    static: { ok: boolean; errors: string[] };
    execution: { ok: boolean; message: string; entry_count: number };
    data_source: string;
    suggestions: string[];
  } | null>(null);
  const [diagnosing, setDiagnosing] = useState(false);

  const checkSandbox = async () => {
    setSandboxMsg("校验中...");
    try {
      const result = await api.checkSandbox(code);
      setSandboxMsg(result.ok ? "沙盒校验通过" : `沙盒未通过：${result.errors.join("；") || "未知原因"}`);
    } catch (err) {
      setSandboxMsg(String(err instanceof Error ? err.message : err));
    }
  };

  const runDiagnose = async () => {
    setDiagnosing(true);
    setDiag(null);
    try {
      const result = await api.sandboxDiagnose({
        code,
        symbol: factor.symbol,
        timeframe: factor.timeframe,
        bars_count: 300,
      });
      setDiag({
        summary: result.summary,
        static: result.static,
        execution: result.execution,
        data_source: result.data_source,
        suggestions: result.suggestions,
      });
    } catch (err) {
      setSandboxMsg(String(err instanceof Error ? err.message : err));
    } finally {
      setDiagnosing(false);
    }
  };

  const submit = async () => {
    setSaving(true);
    try {
      await onSave({
        name,
        description,
        code,
        symbol: factor.symbol,
        timeframe: factor.timeframe,
        source: factor.source,
        model: factor.model,
        params: factor.params,
        tags: tags.split(",").map((s) => s.trim()).filter(Boolean),
        chart_stats: factor.chart_stats,
        prompt_snapshot: factor.prompt_snapshot,
        generated_region: factor.generated_region,
        backtest_stats: factor.backtest_stats,
        market_adapt: marketAdapt,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay">
      <div className="modal">
        <h3>编辑因子</h3>
        <label className="modal-label">因子名称</label>
        <input className="form-input" value={name} onChange={(e) => setName(e.target.value)} />
        <label className="modal-label">描述</label>
        <textarea className="form-input modal-textarea" value={description} onChange={(e) => setDescription(e.target.value)} />
        <label className="modal-label">因子代码</label>
        <textarea className="form-input modal-textarea code" value={code} onChange={(e) => setCode(e.target.value)} spellCheck={false} />
        <label className="modal-label">标签（逗号分隔）</label>
        <input className="form-input" value={tags} onChange={(e) => setTags(e.target.value)} />
        <div className="opt-section-title">适配行情</div>
        <label className="modal-label">适配趋势（逗号分隔，可选：up/down/range）</label>
        <input className="form-input" value={(marketAdapt.trends as string[] ?? []).join(", ")} onChange={(e) => setMarketAdapt((v) => ({ ...v, trends: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) }))} placeholder="例如：up, down" />
        <label className="modal-label">适配波动率</label>
        <select className="form-input" value={String(marketAdapt.volatility ?? "")} onChange={(e) => setMarketAdapt((v) => ({ ...v, volatility: e.target.value ? [e.target.value] : [] }))}>
          <option value="">不限</option>
          <option value="高波动">高波动</option>
          <option value="中等波动">中等波动</option>
          <option value="低波动">低波动</option>
        </select>
        <div className="row-actions" style={{ marginTop: 6 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12 }}>
            最小 ADX
            <input className="form-input" type="number" min="0" max="60" style={{ width: 70, height: 26 }} value={String(marketAdapt.min_adx ?? 0)} onChange={(e) => setMarketAdapt((v) => ({ ...v, min_adx: Number(e.target.value) }))} />
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12 }}>
            ATR 分位下限
            <input className="form-input" type="number" min="0" max="100" style={{ width: 70, height: 26 }} value={String(marketAdapt.atr_min ?? 0)} onChange={(e) => setMarketAdapt((v) => ({ ...v, atr_min: Number(e.target.value) }))} />
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12 }}>
            ATR 分位上限
            <input className="form-input" type="number" min="0" max="100" style={{ width: 70, height: 26 }} value={String(marketAdapt.atr_max ?? 100)} onChange={(e) => setMarketAdapt((v) => ({ ...v, atr_max: Number(e.target.value) }))} />
          </label>
        </div>
        <label className="modal-label">大周期方向</label>
        <select className="form-input" value={String(marketAdapt.macro_direction ?? "")} onChange={(e) => setMarketAdapt((v) => ({ ...v, macro_direction: e.target.value }))}>
          <option value="">不限</option>
          <option value="long">只顺势做多</option>
          <option value="short">只顺势做空</option>
        </select>
        <label className="modal-label">量能状态</label>
        <select className="form-input" value={String(marketAdapt.volume_state ?? "")} onChange={(e) => setMarketAdapt((v) => ({ ...v, volume_state: e.target.value ? [e.target.value] : [] }))}>
          <option value="">不限</option>
          <option value="放量">放量</option>
          <option value="缩量">缩量</option>
          <option value="正常">正常</option>
        </select>
        <label className="modal-label">适配品种（逗号分隔，留空表示全部）</label>
        <input className="form-input" value={(marketAdapt.symbols as string[] ?? []).join(", ")} onChange={(e) => setMarketAdapt((v) => ({ ...v, symbols: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) }))} placeholder="例如：XAUUSD, EURUSD" />
        <div className="row-actions" style={{ marginTop: 10 }}>
          <button className="mini-btn" onClick={checkSandbox}><ShieldCheck size={12} /> 沙盒校验</button>
          <button className="mini-btn" onClick={runDiagnose} disabled={diagnosing}><ShieldCheck size={12} /> 沙盒测试排查</button>
          <button className="mini-btn primary" disabled={saving} onClick={submit}>保存修改</button>
          <button className="mini-btn" onClick={onClose}>取消</button>
        </div>
        {sandboxMsg && <p className={`factor-desc ${sandboxMsg.includes("通过") && !sandboxMsg.includes("未") ? "pnl-pos" : "pnl-neg"}`}>{sandboxMsg}</p>}
        {diagnosing && <p className="factor-desc">排查中，请稍候...</p>}
        {diag && (
          <div className="tool-block" style={{ marginTop: 8 }}>
            <h3>沙盒排查结果</h3>
            <p className={`factor-desc ${diag.summary.includes("通过") ? "pnl-pos" : "pnl-neg"}`}>
              {diag.summary} | 数据源：{diag.data_source}
            </p>
            <p className="factor-desc">静态检查：{diag.static.ok ? "通过" : "未通过"}</p>
            {!diag.static.ok && <p className="factor-desc pnl-neg">{diag.static.errors.join("；")}</p>}
            <p className="factor-desc">运行时执行：{diag.execution.ok ? `通过（信号数 ${diag.execution.entry_count}）` : `失败：${diag.execution.message}`}</p>
            {diag.suggestions.length > 0 && (
              <div className="factor-list">
                {diag.suggestions.map((s, i) => <div className="trade-row" key={i}><div className="t-value">{s}</div></div>)}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
