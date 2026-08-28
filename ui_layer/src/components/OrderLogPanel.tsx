import { Fragment, useCallback, useEffect, useState } from "react";
import { FileClock, RefreshCw } from "lucide-react";
import { api, type OrderLog } from "../api";

const STATUS_LABEL: Record<string, string> = {
  submitted: "已提交",
  pending: "挂单中",
  filled: "已成交",
  rejected: "已拒绝",
  canceled: "已撤销",
  canceling: "撤单中",
  closing: "平仓中",
  closed: "已平仓",
};

const ACTION_LABEL: Record<string, string> = {
  open: "开仓",
  close: "平仓",
  cancel: "撤单",
};

const TYPE_LABEL: Record<string, string> = {
  market: "市价",
  limit: "限价",
  stop: "止损",
  stop_limit: "止损限价",
};

function statusClass(status: string): string {
  if (status === "filled" || status === "closed") return "pnl-pos";
  if (status === "rejected" || status === "canceled") return "pnl-neg";
  return "";
}

export function OrderLogPanel() {
  const [logs, setLogs] = useState<OrderLog[]>([]);
  const [filter, setFilter] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.orderLog();
      setLogs(res.logs);
    } catch {
      // 日志加载失败不影响主流程
    }
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  const filtered = filter
    ? logs.filter((l) => l.symbol.toLowerCase().includes(filter.toLowerCase()) || l.mt5_ticket.includes(filter))
    : logs;

  return (
    <>
      <div className="tool-block">
        <h3><FileClock size={13} /> 订单日志</h3>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input className="form-input" placeholder="搜索品种或订单号" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <button className="mini-btn" onClick={load}><RefreshCw size={12} /> 刷新</button>
        </div>
      </div>
      <div className="tool-block">
        <table className="order-log-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>品种</th>
              <th>方向</th>
              <th>类型</th>
              <th>手数</th>
              <th>价格</th>
              <th>止损/止盈</th>
              <th>动作</th>
              <th>状态</th>
              <th>消息</th>
              <th>详情</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 50).map((log) => (
              <Fragment key={log.id}>
                <tr>
                  <td>{new Date(log.updated_at).toLocaleString()}</td>
                  <td>{log.symbol}</td>
                  <td>{log.side === "buy" ? "买入" : "卖出"}</td>
                  <td>{TYPE_LABEL[log.order_type] ?? log.order_type}</td>
                  <td>{log.volume}</td>
                  <td>{log.price?.toFixed(5) ?? "--"}</td>
                  <td>{log.sl ? log.sl.toFixed(5) : "--"} / {log.tp ? log.tp.toFixed(5) : "--"}</td>
                  <td>{ACTION_LABEL[log.action] ?? log.action}</td>
                  <td className={statusClass(log.status)}>{STATUS_LABEL[log.status] ?? log.status}</td>
                  <td>{log.message || log.reason || "--"}</td>
                  <td>
                    <button className="mini-btn" onClick={() => setExpandedId(expandedId === log.id ? null : log.id)}>
                      {expandedId === log.id ? "收起" : "详情"}
                    </button>
                  </td>
                </tr>
                {expandedId === log.id && (
                  <tr>
                    <td colSpan={11}>
                      <pre className="order-log-detail">{JSON.stringify({
                        "请求": log.request_payload ?? {},
                        "响应": log.response_data ?? {},
                      }, null, 2)}</pre>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && <div className="empty">暂无订单日志</div>}
      </div>
    </>
  );
}
