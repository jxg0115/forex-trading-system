import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import { api, type BrokerInstrument, type BrokerPosition, type CalcSizeResult } from "../api";

interface Props {
  symbol: string;
  timeframe: string;
  onNotify?: (type: "success" | "error", message: string) => void;
}

const S: Record<string, CSSProperties> = {
  wrap: { display: "grid", gap: 12 },
  card: { border: "1px solid var(--border, #e3e3e8)", borderRadius: 10, padding: 14, background: "var(--panel, #fff)" },
  title: { fontSize: 13, fontWeight: 600, marginBottom: 10, display: "flex", gap: 6, alignItems: "center" },
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8 },
  kv: { display: "flex", justifyContent: "space-between", fontSize: 12, padding: "3px 0" },
  row: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" },
  input: { width: 110, padding: "5px 8px", border: "1px solid #d8d8de", borderRadius: 6, fontSize: 12 },
  btn: { padding: "6px 12px", border: "1px solid #d8d8de", borderRadius: 6, background: "#f6f6f8", cursor: "pointer", fontSize: 12 },
  buy: { background: "#e8f5ec", borderColor: "#9fd6b4", color: "#14663a" },
  sell: { background: "#fdeaea", borderColor: "#e6a5a5", color: "#8a2020" },
  table: { width: "100%", borderCollapse: "collapse", fontSize: 12 },
  th: { textAlign: "left", padding: "6px 8px", borderBottom: "1px solid #e8e8ec", color: "#666" },
  td: { padding: "6px 8px", borderBottom: "1px solid #f1f1f4" },
  muted: { color: "#888", fontSize: 11 },
  warn: { background: "#fff8e6", border: "1px solid #f0d9a0", borderRadius: 8, padding: 10, fontSize: 12 },
};

function fmt(v: unknown, nd = 2): string {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(nd) : "-";
}

function countdown(ms: number): string {
  if (!ms) return "-";
  const diff = ms - Date.now();
  if (diff <= 0) return "已结算";
  const h = Math.floor(diff / 3_600_000);
  const m = Math.floor((diff % 3_600_000) / 60_000);
  return `${h}小时${m}分`;
}

export function OkxPanel({ symbol, timeframe, onNotify }: Props) {
  const [account, setAccount] = useState<Record<string, unknown> | null>(null);
  const [positions, setPositions] = useState<BrokerPosition[]>([]);
  const [funding, setFunding] = useState<{ funding_rate: number; next_funding_time: number } | null>(null);
  const [spec, setSpec] = useState<BrokerInstrument | null>(null);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [size, setSize] = useState("1");
  const [sl, setSl] = useState("");
  const [tp, setTp] = useState("");
  const [riskPct, setRiskPct] = useState("1.0");
  const [calc, setCalc] = useState<CalcSizeResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const acc = await api.brokerAccount("okx");
      setAccount(acc.data ?? null);
    } catch {
      setAccount(null);
    }
    try {
      const pos = await api.brokerPositions("okx");
      setPositions(pos.positions ?? []);
    } catch {
      setPositions([]);
    }
    try {
      const fr = await api.brokerFundingRate(symbol);
      setFunding(fr.data ?? null);
    } catch {
      setFunding(null);
    }
    try {
      const ins = await api.brokerInstrument(symbol);
      setSpec(ins.data ?? null);
    } catch {
      setSpec(null);
    }
  }, [symbol]);

  useEffect(() => {
    void refresh();
  }, [refresh, tick]);

  const specLine = useMemo(() => {
    if (!spec) return "";
    const unit = spec.inst_type === "SPOT" ? "币" : "张";
    return `1${unit} = ${fmt(spec.ct_val, 8)} 币 ｜ 步进 ${fmt(spec.lot_step, 8)} ｜ `
      + `最小 ${fmt(spec.min_lot, 8)} ｜ 最大 ${fmt(spec.max_lot, 8)} ｜ 价格精度 ${fmt(spec.tick_sz, 8)}`;
  }, [spec]);

  const runCalc = async () => {
    try {
      const price = Number((await api.brokerTick(symbol)).data?.ask ?? 0);
      const stop = Number(sl);
      if (!price || !stop) {
        onNotify?.("error", "需要当前价与止损价才能换算");
        return;
      }
      const res = await api.brokerCalcSize({
        symbol, entry: price, stop, risk_pct: Number(riskPct),
      });
      setCalc(res.data);
      setSize(String(res.data.size));
    } catch (err) {
      onNotify?.("error", String(err instanceof Error ? err.message : err));
    }
  };

  const submit = async () => {
    setBusy(true);
    try {
      const res = await api.brokerOrder({
        symbol,
        side,
        size: Number(size),
        sl: sl ? Number(sl) : undefined,
        tp: tp ? Number(tp) : undefined,
        comment: "ui",
      });
      onNotify?.(res.success ? "success" : "error", res.message ?? (res.success ? "已提交" : "下单失败"));
      setTick((v) => v + 1);
    } catch (err) {
      onNotify?.("error", String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  };

  const closePos = async (ticket: string) => {
    try {
      const res = await api.brokerClose(ticket);
      onNotify?.(res.success ? "success" : "error", res.message ?? (res.success ? "已平仓" : "平仓失败"));
      setTick((v) => v + 1);
    } catch (err) {
      onNotify?.("error", String(err instanceof Error ? err.message : err));
    }
  };

  return (
    <div style={S.wrap}>
      <div style={S.card}>
        <div style={S.title}>OKX 账户（{timeframe}）</div>
        {account ? (
          <div style={S.grid}>
            <div style={S.kv}><span>总权益</span><b>{fmt(account.equity)} USDT</b></div>
            <div style={S.kv}><span>可用</span><b>{fmt((account.usdt as Record<string, number>)?.avail)}</b></div>
            <div style={S.kv}><span>占用保证金</span><b>{fmt(account.margin)}</b></div>
            <div style={S.kv}><span>持仓模式</span><b>{String(account.pos_side_mode ?? "-") === "both" ? "双向持仓" : "单向"}</b></div>
            <div style={S.kv}><span>环境</span><b>{String(account.server ?? "-")}</b></div>
          </div>
        ) : (
          <div style={S.muted}>未连接或 API 未配置（检查 OKX_ENABLE / OKX_API_KEY，以及网络能否访问 okx.com）</div>
        )}
      </div>

      <div style={S.card}>
        <div style={S.title}>{symbol} 合约与成本</div>
        <div style={S.muted}>{specLine || "加载中…"}</div>
        <div style={{ ...S.kv, marginTop: 6 }}>
          <span>资金费率</span>
          <b>{funding ? `${(funding.funding_rate * 100).toFixed(4)}%` : "现货/无"}</b>
        </div>
        <div style={S.kv}>
          <span>下次结算</span>
          <b>{funding ? countdown(funding.next_funding_time) : "-"}</b>
        </div>
      </div>

      <div style={S.card}>
        <div style={S.title}>下单（size 单位：{spec?.inst_type === "SPOT" ? "币" : "张"}）</div>
        <div style={S.row}>
          <button style={{ ...S.btn, ...(side === "buy" ? S.buy : {}) }} onClick={() => setSide("buy")}>买入/开多</button>
          <button style={{ ...S.btn, ...(side === "sell" ? S.sell : {}) }} onClick={() => setSide("sell")}>卖出/开空</button>
          <input style={S.input} value={size} onChange={(e) => setSize(e.target.value)} placeholder="数量" />
          <input style={S.input} value={sl} onChange={(e) => setSl(e.target.value)} placeholder="止损价" />
          <input style={S.input} value={tp} onChange={(e) => setTp(e.target.value)} placeholder="止盈价" />
        </div>
        <div style={{ ...S.row, marginTop: 8 }}>
          <span style={S.muted}>风险%</span>
          <input style={{ ...S.input, width: 70 }} value={riskPct} onChange={(e) => setRiskPct(e.target.value)} />
          <button style={S.btn} onClick={runCalc}>按风险换算</button>
          <button style={{ ...S.btn, opacity: busy ? 0.6 : 1 }} onClick={submit} disabled={busy}>提交订单</button>
        </div>
        {calc && <div style={{ ...S.muted, marginTop: 8 }}>{calc.message}</div>}
        <div style={{ ...S.warn, marginTop: 10 }}>
          止损止盈通过 OKX 条件单挂载（成交后生效）；改止损是异步的，需要持仓上已挂条件单。
        </div>
      </div>

      <div style={S.card}>
        <div style={S.title}>持仓（{positions.length}）</div>
        {positions.length === 0 ? (
          <div style={S.muted}>暂无持仓</div>
        ) : (
          <table style={S.table}>
            <thead>
              <tr>
                <th style={S.th}>品种</th><th style={S.th}>方向</th><th style={S.th}>数量</th>
                <th style={S.th}>开仓价</th><th style={S.th}>现价</th><th style={S.th}>止损</th>
                <th style={S.th}>止盈</th><th style={S.th}>盈亏</th><th style={S.th}>强平价</th><th style={S.th} />
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.ticket}>
                  <td style={S.td}>{p.symbol}</td>
                  <td style={S.td}>{p.pos_side === "long" ? "多" : p.pos_side === "short" ? "空" : p.type}</td>
                  <td style={S.td}>{fmt(p.volume, 4)}</td>
                  <td style={S.td}>{fmt(p.price_open, 4)}</td>
                  <td style={S.td}>{fmt(p.price_current, 4)}</td>
                  <td style={S.td}>{p.sl ? fmt(p.sl, 4) : "-"}</td>
                  <td style={S.td}>{p.tp ? fmt(p.tp, 4) : "-"}</td>
                  <td style={{ ...S.td, color: p.profit >= 0 ? "#c0392b" : "#2e7d32" }}>{fmt(p.profit)}</td>
                  <td style={S.td}>{p.liquidation_px ? fmt(p.liquidation_px, 2) : "-"}</td>
                  <td style={S.td}>
                    <button style={S.btn} onClick={() => closePos(p.ticket)}>平仓</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
