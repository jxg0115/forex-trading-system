import { useCallback, useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineStyle,
  LineSeries,
  createChart,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Bar, IndicatorData, MarkerPoint, Selection } from "../api";

export type ChartMode = "pan" | "select" | "entry" | "exit" | "support" | "resistance";

interface RegionStats {
  bar_count: number;
  return_pct: number;
  trend: string;
  volatility: string;
}

interface Props {
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
  onSelection: (selection: Selection | null) => void;
  onPoint: (point: MarkerPoint) => void;
  onLevel: (type: "support" | "resistance", price: number) => void;
  onMode: (mode: ChartMode) => void;
}

function toChartTime(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

export function KLineChart({
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
  onSelection,
  onPoint,
  onLevel,
  onMode,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const previewRef = useRef<{ x1: number; y1: number; x2: number; y2: number } | null>(null);
  const selectionRef = useRef(selection);
  const entryRef = useRef(entryPoints);
  const exitRef = useRef(exitPoints);
  const supportRef = useRef(supportLevels);
  const resistanceRef = useRef(resistanceLevels);
  const [preview, setPreview] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);
  const lastBarCount = useRef(0);
  const overlayRef = useRef<Array<{ series: ISeriesApi<"Line"> | ISeriesApi<"Histogram">; pane: number }>>([]);

  selectionRef.current = selection;
  entryRef.current = entryPoints;
  exitRef.current = exitPoints;
  supportRef.current = supportLevels;
  resistanceRef.current = resistanceLevels;

  const drawSelection = useCallback(() => {
    const chart = chartRef.current;
    const candle = candleRef.current;
    const container = containerRef.current;
    const sel = selectionRef.current;
    if (!chart || !candle || !container || !sel) return;
    const x1 = chart.timeScale().timeToCoordinate(toChartTime(sel.timeStart));
    const x2 = chart.timeScale().timeToCoordinate(toChartTime(sel.timeEnd));
    const y1 = candle.priceToCoordinate(sel.priceTop);
    const y2 = candle.priceToCoordinate(sel.priceBottom);
    if (x1 == null || x2 == null || y1 == null || y2 == null) return;
    const box = document.getElementById("selection-box");
    if (box) {
      box.style.display = "block";
      box.style.left = `${Math.min(x1, x2)}px`;
      box.style.top = `${Math.min(y1, y2)}px`;
      box.style.width = `${Math.abs(x2 - x1)}px`;
      box.style.height = `${Math.abs(y2 - y1)}px`;
    }
  }, []);

  const drawMarkers = useCallback(() => {
    const chart = chartRef.current;
    const candle = candleRef.current;
    const host = document.getElementById("chart-markers");
    if (!chart || !candle || !host) return;
    host.innerHTML = "";
    const items = [
      ...entryRef.current.map((p) => ({ type: "entry", time: p.time, price: p.price, label: "入场" })),
      ...exitRef.current.map((p) => ({ type: "exit", time: p.time, price: p.price, label: `出场${p.reason ? `·${p.reason}` : ""}` })),
    ];
    for (const item of items) {
      const x = chart.timeScale().timeToCoordinate(toChartTime(item.time));
      const y = candle.priceToCoordinate(item.price);
      if (x == null || y == null) continue;
      const el = document.createElement("div");
      el.className = `chart-point-marker ${item.type}`;
      el.textContent = `${item.label} ${item.price.toFixed(5)}`;
      el.style.left = `${x}px`;
      el.style.top = `${y}px`;
      host.appendChild(el);
    }

    const drawLevelLine = (price: number, type: "support" | "resistance") => {
      const y = candle.priceToCoordinate(price);
      if (y == null) return;
      const el = document.createElement("div");
      el.className = `chart-level-line ${type}`;
      el.style.top = `${y}px`;
      const label = document.createElement("span");
      label.textContent = `${type === "support" ? "支撑" : "压力"} ${price.toFixed(5)}`;
      el.appendChild(label);
      host.appendChild(el);
    };
    for (const level of supportRef.current) drawLevelLine(level.price, "support");
    for (const level of resistanceRef.current) drawLevelLine(level.price, "resistance");
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: "#0b0f14" },
        textColor: "#8b98a8",
        fontFamily: "Microsoft YaHei, PingFang SC, sans-serif",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#131a22" },
        horzLines: { color: "#131a22" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "#4aa3df", width: 1, style: LineStyle.Dashed, labelBackgroundColor: "#25303c" },
        horzLine: { color: "#4aa3df", width: 1, style: LineStyle.Dashed, labelBackgroundColor: "#25303c" },
      },
      rightPriceScale: { borderColor: "#25303c" },
      timeScale: { borderColor: "#25303c", timeVisible: true, secondsVisible: false, rightOffset: 4 },
    });

    const candle = chart.addSeries(CandlestickSeries, {
      upColor: "#2ecc9a",
      downColor: "#ef6262",
      borderUpColor: "#2ecc9a",
      borderDownColor: "#ef6262",
      wickUpColor: "#2ecc9a",
      wickDownColor: "#ef6262",
    });
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

    chartRef.current = chart;
    candleRef.current = candle;
    volumeRef.current = volume;

    chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
      drawSelection();
      drawMarkers();
    });

    const resizeObserver = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
      drawSelection();
      drawMarkers();
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
    };
  }, [drawSelection, drawMarkers]);

  useEffect(() => {
    chartRef.current?.applyOptions({ handleScroll: mode === "pan" });
  }, [mode]);

  useEffect(() => {
    if (!candleRef.current || !volumeRef.current) return;
    candleRef.current.setData(
      bars.map((b) => ({
        time: toChartTime(b.time),
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      }))
    );
    const volumeData: HistogramData<Time>[] = bars.map((b) => ({
      time: toChartTime(b.time),
      value: b.volume,
      color: b.close >= b.open ? "rgba(46,204,154,0.35)" : "rgba(239,98,98,0.35)",
    }));
    volumeRef.current.setData(volumeData);
    if (bars.length !== lastBarCount.current) {
      lastBarCount.current = bars.length;
      chartRef.current?.timeScale().fitContent();
    }
    drawSelection();
    drawMarkers();
  }, [bars, drawSelection, drawMarkers]);

  useEffect(() => {
    drawMarkers();
  }, [entryPoints, exitPoints, supportLevels, resistanceLevels]);

  useEffect(() => {
    const chart = chartRef.current;
    overlayRef.current.forEach(({ series }) => chart?.removeSeries(series));
    overlayRef.current = [];
    if (!chart || !indicators || !showIndicators) return;

    const lineColors: Record<string, string> = {
      sma_10: "#4aa3df",
      sma_20: "#f0a63a",
      ema_12: "#8b7cf6",
      ema_26: "#2ecc9a",
    };
    for (const key of ["sma_10", "sma_20", "ema_12", "ema_26"]) {
      const points = (indicators.series[key] ?? [])
        .filter((p) => p.value != null)
        .map((p) => ({ time: toChartTime(p.time), value: p.value as number }));
      if (!points.length) continue;
      const series = chart.addSeries(
        LineSeries,
        {
          color: lineColors[key] ?? "#4aa3df",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: true,
          crosshairMarkerVisible: false,
        },
        0
      );
      series.setData(points);
      overlayRef.current.push({ series, pane: 0 });
    }

    const rsiPoints = (indicators.series.rsi_14 ?? [])
      .filter((p) => p.value != null)
      .map((p) => ({ time: toChartTime(p.time), value: p.value as number }));
    if (rsiPoints.length) {
      const rsiSeries = chart.addSeries(
        LineSeries,
        {
          color: "#8b7cf6",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: true,
          crosshairMarkerVisible: false,
        },
        1
      );
      rsiSeries.setData(rsiPoints);
      overlayRef.current.push({ series: rsiSeries, pane: 1 });
    }

    const histPoints = (indicators.series.macd_hist ?? [])
      .filter((p) => p.value != null)
      .map((p) => ({
        time: toChartTime(p.time),
        value: p.value as number,
        color: (p.value ?? 0) >= 0 ? "rgba(46,204,154,0.45)" : "rgba(239,98,98,0.45)",
      }));
    const macdPoints = (indicators.series.macd_line ?? [])
      .filter((p) => p.value != null)
      .map((p) => ({ time: toChartTime(p.time), value: p.value as number }));
    const signalPoints = (indicators.series.macd_signal ?? [])
      .filter((p) => p.value != null)
      .map((p) => ({ time: toChartTime(p.time), value: p.value as number }));
    if (histPoints.length || macdPoints.length) {
      const pane = chart.addSeries(
        HistogramSeries,
        { priceFormat: { type: "price" }, priceLineVisible: false, lastValueVisible: true },
        2
      );
      pane.setData(histPoints);
      overlayRef.current.push({ series: pane, pane: 2 });
      if (macdPoints.length) {
        const macdSeries = chart.addSeries(
          LineSeries,
          { color: "#4aa3df", lineWidth: 1, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false },
          2
        );
        macdSeries.setData(macdPoints);
        overlayRef.current.push({ series: macdSeries, pane: 2 });
      }
      if (signalPoints.length) {
        const signalSeries = chart.addSeries(
          LineSeries,
          { color: "#f0a63a", lineWidth: 1, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false },
          2
        );
        signalSeries.setData(signalPoints);
        overlayRef.current.push({ series: signalSeries, pane: 2 });
      }
    }
  }, [indicators, showIndicators]);

  useEffect(() => {
    if (!selection) {
      const box = document.getElementById("selection-box");
      if (box) box.style.display = "none";
    }
    drawSelection();
  }, [selection, drawSelection]);

  const pointAt = useCallback((x: number, y: number) => {
    const chart = chartRef.current;
    const candle = candleRef.current;
    if (!chart || !candle) return null;
    const time = chart.timeScale().coordinateToTime(x);
    const price = candle.coordinateToPrice(y);
    if (time == null || price == null) return null;
    const seconds = typeof time === "number" ? time : new Date(String(time)).getTime() / 1000;
    return { time: new Date(seconds * 1000).toISOString(), price: Number(price.toFixed(5)) };
  }, []);

  const nearestSelectionForPoint = useCallback(
    (point: { time: string; price: number }) => {
      if (!bars.length) return null;
      const target = new Date(point.time).getTime();
      let idx = 0;
      let best = Number.POSITIVE_INFINITY;
      bars.forEach((b, i) => {
        const d = Math.abs(new Date(b.time).getTime() - target);
        if (d < best) {
          best = d;
          idx = i;
        }
      });
      const start = Math.max(0, idx - 10);
      const end = Math.min(bars.length - 1, idx + 10);
      if (start === end) return null;
      const slice = bars.slice(start, end + 1);
      return {
        timeStart: slice[0].time,
        timeEnd: slice[slice.length - 1].time,
        priceTop: Math.max(...slice.map((b) => b.high)),
        priceBottom: Math.min(...slice.map((b) => b.low)),
      };
    },
    [bars]
  );

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (mode === "pan") return;
    const rect = containerRef.current!.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // 忽略指针已失效的异常，拖动仍可正常结束
    }
    if (mode === "entry" || mode === "exit") {
      const point = pointAt(x, y);
      if (point) onPoint({ ...point, label: mode === "entry" ? "入场点" : "出场点" });
      return;
    }
    if (mode === "support" || mode === "resistance") {
      const point = pointAt(x, y);
      if (point) onLevel(mode, point.price);
      return;
    }
    dragStart.current = { x, y };
    previewRef.current = { x1: x, y1: y, x2: x, y2: y };
    setPreview(previewRef.current);
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragStart.current) return;
    const rect = containerRef.current!.getBoundingClientRect();
    previewRef.current = {
      x1: dragStart.current.x,
      y1: dragStart.current.y,
      x2: e.clientX - rect.left,
      y2: e.clientY - rect.top,
    };
    setPreview(previewRef.current);
  };

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragStart.current || !previewRef.current) return;
    const startPoint = dragStart.current;
    const rect = containerRef.current!.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const start = pointAt(startPoint.x, startPoint.y);
    const end = pointAt(x, y);
    const distX = Math.abs(x - startPoint.x);
    const distY = Math.abs(y - startPoint.y);
    dragStart.current = null;
    previewRef.current = null;
    setPreview(null);
    if (distX < 4 && distY < 4) {
      if (start) {
        const region = nearestSelectionForPoint(start);
        if (region) onSelection(region);
      }
      return;
    }
    if (!start || !end) return;
    const timeStart = start.time <= end.time ? start.time : end.time;
    const timeEnd = start.time <= end.time ? end.time : start.time;
    const priceTop = Math.max(start.price, end.price);
    const priceBottom = Math.min(start.price, end.price);
    if (timeStart === timeEnd || priceTop === priceBottom) return;
    onSelection({ timeStart, timeEnd, priceTop, priceBottom });
  };

  const onPointerCancel = () => {
    dragStart.current = null;
    previewRef.current = null;
    setPreview(null);
  };

  const modeLabel =
    mode === "pan" ? "平移"
    : mode === "select" ? "框选"
    : mode === "entry" ? "标记入场点"
    : mode === "exit" ? "标记出场点"
    : mode === "support" ? "标记支撑位"
    : "标记压力位";
  const fmtValue = (value: number | null | undefined, digits = 5) => value == null ? "--" : value.toFixed(digits);

  return (
    <div
      ref={containerRef}
      className="chart-wrap"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
    >
      <div className="chart-legend">
        <span className="legend-item">
          当前模式：<strong>{modeLabel}</strong>
        </span>
        {entryPoints.length > 0 && <span className="legend-item">入场点 <strong>{entryPoints.length}</strong></span>}
        {exitPoints.length > 0 && <span className="legend-item">出场点 <strong>{exitPoints.length}</strong></span>}
        {supportLevels.length > 0 && <span className="legend-item">支撑位 <strong>{supportLevels.length}</strong></span>}
        {resistanceLevels.length > 0 && <span className="legend-item">压力位 <strong>{resistanceLevels.length}</strong></span>}
      </div>
      {showIndicators && indicators && (
        <div className="indicator-legend">
          <span className="legend-item" title="SMA10：10 周期简单移动平均" style={{ borderLeft: "3px solid #4aa3df" }}><i className="legend-dot" style={{ background: "#4aa3df" }} />SMA10 {fmtValue(indicators.latest.sma_10)}</span>
          <span className="legend-item" title="SMA20：20 周期简单移动平均" style={{ borderLeft: "3px solid #f0a63a" }}><i className="legend-dot" style={{ background: "#f0a63a" }} />SMA20 {fmtValue(indicators.latest.sma_20)}</span>
          <span className="legend-item" title="EMA12：12 周期指数移动平均" style={{ borderLeft: "3px solid #8b7cf6" }}><i className="legend-dot" style={{ background: "#8b7cf6" }} />EMA12 {fmtValue(indicators.latest.ema_12)}</span>
          <span className="legend-item" title="EMA26：26 周期指数移动平均" style={{ borderLeft: "3px solid #2ecc9a" }}><i className="legend-dot" style={{ background: "#2ecc9a" }} />EMA26 {fmtValue(indicators.latest.ema_26)}</span>
          <span className="legend-item" title="RSI14：相对强弱指标，>70 超买，<30 超卖">RSI14 {fmtValue(indicators.latest.rsi_14, 1)}</span>
          <span className="legend-item" title="MACD(12,26,9)：快线-慢线、信号线、柱状值">MACD {fmtValue(indicators.latest.macd_line)} / {fmtValue(indicators.latest.macd_signal)} / {fmtValue(indicators.latest.macd_hist)}</span>
        </div>
      )}
      {mode === "select" && (
        <div className="chart-select-hint">框选模式：在K线图上按住并拖动</div>
      )}
      <div id="chart-markers" className="chart-markers" />
      {selection && regionStats && (
        <div className="region-stats">
          <h4>框选区域特征</h4>
          <div className="region-grid">
            <div>K 线数量 <strong>{regionStats.bar_count}</strong></div>
            <div>区间涨跌 <strong>{regionStats.return_pct.toFixed(2)}%</strong></div>
            <div>趋势 <strong>{regionStats.trend}</strong></div>
            <div>波动 <strong>{regionStats.volatility}</strong></div>
          </div>
        </div>
      )}
      {preview && (
        <div
          className="selection-box"
          style={{
            left: Math.min(preview.x1, preview.x2),
            top: Math.min(preview.y1, preview.y2),
            width: Math.abs(preview.x2 - preview.x1),
            height: Math.abs(preview.y2 - preview.y1),
          }}
        />
      )}
      {selection && (
        <div
          id="selection-box"
          className="selection-box"
          style={{ display: "none" }}
        />
      )}
      <div className="mode-switch" style={{ position: "absolute", right: 14, bottom: 12, zIndex: 6, display: "flex", gap: 6 }}>
        {(["pan", "select", "entry", "exit", "support", "resistance"] as ChartMode[]).map((m) => (
          <button key={m} className={`mini-btn ${mode === m ? "primary" : ""}`} onClick={() => onMode(m)}>
            {m === "pan" ? "平移" : m === "select" ? "框选形态" : m === "entry" ? "标记入场点" : m === "exit" ? "标记出场点" : m === "support" ? "标记支撑位" : "标记压力位"}
          </button>
        ))}
      </div>
    </div>
  );
}
