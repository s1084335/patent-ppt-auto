"""（已裁撤）J-5 生命週期散點圖的軸測試。

🔴 2026-08-07 契約變更：lifecycle 改版「專利狀態分析」（前十大申請人 × 狀態桶
堆疊長條），舊散點渲染器 `render_lifecycle_chart` 連同「件數×家數依年連線」
的編碼一併刪除——本檔原本守的兩個症狀（nice_ticks 非整數步進、年份標籤壓軸線）
隨渲染器消失而失去對象。

⚠ nice_ticks 本身仍在（其他折線圖用），其整數步進契約由
`test_chart_sections` 的趨勢圖測試涵蓋；新狀態圖的契約在
`tests/test_lifecycle_status_analysis.py`。保留本檔名是為了讓「當初為什麼有
這個測試、為什麼拿掉」可追溯，不是漏刪。
"""
