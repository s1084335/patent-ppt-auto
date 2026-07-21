"""案件比對（Claim Comparison）模組：獨立於報表／PPT 的逐要素比對產線。

第一輪只含純邏輯與資料契約：
- claim_model：專利理解稿結構與來源欄位驗證。
- verdict：要素四態、all-elements rule 與引用鏈推論。
- comparison_store：接 0021 schema 的版本化產出與人工閘門 guard。
不含 PDF、圖片抽取、API endpoint、worker。
"""
