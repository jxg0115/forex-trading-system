import puppeteer from "puppeteer-core";

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const URL = process.env.UI_URL || "http://127.0.0.1:5173";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
  if (res.status() >= 500) errors.push(`http ${res.status()} ${res.url()}`);
});

let pass = 0;
const assert = (cond, name) => {
  if (!cond) throw new Error(`FAIL: ${name}`);
  pass += 1;
  console.log(`PASS: ${name}`);
};

async function waitFor(fn, timeout = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await fn()) return true;
    await sleep(500);
  }
  return false;
}

async function setInput(selector, value) {
  await page.evaluate((sel, val) => {
    const el = document.querySelector(sel);
    if (!el) return;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, val);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }, selector, value);
}

async function setByLabel(label, value) {
  await page.evaluate((lbl, val) => {
    const labelEl = [...document.querySelectorAll(".field-label")].find((l) => l.textContent.includes(lbl));
    const input = labelEl?.nextElementSibling;
    if (!input) return;
    const proto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(input, val);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }, label, value);
}

async function clickText(selector, text) {
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
  if (pos) {
    await page.mouse.click(pos.x, pos.y);
  }
  await sleep(300);
}

async function clickTextLegacy(selector, text) {
  await page.evaluate((sel, txt) => {
    const el = [...document.querySelectorAll(sel)].find((e) => e.textContent.includes(txt));
    el?.click();
  }, selector, text);
  await sleep(300);
}

async function pickSymbol(containerSel, text) {
  const input = await page.$(containerSel + " input");
  await input.click();
  await input.type(text);
  await sleep(400);
  await page.evaluate((sel, txt) => {
    const opt = [...document.querySelectorAll(sel + " .symbol-option")].find((o) => o.textContent.includes(txt));
    opt?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  }, containerSel, text);
  await sleep(400);
}

async function openTab(main, sub) {
  await clickText(".tab", main);
  await sleep(400);
  if (sub) await clickText(".sub-tab", sub);
}

async function apiGet(path) {
  return page.evaluate(async (p) => {
    const res = await fetch(p);
    return res.json();
  }, path);
}

await page.goto(URL, { waitUntil: "networkidle2", timeout: 60000 });
await page.waitForSelector(".chart-wrap canvas", { timeout: 60000 });
await sleep(1500);

// 关闭自动执行，避免后台扫描误下单；下单将只通过 UI 手动操作
await page.evaluate(() =>
  fetch("/api/executor/toggle", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: false }),
  })
);
await page.evaluate(() => fetch("/api/matcher/stop", { method: "POST" }));
await sleep(500);

// 顶部：实盘匹配开关
await clickText(".btn", "启动实盘匹配");
await sleep(300);
await page.evaluate(() => {
  const label = [...document.querySelectorAll(".field-label")].find((l) => l.textContent.trim() === "止盈方式");
  const sel = label?.nextElementSibling;
  if (sel && sel.tagName === "SELECT") {
    sel.value = "levels";
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  }
});
await sleep(200);
await setByLabel("止盈关键位缓冲 %", "0.1");
await setByLabel("ATR 止损倍数", "2.0");
const trailingBox = await page.evaluate(() => {
  const label = [...document.querySelectorAll(".field-label")].find((l) => l.textContent.trim() === "移动止损");
  const input = label?.nextElementSibling;
  if (!input) return null;
  input.scrollIntoView({ block: "center" });
  const r = input.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
});
if (trailingBox) await page.mouse.click(trailingBox.x, trailingBox.y);
await sleep(200);
await setByLabel("移动止损激活盈利 %", "0.5");
await setByLabel("移动止损追踪 ATR 倍数", "2.5");
await setByLabel("接近止盈触发距离（ATR）", "1.0");
await setByLabel("移动止盈回撤（ATR）", "1.0");
await setByLabel("止盈锁定缓冲 %", "0.2");
await clickText(".mini-btn", "确认启动");
assert(await waitFor(() => page.evaluate(() => document.body.innerText.includes("实盘匹配 运行中"))), "启动实盘匹配");
await clickText(".btn", "暂停系统交易");
assert(await waitFor(() => page.evaluate(() => document.body.innerText.includes("实盘匹配 已暂停"))), "暂停系统交易");

// 品种搜索切换到 BTCUSD
await pickSymbol(".topbar .symbol-search", "BTCUSD");

// MT5 实盘：UI 真实下单
await openTab("交易中心", "实盘下单");
await sleep(600);
await setByLabel("手数", "0.01");
await clickText(".mini-btn", "发送订单");
assert(
  await waitFor(() => page.evaluate(() => document.body.innerText.includes("订单已发送")), 30000),
  "UI 发送 MT5 订单（BTCUSD 0.01）"
);
const positions = await apiGet("/api/mt5/positions");
assert(positions.positions.length >= 1, "下单后存在持仓");

await clickText(".mini-btn", "一键平仓");
assert(
  await waitFor(async () => (await apiGet("/api/mt5/positions")).positions.length === 0, 30000),
  "UI 一键平仓"
);

// 平仓后等待自动复盘，再导出报告
await sleep(6000);
await openTab("复盘与日志", "复盘报告");
await clickText(".mini-btn", "导出中文复盘报告");
assert(await waitFor(() => page.$(".replay-doc"), 30000), "导出中文复盘报告");

// 因子生成 + 回测 + 入库
await openTab("因子工作台", "形态标注");
await clickText(".btn", "框选形态");
await sleep(300);
const wrap = await page.$(".chart-wrap");
const box = await wrap.boundingBox();
await page.mouse.move(box.x + 150, box.y + 120);
await page.mouse.down();
await page.mouse.move(box.x + 480, box.y + 280, { steps: 15 });
await page.mouse.up();
await sleep(300);
await clickText(".btn", "标记入场点");
await page.mouse.click(box.x + 230, box.y + 160);
await clickText(".btn", "标记出场点");
await page.mouse.click(box.x + 390, box.y + 210);
await clickText(".mini-btn", "到压力位");
await clickText(".btn", "提取并生成 AI 因子");
assert(await waitFor(() => page.$(".code-block"), 60000), "提取并生成 AI 因子");

await clickText(".btn", "运行历史回测");
assert(await waitFor(() => page.evaluate(() => document.body.innerText.includes("回测绩效")), 60000), "运行历史回测");

await clickText(".btn", "存入因子特征库");
await openTab("因子工作台", "因子管理");
assert(await waitFor(() => page.$(".factor-card"), 30000), "因子已入库");

// 模拟盘启停
await clickText(".mini-btn", "模拟盘");
assert(
  await waitFor(
    () =>
      page.evaluate(() => {
        const btn = [...document.querySelectorAll(".mini-btn")].find((b) => b.textContent.includes("启动模拟盘"));
        return !!btn && !btn.disabled;
      }),
    10000
  ),
  "模拟盘按钮可用"
);
await clickText(".mini-btn", "启动模拟盘");
assert(await waitFor(() => page.evaluate(() => document.body.innerText.includes("已启动模拟盘")), 30000), "启动模拟盘");
await clickText(".mini-btn", "暂停模拟盘");
assert(await waitFor(() => page.evaluate(() => document.body.innerText.includes("已暂停")), 30000), "暂停模拟盘");

// 立即扫描并执行（自动执行已关闭，仅扫描）
await clickText(".mini-btn", "立即扫描并执行");
assert(await waitFor(() => page.evaluate(() => document.body.innerText.includes("扫描完成")), 60000), "立即扫描并执行");

// 因子编辑
await openTab("因子工作台", "因子管理");
await clickText(".mini-btn", "编辑");
assert(await page.$(".modal"), "编辑弹窗打开");
await page.evaluate(() => {
  const el = document.querySelector(".modal input");
  if (!el) return;
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(el, "UI测试因子");
  el.dispatchEvent(new Event("input", { bubbles: true }));
});
await clickText(".mini-btn", "保存修改");
assert(
  await waitFor(() => page.evaluate(() => document.body.innerText.includes("UI测试因子")), 30000),
  "编辑因子保存"
);

// 自定义回测
await openTab("因子工作台", "回测与优化");
await pickSymbol(".panel-body .symbol-search", "BTCUSD");
await page.evaluate(() => {
  const inputs = [...document.querySelectorAll('.panel-body input[type="datetime-local"]')];
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  if (inputs.length >= 2) {
    setter.call(inputs[0], "2026-08-01T00:00");
    inputs[0].dispatchEvent(new Event("input", { bubbles: true }));
    setter.call(inputs[1], "2026-08-09T23:59");
    inputs[1].dispatchEvent(new Event("input", { bubbles: true }));
  }
});
await setByLabel("入金", "30000");
await setByLabel("杠杆", "25");
await setByLabel("执行延迟", "60000");
await clickText(".mini-btn", "运行回测");
const customOk = await waitFor(() => page.evaluate(() => document.body.innerText.includes("回测绩效")), 60000);
if (!customOk) {
  const snippet = await page.evaluate(() => document.body.innerText.slice(0, 600));
  throw new Error(`FAIL: 自定义回测\n页面文本: ${snippet}`);
}
assert(customOk, "自定义回测");

// 参数自动优化
await setByLabel("最大迭代次数", "5");
await setByLabel("提前停止轮次", "2");
await clickText(".mini-btn", "运行优化并回测");
assert(
  await waitFor(() => page.evaluate(() => document.body.innerText.includes("最优评分")), 120000),
  "参数自动优化"
);

// 删除因子
await openTab("因子工作台", "因子管理");
await clickText(".factor-card .mini-btn", "删除");
assert(
  await waitFor(() => page.evaluate(() => ![...document.querySelectorAll(".factor-card")].some((c) => c.textContent.includes("UI测试因子"))), 30000),
  "删除测试因子"
);

// 顶部一键平仓（应无持仓或正常响应）
await clickText(".btn", "一键平仓");
await sleep(1000);

console.log(`\n全部通过 ${pass} 项`);
if (errors.length) {
  console.log("浏览器错误：");
  for (const e of errors.slice(0, 10)) console.log(" -", e);
  process.exitCode = 1;
} else {
  console.log("浏览器控制台无错误");
}
await browser.close();
