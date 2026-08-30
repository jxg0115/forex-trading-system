//+------------------------------------------------------------------+
//| DSH_Bridge_EA.mq5 — 数据桥 + 机械执行器（决策全在系统）          |
//| 职责（只做三件事）：                                             |
//|   1. 数据转发：Tester 回放的历史 bar（已收盘）-> bars.csv；       |
//|      启动时把测试参数（品种/周期/入金/杠杆/起始日期）写入         |
//|      bridge_config.csv（系统自动获知测试上下文）；                 |
//|   2. 机械执行：读系统指令 cmds.csv（OPEN/MODIFY_SL/CLOSE）->      |
//|      无脑执行（不开动脑：决策/价位/仓位全部由系统给定）；          |
//|   3. 成交回报：每个成交 deal（开/平，含引擎触发的 SL/TP 平仓）    |
//|      -> trades.csv（系统按 in/out 配对统计盈亏）。                 |
//| 系统（Python 端）负责：信号/开仓/止损止盈价位/移动止损/时间停     |
//| 判定——EA 只是系统的手，不是系统的脑。                            |
//+------------------------------------------------------------------+
#property strict
#property copyright "DSH Bridge"

// 桥目录写死为 Common\Files 内相对路径（Tester 沙箱只允许 Common 文件）。
// 故意不用 input 参数：Tester 设置会记住旧 input（如 D:\dsh\ea_bridge\）并在重跑时覆盖默认值，
// 导致路径被沙箱拒绝（err=5002）。写死宏 = 无法被覆盖。
#define BRIDGE_DIR "dsb\\"
input int    MagicN     = 202608;
input int    Deviations = 5;

string g_configFile, g_barsFile, g_cmdsFile, g_tradesFile, g_execFile;
datetime g_lastBarOpen = 0;
long g_lastCmdSeq = 0;
datetime g_firstBarTime = 0;   // 测试段起点（OnInit 记录，转发时识别新测试重播）

//+------------------------------------------------------------------+
int OnInit()
{
   g_configFile = BRIDGE_DIR + "bridge_config.csv";
   g_barsFile   = BRIDGE_DIR + "bars.csv";
   g_cmdsFile   = BRIDGE_DIR + "cmds.csv";
   g_tradesFile = BRIDGE_DIR + "trades.csv";
   g_execFile   = BRIDGE_DIR + "ea_exec.log";

   // 0) 读旧 config 判断是否同一测试段重启（同段则保留桥文件，避免冲掉桥进度/指令/成交）
   datetime firstBarTime = iTime(_Symbol, PERIOD_CURRENT, Bars(_Symbol, PERIOD_CURRENT) - 1);
   g_firstBarTime = firstBarTime;
   bool sameSegment = false;
   int hc = FileOpen(g_configFile, FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
   if(hc != INVALID_HANDLE)
   {
      if(!FileIsEnding(hc))
      {
         FileReadString(hc);                 // symbol
         FileReadString(hc);                 // period
         FileReadString(hc);                 // balance
         FileReadString(hc);                 // leverage
         string sStart = FileReadString(hc); // start（最老根时间）
         if(sStart != "" && StringToInteger(sStart) == (long)firstBarTime)
            sameSegment = true;
      }
      FileClose(hc);
   }

   // 1) 写测试配置（Tester 注入：品种/周期/入金/杠杆/起止时间 = 最老根/最新根）
   datetime lastBarTime  = iTime(_Symbol, PERIOD_CURRENT, 0);
   int h = FileOpen(g_configFile, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
   if(h != INVALID_HANDLE)
   {
      FileWrite(h, _Symbol,
                IntegerToString(PERIOD_CURRENT),
                DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2),
                IntegerToString((int)AccountInfoInteger(ACCOUNT_LEVERAGE)),
                IntegerToString((long)firstBarTime),
                IntegerToString((long)lastBarTime));
      FileClose(h);
   }
   else
      Print("[DSH_Bridge] FileOpen FAIL: ", g_configFile, " err=", GetLastError());

   // 2) 清桥文件表头（仅新测试段；同段重启保留——避免冲掉桥正在推进的指令/成交）
   if(!sameSegment)
   {
      h = FileOpen(g_barsFile, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
      if(h != INVALID_HANDLE) { FileWrite(h, "time,open,high,low,close"); FileClose(h); }
      else Print("[DSH_Bridge] FileOpen FAIL: ", g_barsFile, " err=", GetLastError());
      h = FileOpen(g_cmdsFile, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
      if(h != INVALID_HANDLE) { FileWrite(h, "seq,cmd,arg1,arg2,arg3,arg4"); FileClose(h); }
      else Print("[DSH_Bridge] FileOpen FAIL: ", g_cmdsFile, " err=", GetLastError());
      h = FileOpen(g_tradesFile, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
      if(h != INVALID_HANDLE) { FileWrite(h, "kind,time,price,dir,vol"); FileClose(h); }
      else Print("[DSH_Bridge] FileOpen FAIL: ", g_tradesFile, " err=", GetLastError());
   }
   else
      Print("[DSH_Bridge] 同一测试段重启——保留桥文件");

   // 2.5) g_lastCmdSeq 从 cmds 现有最大 seq 续接（同段重启不重复执行历史指令；新段只剩表头 seq=0）
   int h2 = FileOpen(g_cmdsFile, FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
   if(h2 != INVALID_HANDLE)
   {
      long maxSeq = 0;
      while(!FileIsEnding(h2))
      {
         long s2 = (long)FileReadNumber(h2);
         FileReadString(h2);
         FileReadNumber(h2); FileReadNumber(h2); FileReadNumber(h2); FileReadNumber(h2);
         if(s2 > maxSeq) maxSeq = s2;
      }
      FileClose(h2);
      g_lastCmdSeq = maxSeq;
      if(maxSeq > 0)
         Print("[DSH_Bridge] 续接指令 seq=", maxSeq);
   }

   Print("[DSH_Bridge] init | sym=", _Symbol, " P", PERIOD_CURRENT,
         " bal=", DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2),
         " lev=", AccountInfoInteger(ACCOUNT_LEVERAGE),
         " start=", TimeToString(firstBarTime, TIME_DATE));
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnTick()
{
   // 1) 转发已收盘 bar（只转 index=1 的完整根，无未来函数）
   datetime barOpen = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(barOpen != g_lastBarOpen)
   {
      g_lastBarOpen = barOpen;
      datetime bt = iTime(_Symbol, PERIOD_CURRENT, 1);
      double o = iOpen(_Symbol, PERIOD_CURRENT, 1);
      double hi = iHigh(_Symbol, PERIOD_CURRENT, 1);
      double lo = iLow(_Symbol, PERIOD_CURRENT, 1);
      double cl = iClose(_Symbol, PERIOD_CURRENT, 1);
      if(bt > 0)
      {
         // 回到测试段起点 = 新测试/重播 -> 清空三文件重写表头（每次测试数据完全独立，不混合）
         if(bt == g_firstBarTime)
         {
            int hw = FileOpen(g_barsFile, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
            if(hw != INVALID_HANDLE) { FileWrite(hw, "time,open,high,low,close"); FileClose(hw); }
            hw = FileOpen(g_cmdsFile, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
            if(hw != INVALID_HANDLE) { FileWrite(hw, "seq,cmd,arg1,arg2,arg3,arg4"); FileClose(hw); }
            hw = FileOpen(g_tradesFile, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
            if(hw != INVALID_HANDLE) { FileWrite(hw, "kind,time,price,dir,vol"); FileClose(hw); }
            Print("[DSH_Bridge] 回到段起点——清空三文件（新测试数据独立）");
         }
         int hf = FileOpen(g_barsFile, FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
         if(hf != INVALID_HANDLE)
         {
            FileSeek(hf, 0, SEEK_END);
            FileWrite(hf, (long)bt, DoubleToString(o, _Digits), DoubleToString(hi, _Digits),
                      DoubleToString(lo, _Digits), DoubleToString(cl, _Digits));
            FileClose(hf);
         }
         else
            Print("[DSH_Bridge] bar 转发 FAIL: ", g_barsFile, " err=", GetLastError());
      }
   }

   // 2) 读系统指令并机械执行
   ReadAndExecuteCmds();
}

//+------------------------------------------------------------------+
// 执行诊断日志：写入 dsb\ea_exec.log（Common 沙箱可写，与桥共用目录）。
// 每个执行事件一行（含时间/seq/retcode），桥侧与人工都可直接读，不依赖 Print 可见性。
// msg 用英文+数字，避免 ANSI 编码中文乱码。
void LogExec(const string msg)
{
   int h = FileOpen(g_execFile, FILE_READ|FILE_WRITE|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + " " + msg + "\n");
   FileClose(h);
}

//+------------------------------------------------------------------+
void ReadAndExecuteCmds()
{
   long newSeq = 0;
   string cmd = "";
   double a1 = 0, a2 = 0, a3 = 0, a4 = 0;
   int h = FileOpen(g_cmdsFile, FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
   if(h != INVALID_HANDLE)
   {
      while(!FileIsEnding(h))
      {
         long s = (long)FileReadNumber(h);
         string c = FileReadString(h);
         double x = FileReadNumber(h), y = FileReadNumber(h), z = FileReadNumber(h), w = FileReadNumber(h);
         if(s > g_lastCmdSeq) { newSeq = s; cmd = c; a1 = x; a2 = y; a3 = z; a4 = w; }
      }
      FileClose(h);
   }
   if(newSeq > g_lastCmdSeq)
   {
      g_lastCmdSeq = newSeq;
      if(cmd == "OPEN")         ExecuteOpen(newSeq, a1, a2, a3, a4); // dir, sl, tp, vol
      else if(cmd == "MODIFY_SL") ExecuteModifySL(newSeq, a1);       // new_sl
      else if(cmd == "CLOSE")   ExecuteClose(newSeq);
      else
      {
         LogExec("UNKNOWN_CMD seq=" + IntegerToString(newSeq) + " cmd=" + cmd);
         Print("[DSH_Bridge] 未知指令: ", cmd);
      }
   }
}

//+------------------------------------------------------------------+
void ExecuteOpen(long seq, double dir, double sl, double tp, double vol)
{
   if(PositionsTotal() > 0)
   {
      LogExec("OPEN seq=" + IntegerToString(seq) + " IGNORE(holding) dir=" + DoubleToString(dir,1)
              + " vol=" + DoubleToString(vol,2) + " sl=" + DoubleToString(sl,_Digits)
              + " tp=" + DoubleToString(tp,_Digits));
      Print("[DSH_Bridge] OPEN 忽略：已持仓 seq=", seq);
      return;
   }
   double price = (dir > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                          : SymbolInfoDouble(_Symbol, SYMBOL_BID));
   MqlTradeRequest req = {};
   MqlTradeResult  res = {};
   req.action    = TRADE_ACTION_DEAL;
   req.symbol    = _Symbol;
   req.volume    = vol;
   req.type      = (dir > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
   req.price     = price;
   req.sl        = sl;
   req.tp        = tp;
   req.magic     = MagicN;
   req.deviation = Deviations;
   req.comment   = "DSH_OPEN";
   if(OrderSend(req, res))
   {
      LogExec("OPEN seq=" + IntegerToString(seq) + " OK dir=" + DoubleToString(dir,1)
              + " vol=" + DoubleToString(vol,2) + " price=" + DoubleToString(price,_Digits)
              + " sl=" + DoubleToString(sl,_Digits) + " tp=" + DoubleToString(tp,_Digits)
              + " deal=" + IntegerToString(res.deal) + " retcode=" + IntegerToString(res.retcode));
      Print("[DSH_Bridge] OPEN ok dir=", (int)dir, " vol=", DoubleToString(vol, 2),
            " price=", DoubleToString(price, _Digits), " sl=", DoubleToString(sl, _Digits),
            " tp=", DoubleToString(tp, _Digits), " deal=", res.deal);
   }
   else
   {
      LogExec("OPEN seq=" + IntegerToString(seq) + " FAIL retcode=" + IntegerToString(res.retcode)
              + " " + res.comment + " price=" + DoubleToString(price,_Digits));
      Print("[DSH_Bridge] OPEN FAIL retcode=", res.retcode, " ", res.comment);
   }
}

//+------------------------------------------------------------------+
void ExecuteModifySL(long seq, double newSl)
{
   bool found = false;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(PositionGetInteger(POSITION_MAGIC) == MagicN)
      {
         found = true;
         double tp = PositionGetDouble(POSITION_TP);
         MqlTradeRequest req = {};
         MqlTradeResult  res = {};
         req.action    = TRADE_ACTION_SLTP;
         req.symbol    = _Symbol;
         req.position  = t;
         req.sl        = newSl;
         req.tp        = tp;
         req.magic     = MagicN;
         req.deviation = Deviations;
         if(OrderSend(req, res))
         {
            LogExec("MODIFY_SL seq=" + IntegerToString(seq) + " OK ticket=" + IntegerToString(t)
                    + " sl=" + DoubleToString(newSl, _Digits) + " retcode=" + IntegerToString(res.retcode));
            Print("[DSH_Bridge] MODIFY_SL ok #", t, " sl=", DoubleToString(newSl, _Digits));
         }
         else
         {
            LogExec("MODIFY_SL seq=" + IntegerToString(seq) + " FAIL ticket=" + IntegerToString(t)
                    + " retcode=" + IntegerToString(res.retcode) + " " + res.comment);
            Print("[DSH_Bridge] MODIFY_SL FAIL retcode=", res.retcode, " ", res.comment);
         }
         break;
      }
   }
   if(!found)
      LogExec("MODIFY_SL seq=" + IntegerToString(seq) + " NO_POSITION sl=" + DoubleToString(newSl, _Digits));
}

//+------------------------------------------------------------------+
void ExecuteClose(long seq)
{
   bool found = false;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(PositionGetInteger(POSITION_MAGIC) == MagicN)
      {
         found = true;
         double price = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
                        ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                        : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         MqlTradeRequest req = {};
         MqlTradeResult  res = {};
         req.action    = TRADE_ACTION_DEAL;
         req.symbol    = _Symbol;
         req.volume    = PositionGetDouble(POSITION_VOLUME);
         req.type      = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY
                          ? ORDER_TYPE_SELL : ORDER_TYPE_BUY);
         req.price     = price;
         req.magic     = MagicN;
         req.deviation = Deviations;
         req.comment   = "DSH_CLOSE";
         if(OrderSend(req, res))
         {
            LogExec("CLOSE seq=" + IntegerToString(seq) + " OK ticket=" + IntegerToString(t)
                    + " price=" + DoubleToString(price, _Digits) + " deal=" + IntegerToString(res.deal)
                    + " retcode=" + IntegerToString(res.retcode));
            Print("[DSH_Bridge] CLOSE ok #", t, " price=", DoubleToString(price, _Digits),
                  " deal=", res.deal);
         }
         else
         {
            LogExec("CLOSE seq=" + IntegerToString(seq) + " FAIL ticket=" + IntegerToString(t)
                    + " retcode=" + IntegerToString(res.retcode) + " " + res.comment);
            Print("[DSH_Bridge] CLOSE FAIL retcode=", res.retcode, " ", res.comment);
         }
         break;
      }
   }
   if(!found)
      LogExec("CLOSE seq=" + IntegerToString(seq) + " NO_POSITION");
}

//+------------------------------------------------------------------+
// 成交回报：每个 deal（开/平，含引擎触发的 SL/TP 平仓）-> trades.csv
void OnTradeTransaction(const MqlTradeTransaction& trans,
                        const MqlTradeRequest& request,
                        const MqlTradeResult& result)
{
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
   ulong deal = trans.deal;
   if(!HistoryDealSelect(deal)) return;
   long entry = HistoryDealGetInteger(deal, DEAL_ENTRY); // IN=0, OUT=1
   double price = HistoryDealGetDouble(deal, DEAL_PRICE);
   long dir = HistoryDealGetInteger(deal, DEAL_TYPE);   // BUY=0, SELL=1
   datetime dt = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
   double vol = HistoryDealGetDouble(deal, DEAL_VOLUME);
   string kindStr = (entry == DEAL_ENTRY_IN ? "in" : "out");
   int h = FileOpen(g_tradesFile, FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
   if(h != INVALID_HANDLE)
   {
      FileSeek(h, 0, SEEK_END);
      FileWrite(h, kindStr, IntegerToString((long)dt), DoubleToString(price, _Digits),
                IntegerToString((int)dir), DoubleToString(vol, 2));
      FileClose(h);
      LogExec("DEAL " + kindStr + " price=" + DoubleToString(price, _Digits)
              + " dir=" + IntegerToString((int)dir) + " vol=" + DoubleToString(vol, 2)
              + " deal=" + IntegerToString((long)deal));
   }
   else
   {
      LogExec("DEAL_WRITE_FAIL " + kindStr + " price=" + DoubleToString(price, _Digits)
              + " dir=" + IntegerToString((int)dir) + " err=" + IntegerToString(GetLastError()));
      Print("[DSH_Bridge] trades 回报 FAIL: ", g_tradesFile, " err=", GetLastError());
   }
}