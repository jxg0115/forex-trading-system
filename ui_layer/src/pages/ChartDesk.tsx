import {
  Activity,
  Ban,
  Bot,
  BoxSelect,
  Eye,
  RefreshCw,
  Save,
  Sparkles,
  StopCircle,
  TestTube2,
  Undo2,
  X,
} from "lucide-react";
import { KLineChart, type ChartMode, type ModelSignal } from "../components/KLineChart";
import { MarketStatusPanel } from "../components/MarketStatusPanel";
import type { Bar, Factor, IndicatorData, MarkerPoint, Selection } from "../api";

export interface RegionStats {
  bar_count: number;
  return_pct: number;
  trend: string;
  volatility: string;
}

export interface ChartDeskProps {
  onNotify: (type: "success" | "error", message: string) => void;

  // 行情数据
  bars: Bar[];
  selection: Selection | null;
  entryPoints: MarkerPoint[];
  exitPoints: MarkerPoint[];
  supportLevels: Array<{ price: number; label?: string; touched?: number }>;
  resistanceLevels: Array<{ price: number; label?: string; touched?: number }>;
  mode: ChartMode;
  regionStats: RegionStats | null;
  indicators: IndicatorData | null;
  showIndicators: boolean;
  modelSignal: ModelSignal | null;
  snapshot: { current_price: number; change_pct_24h: number } | null;
  busy: boolean;
  draft: unknown;
  selectedFactor: Factor | null;
  markerHistoryLength: number;
  levelHistoryLength: number;

  // 回调
  onMode: (m: ChartMode) => void;
  onSelection: (s: Selection | null) => void;
  onAddPoint: (p: MarkerPoint) => void;
  onAddLevel: (t: "support" | "resistance", price: number) => void;
  onSuggestPoints: () => void;
  onResetSelection: () => void;
  onUndoMarker: () => void;
  onClearMarkers: () => void;
  onClearSelection: () => void;
  onToggleIndicators: () => void;
  onGenerate: () => void;
  onBacktest: () => void;
  onSave: () => void;
  onViewFactor: (f: Factor) => void;
  onDisableFactor: (f: Factor) => void;
}

/**
 * 看盘台：K 线图 + 形态标注工具条 + 实时状态栏。
 * 数据与回调全部由 App.tsx 注入，本组件不持有业务状态。
 */
export function ChartDesk(props: ChartDeskProps) {
  const {
    bars,
    selection,
    entryPoints,
    exitPoints,
    supportLevels,
    resistanceLevels,
    mode,
    regionStats,
    indicators,
    showIndicators,
    modelSignal,
    snapshot,
    busy,
    draft,
    selectedFactor,
    markerHistoryLength,
    levelHistoryLength,
    onMode,
    onSelection,
    onAddPoint,
    onAddLevel,
    onSuggestPoints,
    onResetSelection,
    onUndoMarker,
    onClearMarkers,
    onClearSelection,
    onToggleIndicators,
    onGenerate,
    onBacktest,
    onSave,
    onViewFactor,
    onDisableFactor,
  } = props;

  const hasMarkers =
    entryPoints.length > 0 || exitPoints.length > 0 || supportLevels.length > 0 || resistanceLevels.length > 0;

  return (
    <div className="page">
      <div className="page-head">
        <Activity size={15} /> <strong>看盘台</strong> <span>K 线图 · 形态标注 · 实时状态</span>
      </div>

      <div className="main">
        <section className="chart-zone">
          <div className="action-bar">
            <button className={`btn ${mode === "select" ? "active" : ""}`} onClick={() => onMode("select")}>
              <BoxSelect size={14} /> 框选形态
            </button>
            <button className={`btn ${mode === "entry" ? "active" : ""}`} onClick={() => onMode("entry")}>
              <Sparkles size={14} /> 标记入场点
            </button>
            <button className={`btn ${mode === "exit" ? "active" : ""}`} onClick={() => onMode("exit")}>
              <Ban size={14} /> 标记出场点
            </button>
            <button className={`btn ${mode === "resistance" ? "active" : ""}`} onClick={() => onMode("resistance")}>
              <Ban size={14} /> 标记压力位
            </button>
            <button className={`btn ${mode === "support" ? "active" : ""}`} onClick={() => onMode("support")}>
              <Sparkles size={14} /> 标记支撑位
            </button>
            <button
              className="btn"
              disabled={!selection || supportLevels.length === 0 || resistanceLevels.length === 0}
              onClick={onSuggestPoints}
            >
              <Sparkles size={14} /> 自动建议入场/出场
            </button>
            <button className="btn" onClick={onResetSelection}>
              <RefreshCw size={14} /> 重置框选
            </button>
            <button className="btn" disabled={markerHistoryLength === 0 && levelHistoryLength === 0} onClick={onUndoMarker}>
              <Undo2 size={14} /> 撤销标记
            </button>
            <button className="btn" disabled={!hasMarkers} onClick={onClearMarkers}>
              <X size={14} /> 清除标注
            </button>
            <button className="btn" disabled={!selection} onClick={onClearSelection}>
              <X size={14} /> 取消框选
            </button>
            <span className="btn-sep" />
            <button className="btn primary" disabled={busy} onClick={onGenerate}>
              <Bot size={14} /> 提取并生成 AI 因子
            </button>
            <button className="btn" disabled={busy || !draft} onClick={onBacktest}>
              <TestTube2 size={14} /> 运行历史回测
            </button>
            <button className="btn" disabled={busy || !draft} onClick={onSave}>
              <Save size={14} /> 存入因子特征库
            </button>
            <span className="btn-sep" />
            <button className={`btn ${showIndicators ? "active" : ""}`} onClick={onToggleIndicators}>
              <Activity size={14} /> 指标叠加
            </button>
            <button className="btn" disabled={!selectedFactor} onClick={() => selectedFactor && onViewFactor(selectedFactor)}>
              <Eye size={14} /> 查看因子逻辑
            </button>
            <button className="btn" disabled={!selectedFactor} onClick={() => selectedFactor && onDisableFactor(selectedFactor)}>
              <StopCircle size={14} /> 禁用此因子
            </button>
            <span className="btn-sep" />
            {snapshot && (
              <span className="status-pill">
                最新价 <strong>{snapshot.current_price.toFixed(5)}</strong>
                <span className={snapshot.change_pct_24h >= 0 ? "pnl-pos" : "pnl-neg"}>
                  {snapshot.change_pct_24h >= 0 ? "▲" : "▼"}
                  {Math.abs(snapshot.change_pct_24h).toFixed(3)}%
                </span>
              </span>
            )}
          </div>
          <KLineChart
            bars={bars}
            selection={selection}
            entryPoints={entryPoints}
            exitPoints={exitPoints}
            supportLevels={supportLevels}
            resistanceLevels={resistanceLevels}
            mode={mode}
            regionStats={regionStats}
            indicators={indicators}
            showIndicators={showIndicators}
            onSelection={onSelection}
            onPoint={onAddPoint}
            onLevel={onAddLevel}
            onMode={onMode}
            modelSignal={modelSignal}
          />
        </section>

        <aside className="sidebar">
          <div className="tabs">
            <button className="tab active" style={{ flex: 1 }}>
              <Activity size={14} /> 实时状态
            </button>
          </div>
          <div className="panel-body">
            <MarketStatusPanel />
          </div>
        </aside>
      </div>
    </div>
  );
}
