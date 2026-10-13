import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { KeyRound, PlugZap, ShieldAlert, Trash2 } from "lucide-react";
import { api, type OkxConfig, type OkxRuntime, type OkxTestResult } from "../api";

interface Props {
  onNotify?: (type: "success" | "error", message: string) => void;
}

const S: Record<string, CSSProperties> = {
  wrap: { display: "grid", gap: 12 },
  card: { border: "1px solid var(--border, #e3e3e8)", borderRadius: 10, padding: 14, background: "var(--panel, #fff)" },
  title: { fontSize: 13, fontWeight: 600, marginBottom: 10, display: "flex", gap: 6, alignItems: "center" },
  field: { display: "grid", gap: 4, marginBottom: 10 },
  label: { fontSize: 12, color: "#555", display: "flex", gap: 6, alignItems: "center" },
  input: { width: "100%", boxSizing: "border-box", padding: "6px 8px", border: "1px solid #d8d8de", borderRadius: 6, fontSize: 12 },
  select: { padding: "6px 8px", border: "1px solid #d8d8de", borderRadius: 6, fontSize: 12 },
  row: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" },
  btn: { padding: "6px 12px", border: "1px solid #d8d8de", borderRadius: 6, background: "#f6f6f8", cursor: "pointer", fontSize: 12, display: "flex", gap: 5, alignItems: "center" },
  primary: { background: "#e8f0fe", borderColor: "#9dbcf0", color: "#1b4b8f" },
  danger: { background: "#fdeaea", borderColor: "#e6a5a5", color: "#8a2020" },
  hint: { color: "#888", fontSize: 11, lineHeight: 1.6 },
  warn: { background: "#fff8e6", border: "1px solid #f0d9a0", borderRadius: 8, padding: 10, fontSize: 12, lineHeight: 1.7 },
  kv: { display: "flex", justifyContent: "space-between", fontSize: 12, padding: "3px 0" },
  check: { display: "flex", gap: 6, alignItems: "center", fontSize: 12, marginRight: 10 },
  badge: { padding: "2px 8px", borderRadius: 999, fontSize: 11, border: "1px solid #d8d8de" },
};

const INST_TYPES = ["SWAP", "SPOT", "FUTURES"];

export function OkxSettings({ onNotify }: Props) {
  const [cfg, setCfg] = useState<OkxConfig | null>(null);
  const [runtime, setRuntime] = useState<OkxRuntime | null>(null);
  const [envPath, setEnvPath] = useState("");
  const [form, setForm] = useState({
    enable: false,
    api_key: "",
    secret_key: "",
    passphrase: "",
    flag: "1",
    td_mode: "cross",
    pos_side_mode: "both",
    proxy_url: "",
    inst_types: "SWAP,SPOT,FUTURES",
    venue_mode: "route",
  });
  const [showSecrets, setShowSecrets] = useState(false);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<OkxTestResult | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.okxConfig();
      setCfg(res.config);
      setRuntime(res.runtime);
      setEnvPath(res.env_path);
      setForm((prev) => ({
        ...prev,
        enable: res.config.enable,
        api_key: "",
        secret_key: "",
        passphrase: "",
        flag: res.config.flag || "1",
        td_mode: res.config.td_mode || "cross",
        pos_side_mode: res.config.pos_side_mode || "both",
        proxy_url: res.config.proxy_url || "",
        inst_types: res.config.inst_types || "SWAP,SPOT,FUTURES",
        venue_mode: res.config.venue_mode || "route",
      }));
    } catch (err) {
      onNotify?.("error", `读取 OKX 配置失败：${(err as Error).message}`);
    }
  }, [onNotify]);

  useEffect(() => {
    void load();
  }, [load]);

  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const toggleInst = (name: string) => {
    const current = form.inst_types.split(",").map((s) => s.trim()).filter(Boolean);
    const next = current.includes(name) ? current.filter((s) => s !== name) : [...current, name];
    set("inst_types", next.join(","));
  };

  const handleSave = async () => {
    setBusy(true);
    try {
      const res = await api.saveOkxConfig({
        enable: form.enable,
        api_key: form.api_key,
        secret_key: form.secret_key,
        passphrase: form.passphrase,
        flag: form.flag,
        td_mode: form.td_mode,
        pos_side_mode: form.pos_side_mode,
        proxy_url: form.proxy_url,
        inst_types: form.inst_types,
        venue_mode: form.venue_mode,
      });
      setCfg(res.config);
      setRuntime(res.runtime);
      setForm((prev) => ({ ...prev, api_key: "", secret_key: "", passphrase: "" }));
      onNotify?.("success", res.message || "已保存");
    } catch (err) {
      onNotify?.("error", `保存失败：${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleTest = async () => {
    setBusy(true);
    setTest(null);
    try {
      const res = await api.testOkxConfig({
        api_key: form.api_key,
        secret_key: form.secret_key,
        passphrase: form.passphrase,
        flag: form.flag,
        proxy_url: form.proxy_url,
      });
      setTest(res);
      onNotify?.(res.success ? "success" : "error", res.message);
    } catch (err) {
      setTest({ success: false, message: (err as Error).message });
      onNotify?.("error", `测试失败：${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleClear = async () => {
    setBusy(true);
    try {
      const res = await api.saveOkxConfig({ clear_secrets: true, enable: false });
      setCfg(res.config);
      setRuntime(res.runtime);
      onNotify?.("success", "已清除密钥并停用 OKX");
    } catch (err) {
      onNotify?.("error", `清除失败：${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const isLive = form.flag === "0";

  return (
    <div style={S.wrap}>
      <div style={S.card}>
        <div style={S.title}><KeyRound size={14} /> 欧易 OKX API 密钥</div>

        <div style={S.row}>
          <span style={S.badge}>
            状态：{runtime?.connected ? "已连接" : runtime?.configured ? "已配置未连接" : "未配置"}
          </span>
          {runtime?.server && <span style={S.badge}>{runtime.server}</span>}
          {runtime?.proxy && <span style={S.badge}>代理 {runtime.proxy}</span>}
          <span style={{ ...S.badge, ...(form.enable ? { background: "#e8f5ec", borderColor: "#9fd6b4", color: "#14663a" } : {}) }}>
            {form.enable ? "已启用" : "已停用"}
          </span>
        </div>
        {runtime?.last_error && (
          <div style={{ ...S.warn, marginTop: 8, borderColor: "#e6a5a5", background: "#fdeaea" }}>
            上次错误：{runtime.last_error}
          </div>
        )}
        <div style={{ ...S.kv, marginTop: 6 }}>
          <span>配置文件</span><span style={{ color: "#888" }}>{envPath || "-"}</span>
        </div>
        <div style={S.kv}>
          <span>已保存的 Key</span><span style={{ color: "#888" }}>{cfg?.api_key || "（未填写）"}</span>
        </div>
        <div style={S.kv}>
          <span>Secret / Passphrase</span>
          <span style={{ color: "#888" }}>
            {cfg?.has_secret ? "已保存" : "未填写"} / {cfg?.has_passphrase ? "已保存" : "未填写"}
          </span>
        </div>
      </div>

      <div style={S.card}>
        <div style={S.title}><PlugZap size={14} /> 连接参数</div>

        <label style={S.check}>
          <input type="checkbox" checked={form.enable} onChange={(e) => set("enable", e.target.checked)} />
          启用 OKX 网关（保存后立即重载，无需重启后端）
        </label>

        <div style={S.row}>
          <label style={S.label}>
            环境
            <select style={S.select} value={form.flag} onChange={(e) => set("flag", e.target.value)}>
              <option value="1">模拟盘（沙盒，建议先跑通）</option>
              <option value="0">实盘</option>
            </select>
          </label>
          <label style={S.label}>
            保证金模式
            <select style={S.select} value={form.td_mode} onChange={(e) => set("td_mode", e.target.value)}>
              <option value="cross">cross 全仓</option>
              <option value="isolated">isolated 逐仓</option>
              <option value="cash">cash 现货</option>
            </select>
          </label>
          <label style={S.label}>
            持仓模式
            <select style={S.select} value={form.pos_side_mode} onChange={(e) => set("pos_side_mode", e.target.value)}>
              <option value="both">both 双向（可同时持多空）</option>
              <option value="net">net 单向净持仓</option>
            </select>
          </label>
          <label style={S.label}>
            场所路由
            <select style={S.select} value={form.venue_mode} onChange={(e) => set("venue_mode", e.target.value)}>
              <option value="route">route 按品种自动路由</option>
              <option value="mt5">mt5 全局切到 MT5</option>
              <option value="okx">okx 全局切到 OKX</option>
            </select>
          </label>
        </div>

        <div style={S.field}>
          <span style={S.label}>可交易合约类型</span>
          <div style={S.row}>
            {INST_TYPES.map((t) => (
              <label key={t} style={S.check}>
                <input
                  type="checkbox"
                  checked={form.inst_types.split(",").includes(t)}
                  onChange={() => toggleInst(t)}
                />
                {t}
              </label>
            ))}
          </div>
        </div>

        <div style={S.field}>
          <span style={S.label}>代理（留空=自动探测系统代理，连不上时填 http://127.0.0.1:端口，直连填 direct）</span>
          <input
            style={S.input}
            value={form.proxy_url}
            placeholder="留空自动探测"
            onChange={(e) => set("proxy_url", e.target.value)}
          />
        </div>
      </div>

      <div style={S.card}>
        <div style={S.title}>
          <ShieldAlert size={14} /> 密钥三件套
          <button
            style={{ ...S.btn, marginLeft: "auto", padding: "3px 8px", fontSize: 11 }}
            onClick={() => setShowSecrets((v) => !v)}
          >
            {showSecrets ? "隐藏" : "显示"}
          </button>
        </div>

        <div style={S.field}>
          <span style={S.label}>API Key</span>
          <input
            style={S.input}
            value={form.api_key}
            placeholder={cfg?.api_key || "在 OKX 创建的 apiKey"}
            onChange={(e) => set("api_key", e.target.value)}
          />
        </div>
        <div style={S.field}>
          <span style={S.label}>Secret Key</span>
          <input
            style={S.input}
            type={showSecrets ? "text" : "password"}
            value={form.secret_key}
            placeholder={cfg?.has_secret ? "已保存，留空则不修改" : "创建时只显示一次"}
            onChange={(e) => set("secret_key", e.target.value)}
          />
        </div>
        <div style={S.field}>
          <span style={S.label}>Passphrase</span>
          <input
            style={S.input}
            type={showSecrets ? "text" : "password"}
            value={form.passphrase}
            placeholder={cfg?.has_passphrase ? "已保存，留空则不修改" : "创建 Key 时自己设置的口令"}
            onChange={(e) => set("passphrase", e.target.value)}
          />
        </div>
        <p style={S.hint}>
          留空 = 保持已保存的值不变（方便只改环境开关）。密钥明文只写入本机 .env，接口永不回传明文。
        </p>

        <div style={S.row}>
          <button style={{ ...S.btn, ...S.primary }} disabled={busy} onClick={handleTest}>
            <PlugZap size={12} /> 测试连接
          </button>
          <button style={S.btn} disabled={busy} onClick={handleSave}>
            保存并重载
          </button>
          <button style={{ ...S.btn, ...S.danger }} disabled={busy} onClick={handleClear}>
            <Trash2 size={12} /> 清除密钥
          </button>
        </div>

        {test && (
          <div style={{ ...S.warn, marginTop: 8, borderColor: test.success ? "#9fd6b4" : "#e6a5a5", background: test.success ? "#e8f5ec" : "#fdeaea" }}>
            {test.stage ? `[${test.stage}] ` : ""}{test.message}
            {test.success && test.equity != null && ` ｜ 权益 ${test.equity}`}
            {test.proxy && ` ｜ 代理 ${test.proxy}`}
          </div>
        )}
      </div>

      <div style={S.card}>
        <div style={S.title}><ShieldAlert size={14} /> 安全提醒</div>
        <p style={S.hint}>
          1. 在 OKX 右上角切到「模拟盘 / Demo Trading」再创建 Key —— 沙盒 Key 与实盘 Key <b>不通用</b>，
          环境选错会直接鉴权失败。<br />
          2. 权限只勾 <b>只读 + 交易</b>，<b>绝对不要勾提币</b>；能绑 IP 白名单就绑。<br />
          3. Secret Key 只在创建时显示一次，关掉窗口就找不回来了。<br />
          4. 这里保存后会立刻热重载网关；若账户实际是单向持仓而此处选了双向，下单会被拒。
        </p>
        {isLive && (
          <div style={{ ...S.warn, marginTop: 8, borderColor: "#e6a5a5", background: "#fdeaea" }}>
            当前选择的是<b>实盘</b>环境，保存后会用真实资金下单，请确认 Key 权限与风控参数。
          </div>
        )}
      </div>
    </div>
  );
}
