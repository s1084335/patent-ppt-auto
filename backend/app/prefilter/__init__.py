"""初階篩選（負面關鍵字）——分群之前把整批不該進入分析的專利篩掉。

規則（負面關鍵字與其英文比對詞）存 `derived_layer.workspace_negative_keywords`；
命中結果沿用既有的 `derived_layer.workspace_excluded_patents`，schema 不改。

⚠ 本套件只做「規則治理」與「確定性比對」；AI 只負責把中英混雜的關鍵字轉成
英文比對詞，且產出一律為未確認草稿（PRE-002）。
"""
