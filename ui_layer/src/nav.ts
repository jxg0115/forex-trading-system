import type { ReactNode } from "react";

/**
 * 前端整页导航定义。
 *
 * 系统从「单页塞满所有功能」改为「四台 + 独立页」的结构：
 *   - 每个页面用 URL hash 承载（如 #chart），因此刷新能停在原页、浏览器后退能返回。
 *   - 顶栏渲染「主页面」按钮；独立页（K 线形态）在顶栏右侧单独给一个外跳入口。
 */

export type PageId = "chart" | "strategy" | "perf" | "system";

export interface PageDef {
  id: PageId;
  /** hash 值（不带 #） */
  hash: string;
  label: string;
  /** 一句话说明，鼠标悬停提示用 */
  hint: string;
}

/** 主页面（顶栏 4 个按钮） */
export const PAGES: PageDef[] = [
  { id: "chart", hash: "chart", label: "看盘台", hint: "K 线图 + 形态标注 + 实时状态" },
  { id: "strategy", hash: "strategy", label: "策略台", hint: "因子工作台 / 因子挖掘 / 交易中心" },
  { id: "perf", hash: "perf", label: "绩效台", hint: "复盘 / 因子统计 / 订单日志 / 收益日历" },
  { id: "system", hash: "system", label: "系统台", hint: "AI 管理 / 告警 / 系统操作" },
];

/** K 线形态独立页（自成一个整页，不属四台） */
export const VPATTERN_HASH = "vpattern";

/** 主界面（无 hash）：兼容旧链接与首次进入，等同看盘台 */
export const DEFAULT_PAGE: PageId = "chart";

/**
 * 每台页面的默认页内标签。
 * 放在这里而不是组件里，是为了让「切台时落回哪个子页」有唯一事实来源，
 * 避免顶部导航、hash 直达、浏览器后退三条路径行为不一致。
 */
export const DEFAULT_TAB: Record<string, { tab: string; sub: string }> = {
  strategy: { tab: "factors", sub: "mark" },
  perf: { tab: "replay", sub: "replay" },
  system: { tab: "ai", sub: "" },
};

/** 全部已知 hash（含独立页），用于把任意 hash 解析成页面状态 */
const KNOWN: Record<string, string> = {
  chart: "chart",
  strategy: "strategy",
  perf: "perf",
  system: "system",
  [VPATTERN_HASH]: VPATTERN_HASH,
};

/**
 * 把 URL hash 解析成内部页面标识。
 * - 认得的 hash → 原样返回
 * - 空 hash → 默认页（看盘台）
 * - 不认得的 hash → 默认页（避免脏链接白屏）
 */
export function parseHash(raw: string): string {
  const h = raw.replace(/^#/, "").trim();
  if (!h) return DEFAULT_PAGE;
  return KNOWN[h] ?? DEFAULT_PAGE;
}

/** 生成用于跳转的 hash 字符串 */
export function toHash(id: string): string {
  return `#${id}`;
}

export type PageRenderer = (props: { onNotify: (type: "success" | "error", message: string) => void }) => ReactNode;
