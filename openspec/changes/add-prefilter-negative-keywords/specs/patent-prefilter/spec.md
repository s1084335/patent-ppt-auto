## Purpose

分群之前依使用者提供的負面關鍵字篩掉整批不該進入分析的專利：AI 只負責把中英混雜的
關鍵字轉成英文比對詞並提供建議，比對本身為確定性運算，剔除與否一律由使用者裁決；
被剔除者封存後不進分析，滿保留期才硬刪。

## ADDED Requirements

### Requirement: PRE-001 負面關鍵字以 workspace 為單位保存

系統 SHALL 以 workspace 為範圍保存負面關鍵字；每筆關鍵字 SHALL 記錄使用者輸入的
原始詞、對應的英文比對詞集合、啟用狀態與最後更新時間。

不同 workspace 的關鍵字 SHALL 互不影響。

#### Scenario: 關鍵字只作用於所屬 workspace
- **WHEN** 於 workspace A 建立負面關鍵字後，切換至 workspace B 執行篩選
- **THEN** workspace B 的篩選 SHALL NOT 套用 workspace A 的關鍵字

#### Scenario: 停用的關鍵字不參與比對
- **WHEN** 某筆關鍵字被標記為停用後執行篩選
- **THEN** 該筆的比對詞 SHALL NOT 產生任何命中

#### Scenario: 重跑可重現
- **WHEN** 關鍵字與資料皆未變動而重複執行篩選
- **THEN** 兩次的命中集合 SHALL 完全相同

### Requirement: PRE-002 AI 轉換僅產生建議

系統 SHALL 提供將使用者輸入的負面關鍵字（中文、英文或混雜）轉換為英文比對詞的能力。

AI 產出 SHALL 僅為建議；未經使用者確認的比對詞 SHALL NOT 用於比對，
且 SHALL NOT 產生任何待裁決項目。

#### Scenario: 中文關鍵字取得英文比對詞
- **WHEN** 使用者輸入中文負面關鍵字並要求轉換
- **THEN** 系統 SHALL 回傳英文比對詞建議，含同義詞與常見詞形

#### Scenario: 未確認即不生效
- **WHEN** 轉換完成但使用者尚未確認比對詞
- **THEN** 執行篩選 SHALL NOT 產生任何待裁決項目

#### Scenario: 使用者可增刪比對詞
- **WHEN** 使用者於確認前刪除某個 AI 建議的比對詞
- **THEN** 該詞 SHALL NOT 出現在後續比對中

#### Scenario: 轉換不可用時不阻斷
- **WHEN** AI 轉換失敗或不可用
- **THEN** 系統 SHALL 明確告知失敗，且使用者 SHALL 仍可自行輸入英文比對詞完成篩選

### Requirement: PRE-003 比對為確定性且採前綴詞界

比對 SHALL 針對 `title`、`abstract` 與獨立項三個欄位執行，且 SHALL 採**前綴詞界**
比對：比對詞 SHALL 命中以該詞起始的完整單字，SHALL NOT 命中詞中出現該序列的單字。

比對過程 SHALL NOT 涉及 AI。

#### Scenario: 命中詞形變化
- **WHEN** 比對詞為 `mow`
- **THEN** `mower`、`mowing`、`mowed` SHALL 命中

#### Scenario: 不命中詞中序列
- **WHEN** 比對詞為 `ion`
- **THEN** `invention`、`consumption`、`application` SHALL NOT 命中

#### Scenario: 比對欄位缺值不致誤判
- **WHEN** 某專利的三個比對欄位皆為空
- **THEN** 該專利 SHALL NOT 命中，且系統 SHALL 可列出此類專利數量供揭露

### Requirement: PRE-004 命中結果於套用前可預覽

系統 SHALL 在使用者確認比對詞的同時，逐筆關鍵字顯示預估命中件數。

使用者 SHALL 可在看到命中件數後才決定是否套用。

#### Scenario: 套用前先看到影響
- **WHEN** 使用者完成比對詞確認畫面
- **THEN** 每筆關鍵字 SHALL 顯示其命中件數，且總計 SHALL 於套用按鈕上可見

#### Scenario: 零命中須明示
- **WHEN** 某筆關鍵字命中 0 件
- **THEN** 系統 SHALL 顯示 0 而非省略該列

### Requirement: PRE-008 AI 對命中專利提供留或剔的建議

系統 SHALL 針對比對命中的專利，由 AI 依該專利的**標題、摘要與獨立項**內容，
對照整批專利的技術範圍，提出「保留」或「剔除」的建議與理由。

AI 產出 SHALL 僅為建議：SHALL NOT 直接改變任何專利的排除狀態，
使用者 SHALL 仍逐筆確認。

⚠ 建議 SHALL 與比對分離：比對是確定性運算（PRE-003），建議是 AI 判讀；
兩者 SHALL 分別可追溯——使用者要分得出「為什麼被抓到」與「為什麼建議剔除」。

⚠ 判讀依據 SHALL 限於該專利的標題、摘要與獨立項，以及整批專利的範圍描述；
SHALL NOT 依賴分群結果——初階篩選發生在分群之前。

#### Scenario: 依三個欄位判讀

- **WHEN** AI 對某個命中專利提出建議
- **THEN** 判讀依據 SHALL 為該專利的標題、摘要與獨立項
- **AND** 建議 SHALL 說明該專利與整批專利範圍的關係

#### Scenario: 建議不改變狀態

- **WHEN** AI 完成建議
- **THEN** 該專利 SHALL 仍為待裁決狀態
- **AND** SHALL NOT 因建議而被排除或保留

#### Scenario: 建議與命中原因分別呈現

- **WHEN** 使用者檢視待裁決清單
- **THEN** SHALL 可分別看到「命中的關鍵字與比對詞」與「AI 的建議與理由」

#### Scenario: 建議不可用時不阻斷

- **WHEN** AI 建議失敗或尚未產生
- **THEN** 待裁決清單 SHALL 仍可正常呈現與裁決
- **AND** SHALL 明確標示該筆尚無建議，SHALL NOT 以空白混充為「建議保留」

#### Scenario: 三個欄位皆空者

- **WHEN** 某命中專利的標題、摘要與獨立項皆為空
- **THEN** 系統 SHALL 標示無判讀依據
- **AND** SHALL NOT 產生沒有根據的建議

### Requirement: PRE-005 剔除一律由使用者裁決

命中的專利 SHALL 進入待裁決狀態，且待裁決狀態 SHALL NOT 影響分析母體。

只有使用者明確裁決「剔除」後，專利才 SHALL 進入封存狀態。

系統 SHALL 支援逐筆與批次裁決，且 SHALL 記錄每筆的命中來源關鍵字。

#### Scenario: 待裁決不影響分析
- **WHEN** 專利處於待裁決狀態而執行分群
- **THEN** 該專利 SHALL 仍列入分群母體

#### Scenario: 保留裁決
- **WHEN** 使用者對待裁決專利裁決「保留」
- **THEN** 該專利 SHALL 回到一般狀態，且 SHALL NOT 於相同關鍵字下再次列為待裁決

#### Scenario: 可追溯命中原因
- **WHEN** 使用者檢視某筆待裁決專利
- **THEN** 系統 SHALL 顯示它是被哪一筆關鍵字的哪個比對詞命中

### Requirement: PRE-006 封存語意

專利經使用者裁決剔除後 SHALL 立即進入封存狀態。

封存狀態的專利：
- SHALL NOT 進入任何分析母體（分群、報表、門檻推導）
- SHALL NOT 出現在瀏覽專利清單
- SHALL 出現在剔除名單且 SHALL 可還原
- 專利資料本體 SHALL 保留

#### Scenario: 封存後退出分析
- **WHEN** 專利被封存後重新執行分群
- **THEN** 分群母體件數 SHALL 等於 workspace 成員數減去已封存數

#### Scenario: 封存後不在瀏覽清單
- **WHEN** 使用者瀏覽專利清單
- **THEN** 已封存專利 SHALL NOT 出現

#### Scenario: 可還原
- **WHEN** 使用者於剔除名單對已封存專利執行還原
- **THEN** 該專利 SHALL 重新出現在瀏覽清單，且 SHALL 重新計入分析母體

### Requirement: PRE-007 保留期與硬刪

封存滿保留期（一年）的專利 SHALL 成為硬刪對象。

硬刪 SHALL 提供 dry-run，且 dry-run SHALL NOT 變更任何資料。

硬刪 SHALL 一併清除無外鍵保護的引用：workspace 成員名單與剔除名單中的對應項目。

硬刪 SHALL 標記受影響的既有報表版本為「來源已不完整」。

#### Scenario: 未滿保留期不刪
- **WHEN** 專利封存未滿一年
- **THEN** 硬刪 SHALL NOT 將其列入刪除對象

#### Scenario: dry-run 不改資料
- **WHEN** 以 dry-run 執行硬刪
- **THEN** 系統 SHALL 列出將被刪除的專利，且資料庫內容 SHALL 完全不變

#### Scenario: 引用一併清除
- **WHEN** 專利被硬刪
- **THEN** 所有 workspace 成員名單 SHALL NOT 再含該專利識別碼，且剔除名單 SHALL NOT 留下對應項目

#### Scenario: 舊報表標記
- **WHEN** 被硬刪的專利曾包含在某報表版本的母體中
- **THEN** 該報表版本 SHALL 被標記為「來源已不完整」，且使用者檢視該版本時 SHALL 看得到此標記

#### Scenario: 失敗隔離
- **WHEN** 硬刪批次中某筆失敗
- **THEN** 其餘筆 SHALL 不受影響，且系統 SHALL 逐筆回報結果
