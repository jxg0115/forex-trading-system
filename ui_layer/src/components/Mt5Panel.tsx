import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Cable,
  Calculator,
  Play,
  RefreshCw,
  Send,
  Square,
  XCircle,
} from "lucide-react";
import { api, type Mt5Account, type Mt5Order, type Mt5Position, type Mt5Status, type Mt5Tick } from "../api";
import { SymbolSearchSelect } from "./SymbolSearchSelect";

interface Props {
  symbol: string;
  timeframe: string;
}

const ORDER_TYPES = [
  { value: "BUY", label: "市价买入" },
  { value: "SELL", label: "市价卖出" },
  { value: "BUY_LIMIT", label: "Buy Limit 低位限价" },
  { value: "SELL_LIMIT", label: "Sell Limit 高位限价" },
  { value: "BUY_STOP", label: "Buy Stop 高位突破" },
  { value: "SELL_STOP", label: "Sell Stop 低位突破" },
  { value: "BUY_STOP_LIMIT", label: "Buy Stop Limit 止损限价" },
  { value: "SELL_STOP_LIMIT", label: "Sell Stop Limit 止损限价" },
];

export function Mt5Panel({ symbol, timeframe }: Props) {
  const [status, setStatus] = useState<Mt5Status | null>(null);
  const [account, setAccount] = useState<Mt5Account | null>(null);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [positions, setPositions] = useState<Mt5Position[]>([]);
  const [orders, setOrders] = useState<Mt5Order[]>([]);
  const [tick, setTick] = useState<Mt5Tick | null>(null);
  const [busy, setBusy] = useState(false);
  const [setupBusy, setSetupBusy] = useState(false);
  const [setup, setSetup] = useState({ mt5_path: "", login: "", password: "", server: "" });
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [form, setForm] = useState({
    symbol,
    action_type: "BUY",
    volume: "0.10",
    price: "",
    stoplimit: "",
    sl: "",
    tp: "",
    expiration_bars: "3",
    comment: "AI_Exec",
    risk_percent: "1.0",
    sl_pips: "20",
  });

  const loadAll = useCallback(async () => {
    try {
      const st = await api.mt5Status();
      setStatus(st);
      if (st.connected && st.account) {
        setAccount(st.account);
      }
      const [symRes, posRes, ordRes] = await Promise.all([
        api.mt5Symbols(),
        api.mt5Positions(),
        api.mt5Orders(),
      ]);
      setSymbols(symRes.symbols);
      setPositions(posRes.positions);
      setOrders(ordRes.orders);
    } catch (err) {
      setMsg({ type: "error", text: String(err instanceof Error ? err.message : err) });
    }
  }, []);

  const loadSetup = useCallback(async () => {
    try {
      const data = await api.mt5Setup();
      setSetup((v) => ({
        ...v,
        mt5_path: data.mt5_path || "",
        login: String(data.login ?? ""),
        server: data.server || "",
      }));
    } catch {
      // 忽略配置读取失败
    }
  }, []);

  useEffect(() => {
    loadAll();
    loadSetup();
  }, [loadAll, loadSetup]);

  const saveSetup = useCallback(async () => {
    setSetupBusy(true);
    try {
      const res = await api.saveMt5Setup({
        mt5_path: setup.mt5_path,
        login: setup.login,
        password: setup.password,
        server: setup.server,
      });
      setMsg({ type: "success", text: res.message });
      await loadAll();
    } catch (err) {
      setMsg({ type: "error", text: String(err instanceof Error ? err.message : err) });
    } finally {
      setSetupBusy(false);
    }
  }, [setup, loadAll]);

  useEffect(() => {
    setForm((f) => ({ ...f, symbol }));
  }, [symbol]);

  useEffect(() => {
    if (!status?.connected) {
      setTick(null);
      return;
    }
    const ws = new WebSocket(`ws://${window.location.host}/api/mt5/ws/tick?symbol=${form.symbol}`);
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.data) setTick(msg.data);
      } catch {
        // 忽略异常帧
      }
    };
    ws.onclose = () => setTick(null);
    return () => ws.close();
  }, [status?.connected, form.symbol]);

  const sendOrder = useCallback(async () => {
    setBusy(true);
    try {
      const payload = {
        symbol: form.symbol,
        action_type: form.action_type,
        volume: Number(form.volume),
        price: form.price ? Number(form.price) : undefined,
        stoplimit_price: form.stoplimit ? Number(form.stoplimit) : undefined,
        sl: form.sl ? Number(form.sl) : undefined,
        tp: form.tp ? Number(form.tp) : undefined,
        expiration_bars: Number(form.expiration_bars),
        comment: form.comment,
      };
      const res = await api.mt5SendOrder(payload);
      setMsg({ type: "success", text: `订单已发送：${form.action_type} ${form.volume} 手` });
      await loadAll();
    } catch (err) {
      setMsg({ type: "error", text: String(err instanceof Error ? err.message : err) });
    } finally {
      setBusy(false);
    }
  }, [form, loadAll]);

  const calcLot = useCallback(async () => {
    setBusy(true);
    try {
      const res = await api.mt5CalcLot(form.symbol, Number(form.risk_percent), Number(form.sl_pips));
      setForm((f) => ({ ...f, volume: String(res.calculated_lot) }));
      setMsg({ type: "success", text: `按风险 ${form.risk_percent}% 计算，建议手数 ${res.calculated_lot}` });
    } catch (err) {
      setMsg({ type: "error", text: String(err instanceof Error ? err.message : err) });
    } finally {
      setBusy(false);
    }
  }, [form.symbol, form.risk_percent, form.sl_pips]);

  const closePosition = useCallback(async (ticket: number) => {
    try {
      await api.mt5Close(ticket);
      setMsg({ type: "success", text: `持仓 #${ticket} 已平仓` });
      await loadAll();
    } catch (err) {
      setMsg({ type: "error", text: String(err instanceof Error ? err.message : err) });
    }
  }, [loadAll]);

  const cancelOrder = useCallback(async (ticket: number) => {
    try {
      await api.mt5Cancel(ticket);
      setMsg({ type: "success", text: `挂单 #${ticket} 已撤销` });
      await loadAll();
    } catch (err) {
      setMsg({ type: "error", text: String(err instanceof Error ? err.message : err) });
    }
  }, [loadAll]);

  const closeAll = useCallback(async () => {
    try {
      const res = await api.mt5CloseAll();
      setMsg({ type: "success", text: `一键平仓完成：${res.closed.length} / ${res.total}` });
      await loadAll();
    } catch (err) {
      setMsg({ type: "error", text: String(err instanceof Error ? err.message : err) });
    }
  }, [loadAll]);

  return (
    <>
      <div className="tool-block">
        <h3><Cable size={13} /> MT5 自动配置</h3>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <div style={{ gridColumn: "1 / -1" }}>
            <label className="field-label">MT5 终端路径（terminal64.exe）</label>
            <input className="form-input" value={setup.mt5_path} onChange={(e) => setSetup((v) => ({ ...v, mt5_path: e.target.value }))} placeholder="例如 D:\MT5\terminal64.exe" />
          </div>
          <div>
            <label className="field-label">账号</label>
            <input className="form-input" value={setup.login} onChange={(e) => setSetup((v) => ({ ...v, login: e.target.value }))} placeholder="MT5 账号" />
          </div>
          <div>
            <label className="field-label">服务器</label>
            <input className="form-input" value={setup.server} onChange={(e) => setSetup((v) => ({ ...v, server: e.target.value }))} placeholder="例如 Exness-MT5" />
          </div>
          <div style={{ gridColumn: "1 / -1" }}>
            <label className="field-label">密码</label>
            <input className="form-input" type="password" value={setup.password} onChange={(e) => setSetup((v) => ({ ...v, password: e.target.value }))} placeholder="MT5 密码" />
          </div>
        </div>
        <div className="row-actions" style={{ marginTop: 8 }}>
          <button className="mini-btn primary" disabled={setupBusy} onClick={saveSetup}>
            <RefreshCw size={12} /> {setupBusy ? "配置中..." : "自动配置 MT5"}
          </button>
        </div>
      </div>
      <div className="tool-block">
        <h3><Cable size={13} /> MT5 网关状态</h3>
        <div className="metric-grid" style={{ marginBottom: 8 }}>
          <div className="metric">
            <div className="label">连接状态</div>
            <div className={`value ${status?.connected ? "good" : "bad"}`}>{status?.connected ? "已连接" : "未连接"}</div>
          </div>
          <div className="metric">
            <div className="label">经纪商模式</div>
            <div className="value" style={{ fontSize: 12 }}>{status?.broker_mode ?? "模拟"}</div>
          </div>
          <div className="metric">
            <div className="label">账户</div>
            <div className="value" style={{ fontSize: 11 }}>{account?.login ?? "--"}</div>
          </div>
        </div>
        <div className="row-actions">
          <button className="mini-btn" onClick={loadAll}><RefreshCw size={12} /> 刷新状态</button>
          <button className="mini-btn primary" disabled={!status?.connected} onClick={closeAll}><Square size={12} /> 一键平仓</button>
        </div>
        {status?.connected && account && !account.trade_allowed && (
          <p className="factor-desc pnl-neg">MT5 算法交易未启用，请点击终端工具栏的“算法交易”按钮后再自动下单。</p>
        )}
        {status?.connected && account && account.trade_allowed && (
          <p className="factor-desc pnl-pos">MT5 算法交易已启用，AI 自动下单可正常执行。</p>
        )}
        {msg && msg.text.includes("10018") && (
          <p className="factor-desc pnl-neg">MT5 市场当前休市，下单会被拒绝；请等待交易时段再试。</p>
        )}
        {msg && <p className={`factor-desc ${msg.type === "error" ? "pnl-neg" : "pnl-pos"}`}>{msg.text}</p>}
      </div>

      {account && (
        <div className="tool-block">
          <h3><Activity size={13} /> 账户资金</h3>
          <div className="metric-grid">
            <div className="metric"><div className="label">余额</div><div className="value">{account.balance.toFixed(2)}</div></div>
            <div className="metric"><div className="label">净值</div><div className="value">{account.equity.toFixed(2)}</div></div>
            <div className="metric"><div className="label">浮动盈亏</div><div className={`value ${account.profit >= 0 ? "good" : "bad"}`}>{account.profit >= 0 ? "+" : ""}{account.profit.toFixed(2)}</div></div>
            <div className="metric"><div className="label">占用保证金</div><div className="value">{account.margin.toFixed(2)}</div></div>
            <div className="metric"><div className="label">可用保证金</div><div className="value">{account.free_margin.toFixed(2)}</div></div>
            <div className="metric"><div className="label">杠杆</div><div className="value">1:{account.leverage}</div></div>
          </div>
        </div>
      )}

      <div className="tool-block">
        <h3><Activity size={13} /> 实时盘口</h3>
        <div className="metric-grid">
          <div className="metric"><div className="label">买价 Bid</div><div className="value good">{tick?.bid.toFixed(5) ?? "--"}</div></div>
          <div className="metric"><div className="label">卖价 Ask</div><div className="value bad">{tick?.ask.toFixed(5) ?? "--"}</div></div>
          <div className="metric"><div className="label">点差</div><div className="value">{tick?.spread_points ?? "--"} 点</div></div>
        </div>
      </div>

      <div className="tool-block">
        <h3><Send size={13} /> MT5 下单</h3>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <div>
              <label className="field-label">交易品种</label>
              <SymbolSearchSelect symbols={symbols} value={form.symbol} onChange={(s) => setForm({ ...form, symbol: s })} />
            </div>
            <div>
              <label className="field-label">订单类型</label>
              <select className="form-input" value={form.action_type} onChange={(e) => setForm({ ...form, action_type: e.target.value })}>
                {ORDER_TYPES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            <div>
              <label className="field-label">手数</label>
              <input className="form-input" type="number" step="0.01" min="0" value={form.volume} onChange={(e) => setForm({ ...form, volume: e.target.value })} />
            </div>
            <div>
              <label className="field-label">挂单价格（市价单可留空）</label>
              <input className="form-input" type="number" step="0.00001" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} />
            </div>
            <div>
              <label className="field-label">止损限价触发价</label>
              <input className="form-input" type="number" step="0.00001" value={form.stoplimit} onChange={(e) => setForm({ ...form, stoplimit: e.target.value })} />
            </div>
            <div>
              <label className="field-label">止损价</label>
              <input className="form-input" type="number" step="0.00001" value={form.sl} onChange={(e) => setForm({ ...form, sl: e.target.value })} />
            </div>
            <div>
              <label className="field-label">止盈价</label>
              <input className="form-input" type="number" step="0.00001" value={form.tp} onChange={(e) => setForm({ ...form, tp: e.target.value })} />
            </div>
            <div>
              <label className="field-label">挂单超时K线数</label>
              <input className="form-input" type="number" min="1" value={form.expiration_bars} onChange={(e) => setForm({ ...form, expiration_bars: e.target.value })} />
            </div>
            <div>
              <label className="field-label">订单备注</label>
              <input className="form-input" value={form.comment} onChange={(e) => setForm({ ...form, comment: e.target.value })} />
            </div>
          </div>
        <div className="row-actions" style={{ marginTop: 8 }}>
          <button className="mini-btn primary" disabled={busy || !status?.connected} onClick={sendOrder}><Play size={12} /> 发送订单</button>
        </div>
      </div>

      <div className="tool-block">
          <h3><Calculator size={13} /> 风险计算手数</h3>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
            <div>
              <label className="field-label">单笔风险 %</label>
              <input className="form-input" type="number" step="0.1" value={form.risk_percent} onChange={(e) => setForm({ ...form, risk_percent: e.target.value })} />
            </div>
            <div>
              <label className="field-label">止损点数</label>
              <input className="form-input" type="number" step="0.1" value={form.sl_pips} onChange={(e) => setForm({ ...form, sl_pips: e.target.value })} />
            </div>
            <div style={{ display: "flex", alignItems: "flex-end" }}>
              <button className="mini-btn primary" disabled={!status?.connected} onClick={calcLot}><Calculator size={12} /> 计算手数</button>
            </div>
          </div>
      </div>

      <div className="tool-block">
        <h3><Activity size={13} /> 活动持仓</h3>
        {positions.length === 0 ? (
          <div className="empty">暂无 MT5 持仓</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {positions.map((p) => (
              <div className="trade-row" key={p.ticket}>
                <div><div className="t-label">{p.symbol} {p.type === "BUY" ? "买入" : "卖出"}</div><div className="t-value">{p.volume} 手 @ {p.price_open.toFixed(5)}</div></div>
                <div><div className="t-label">浮动盈亏</div><div className={`t-value ${p.profit >= 0 ? "pnl-pos" : "pnl-neg"}`}>{p.profit >= 0 ? "+" : ""}{p.profit.toFixed(2)}</div></div>
                <div><div className="t-label">止损/止盈</div><div className="t-value">{p.sl ? p.sl.toFixed(5) : "--"} / {p.tp ? p.tp.toFixed(5) : "--"}</div></div>
                <button className="mini-btn danger" onClick={() => closePosition(p.ticket)}>平仓</button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="tool-block">
        <h3><XCircle size={13} /> 未成交挂单</h3>
        {orders.length === 0 ? (
          <div className="empty">暂无 MT5 挂单</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {orders.map((o) => (
              <div className="trade-row" key={o.ticket}>
                <div><div className="t-label">{o.symbol} {o.type}</div><div className="t-value">@{o.price_open.toFixed(5)}</div></div>
                <div><div className="t-label">手数</div><div className="t-value">{o.volume_initial}</div></div>
                <button className="mini-btn" onClick={() => cancelOrder(o.ticket)}>撤单</button>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
