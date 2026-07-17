"""Central Patent MCP Server 套件。

把報表引擎（backend.app.reports）與分群引擎（backend.app.clustering）的確定性
能力包成 MCP tools，供 Claude Code（MCP client）依 skill 調用。

分層設計：
    tools_reporting / tools_clustering  純函式工具實作（不 import mcp SDK，可直接單元測試）
    _shared                             JSON 序列化正規化等共用工具
    server                              FastMCP 綁定與傳輸（stdio／streamable-http）

邊界原則（與專案目標一致）：工具只回引擎算好的結構化結果，不做解讀；
解讀／標籤／敘事是呼叫方（Claude Code）的責任，正式結果由使用者定案。
"""
