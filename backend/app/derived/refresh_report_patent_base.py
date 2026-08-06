from __future__ import annotations

import argparse
import json
from typing import Any


# 0021 起 derived_layer.report_patent_base 為相容 VIEW；重建須寫回實體表 legacy_0021.report_patent_base，
# 讀取端（報表引擎）仍走 derived_layer VIEW。VIEW 無法 TRUNCATE，故此處目標為 legacy_0021。
REFRESH_SQL = """
TRUNCATE TABLE legacy_0021.report_patent_base;

-- ⚠ 0046（2026-08-06）移除了 `source_one` CTE。它存在的唯一理由是「配對出該專利
-- 最新的 raw_record，好去 patent_attributes 取那一列」；欄位搬進 core table 後
-- 一專利一列，**沒有列要選了**，這個 CTE 與底下三個 attributes JOIN 一併消失。
WITH base AS (
    SELECT
        p.id AS patent_id,
        p."授權公告號",
        p."審查的公告號",
        p."未審查的公開號",
        p."申請號",
        p.country_code,
        p.application_date,
        p.application_year,
        p.publication_year,
        CASE
            WHEN BTRIM(COALESCE(p."授權公告日", '')) ~ '^[0-9]{4}'
            THEN SUBSTRING(BTRIM(p."授權公告日") FROM 1 FOR 4)::integer
        END AS "授權公告年",
        p.title,
        -- abstract（2026-07-28 補搬）：文獻備註第三級來源。外觀設計沒有任何權利要求
        -- （實測 CN 11 筆四欄全空），只有摘要 11/11、最長 530 字——沒有它那批專利
        -- 永遠沒備註，AI 補分也拿不到輸入。所有專利類型都有摘要，是通用保底。
        p.abstract,
        p."Orig. IPC(Main)",
        p."Orig. CPC(Main)",
        -- 現行分類（2026-07-28 補搬）：core_layer.patents 早有值（Curr. IPC 12 筆、
        -- Curr. CPC 19 筆）、legacy_0021 實體表也早有欄位，但這支 refresh 從未搬過，
        -- 導致 derived 兩欄恆為 NULL、報表永遠只能讀 Orig.。
        -- ⚠ 只搬 Main（單值）；All 是 ' | ' 分隔多值，混進這張一列一專利的寬表會讓
        -- group by 把整串當成一個分類，統計必錯——要用 All 需另立展開表。
        p."Curr. IPC(Main)",
        p."Curr. CPC(Main)",
        pp."申請人",
        pp."申請人國籍",
        pp."標準化申請人",
        pp."發明人",
        pp."發明人國籍",
        pp."最近專利權人[US,JP,KR,CN,CA,AU]",
        pp."標準當前專利權人[US,JP,KR,CN,CA,AU]",
        pp."最近受讓人[US,KR,CN]",
        pp."申請人代表碼",
        pp."標準當前專利權人代碼[US,JP,KR,CN,CA,AU]",
        p."主權項",
        p."獨立項[KR,JP,US,CN,EP,IN]",
        p."所有權利要求[JP,KR,CN]",
        p."WIPS同族ID",
        p.legal_status,
        -- 專利種類兩欄（0045，2026-08-06）：A4 設計案標示與專利種類維度用。
        -- ⚠ 這裡只**搬運**兩個原始欄，判定邏輯一律呼叫唯一定義處
        -- `backend/app/transforms/patent_kind.py`（`is_design()`／`patent_kind()`），
        -- 不得在此或任何消費端自行寫字面比對——散開後改一處另一處不會報錯。
        -- 那份模組也寫明了為何不能用 patent_type 判定（它天生只有 P／U 兩值，
        -- 11 件設計案全被歸進 P，實測發明實為 17 件而非 28）。
        p.patent_type,
        p.document_kind,
        p."WIPS同族各國家文獻數量(申請為準)",
        -- ⚠ EPC 有效／無效兩欄的**成對性**現由 migration 0046 的 `PAIRED_GROUPS` 保證
        -- （回填時一次從同一 raw_record 取整組）。原因不變：「空」本身是規則輸入
        -- ——剛授權判定靠無效國為空、到期判定靠有效國清空，兩欄若來自不同批次就算錯。
        p."EPC有效國家[EP]",
        p."EPC無效國家[EP]",
        -- 引用數是快照值，驗證純數字才轉型（非數字一律回 NULL，不讓髒值中斷整批 refresh）
        CASE WHEN BTRIM(COALESCE(p."(F1)引用文獻數", '')) ~ '^[0-9]+$'
             THEN BTRIM(p."(F1)引用文獻數")::integer END AS "(F1)引用文獻數",
        CASE WHEN BTRIM(COALESCE(p."(B1)引用文獻數", '')) ~ '^[0-9]+$'
             THEN BTRIM(p."(B1)引用文獻數")::integer END AS "(B1)引用文獻數",
        -- 發明人數在 patent_people（它是人員統計，不是專利屬性）
        CASE WHEN BTRIM(COALESCE(pp."發明人數", '')) ~ '^[0-9]+$'
             THEN BTRIM(pp."發明人數")::integer END AS "發明人數"
    FROM core_layer.patents p
    -- patent_people 的 PK 為 patent_id（一對一），LEFT JOIN 不放大列數。
    LEFT JOIN core_layer.patent_people pp ON pp.patent_id = p.id
),
-- 代碼→對照表公司名（2026-07-23 定案「申請人代碼是公司收斂的依據」）。
-- 對照表的正式名是人工裁決過的顯示名，優先級高於任何從專利資料統計出來的名稱。
-- 同一代碼在對照表可有多列（多個別稱），正式名理應一致；仍以 mode() 取最常見值
-- 並在平手時依名稱排序，確保 deterministic。只採 confirmed 列，未裁決的不參與收斂。
-- 這是一次 GROUP BY 掃描（代碼數量級，26 筆等級），之後以 hash join 掛回，非 N+1。
--
-- ⚠ 2026-07-28 四欄定案（使用者：「優先順序是顯示中文、沒中文才正規化，沒正規化才原值」）：
-- 對照表這層只有前兩段（中文名 → 英文正式名）；**第三段「原值」不在這裡**，
-- 它是專利本身的原始欄位，接在下方 applicant_display_name 的 COALESCE 末端。
-- 分層的理由：對照表兩欄都空的列根本不該參與收斂（WHERE 已濾掉），
-- 若在此處補原值當第三段，會讓「沒填名稱的空組」也搶到 mode() 名額。
-- mode() 作用在 COALESCE 之後，才不會出現「中文名跟英文名各自 mode 再拼」的錯配。
code_alias_names AS (
    SELECT
        NULLIF(BTRIM(ca."申請人代碼"), '') AS company_code,
        -- ⚠ 中文名**不走眾數**（2026-07-29 使用者實測抓到）：
        -- 中文名是人工裁決的結果，只要該代碼**有任何一列**填了就該用它，
        -- 不該被「有幾列填了」這種資料形狀決定。
        --
        -- 實測 UN226597 的 4 列：中文、英文、英文、中文 → mode() 2:2 平手，
        -- 平手取字母序而 'N' < '南' → **使用者填了中文卻顯示英文**。
        -- （成因是中文名只寫進 1 列、又被當別稱多寫 1 列，兩個寫入端缺陷，
        --   另有測試鎖住；但顯示端本來就不該賭眾數。）
        --
        -- 正規化名稱仍用 mode()：那是多個英文寫法擇一，眾數是對的口徑。
        COALESCE(
            MAX(NULLIF(BTRIM(ca."公司中文名稱"), '')),
            mode() WITHIN GROUP (ORDER BY NULLIF(BTRIM(ca."正規化名稱"), ''))
        ) AS company_name
    FROM derived_layer.company_aliases ca
    WHERE ca.review_status = 'confirmed'
      AND NULLIF(BTRIM(ca."申請人代碼"), '') IS NOT NULL
      AND COALESCE(
            NULLIF(BTRIM(ca."公司中文名稱"), ''),
            NULLIF(BTRIM(ca."正規化名稱"), '')
      ) IS NOT NULL
    GROUP BY NULLIF(BTRIM(ca."申請人代碼"), '')
),
-- 代碼對照：同一 WIPS 人名代碼（申請人代表碼／標準當前專利權人代碼）在庫內
-- 可能對到多種名稱寫法（跨檔匯出甚至標準化名也會漂移）。
-- 每個代碼選一種統一輸出：優先取最常見的標準化名，沒有就取最常見的原始名；
-- mode() 平手時依排序取第一個，結果 deterministic。
-- 排在 code_alias_names 之後、別稱之後——它是「庫內自我收斂」的保底，
-- 沒有人工裁決名時才用，避免未經裁決的統計名蓋掉對照表。
applicant_code_names AS (
    SELECT
        NULLIF(BTRIM(pp."申請人代表碼"), '') AS person_code,
        COALESCE(
            mode() WITHIN GROUP (ORDER BY NULLIF(BTRIM(pp."標準化申請人"), '')),
            mode() WITHIN GROUP (ORDER BY NULLIF(BTRIM(pp."申請人"), ''))
        ) AS canonical_name
    FROM core_layer.patent_people pp
    WHERE NULLIF(BTRIM(pp."申請人代表碼"), '') IS NOT NULL
    GROUP BY NULLIF(BTRIM(pp."申請人代表碼"), '')
),
-- 受讓人共用同一份代碼對照（2026-07-28 使用者定案「受讓人也是用我這套收斂」）。
--
-- ⚠ WIPS 匯出**沒有受讓人代碼欄**（實查只有「申请人名称标准化代码[JP]」與
-- 「标准当前专利权人代码[US,JP,KR,CN,CA,AU]」兩個人名代碼欄），故無法像另兩欄
-- 那樣直接以代碼 join。改為**以別稱字面反查代碼**：使用者在代碼區塊建立一組
-- （代碼＋正規化名＋N 變體）時，該組的每個變體都寫成 company_aliases 一列；
-- 受讓人欄的字面只要命中任一變體，就能反查到代碼、取得該代碼的公司名。
--
-- 與 assignee_alias（既有別稱 LATERAL）的差異：那支只回「該列自己的顯示名」，
-- 這支經由代碼再取一次，確保同一代碼下**所有變體**收斂到同一顯示名——即使
-- 使用者事後改了某一列的名稱，仍以代碼層的 mode() 為準，不會各列漂移。
--
-- 只採 confirmed（同 code_alias_names 護欄）：AI 草稿不得經此路徑滲進正式顯示名。
assignee_code_names AS (
    SELECT
        lower(regexp_replace(BTRIM(ca."別稱"), '\\s+', ' ', 'g')) AS alias_key,
        mode() WITHIN GROUP (ORDER BY can2.company_name) AS company_name
    FROM derived_layer.company_aliases ca
    JOIN code_alias_names can2
        ON can2.company_code = NULLIF(BTRIM(ca."申請人代碼"), '')
    WHERE ca.review_status = 'confirmed'
      AND NULLIF(BTRIM(ca."別稱"), '') IS NOT NULL
    GROUP BY lower(regexp_replace(BTRIM(ca."別稱"), '\\s+', ' ', 'g'))
),
owner_code_names AS (
    SELECT
        NULLIF(BTRIM(pp."標準當前專利權人代碼[US,JP,KR,CN,CA,AU]"), '') AS person_code,
        COALESCE(
            mode() WITHIN GROUP (ORDER BY NULLIF(BTRIM(pp."標準當前專利權人[US,JP,KR,CN,CA,AU]"), '')),
            mode() WITHIN GROUP (ORDER BY NULLIF(BTRIM(pp."最近專利權人[US,JP,KR,CN,CA,AU]"), ''))
        ) AS canonical_name
    FROM core_layer.patent_people pp
    WHERE NULLIF(BTRIM(pp."標準當前專利權人代碼[US,JP,KR,CN,CA,AU]"), '') IS NOT NULL
    GROUP BY NULLIF(BTRIM(pp."標準當前專利權人代碼[US,JP,KR,CN,CA,AU]"), '')
)
INSERT INTO legacy_0021.report_patent_base (
    patent_id,
    "授權公告號",
    "審查的公告號",
    "未審查的公開號",
    "申請號",
    country_code,
    application_date,
    application_year,
    publication_year,
    "授權公告年",
    title,
    abstract,
    "Orig. IPC(Main)",
    "Orig. CPC(Main)",
    "Curr. IPC(Main)",
    "Curr. CPC(Main)",
    "申請人",
    "申請人國籍",
    "標準化申請人",
    applicant_display_name,
    "發明人",
    "發明人國籍",
    "最近專利權人[US,JP,KR,CN,CA,AU]",
    "標準當前專利權人[US,JP,KR,CN,CA,AU]",
    current_assignee_display_name,
    "最近受讓人[US,KR,CN]",
    recent_assignee_display_name,
    "主權項",
    "獨立項[KR,JP,US,CN,EP,IN]",
    "所有權利要求[JP,KR,CN]",
    "比對用權利要求",
    "WIPS同族ID",
    legal_status,
    patent_type,
    document_kind,
    "WIPS同族各國家文獻數量(申請為準)",
    "EPC有效國家[EP]",
    "EPC無效國家[EP]",
    "(F1)引用文獻數",
    "(B1)引用文獻數",
    "發明人數"
)
SELECT
    b.patent_id,
    b."授權公告號",
    b."審查的公告號",
    b."未審查的公開號",
    b."申請號",
    b.country_code,
    b.application_date,
    b.application_year,
    b.publication_year,
    b."授權公告年",
    b.title,
    b.abstract,
    b."Orig. IPC(Main)",
    b."Orig. CPC(Main)",
    b."Curr. IPC(Main)",
    b."Curr. CPC(Main)",
    b."申請人",
    b."申請人國籍",
    b."標準化申請人",
    -- 收斂順位：代碼對照表 > 別稱對照表 > 庫內代碼統計名 > 標準化申請人 > 申請人。
    -- 代碼命中即優先，確保同一代碼的所有專利落到同一顯示名（＝代碼是收斂依據）。
    --
    -- ⚠ 2026-07-28 使用者定案「顯示只取主申請人」：WIPS 以 ` | ` 分隔多申請人，
    -- 這裡一律 `split_part(..., '|', 1)` 取第一個（WIPS 慣例第一個是主申請人）。
    -- **原始欄位（上方 b."申請人" 等）保留完整字面不動**，只有顯示名與比對取主值。
    -- 不取主值的後果：含 `|` 的 25 筆（實測）拿整串去比對別稱表，永遠對不上任何
    -- 別稱——那些專利的公司收斂**完全失效**，且不報錯。
    COALESCE(acan.company_name, applicant_alias.display_name, acn.canonical_name, NULLIF(BTRIM(split_part(COALESCE(b."標準化申請人", ''), '|', 1)), ''), NULLIF(BTRIM(split_part(COALESCE(b."申請人", ''), '|', 1)), '')) AS applicant_display_name,
    b."發明人",
    b."發明人國籍",
    b."最近專利權人[US,JP,KR,CN,CA,AU]",
    b."標準當前專利權人[US,JP,KR,CN,CA,AU]",
    -- 同上，以「標準當前專利權人代碼」為收斂依據。
    COALESCE(ocan.company_name, owner_alias.display_name, ocn.canonical_name, NULLIF(BTRIM(split_part(COALESCE(b."標準當前專利權人[US,JP,KR,CN,CA,AU]", ''), '|', 1)), ''), NULLIF(BTRIM(split_part(COALESCE(b."最近專利權人[US,JP,KR,CN,CA,AU]", ''), '|', 1)), '')) AS current_assignee_display_name,
    b."最近受讓人[US,KR,CN]",
    -- 最近受讓人在 WIPS 匯出無對應代碼欄（mappings/wips.py 僅有申請人代表碼與
    -- 標準當前專利權人代碼兩個代碼欄）。2026-07-28 起改以「別稱字面反查代碼」補上
    -- 代碼層（assignee_code_names），使受讓人與另兩欄共用同一份使用者對照表；
    -- 來源日後若真的新增受讓人代碼欄，可改為直接 join，語意不變。
    -- 順位與另兩欄一致：代碼對照 > 別稱對照 > 原始字面。代碼是使用者的裁決依據，
    -- 優先級最高（見 assignee_code_names 的說明——受讓人以別稱反查代碼）。
    COALESCE(acan_assignee.company_name, assignee_alias.display_name, NULLIF(BTRIM(split_part(COALESCE(b."最近受讓人[US,KR,CN]", ''), '|', 1)), '')) AS recent_assignee_display_name,
    b."主權項",
    b."獨立項[KR,JP,US,CN,EP,IN]",
    b."所有權利要求[JP,KR,CN]",
    COALESCE(NULLIF(BTRIM(b."獨立項[KR,JP,US,CN,EP,IN]"), ''), NULLIF(BTRIM(b."主權項"), ''), NULLIF(BTRIM(b."所有權利要求[JP,KR,CN]"), '')) AS "比對用權利要求",
    b."WIPS同族ID",
    b.legal_status,
    b.patent_type,
    b.document_kind,
    b."WIPS同族各國家文獻數量(申請為準)",
    b."EPC有效國家[EP]",
    b."EPC無效國家[EP]",
    b."(F1)引用文獻數",
    b."(B1)引用文獻數",
    b."發明人數"
FROM base b
LEFT JOIN LATERAL (
    -- 顯示順位同 code_alias_names（使用者第①點）：中文 → 英文正式名 → 舊欄。
    SELECT COALESCE(NULLIF(BTRIM(ca."公司中文名稱"), ''), NULLIF(BTRIM(ca."正規化名稱"), '')) AS display_name
    FROM derived_layer.company_aliases ca
    -- 只採 confirmed：未裁決／AI 草稿（ai_suggested）不得經別稱路徑滲進正式顯示名。
    -- 沿 code_alias_names 同一護欄；缺這行時 keep_original 草稿的「別稱」＝英文原文，
    -- 會被下面的字面比對命中（2026-07-26 修）。
    WHERE ca.review_status = 'confirmed'
      AND lower(regexp_replace(BTRIM(ca."別稱"), '\\s+', ' ', 'g')) IN (
        lower(regexp_replace(BTRIM(split_part(COALESCE(b."標準化申請人", ''), '|', 1)), '\\s+', ' ', 'g')),
        lower(regexp_replace(BTRIM(split_part(COALESCE(b."申請人", ''), '|', 1)), '\\s+', ' ', 'g'))
    )
    ORDER BY
        -- ⚠ 有填中文名的列優先（2026-07-29）：同一代碼的多個變體列，
        -- 只有部分列填了中文名（寫入端只更新命中的那一列）。若照 id 排，
        -- 可能取到中文欄為空的列 → 顯示英文，使用者以為中文名沒生效。
        CASE WHEN NULLIF(BTRIM(ca."公司中文名稱"), '') IS NOT NULL THEN 0 ELSE 1 END,
        CASE
            WHEN lower(regexp_replace(BTRIM(ca."別稱"), '\\s+', ' ', 'g')) = lower(regexp_replace(BTRIM(split_part(COALESCE(b."標準化申請人", ''), '|', 1)), '\\s+', ' ', 'g')) THEN 1
            ELSE 2
        END,
        ca.id
    LIMIT 1
) applicant_alias ON true
LEFT JOIN LATERAL (
    -- 顯示順位同 code_alias_names：中文名 → 英文正式名（原值那段在外層 COALESCE）。
    SELECT COALESCE(NULLIF(BTRIM(ca."公司中文名稱"), ''), NULLIF(BTRIM(ca."正規化名稱"), '')) AS display_name
    FROM derived_layer.company_aliases ca
    -- 只採 confirmed（同 applicant_alias 理由）。
    WHERE ca.review_status = 'confirmed'
      AND lower(regexp_replace(BTRIM(ca."別稱"), '\\s+', ' ', 'g')) IN (
        lower(regexp_replace(BTRIM(split_part(COALESCE(b."標準當前專利權人[US,JP,KR,CN,CA,AU]", ''), '|', 1)), '\\s+', ' ', 'g')),
        lower(regexp_replace(BTRIM(split_part(COALESCE(b."最近專利權人[US,JP,KR,CN,CA,AU]", ''), '|', 1)), '\\s+', ' ', 'g'))
    )
    ORDER BY
        -- ⚠ 有填中文名的列優先（2026-07-29）：同一代碼的多個變體列，
        -- 只有部分列填了中文名（寫入端只更新命中的那一列）。若照 id 排，
        -- 可能取到中文欄為空的列 → 顯示英文，使用者以為中文名沒生效。
        CASE WHEN NULLIF(BTRIM(ca."公司中文名稱"), '') IS NOT NULL THEN 0 ELSE 1 END,
        CASE
            WHEN lower(regexp_replace(BTRIM(ca."別稱"), '\\s+', ' ', 'g')) = lower(regexp_replace(BTRIM(split_part(COALESCE(b."標準當前專利權人[US,JP,KR,CN,CA,AU]", ''), '|', 1)), '\\s+', ' ', 'g')) THEN 1
            ELSE 2
        END,
        ca.id
    LIMIT 1
) owner_alias ON true
LEFT JOIN LATERAL (
    -- 顯示順位同 code_alias_names：中文名 → 英文正式名（原值那段在外層 COALESCE）。
    SELECT COALESCE(NULLIF(BTRIM(ca."公司中文名稱"), ''), NULLIF(BTRIM(ca."正規化名稱"), '')) AS display_name
    FROM derived_layer.company_aliases ca
    -- 只採 confirmed（同 applicant_alias 理由）。受讓人無代碼欄，只有這條別稱路徑，
    -- 缺護欄時草稿是**唯一**能命中的來源，風險比另兩欄更高。
    WHERE ca.review_status = 'confirmed'
      AND lower(regexp_replace(BTRIM(ca."別稱"), '\\s+', ' ', 'g')) = lower(regexp_replace(BTRIM(split_part(COALESCE(b."最近受讓人[US,KR,CN]", ''), '|', 1)), '\\s+', ' ', 'g'))
    -- 有填中文名的列優先（同上兩處 LATERAL 的理由）
    ORDER BY CASE WHEN NULLIF(BTRIM(ca."公司中文名稱"), '') IS NOT NULL THEN 0 ELSE 1 END, ca.id
    LIMIT 1
) assignee_alias ON true
LEFT JOIN applicant_code_names acn
    ON acn.person_code = NULLIF(BTRIM(b."申請人代表碼"), '')
LEFT JOIN owner_code_names ocn
    ON ocn.person_code = NULLIF(BTRIM(b."標準當前專利權人代碼[US,JP,KR,CN,CA,AU]"), '')
-- 代碼→人工裁決公司名：兩個 CTE 都是代碼層級的小集合，equi-join 走 hash join，
-- 每筆專利仍是常數成本，不引入 N+1（對照 LATERAL 別稱 join 每列各查一次）。
LEFT JOIN code_alias_names acan
    ON acan.company_code = NULLIF(BTRIM(b."申請人代表碼"), '')
LEFT JOIN code_alias_names ocan
    ON ocan.company_code = NULLIF(BTRIM(b."標準當前專利權人代碼[US,JP,KR,CN,CA,AU]"), '')
-- 受讓人以「別稱字面 → 代碼 → 公司名」反查（WIPS 無受讓人代碼欄）。
-- equi-join 走 hash join，別稱集合是小表，不引入 N+1。
LEFT JOIN assignee_code_names acan_assignee
    ON acan_assignee.alias_key = lower(regexp_replace(BTRIM(split_part(COALESCE(b."最近受讓人[US,KR,CN]", ''), '|', 1)), '\\s+', ' ', 'g'));
"""


def refresh_report_patent_base() -> dict[str, Any]:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required for database refresh. Install psycopg[binary].") from exc

    from backend.app.db.connection import get_connection_kwargs

    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(REFRESH_SQL)
            cur.execute("SELECT count(*) FROM derived_layer.report_patent_base")
            row_count = cur.fetchone()[0]
        conn.commit()
    return {"status": "refreshed", "report_patent_base_rows": row_count}


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh derived_layer.report_patent_base from raw/core tables.")
    parser.parse_args()
    summary = refresh_report_patent_base()
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
