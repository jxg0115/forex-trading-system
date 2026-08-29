# EA 桥整体系统测试说明（MT5 策略测试器）

## 一、架构（一句话）

**EA 是纯数据桥 + 机械执行器，决策 100% 在系统（Python）**——MT5 Tester 回放真实历史报价 → EA 转发 bar 给系统 → 系统算全部决策（信号/开仓/止损止盈价位/移动止损/时间停）→ EA 机械执行 + 回报成交 → 系统统计整体测试数据（笔数/胜率/盈亏比/最大亏损/最高盈利/收益曲线），**订单不保存到系统数据库**（明细看 MT5 Tester 报告）。

```
MT5 策略测试器（你填：专家/品种/周期/日期/入金/杠杆）
        │ 回放历史报价
DSH_Bridge_EA.mq5（D:\MT4BC\MQL5\Experts\，已编译 ex5）
        │ 转发已收盘 bar → bars.csv；回报成交 → trades.csv
        │ 读指令 ← cmds.csv；启动写测试上下文 → bridge_config.csv
系统（Python）tools/ea_bridge.py（后台常驻）
        ├─ 决策：candidate_entry（swing100+趋势段+延迟3，与回测同一序列）
        │      开仓价位 2ATR 止损 / 3R 止盈；移动止损（盈利1R激活、回撤1.5ATR提损）；
        │      时间停 120 bar
        └─ 统计：in/out 配对 → 笔数/胜率/PF/最大亏损/最高盈利 + 收益曲线
               → stats.txt + equity.csv（D:\dsh\ea_bridge\）
```

## 二、文件约定（`Common\Files\dsb`：`C:\Users\xg\AppData\Roaming\MetaQuotes\Terminal\Common\Files\dsb\`）

> **重要**：MT5 策略测试器（Tester 沙箱）只允许 EA 写 **Common 文件**（`FILE_COMMON` 标志）——绝对路径（如 `D:\dsh`）会被静默拒绝（EA 文件写失败且不报错，表现为"没有数据"）。桥目录统一用 `Common\Files\dsb`（EA 与系统桥共用）。

| 文件 | 谁写 | 内容 |
|---|---|---|
| bridge_config.csv | EA 启动 | 品种/周期/入金/杠杆/起始时间（系统自动获知测试上下文） |
| bars.csv | EA 每根收盘 | time(秒戳),open,high,low,close（只转已收盘 bar，无未来函数） |
| cmds.csv | 系统 | seq,cmd,arg1..arg4（OPEN dir,sl,tp,vol / MODIFY_SL new_sl / CLOSE） |
| trades.csv | EA 每笔成交 | kind(in/out),time,price,dir,vol（含引擎触发的 SL/TP 平仓 out） |
| stats.txt | 系统 | 每笔明细 + 整体统计（UTF-8） |
| equity.csv | 系统 | time_unix,equity_pct_cum（收益曲线数据，可导入任意工具画图） |

## 三、使用步骤

1. **启动系统桥**（后台常驻）：
   ```
   C:\Users\xg\Documents\外汇交易系统\runtime\python-embed\python.exe -u C:\Users\xg\Documents\外汇交易系统\tools\ea_bridge.py
   ```
   （也可放入启动任务；输出显示 "[ea_bridge] system-side ready" 即就绪）
2. **打开 MT5 终端**（D:\MT4BC\terminal64.exe，登录项目账户实例 AD19930F...）→ 工具栏「策略测试器」→ 填字段：
   - 专家：DSH_Bridge_EA（数据桥）
   - 交易品种：GOLD（建议先 GOLD H1——系统已验证品种）
   - 周期：H1
   - 日期：按你要测的范围（如 2026-01-01 ~ 2026-08-28；或更长）
   - 入金 / 杠杆：按测试设置（EA 与系统自动获知）
   - 勾选「移动止损止盈」默认由系统负责（系统即移动止损逻辑，无需 MT5 勾选）
3. 点「开始」：Tester 回放 → EA 转发 bar → 系统算决策发指令 → EA 执行 → 回合成交
4. 运行中/结束后：**系统统计**看 `Common\Files\dsb\stats.txt`（笔数/胜率/PF/最大亏损/最高盈利）与 `equity.csv`（曲线）；**MT5 Tester 报告**看成交明细/净值曲线（自行核对）

## 四、统计口径（诚实标注）

- 盈亏按**价格百分比**（与仓位无关）：多 `(out-in)/in`、空 `(in-out)/in`
- **胜率/PF/最大亏损/最高盈利/累计收益**：基于每笔平仓（in/out 配对 FIFO）
- **固定仓位 0.01**（统计与仓位无关；若要真实账户收益，以 MT5 Tester 报告的净值曲线为准）
- **移动止损默认开启**（盈利 ≥1R 激活，回撤 1.5ATR 提损——引擎 hw 语义）；**注意**：系统历史回测（rolling_recheck）用的是**固定 SLTP 未开移动**——**两者不可直接逐数对比**（交叉验证时注明规则差异；若需严格对比，可在回测侧同样开启 hw）

## 五、交叉验证方法

Tester 报告（你看到）与 系统统计 与 系统历史回测（rolling_recheck 同候选同段）三方对照：
- Tester 报告 ≈ 系统统计（成交一一对应）→ 桥链路正确
- 系统统计（含移动止损）vs rolling_recheck（固定 SLTP）→ 差异 = **移动止损规则的贡献**（量化规则价值）
- 若差异异常大 → 检查执行模型差距（滑点/点差/bar 对齐）

## 六、诚实边界

- 候选因子为**弱正期望**（12 年定论：中位 PF ~1.19、不入库、保持观察）——**EA 桥测试不改变该定论**，它验证的是**执行链路逼真度与整体系统测试能力**，不是"因子变强了"
- 跨品种/周期测试 = **新测试**（非已验证 claim），系统如实记录测试上下文（bridge_config）
- EA 只转发**已收盘** bar、只执行系统指令——无未来函数、无 EA 自主决策
- 桥链路已空跑验证（OPEN→in 回报→移动止损提损 9 次→时间停 CLOSE→out→统计 n=1 累计 +12.8%）

## 七、文件位置

- EA（源 + 编译 ex5）：`C:\Users\xg\AppData\Roaming\MetaQuotes\Terminal\AD19930FB9E178C6A37EC8F2329F4307\MQL5\Experts\DSH_Bridge_EA.mq5`（源副本 `D:\MT4BC\MQL5\Experts\`；MetaEditor64 编译）
- 系统桥：`tools/ea_bridge.py`
- 桥目录：`Common\Files\dsb`（`C:\Users\xg\AppData\Roaming\MetaQuotes\Terminal\Common\Files\dsb\`）——Tester 沙箱只允许 EA 写 Common 文件（FILE_COMMON）