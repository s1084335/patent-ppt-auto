# Patent Comparison Design

## 架構與資料流

案件由 comparison API 建立並保存狀態。標的可由庫內專利或使用者內容建立；references 可由搜尋候選或外部內容建立。Claim parser 產結構化 claim model，understanding payload 版本化保存，人工 approve 是後續分析的硬閘門。

目前 repository 已包含 claim source/parser、target source、reference search、specification ingest、圖頁偵測、專利圖片與 verdict aggregation 等模組；是否形成正式產品能力仍以 API 接線、測試與 OpenSpec requirement 為準，檔案存在不等於已交付。

## 程式落點

- API：`backend/app/api/comparison.py`
- 狀態：`backend/app/comparison/comparison_store.py`
- Claim：`claim_source.py`、`claim_parser.py`、`claim_model.py`
- 來源：`target_source.py`、`reference_search.py`、`specification_ingest.py`
- 證據：`pdf_fetch.py`、`figure_page_detector.py`、`patent_images.py`
- 分析：`understanding_payload.py`、`verdict.py`、`verdict_aggregation.py`

## 測試證據

- `tests/test_api_comparison.py`
- `tests/test_comparison_model.py`
- `tests/test_comparison_store.py`
- `tests/test_claim_parser.py`、`tests/test_claim_source.py`
- `tests/test_target_source.py`
- `tests/test_reference_search.py`
- `tests/test_specification_ingest.py`
- `tests/test_understanding_payload.py`
- `tests/test_verdict_aggregation.py`
- `scripts/comparison_e2e_smoke.py`

## 輸出契約

目前輸出為案件／工作狀態、subject/target metadata、versioned understanding、approval、reference candidates、specification content 與 element-analysis data。正式 PDF 與法律結論不在本 baseline 的已交付範圍。

## 驗收邊界

自動測試驗資料契約；完整案件流程需以真實或受控專利內容跑 API smoke，逐一確認來源可追溯、版本閘門生效及缺證不被補造。

