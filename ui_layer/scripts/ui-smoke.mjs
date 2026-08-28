import puppeteer from "puppeteer-core";

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const URL = process.env.UI_URL || "http://127.0.0.1:5173";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const waitFor = async (fn, timeout = 30000) => {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await fn()) return true;
    await sleep(500);
  }
  return false;
};

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: "new",
  args: ["--no-sandbox", "--disable-gpu"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1600, height: 1000 });
page.on("dialog", (dialog) => dialog.accept());

const errors = [];
page.on("console", (msg) => {
  if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
});
page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
page.on("response", (res) => {
  if (res.status() >= 400) errors.push(`http ${res.status()} ${res.url()}`);
});

let pass = 0;
const assert = (cond, name) => {
  if (!cond) throw new Error(`FAIL: ${name}`);
  pass += 1;
  console.log(`PASS: ${name}`);
};

await page.goto(URL, { waitUntil: "networkidle2", timeout: 60000 });
await page.waitForSelector(".chart-wrap canvas", { timeout: 60000 });
await sleep(1500);
await page.evaluate(() => fetch("/api/matcher/stop", { method: "POST" }));
await sleep(800);

const text = () => page.evaluate(() => document.body.innerText);

for (const label of [
  "外汇 AI 量化交易系统",
  "框选形态",
  "标记入场点",
  "标记出场点",
  "标记压力位",
  "标记支撑位",
  "提取并生成 AI 因子",
  "运行历史回测",
  "存入因子特征库",
  "查看因子逻辑",
  "禁用此因子",
  "指标叠加",
  "一键平仓",
]) {
  assert((await text()).includes(label), `顶部/工具栏包含：${label}`);
}
assert(
  (await text()).includes("启动实盘匹配") || (await text()).includes("暂停系统交易"),
  "顶部匹配开关存在"
);
assert(
  (await text()).includes("AI 未配置") || (await text()).includes("AI 已配置") || (await text()).includes("AI 未启用"),
  "顶部 AI 状态显示"
);

for (const tab of ["因子工作台", "交易中心", "复盘与日志", "AI 管理"]) {
  const exists = await page.evaluate((s) => [...document.querySelectorAll(".tab")].some((el) => el.textContent.includes(s)), tab);
  assert(exists, `主工作台存在：${tab}`);
}

const realClick = async (selector, text) => {
  await page.evaluate((sel, txt) => {
    const el = [...document.querySelectorAll(sel)].find((e) => e.textContent.includes(txt));
    el?.scrollIntoView({ block: "center" });
  }, selector, text);
  await sleep(100);
  const pos = await page.evaluate((sel, txt) => {
    const el = [...document.querySelectorAll(sel)].find((e) => e.textContent.includes(txt));
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  }, selector, text);
  if (pos) await page.mouse.click(pos.x, pos.y);
  await sleep(300);
};

const clickButton = (label) => realClick(".btn", label);

const clickText = (selector, text) => realClick(selector, text);

const openTab = (main, sub) =>
  (async () => {
    await realClick(".tab", main);
    await sleep(400);
    await realClick(".sub-tab", sub);
  })();

for (const [main, sub] of [
  ["因子工作台", "形态标注"],
  ["因子工作台", "回测与优化"],
  ["因子工作台", "因子管理"],
  ["交易中心", "信号与执行"],
  ["交易中心", "实盘下单"],
  ["复盘与日志", "复盘报告"],
  ["复盘与日志", "订单日志"],
]) {
  await openTab(main, sub);
  await sleep(600);
  assert(await page.$(".panel-body"), `切换子标签：${main} / ${sub}`);
}

await clickButton("框选形态");
await sleep(300);
const wrap = await page.$(".chart-wrap");
const box = await wrap.boundingBox();
assert(box && box.width > 200, "图表区域可定位");

await page.mouse.move(box.x + 150, box.y + 120);
await page.mouse.down();
await page.mouse.move(box.x + 480, box.y + 280, { steps: 15 });
await page.mouse.up();
await sleep(500);
const hasSelection = await page.evaluate(() => {
  const el = document.getElementById("selection-box");
  return !!el && el.style.display !== "none";
});
assert(hasSelection, "框选产生选择框且图表未平移");

await clickButton("取消框选");
await sleep(300);
assert(!(await page.$("#selection-box")), "取消框选");

await clickButton("标记入场点");
await sleep(200);
await page.mouse.click(box.x + 220, box.y + 160);
await clickButton("标记出场点");
await sleep(200);
await page.mouse.click(box.x + 380, box.y + 200);
await clickText(".mini-btn", "到压力位");
await sleep(400);
let markers = await page.$$(".chart-point-marker");
assert(markers.length === 2, "入场/出场精确点位已标记");

await clickButton("撤销标记");
await sleep(300);
markers = await page.$$(".chart-point-marker");
assert(markers.length === 1, "撤销标记");

await clickButton("清除标注");
await sleep(300);
markers = await page.$$(".chart-point-marker");
assert(markers.length === 0, "清除标注");

await clickButton("标记压力位");
await page.mouse.click(box.x + 300, box.y + 150);
await clickButton("标记支撑位");
await page.mouse.click(box.x + 300, box.y + 240);
await sleep(400);
let levels = await page.$$(".chart-level-line");
assert(levels.length >= 2, "压力/支撑位标记");
await clickButton("撤销标记");
await sleep(300);
levels = await page.$$(".chart-level-line");
assert(levels.length === 1, "撤销压力/支撑位");
await clickButton("清除标注");
await sleep(300);
levels = await page.$$(".chart-level-line");
assert(levels.length === 0, "清除压力/支撑位");

// 自动建议入场/出场（基于支撑/压力）
await clickButton("框选形态");
await sleep(300);
await page.mouse.move(box.x + 150, box.y + 120);
await page.mouse.down();
await page.mouse.move(box.x + 480, box.y + 280, { steps: 15 });
await page.mouse.up();
await sleep(300);
await clickButton("标记压力位");
await page.mouse.click(box.x + 300, box.y + 150);
await clickButton("标记支撑位");
await page.mouse.click(box.x + 300, box.y + 240);
await sleep(300);
await clickButton("自动建议入场/出场");
await sleep(400);
markers = await page.$$(".chart-point-marker");
assert(markers.length >= 2, "自动建议入场/出场");
await clickButton("清除标注");
await sleep(300);
markers = await page.$$(".chart-point-marker");
assert(markers.length === 0, "清除自动建议标注");

assert(!!(await page.$(".indicator-legend")), "指标图例默认显示");
await clickButton("指标叠加");
await sleep(300);
assert(!(await page.$(".indicator-legend")), "关闭指标叠加");
await clickButton("指标叠加");
await sleep(300);
assert(!!(await page.$(".indicator-legend")), "重新开启指标叠加");

const searchInput = await page.$(".symbol-search input");
await searchInput.click();
await searchInput.type("BTC");
await sleep(400);
const options = await page.$$eval(".symbol-option", (els) => els.map((e) => e.textContent));
assert(options.some((o) => o.includes("BTC")), "品种搜索返回 BTC 结果");

await clickText(".mini-btn", "平移");
const modeLabel = await page.evaluate(() => document.querySelector(".chart-legend")?.textContent || "");
assert(modeLabel.includes("平移"), "切回平移模式");

// 因子卡片沙盒测试
await openTab("因子工作台", "因子管理");
const cardReady = await waitFor(() => page.$(".factor-card"), 10000);
if (cardReady) {
  await clickText(".factor-card .mini-btn", "沙盒测试");
  assert(
    await waitFor(() => page.evaluate(() => [...document.querySelectorAll(".factor-card .factor-desc")].some((p) => p.textContent.includes("沙盒测试") && (p.textContent.includes("通过") || p.textContent.includes("未通过")))), 60000),
    "因子卡片沙盒测试"
  );
  await clickText(".factor-card .mini-btn", "查看因子逻辑");
  assert(
    await waitFor(() => page.evaluate(() => [...document.querySelectorAll(".metric")].some((m) => m.textContent.includes("沙盒检查") && (m.textContent.includes("通过") || m.textContent.includes("未通过")))), 60000),
    "查看因子逻辑沙盒状态一致"
  );
} else {
  console.log("SKIP: 因子库为空，跳过因子卡片沙盒测试");
}

// 启动实盘匹配弹窗：止损方式支持“支撑/压力位”
await page.evaluate(() => fetch("/api/matcher/stop", { method: "POST" }));
await sleep(1200);
const matcherStartVisible = await page.evaluate(() =>
  [...document.querySelectorAll(".btn")].some((b) => b.textContent.includes("启动实盘匹配"))
);
if (!matcherStartVisible) {
  await clickText(".btn", "暂停系统交易");
  await sleep(1500);
}
await clickText(".btn", "启动实盘匹配");
await sleep(400);
assert(
  await page.evaluate(() =>
    [...document.querySelectorAll("select.form-input option")].some((o) => o.textContent.includes("支撑/压力位"))
  ),
  "止损/止盈方式包含支撑/压力位"
);
await page.evaluate(() => {
  const label = [...document.querySelectorAll(".field-label")].find((l) => l.textContent.trim() === "止盈方式");
  const sel = label?.nextElementSibling;
  if (sel && sel.tagName === "SELECT") {
    sel.value = "levels";
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  }
});
await sleep(300);
assert(
  await page.evaluate(() =>
    [...document.querySelectorAll(".field-label")].some((l) => l.textContent.includes("止盈关键位缓冲"))
  ),
  "止盈关键位缓冲参数显示"
);
const trailingBox = await page.evaluate(() => {
  const label = [...document.querySelectorAll(".field-label")].find((l) => l.textContent.trim() === "移动止损");
  const input = label?.nextElementSibling;
  if (!input) return null;
  input.scrollIntoView({ block: "center" });
  const r = input.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
});
if (trailingBox) await page.mouse.click(trailingBox.x, trailingBox.y);
await sleep(300);
assert(
  await page.evaluate(() =>
    [...document.querySelectorAll(".field-label")].some((l) => l.textContent.includes("移动止损激活盈利"))
  ),
  "移动止损激活参数显示"
);
assert(
  await page.evaluate(() =>
    [...document.querySelectorAll(".field-label")].some((l) => l.textContent.includes("移动止损追踪 ATR"))
  ),
  "移动止损 ATR 参数显示"
);
assert(
  await page.evaluate(() =>
    [...document.querySelectorAll(".field-label")].some((l) => l.textContent.includes("接近止盈触发距离"))
  ),
  "接近止盈触发距离参数显示"
);
assert(
  await page.evaluate(() =>
    [...document.querySelectorAll(".field-label")].some((l) => l.textContent.includes("移动止盈回撤"))
  ),
  "移动止盈回撤参数显示"
);
assert(
  await page.evaluate(() =>
    [...document.querySelectorAll(".field-label")].some((l) => l.textContent.includes("止盈锁定缓冲"))
  ),
  "止盈锁定缓冲参数显示"
);
assert(
  await page.evaluate(() => document.body.innerText.includes("三阶段保护")),
  "移动止损备注说明显示"
);
await clickText(".mini-btn", "取消");
await sleep(300);

// AI 管理模块
await clickText(".tab", "AI 管理");
await sleep(800);
assert(
  await page.evaluate(() => document.body.innerText.includes("AI 连接状态")),
  "AI 管理页面显示"
);
await clickText(".mini-btn", "新增 AI 配置");
await sleep(400);
await page.evaluate(() => {
  const input = [...document.querySelectorAll(".modal input")].find((i) => i.type === "text");
  if (input) {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(input, "UI测试AI");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
});
await page.evaluate(() => {
  const pw = document.querySelector(".modal input[type=password]");
  if (pw) {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(pw, "sk-ui-test");
    pw.dispatchEvent(new Event("input", { bubbles: true }));
  }
});
const roleBox = await page.evaluate(() => {
  const target = [...document.querySelectorAll(".modal label")].find((l) => l.textContent.includes("K线框选学习"));
  const cb = target?.querySelector("input[type=checkbox]");
  if (!cb) return null;
  cb.scrollIntoView({ block: "center" });
  const r = cb.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
});
if (roleBox) await page.mouse.click(roleBox.x, roleBox.y);
await sleep(200);
await clickText(".mini-btn", "保存");
assert(
  await waitFor(() => page.evaluate(() => document.body.innerText.includes("UI测试AI")), 30000),
  "新增 AI 配置"
);
const delBox = await page.evaluate(() => {
  const row = [...document.querySelectorAll(".trade-row")].find((r) => r.textContent.includes("UI测试AI"));
  const btn = row && [...row.querySelectorAll(".mini-btn")].find((b) => b.textContent.includes("删除"));
  if (!btn) return null;
  btn.scrollIntoView({ block: "center" });
  const r = btn.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
});
if (delBox) await page.mouse.click(delBox.x, delBox.y);
assert(
  await waitFor(() => page.evaluate(() => ![...document.querySelectorAll(".trade-row")].some((r) => r.textContent.includes("UI测试AI"))), 30000),
  "删除 AI 配置"
);

console.log(`\n全部通过 ${pass} 项`);
if (errors.length) {
  console.log("浏览器错误：");
  for (const e of errors.slice(0, 10)) console.log(" -", e);
  process.exitCode = 1;
} else {
  console.log("浏览器控制台无错误");
}
await browser.close();
