"""测试统一使用独立测试数据库与模拟经纪商，避免影响真实数据。"""

import os

os.environ["BROKER"] = "paper"
os.environ["MT5_PATH"] = ""
os.environ["DATABASE_URL"] = "sqlite:///./data/test_factor_store.db"
