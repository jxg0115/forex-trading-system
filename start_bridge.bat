@echo off
rem 一键启动系统桥（EA 桥整体测试用）——启动后保持此窗口开着
cd /d "C:\Users\xg\Documents\外汇交易系统"
start "DSH_Bridge" "runtime\python-embed\python.exe" -u "tools\ea_bridge.py"
echo bridge started. 查看新开的 "DSH_Bridge" 窗口输出（[ea_bridge] system-side ready 即就绪）