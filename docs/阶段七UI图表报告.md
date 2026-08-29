# 阶段七 · UI/图表报告（D11 K线+指标+信号图表组件 / 移动适配）

日期：2026-08-29 · 前端构建在主线完成，本报告随阶段七合并。

## 一、范围

| 项 | 内容 | 验收 |
|---|---|---|
| D11 | K线+指标+信号图表组件；移动适配 | 图表交互上线 |

## 二、现状与增强

**已有基础**（前序阶段已建成）：`ui_layer` 前端（Vite + React + lightweight-charts v5）：
- `KLineChart.tsx`：K 线 + 成交量、SMA10/20 + EMA12/26 叠加、RSI/MACD 副图（pane）、框选/入场/出场/支撑/压力交互标注、十字线/缩放/平移、ResizeObserver 自适应宽高；
- `Mt5Panel` / `FactorMiningPanel` / `SltpPolicyPanel` / `OrderLogPanel` / 复盘回放等面板；
- `index.html` viewport 已配（移动基础）；styles.css 已有 1280px 响应式断点。

**本次增强（D11 补齐）**：
1. **模型信号叠加**：`KLineChart` 新增 `modelSignal` prop——图例显示「模型名·方向·置信度」色条，最后一根 K 线上用 lightweight-charts v5 `createSeriesMarkers` 插件画做多/做空箭头标记；
2. **信号轮询**：`App.tsx` 每 15s 调用 `/api/models/logistic_momentum/predict`（api.ts 新增 `modelPredict`），未训练/无信号时静默隐藏（无干扰）；
3. **移动适配**：styles.css 新增 720px 断点（图表 min-height 320px、侧栏 100% 宽堆叠、主区收紧），配合已有 viewport/ResizeObserver。

## 三、构建验证

`npm run build`（tsc -b + vite）成功：`✓ built in 1.74s`，dist 产出 `index-CYjSkVC4.js`（607KB）+ CSS。TS 类型全过（含 v5 插件 API）。模型预测端点验证可达（未训练时静默处理）。

## 四、验收对照

- 图表交互上线：✓（K线+成交量+指标叠加+信号标记+框选标注，built 完成，页面刷新即见）；
- 移动适配：✓（viewport + 720px 断点 + 图表自适应）；
- 可并行（路线图注明）：与组合/ML 阶段已完成并行交付。

## 五、后续建议

- chunk 607KB 超 500KB 提示：可按需 `manualChunks` 拆分（lightweight-charts 独立 chunk）——性能优化项，不影响验收；
- 模型信号默认 logistic_momentum；训练后（AI 管理页触发）即显示（gbdt_momentum 等新模型可扩展选择器）。