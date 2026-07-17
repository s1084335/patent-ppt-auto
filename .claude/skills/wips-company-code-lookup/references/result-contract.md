# 結果契約與重複定義

## WIPS candidate

```json
{
  "query": "rexon",
  "company_code": "UN116754",
  "company_name": "REXON INDUSTRIAL CORP., LTD.",
  "aliases": ["REXON INDUSTRIAL CORP., LTD.", "力山工業股份有限公司"],
  "observed_alias_count": 2,
  "complete": true,
  "source": "wips_global",
  "source_url": "https://www.wipsglobal.com/servicecn/wap/wapView.wips?trgtFld=devWapField",
  "retrieved_at": "2026-07-17T10:00:00+08:00",
  "evidence": {"source_result_code": "EN065031", "expanded": true}
}
```

## 欄位映射

| candidate | `derived_layer.company_aliases` | 規則 |
|---|---|---|
| `company_code` | `申請人代碼` | 只放 WIPS 標準申請人代碼，例如 `UN116754` |
| `company_name` | `公司名稱` | WIPS 標準公司名稱 |
| `aliases[]` 每一筆 | `別稱` | 一個別稱一列，保留清理後原文 |
| `source_url` / evidence | audit artifact／`source_file` 參照 | 不新增第二張對照表 |

`EN...` 等來源結果代碼不能放進 `申請人代碼`，除非未來經 migration 新增獨立欄位。

## 唯一清理函式

所有入口都使用 backend 既有語意：

1. `clean_text`：轉字串、去除首尾空白、將連續空白合併為單一空白。
2. `normalize_lookup`：對 `clean_text` 結果再做 `casefold`，只供碰撞偵測與查詢。

Skill 不自行實作另一套正規化函式。

## 重複分類

### exact_duplicate

經 `clean_text` 後，三元組 `(company_code or "", company_name, alias_name)` 完全相同。行為：不新增、不更新既有原文，計入略過筆數。

### normalized_collision

同一 `company_code` 下，`normalize_lookup(alias_name)` 相同，但 `clean_text(alias_name)` 原文不同。行為：保留原文並列入人工審查，不自動新增、刪除或視為 exact duplicate。

### code_conflict

相同或正規化後相同的公司名稱／別稱，對應不同且非空的 `company_code`。行為：阻擋自動合併與寫入，列出兩側代碼及名稱供人工確認。

### to_insert

不屬於以上分類，必要欄位完整且 `complete=true`。行為：僅在使用者確認 preview 後寫入。

### rejected

缺少 `company_name`、缺少 alias、擷取不完整、觀察筆數不一致或來源證據不足。行為：不得寫入。

## Preview 回傳要求

Central Patent MCP 至少回傳各分類筆數、各筆原文與判定原因。寫入命令只能接受 preview 中的 `to_insert`，完成後按 `company_code` 回讀並核對新增筆數。
