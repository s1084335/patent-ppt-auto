"""Schema 註解：dialect 中立的唯一來源 ＋ PG／SQL Server 雙 emitter。

用途：把每張表/欄的「語意說明」集中在 `COMMENTS` 一個 dict（單一來源），
由 emitter 依目標資料庫產生對應 DDL——PostgreSQL 發 `COMMENT ON`、
SQL Server 發 extended property（`sp_addextendedproperty`，`MS_Description`）。
最終目的地為 SQL Server（decisions.md 2026-07-17 定案修正），移植時只換
emitter，`COMMENTS` 內容一字不改。

撰寫規則：
- 描述「語意」（欄位業務意義），不描述「引擎機制」——不寫「部分索引」「generated
  STORED」「JSONB」這類 PG 專有措辭，讓同一份文字在 SQL Server 也精確。
- 中文說明用途，API／欄位代碼／專有名詞保留英文。

範圍（scope A）：app_layer／derived_layer 每欄；core_layer／raw_layer 僅表級。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


# 標記為 VIEW 的物件：PG 用 COMMENT ON VIEW；SQL Server level1type='VIEW'。
VIEWS: set[str] = {
    "derived_layer.v_unmapped_company_names",
    "core_layer.patent_source_summary",
}


# schema.table -> {"__table__": 表說明, 欄名: 欄說明, ...}
# 只列 __table__ 的表＝僅表級註解（core_layer／raw_layer）。
COMMENTS: dict[str, dict[str, str]] = {
    # ── app_layer：應用/操作層 ─────────────────────────────
    "app_layer.processing_jobs": {
        "__table__": "worker 工作佇列：一列一個待辦/執行中/已結束的背景工作，兼存狀態與結果",
        "job_id": "工作編號",
        "job_type": "工作類型，例如分群校準、定案、增量、報表產生",
        "status": "工作狀態：待領取/執行中/成功/失敗/取消",
        "workspace_id": "所屬 workspace；工作與 workspace 無關時為空",
        "payload_json": "工作輸入參數",
        "result_json": "工作產出結果，成功後寫入",
        "progress_percent": "執行進度百分比（0-100）",
        "current_stage": "目前執行階段的可讀標籤",
        "idempotency_key": "冪等鍵；同一鍵只會有一筆工作",
        "attempt_count": "已嘗試執行次數",
        "max_attempts": "允許的最大嘗試次數，超過即判失敗",
        "locked_by": "目前持有此工作的 worker 識別碼",
        "locked_at": "本次領取的時間",
        "heartbeat_at": "worker 最後心跳時間，用於逾時回收",
        "error_message": "失敗時的可讀錯誤說明",
        "created_at": "建立時間",
        "started_at": "首次開始執行時間",
        "finished_at": "結束時間（成功或失敗）",
    },
    "app_layer.company_normalization_tasks": {
        "__table__": "公司/專利權人待補全任務清單：對照表未覆蓋的公司名，供 WIPS 查詢補全",
        "task_id": "任務編號",
        "raw_name": "待正規化的原始公司名",
        "lookup_key": "原始公司名的正規化比對鍵",
        "source_fields_json": "此名稱出現在哪些專利欄位",
        "patent_count": "此名稱涉及的專利件數",
        "status": "任務狀態：待處理/已查詢/待審/已匯入/查無結果/略過",
        "query_string": "實際送 WIPS 的查詢字串",
        "wips_result_json": "WIPS 查詢回來的結果",
        "note": "備註",
        "created_at": "建立時間",
        "updated_at": "更新時間",
        "resolved_at": "結案時間",
    },
    "app_layer.analysis_runs": {
        "__table__": "分析執行紀錄：一次分析的根紀錄，凍結該次的篩選與專利集合快照",
        "analysis_id": "分析編號",
        "analysis_name": "分析名稱",
        "analysis_type": "分析類型，例如報表、侵權比對",
        "status": "分析狀態：待處理/執行中/完成/失敗",
        "filter_json": "定義此分析的篩選條件",
        "parameters_json": "執行參數",
        "selected_patent_ids_json": "此分析凍結的專利集合快照",
        "error_message": "失敗原因",
        "created_at": "建立時間",
        "completed_at": "完成時間",
    },
    "app_layer.analysis_outputs": {
        "__table__": "分析數據產出：一次分析各報表的結果，一報表一列",
        "output_id": "產出編號",
        "analysis_id": "所屬分析",
        "output_type": "產出類型，例如報表數據",
        "output_name": "產出名稱，通常為報表代碼",
        "result_json": "報表數據結果",
        "ai_model": "產出若由 AI 生成，記錄使用的模型",
        "prompt_version": "產出若由 AI 生成，記錄 prompt 版本",
        "created_at": "建立時間",
    },
    "app_layer.export_runs": {
        "__table__": "檔案產出登錄：一次分析產生的每個檔案（報表/圖表/未來簡報），含路徑與雜湊",
        "export_id": "產出檔編號",
        "analysis_id": "所屬分析",
        "export_type": "檔案類型，例如報表 HTML、圖表 SVG、資料 JSON",
        "file_path": "檔案位置",
        "file_hash": "檔案 sha256，用於完整性與去重",
        "parameters_json": "產生此檔案的參數",
        "created_at": "建立時間",
    },
    "app_layer.workspaces": {
        "__table__": "分群 workspace：一組專利的分群工作空間",
        "workspace_id": "workspace 編號",
        "workspace_name": "workspace 名稱",
        "description": "說明",
        "status": "狀態，例如啟用、封存",
        "filter_json": "建立時的篩選條件",
        "parameters_json": "分群參數",
        "created_by": "建立者",
        "created_at": "建立時間",
        "updated_at": "更新時間",
        "archived_at": "封存時間",
    },
    "app_layer.workspace_patents": {
        "__table__": "workspace 與專利的成員關係：哪些專利屬於某個 workspace",
        "workspace_id": "所屬 workspace",
        "patent_id": "專利編號",
        "source_type": "加入來源，例如人工加入",
        "source_ref": "來源參照",
        "added_by": "加入者",
        "added_at": "加入時間",
    },
    # ── derived_layer：衍生計算層 ──────────────────────────
    "derived_layer.company_aliases": {
        "__table__": "公司/專利權人對照表（唯一一張）：以別稱對應標準公司名，報表統計以此正規化",
        "id": "對照編號",
        "申請人代碼": "WIPS 標準專利權人/申請人代碼",
        "公司名稱": "正規化後的標準公司名",
        "別稱": "對應到同一公司的名稱變體",
        "source_file": "來源檔案",
        "imported_at": "匯入時間",
        "alias_lookup_key": "別稱正規化後的比對鍵，與報表統計採同一口徑",
        "source_type": "對照來源：Excel 種子/WIPS 查詢/人工",
        "review_status": "審核狀態：已確認生效；待審用於暫存歧義",
        "wips_metadata_json": "WIPS 查詢的來源與時間等佐證",
        "updated_at": "更新時間",
    },
    "derived_layer.report_patent_base": {
        "__table__": "報表底表：一列一專利、攤平報表所需欄位；多數統計報表以此為來源，需 refresh 重建",
        "patent_id": "專利編號（對應核心專利主表）",
        "授權公告號": "各國授權公告號",
        "審查的公告號": "核准/審查公告號",
        "未審查的公開號": "早期公開號",
        "未審查的公開號(轉換後)": "早期公開號的標準化轉換值",
        "申請號": "申請號原值",
        "申請號(轉換後)": "申請號標準化轉換值（如台灣年號換算）",
        "country_code": "受理局國別",
        "application_date": "申請日",
        "application_year": "申請年",
        "publication_year": "公告/公開年",
        "title": "專利名稱",
        "Curr. IPC(Main)": "現行 IPC 主分類",
        "Curr. CPC(Main)": "現行 CPC 主分類",
        "申請人": "申請人原始名",
        "申請人國籍": "申請人國別",
        "標準化申請人": "WIPS 標準化申請人名",
        "applicant_display_name": "報表統計用申請人顯示名（經對照表/代碼正規化）",
        "發明人": "發明人",
        "發明人國籍": "發明人國別",
        "最近專利權人[US,JP,KR,CN,CA,AU]": "最近專利權人",
        "標準當前專利權人[US,JP,KR,CN,CA,AU]": "WIPS 標準當前專利權人",
        "current_assignee_display_name": "報表統計用現專利權人顯示名（正規化後）",
        "最近受讓人[US,KR,CN]": "最近受讓人",
        "recent_assignee_display_name": "報表統計用受讓人顯示名（正規化後）",
        "主權項": "主要獨立請求項文本",
        "獨立項[KR,JP,US,CN,EP,IN]": "各國獨立項文本",
        "所有權利要求[JP,KR,CN]": "全部請求項文本",
        "比對用權利要求": "分群/比對用請求項（取獨立項、主權項或全部請求項的第一個有值者）",
        "WIPS同族ID": "WIPS 專利家族識別碼",
        "legal_status": "正規化後的法律狀態",
        "WIPS同族各國家文獻數量(申請為準)": "家族各國文獻數量",
        "EPC有效國家[EP]": "EPC 生效國",
        "EPC無效國家[EP]": "EPC 失效國",
        "(F1)引用文獻數": "引用文獻數量（WIPS F1 欄快照）",
        "(B1)引用文獻數": "引用文獻數量（WIPS B1 欄快照）",
        "發明人數": "發明人數量",
    },
    "derived_layer.report_family_country": {
        "__table__": "家族×國家佈局底表：每個專利家族在各受理局的分布，供家族層級報表",
        "family_id": "家族識別碼（單件無家族者以代理鍵表示）",
        "country_code": "家族成員所屬受理局",
        "direct_patent_count": "該國直接專利件數",
        "via_ep_count": "經 EP 進入該國的件數",
        "family_incomplete": "此家族資料是否不完整",
        "is_surrogate_family": "是否為單件代理家族（無 WIPS 家族 id）",
    },
    "derived_layer.report_family_quality": {
        "__table__": "家族品質底表：各家族的完整度與狀態統計，供家族資料品質檢視",
        "family_id": "家族識別碼",
        "is_surrogate_family": "是否為單件代理家族",
        "member_rows": "家族成員列數",
        "expected_counts_raw": "家族各國預期數量的原始值",
        "family_incomplete": "此家族是否不完整",
        "incomplete_detail_json": "不完整的明細",
        "unknown_status_count": "法律狀態未知的件數",
        "pending_status_count": "審查中的件數",
        "ep_in_transition_count": "EP 進入各國轉換階段的件數",
        "ep_missing_epc_count": "EP 缺生效國資訊的件數",
        "non_country_row_count": "非國別匯總列數（如 WO/EP 匯總）",
        "refreshed_at": "本表刷新時間",
    },
    "derived_layer.topics": {
        "__table__": "分群主題：某 workspace 某通道的永久主題（含合併/復原後的穩定身分）",
        "topic_id": "主題編號",
        "workspace_id": "所屬 workspace",
        "source_field": "分群通道：技術（獨立項）或功效（效果摘要）",
        "created_run_id": "建立此主題的執行",
        "topic_code": "主題代碼",
        "model_topic_ids": "對應的模型主題編號（合併後可能多個）",
        "topic_kind": "主題種類：模型主題或系統其他桶",
        "doc_count": "主題目前的專利件數",
        "coherence": "主題凝聚度指標",
        "diversity": "主題多樣性指標",
        "balance": "主題平衡度指標",
        "keywords_json": "主題關鍵字（供人工掃描）",
        "representative_patent_ids_json": "代表性專利清單（供標籤/摘要參考）",
        "label": "主題名稱",
        "summary": "主題摘要",
        "label_source": "名稱來源：AI 產生、人工、或程式後備",
        "label_metadata_json": "名稱相關的附屬資訊",
        "display_order": "前端顯示排序",
        "status": "主題狀態：啟用、已合併、已復原",
        "merged_into_topic_id": "被合併進哪個主題",
        "merged_by": "合併操作者",
        "merged_at": "合併時間",
        "created_at": "建立時間",
        "updated_at": "更新時間",
        "reverted_by": "復原操作者",
        "reverted_at": "復原時間",
    },
    "derived_layer.topic_runs": {
        "__table__": "分群執行紀錄：校準/定案/增量/合併/復原各次執行與其模型 artifact",
        "run_id": "執行編號",
        "workspace_id": "所屬 workspace",
        "source_field": "分群通道",
        "run_mode": "執行模式：全量/增量/合併/復原",
        "previous_run_id": "前一次執行",
        "status": "執行狀態",
        "input_doc_count": "輸入文件數",
        "new_doc_count": "本次新增文件數",
        "topic_count": "產出主題數",
        "parameters_json": "執行參數",
        "metrics_json": "執行指標",
        "model_artifact_path": "模型 artifact 檔案位置",
        "model_artifact_hash": "模型 artifact 雜湊",
        "error_message": "失敗原因",
        "created_at": "建立時間",
        "completed_at": "完成時間",
        "artifact_version": "artifact 版本序",
        "updated_at": "更新時間",
        "reverted_at": "此執行被復原的時間",
        "reverted_by": "復原操作者",
        "reverted_by_run_id": "執行哪次復原的紀錄",
    },
    "derived_layer.topic_candidates": {
        "__table__": "候選主題數方案：校準時掃描不同主題數產生的候選，供使用者定案",
        "candidate_id": "候選編號",
        "run_id": "所屬執行",
        "candidate_type": "候選型態：保守/平衡/細分",
        "candidate_k": "候選的主題數",
        "coherence": "凝聚度指標",
        "diversity": "多樣性指標",
        "balance": "平衡度指標",
        "score": "綜合分數（僅排序輔助，不代使用者定案）",
        "parameters_json": "候選參數",
        "llm_explanation": "AI 對此候選方案的差異說明",
        "is_selected": "是否為使用者選定的方案",
        "selected_by": "選定者",
        "selected_at": "選定時間",
        "created_at": "建立時間",
    },
    "derived_layer.topic_assignments": {
        "__table__": "專利主題歸屬：每件專利在某通道被指派到哪個主題",
        "assignment_id": "歸屬編號",
        "workspace_id": "所屬 workspace",
        "source_field": "分群通道",
        "patent_id": "專利編號",
        "topic_id": "指派到的主題",
        "assigned_run_id": "做出此指派的執行",
        "distance_to_centroid": "與主題中心的距離",
        "is_current": "是否為目前有效的指派",
        "created_at": "建立時間",
    },
    "derived_layer.v_unmapped_company_names": {
        "__table__": "未映射公司名檢視：對照表尚未覆蓋的公司名，依出現專利數排序，供 WIPS 補全優先查",
        "lookup_key": "公司名的正規化比對鍵",
        "sample_raw_name": "此鍵的一個原始公司名樣本",
        "source_fields": "此名稱出現在哪些專利欄位",
        "patent_count": "涉及的專利件數",
    },
    # ── core_layer：核心專利資料（僅表級；不可被下游改動）──
    "core_layer.patents": {
        "__table__": "核心專利主表：清洗後的正式專利值，下游只讀不改",
    },
    "core_layer.patent_people": {
        "__table__": "專利人物/公司欄位：申請人、發明人、專利權人、受讓人及其代碼與國別",
    },
    "core_layer.patent_sources": {
        "__table__": "專利與原始匯入紀錄的來源對應；主鍵 (patent_id, raw_record_id)，不用 surrogate id 或 dedupe_key",
    },
    "core_layer.patent_attributes": {
        "__table__": "專利屬性快照：引用數、發明人數、家族數量、EPC 生效/失效國等隨匯入變動的欄位",
    },
    "core_layer.patent_technical_embeddings": {
        "__table__": "技術通道向量：專利獨立項文本的 embedding，供分群使用",
    },
    "core_layer.patent_effect_embeddings": {
        "__table__": "功效通道向量：專利效果摘要文本的 embedding，供分群使用",
    },
    "core_layer.patent_source_summary": {
        "__table__": "每件專利的來源檔案彙總檢視：列出該專利來自哪些匯入檔（來源系統/檔名/雜湊/匯入時間）",
    },
    # ── raw_layer：原始匯入層（僅表級）───────────────────
    "raw_layer.raw_records": {
        "__table__": "原始匯入紀錄：每列保存來源檔的整筆原始資料，不做任何清洗",
    },
    "raw_layer.source_files": {
        "__table__": "匯入來源檔案登錄：記錄每個匯入檔的來源與匯入資訊",
    },
}


def _escape_pg(text: str) -> str:
    """PostgreSQL 字串常值：單引號跳脫為兩個單引號。"""
    return text.replace("'", "''")


def _emit_pg(qualified: str, column: str | None, text: str) -> str:
    """產生一句 PostgreSQL COMMENT ON。column 為 None 代表表/view 級。"""
    schema, table = qualified.split(".")
    body = _escape_pg(text)
    if column is None:
        obj = "VIEW" if qualified in VIEWS else "TABLE"
        return f"COMMENT ON {obj} {schema}.{table} IS '{body}'"
    return f'COMMENT ON COLUMN {schema}.{table}."{column}" IS \'{body}\''


def _emit_mssql(qualified: str, column: str | None, text: str) -> str:
    """產生一句 SQL Server extended property（MS_Description）。

    SQL Server 無 COMMENT ON，改以 sp_addextendedproperty 掛 MS_Description；
    level0=SCHEMA、level1=TABLE/VIEW、level2=COLUMN。實際移植時若屬性已存在，
    需改用 sp_updateextendedproperty；此處產生 add 版本，emit 前可先行清除。
    """
    schema, table = qualified.split(".")
    body = text.replace("'", "''")
    level1 = "VIEW" if qualified in VIEWS else "TABLE"
    parts = [
        "EXEC sp_addextendedproperty",
        f"@name=N'MS_Description', @value=N'{body}'",
        f"@level0type=N'SCHEMA', @level0name=N'{schema}'",
        f"@level1type=N'{level1}', @level1name=N'{table}'",
    ]
    if column is not None:
        parts.append(f"@level2type=N'COLUMN', @level2name=N'{column}'")
    return ", ".join(parts)


def emit(
    dialect: str,
    *,
    include: "Callable[[str, str | None], bool] | None" = None,
) -> list[str]:
    """依目標資料庫產生所有註解 DDL；內容來自 COMMENTS，兩庫共用。

    include：可選過濾器 `(qualified, column_or_None) -> bool`，回 False 者略過。
    供歷史 migration 只對「當下已存在的物件」下註解，避免引用尚未建立（未來
    migration 才建）的 table/column 而失敗。
    """
    if dialect not in ("postgresql", "mssql"):
        raise ValueError(f"unsupported dialect: {dialect}")
    emitter = _emit_pg if dialect == "postgresql" else _emit_mssql
    statements: list[str] = []
    for qualified, cols in COMMENTS.items():
        for name, text in cols.items():
            column = None if name == "__table__" else name
            if include is not None and not include(qualified, column):
                continue
            statements.append(emitter(qualified, column, text))
    return statements


def _emit_pg_clear(qualified: str, column: str | None) -> str:
    """PostgreSQL 移除註解：IS NULL。"""
    schema, table = qualified.split(".")
    if column is None:
        obj = "VIEW" if qualified in VIEWS else "TABLE"
        return f"COMMENT ON {obj} {schema}.{table} IS NULL"
    return f'COMMENT ON COLUMN {schema}.{table}."{column}" IS NULL'


def _emit_mssql_clear(qualified: str, column: str | None) -> str:
    """SQL Server 移除註解：sp_dropextendedproperty。"""
    schema, table = qualified.split(".")
    level1 = "VIEW" if qualified in VIEWS else "TABLE"
    parts = [
        "EXEC sp_dropextendedproperty @name=N'MS_Description'",
        f"@level0type=N'SCHEMA', @level0name=N'{schema}'",
        f"@level1type=N'{level1}', @level1name=N'{table}'",
    ]
    if column is not None:
        parts.append(f"@level2type=N'COLUMN', @level2name=N'{column}'")
    return ", ".join(parts)


def emit_clear(
    dialect: str,
    *,
    include: "Callable[[str, str | None], bool] | None" = None,
) -> list[str]:
    """產生移除所有註解的 DDL（供 migration downgrade）。include 同 emit。"""
    if dialect not in ("postgresql", "mssql"):
        raise ValueError(f"unsupported dialect: {dialect}")
    emitter = _emit_pg_clear if dialect == "postgresql" else _emit_mssql_clear
    statements: list[str] = []
    for qualified, cols in COMMENTS.items():
        for name in cols:
            column = None if name == "__table__" else name
            if include is not None and not include(qualified, column):
                continue
            statements.append(emitter(qualified, column))
    return statements


def validate_against_db(conn: Any) -> dict[str, list[str]]:
    """對照實際 DB：找出 dict 寫錯的欄名（typo）與應註而漏註的欄位（覆蓋率）。

    回傳：
    - missing_in_db：dict 有、DB 卻不存在的 table/column（typo 或已改名）。
    - uncommented_columns：app_layer/derived_layer 存在、dict 未涵蓋的欄位。
    只檢查結構，不寫入任何東西。
    """
    missing: list[str] = []
    for qualified, cols in COMMENTS.items():
        schema, table = qualified.split(".")
        exists = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name=%s "
            "UNION ALL SELECT 1 FROM information_schema.views "
            "WHERE table_schema=%s AND table_name=%s",
            (schema, table, schema, table),
        ).fetchone()
        if not exists:
            missing.append(f"{qualified} (table/view 不存在)")
            continue
        db_cols = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s",
                (schema, table),
            ).fetchall()
        }
        for name in cols:
            if name == "__table__":
                continue
            if name not in db_cols:
                missing.append(f"{qualified}.{name}")

    # 覆蓋率：只要求 app_layer/derived_layer 逐欄
    uncommented: list[str] = []
    covered = {
        f"{q}.{c}" for q, cols in COMMENTS.items() for c in cols if c != "__table__"
    }
    rows = conn.execute(
        "SELECT table_schema, table_name, column_name "
        "FROM information_schema.columns "
        "WHERE table_schema IN ('app_layer','derived_layer') "
        "ORDER BY table_schema, table_name, ordinal_position"
    ).fetchall()
    for schema, table, column in rows:
        key = f"{schema}.{table}.{column}"
        if key not in covered:
            uncommented.append(key)

    return {"missing_in_db": missing, "uncommented_columns": uncommented}
