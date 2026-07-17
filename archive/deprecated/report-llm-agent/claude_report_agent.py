"""已改名為 report_agent.py（加入雙 provider 後模組不再只服務 Claude）。

本檔僅為轉接殼避免舊 import 壞掉；確認無人引用後可刪除。
"""
from backend.app.llm.report_agent import (  # noqa: F401
    agent_available,
    ask_reports,
    resolve_config,
)
