# Delta Spec: report-export — 漸進預覽與單頁編輯

## ADDED Requirements

### Requirement: 逐頁事件

deck 產製過程中，每完成一頁的機械組版，系統 SHALL 發出一則逐頁事件。

#### Scenario: 機械步產出一頁即推一頁

- **WHEN** `build_svg` 完成第 N 頁
- **THEN** 系統 SHALL 發出事件，內容含 `run_id`、`version`、`page`、`total`、
  該頁資產相對路徑
- **AND** 事件 payload SHALL NOT 內嵌圖片位元組
  （⚠ PostgreSQL `NOTIFY` payload 上限 8000 bytes）

#### Scenario: 事件必須帶分母

- **WHEN** 發出逐頁事件
- **THEN** payload SHALL 含 `total`（該份簡報總頁數）
- ⚠ 沒有分母時前端只能顯示「已產 7 頁」，讀起來像「這份只有 7 頁」

#### Scenario: 目視迴圈不逐輪推送

- **WHEN** CLI 目視迴圈修改了某些頁
- **THEN** 系統 SHALL NOT 逐輪發出逐頁事件
- **AND** 目視迴圈結束後 SHALL 整批更新所有受影響的頁
- ⚠ 不更新等於讓使用者停在舊畫面而不自知（缺席型偏差）

#### Scenario: 失敗時保留已推的頁

- **WHEN** deck 產製在中途失敗
- **THEN** 已發出的逐頁事件 SHALL NOT 被撤回或清除
- **AND** 前端 SHALL 仍能顯示失敗前已完成的頁
- ⚠ 實機 `#426` 跑 3763 秒失敗，前面十幾頁已正確組好卻一頁沒被看到

### Requirement: 單頁編輯

使用者 SHALL 能修改已產出簡報中單一頁的內容，並在不重新撰稿的情況下取得更新後的簡報。

#### Scenario: 編輯範圍是單頁，重組範圍是全份

- **WHEN** 使用者送出第 N 頁的 content 修改
- **THEN** 系統 SHALL 只把修改套用到該頁的 content
- **AND** SHALL 重組**整份**簡報（實測 1.8 秒）
- **AND** SHALL NOT 重新執行 CLI 撰稿
- ⚠ 省下的成本來自不重撰稿與不重目視（`#426` 中佔約 62 分鐘），
  不是來自不重組版（1.8 秒）

#### Scenario: 未修改的頁必須逐位元組相同

- **WHEN** 只修改第 N 頁並重組
- **THEN** 其餘每一頁的 SVG SHALL 與修改前逐位元組相同
- ⚠ 這是可驗證的推論，不是假設；不成立代表組版有隱藏的跨頁耦合

#### Scenario: 重跑閘門

- **WHEN** 單頁編輯完成並重組
- **THEN** 系統 SHALL 對**全份**執行 `check_content` 與 `audit_deck`
- **AND** SHALL NOT 重新執行 CLI 目視迴圈
- ⚠ 閘門守的是結構與口徑（人改也可能違反），秒級零 token；
  目視守的是 AI 撰稿品質，人自己改的不需要 AI 再審

#### Scenario: 版面溢出必須呈現給使用者

- **WHEN** 重組後 `make_deck` 回報版面溢出或圖內字級不足
- **THEN** 系統 SHALL 把該結果呈現給使用者
- **AND** SHALL NOT 靜默吞掉
- ⚠ 吞掉的話人改出溢出的內容而系統不吭聲

### Requirement: 人工編輯的稽核軌跡

人工編輯 SHALL 與 AI 撰稿在證據鏈上可區分。

#### Scenario: 每次編輯留痕

- **WHEN** 使用者完成一次單頁編輯
- **THEN** 系統 SHALL 記錄改前 hash、改後 hash、頁號、時間、編輯者
- ⚠ 不留痕會讓證據鏈出現無法解釋的斷點——日後問「這句的依據在哪」，
  audit 裡找不到，也沒有任何地方說明它是人改的

#### Scenario: 稽核落點優先不加 migration

- **WHEN** 決定稽核資訊的儲存位置
- **THEN** SHALL 優先沿用既有 manifest（已記 content hash）
- **AND** 若 manifest 結構容不下，SHALL 在 tasks 明確揭露後才加 migration
- **AND** SHALL NOT 把稽核資訊塞進語意不相干的既有欄位

## MODIFIED Requirements

### Requirement: 匯出報告頁

匯出報告頁 SHALL 在既有的 deck 紀錄區內提供逐頁預覽與單頁編輯，不新增路由。

#### Scenario: 編輯入口在看到問題的地方

- **WHEN** 使用者在逐頁預覽中發現某頁有問題
- **THEN** 該頁 SHALL 可就地展開 content 欄位進行編輯
- **AND** SHALL NOT 需要跳轉到另一個頁面或服務
