"""市場資料線：附錄 3（市場規模／區域趨勢／銷售對象）、附錄 2 Key Players、痛點調查的統一機制。

第一輪只含純邏輯（無 DB／網路）：
- evidence_model：範圍與證據的邏輯層契約、可比較性 key、時效。
- aggregate：同可比較性 key 的 min–max 彙總（不平均）、divergent/single_source 標記、AI 第一篩的確定性輔助。
證據庫表（0022 `derived_layer.market_evidence`）與 store 為下一輪。規格見 .agents/skills/market-data-flow.md。
"""
