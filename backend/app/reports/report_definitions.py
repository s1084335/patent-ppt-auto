from __future__ import annotations

from dataclasses import dataclass


# IPC／CPC 代碼格式：一律 [A-H] 開頭（八大部；CPC 另有 Y 部，一併放行）。
# 洛迦諾分類是 NN-NN 純數字，用開頭字元即可穩定辨別，不需維護代碼清單。
IPC_LIKE_PATTERN = "^[A-HY]"


def is_ipc_like(value: str | None) -> bool:
    """是否為 IPC／CPC 代碼（非洛迦諾等其他分類體系）。"""
    text = (value or "").strip()
    return bool(text) and text[0].upper() in "ABCDEFGHY"


@dataclass(frozen=True)
class ReportDefinition:
    name: str
    report_type: str
    label: str
    # 中文標題：報表輸出（圖表標題、index 章節、PPT）一律用這個；label 保留英文供內部/log 用
    label_zh: str
    source_table: str
    columns: tuple[str, ...]
    group_by: tuple[str, ...] = ()
    count_column: str = "patent_id"
    default_order: tuple[tuple[str, str], ...] = ()
    default_limit: int | None = None
    exclude_blank_columns: tuple[str, ...] = ()
    # 值格式白名單：(欄名, POSIX 正規式)，只收符合格式的列。
    # 2026-07-28 動因：WIPS 把外觀設計的**洛迦諾分類**（21-02／19-07）塞進與 IPC
    # 同一個「Orig. IPC(Main)」欄，報表沒分辨就一起統計——圖上多出兩個假 subclass，
    # 且 60 筆裡有 11 筆不是發明專利的 IPC，集中度佔比被稀釋。
    # 設計為通用欄位＋正規式（非寫死 IPC），其他欄位日後遇到同類混入可比照使用。
    value_pattern_columns: tuple[tuple[str, str], ...] = ()
    # 來源表沒有 patent_id 欄（如家族×國家表）時設 False：
    # run_report 收到 patent_ids 會 fail loud，analysis_runner 會跳過此報表。
    # 前端版面（2026-07-29 使用者定案「年度矩陣可以和其他種類報表的版面不同」）：
    #   side_by_side＝左數據右圖表 45/55（一般報表）
    #   stacked     ＝上下排列（年度矩陣：交叉表欄多列少，橫向需要空間）
    layout: str = "side_by_side"
    # 需要市場資料才能產（2026-07-29 使用者定案）：痛點四象限的 Y 軸是痛點嚴重度，
    # 無市場資料時全部落 unknown，整張圖沒有判讀價值。前端據此禁用選項。
    # ⚠ 主題統計表與機會四象限**不需要**——使用者明示「可以選」。
    requires_market_data: bool = False
    supports_patent_ids: bool = True
    allowed_filter_columns: tuple[str, ...] = ()
    # aggregate 型報表的額外聚合欄：(函式, 來源欄, 輸出別名)。
    # 函式白名單見 report_engine.AGGREGATE_FUNCTIONS（sum / count_distinct / avg / max）。
    # 例：(("sum", "(F1)引用文獻數", "cited_total"),) → COALESCE(SUM("(F1)引用文獻數"), 0) AS cited_total
    aggregates: tuple[tuple[str, str, str], ...] = ()
    # 資料來源備註：cluster 型報表用來標明分群／市場線等外部依賴（如「待市場線痛點資料」）；
    # 只作說明用途，不影響引擎行為。
    data_source_note: str = ""


REPORT_SOURCE_TABLE = "derived_layer.report_patent_base"

# 申請人展開視圖（0042，2026-07-29 使用者定案）：共同申請人 `A | B` 拆成兩列，
# 各自計數。**只給三個申請人報表用**——其餘報表必須維持一專利一列的 base，
# 否則專利總數會從 60 變成 74（重複計數）。
# ⚠ 件數總和大於專利總數是刻意的（使用者確認為專利分析慣例），報表需加註。
APPLICANT_EXPANDED_TABLE = "derived_layer.report_patent_applicant_expanded"

REPORT_DEFINITIONS: dict[str, ReportDefinition] = {
    "application_trend": ReportDefinition(
        name="application_trend",
        report_type="aggregate",
        label="Patent Application Trend",
        label_zh="專利申請趨勢",
        source_table=REPORT_SOURCE_TABLE,
        columns=("application_year",),
        group_by=("application_year",),
        default_order=(("application_year", "asc"),),
        exclude_blank_columns=("application_year",),
        allowed_filter_columns=(
            "patent_id",
            "application_year",
            "country_code",
            "Orig. IPC(Main)",
            "Orig. CPC(Main)",
            "applicant_display_name",
            "current_assignee_display_name",
        ),
    ),
    "publication_trend": ReportDefinition(
        name="publication_trend",
        report_type="aggregate",
        label="Patent Publication Trend",
        label_zh="專利公告趨勢",
        source_table=REPORT_SOURCE_TABLE,
        columns=("publication_year",),
        group_by=("publication_year",),
        default_order=(("publication_year", "asc"),),
        exclude_blank_columns=("publication_year",),
        allowed_filter_columns=(
            "patent_id",
            "publication_year",
            "country_code",
            "Orig. IPC(Main)",
            "Orig. CPC(Main)",
            "applicant_display_name",
            "current_assignee_display_name",
        ),
    ),
    "country_distribution": ReportDefinition(
        name="country_distribution",
        report_type="aggregate",
        label="Patent Jurisdiction Distribution",
        label_zh="專利受理局分布",
        source_table=REPORT_SOURCE_TABLE,
        columns=("country_code",),
        group_by=("country_code",),
        default_order=(("patent_count", "desc"), ("country_code", "asc")),
        exclude_blank_columns=("country_code",),
    ),
    # 公司×國家交叉表：申請人（100% 覆蓋口徑）×受理局（按件、含死案）。
    # 圖表預設取前 20 大公司；正式流程由使用者給「追蹤公司清單」，以
    # filters {"applicant_display_name": {"values": [...]}} 圈定後出圖與報告。
    "applicant_country_distribution": ReportDefinition(
        name="applicant_country_distribution",
        report_type="aggregate",
        label="Applicant × Jurisdiction Matrix",
        label_zh="公司×國家交叉表",
        source_table=APPLICANT_EXPANDED_TABLE,  # 0042：共同申請人各自計數
        columns=("applicant_display_name", "country_code"),
        group_by=("applicant_display_name", "country_code"),
        default_order=(
            ("patent_count", "desc"),
            ("applicant_display_name", "asc"),
            ("country_code", "asc"),
        ),
        exclude_blank_columns=("applicant_display_name", "country_code"),
    ),
    "ipc_main_distribution": ReportDefinition(
        name="ipc_main_distribution",
        report_type="aggregate",
        label="IPC Classification Distribution",
        label_zh="IPC 主分類分布",
        source_table=REPORT_SOURCE_TABLE,
        columns=("Orig. IPC(Main)",),
        group_by=("Orig. IPC(Main)",),
        default_order=(("patent_count", "desc"), ("Orig. IPC(Main)", "asc")),
        exclude_blank_columns=("Orig. IPC(Main)",),
        # 排除洛迦諾分類（外觀設計的 21-02／19-07 等）——非 IPC 體系，混入會產生
        # 假 subclass 並稀釋集中度佔比。見 IPC_LIKE_PATTERN。
        value_pattern_columns=(("Orig. IPC(Main)", IPC_LIKE_PATTERN),),
    ),
    "cpc_main_distribution": ReportDefinition(
        name="cpc_main_distribution",
        report_type="aggregate",
        label="CPC Classification Distribution",
        label_zh="CPC 主分類分布",
        source_table=REPORT_SOURCE_TABLE,
        columns=("Orig. CPC(Main)",),
        group_by=("Orig. CPC(Main)",),
        default_order=(("patent_count", "desc"), ("Orig. CPC(Main)", "asc")),
        exclude_blank_columns=("Orig. CPC(Main)",),
        # 同 IPC：CPC 欄同樣可能混入非 CPC 體系的代碼。
        value_pattern_columns=(("Orig. CPC(Main)", IPC_LIKE_PATTERN),),
    ),
    "applicant_ranking": ReportDefinition(
        name="applicant_ranking",
        report_type="aggregate",
        label="Top Patent Applicants",
        label_zh="主要申請人排名",
        source_table=APPLICANT_EXPANDED_TABLE,  # 0042：共同申請人各自計數
        columns=("applicant_display_name",),
        group_by=("applicant_display_name",),
        aggregates=(
            # _excl_group：申請人＝最新受讓人（未離手）不算轉讓（2026-07-22 使用者定案）
            ("count_nonblank_excl_group", "recent_assignee_display_name", "recent_assignee_count"),
            ("string_agg_distinct_nonblank_excl_group", "recent_assignee_display_name", "recent_assignee_display_names"),
            # 受讓取得（2026-07-29 A 方案）：反向計數——有多少專利的最新受讓人是本公司。
            # 上面兩欄是「轉出」方向；沒有這欄的話，受讓方那列看不到自己拿到幾件。
            ("count_as_value_of", "recent_assignee_display_name", "acquired_count"),
        ),
        default_order=(("patent_count", "desc"), ("applicant_display_name", "asc")),
        default_limit=100,
        exclude_blank_columns=("applicant_display_name",),
    ),
    "owner_ranking": ReportDefinition(
        name="owner_ranking",
        report_type="aggregate",
        label="Current Patent Assignee Ranking",
        label_zh="現專利權人排名",
        source_table=REPORT_SOURCE_TABLE,
        columns=("current_assignee_display_name",),
        group_by=("current_assignee_display_name",),
        default_order=(("patent_count", "desc"), ("current_assignee_display_name", "asc")),
        default_limit=100,
        exclude_blank_columns=("current_assignee_display_name",),
    ),
    "applicant_year_matrix": ReportDefinition(
        name="applicant_year_matrix",
        layout="stacked",  # 交叉表欄多，需滿寬
        report_type="aggregate",
        label="Applicant Year Matrix",
        label_zh="申請人年度專利分布矩陣",
        source_table=APPLICANT_EXPANDED_TABLE,  # 0042：共同申請人各自計數
        columns=("applicant_display_name", "application_year"),
        group_by=("applicant_display_name", "application_year"),
        default_order=(
            ("patent_count", "desc"),
            ("applicant_display_name", "asc"),
            ("application_year", "asc"),
        ),
        exclude_blank_columns=("applicant_display_name", "application_year"),
    ),
    "owner_year_matrix": ReportDefinition(
        name="owner_year_matrix",
        layout="stacked",  # 交叉表欄多，需滿寬
        report_type="aggregate",
        label="Owner Year Matrix",
        label_zh="專利權人年度布局矩陣",
        source_table=REPORT_SOURCE_TABLE,
        columns=("current_assignee_display_name", "application_year"),
        group_by=("current_assignee_display_name", "application_year"),
        default_order=(
            ("patent_count", "desc"),
            ("current_assignee_display_name", "asc"),
            ("application_year", "asc"),
        ),
        exclude_blank_columns=("current_assignee_display_name", "application_year"),
    ),
    # 生命週期：年度 × 申請人家數 vs 件數（技術生命週期判讀的標準圖）。
    "lifecycle": ReportDefinition(
        name="lifecycle",
        report_type="aggregate",
        label="Patent Lifecycle",
        label_zh="專利生命週期",
        source_table=REPORT_SOURCE_TABLE,
        columns=("application_year",),
        group_by=("application_year",),
        aggregates=(("count_distinct", "applicant_display_name", "applicant_count"),),
        default_order=(("application_year", "asc"),),
        exclude_blank_columns=("application_year",),
    ),
    # 國家佈局（現有保護口徑）：家族×國家 一列，COUNT(family_id) = 各國家族數。
    # rows 已按 (family_id, country_code) 去重，alias 沿用 patent_count 讓
    # choropleth/render 零改動複用；語意是「家族數」，見 docs/report_field_matrix.md。
    "family_country_layout": ReportDefinition(
        name="family_country_layout",
        report_type="aggregate",
        label="Family Country Layout (Active Protection)",
        label_zh="國家佈局（現有保護）",
        source_table="derived_layer.report_family_country",
        columns=("country_code",),
        group_by=("country_code",),
        count_column="family_id",
        default_order=(("patent_count", "desc"), ("country_code", "asc")),
        exclude_blank_columns=("country_code",),
        supports_patent_ids=False,
    ),
    # 家族品質明細：完整性核對與異常現形（不完整/生效程序進行中/unknown 狀態等）。
    "family_quality_detail": ReportDefinition(
        name="family_quality_detail",
        report_type="detail",
        label="Family Coverage Quality Detail",
        label_zh="家族完整性明細",
        source_table="derived_layer.report_family_quality",
        columns=(
            "family_id",
            "is_surrogate_family",
            "member_rows",
            "expected_counts_raw",
            "family_incomplete",
            "unknown_status_count",
            "pending_status_count",
            "ep_in_transition_count",
            "ep_missing_epc_count",
            "non_country_row_count",
        ),
        default_order=(("family_incomplete", "desc"), ("family_id", "asc")),
        supports_patent_ids=False,
    ),
    # ── 分群相關報表（report_type="cluster"）──────────────────────────────
    # 這三支不吃單表 SQL：資料源＝分群定案（topic_assignments→主題）JOIN 申請人，由
    # chart_runner 的 cluster_analytics section（吃注入的 cluster_data）出圖，不走
    # report_engine.build_report_sql。前端「報表種類」需列出它們，故在此註冊定義；
    # run_report/run_reports_batch 對 cluster 型一律跳過（回 skipped_reason），不進 SQL。
    #
    # cluster_topic_table：每主題一列＝件數＋獨立申請人廣度（關鍵欄，件數第一層、
    # 競爭者廣度才見結構）＋前三大申請人＋年份跨度。件數與獨立申請人數由
    # cluster_analytics.build_topic_effect_table 以 set 去重一次算完（非 N+1）；
    # 主題→專利歸屬取自 topic_assignments（見 topic_state_repository），不是
    # topic_state_json 的 patent_ids（後者在 repository 被覆寫丟棄）。
    "cluster_topic_table": ReportDefinition(
        name="cluster_topic_table",
        report_type="cluster",
        label="Cluster Topic Table",
        label_zh="主題分類統計表",
        source_table="",
        columns=(),
        supports_patent_ids=False,
        data_source_note="分群定案（topic_assignments→主題）JOIN report_patent_base 申請人；技術／功效兩通道各自主題",
    ),
    # opportunity_quadrant：四象限（x 專利密度、y 競爭者結構強度），中位數切象限。
    # 中位數門檻由 build_opportunity_matrix 一併回傳並隨 chart_rows 落 report_data.json
    # （報表可重現，不每次重算）。
    "opportunity_quadrant": ReportDefinition(
        name="opportunity_quadrant",
        report_type="cluster",
        label="Opportunity Quadrant",
        label_zh="機會四象限",
        source_table="",
        columns=(),
        supports_patent_ids=False,
        data_source_note="依 cluster_topic_table；x 專利密度、y 競爭者結構強度，中位數門檻入庫",
    ),
    # pain_point_quadrant：痛點 × 專利佈局交叉。痛點資料須走市場線（市場資料上傳→
    # AI 摘要→使用者確認），該線尚未實作；缺痛點資料時 build_pain_point_matrix 以空
    # pain_data 產出（severity 全部 unknown＝待調查灰帶），優雅回空、不假造痛點。
    "pain_point_quadrant": ReportDefinition(
        name="pain_point_quadrant",
        requires_market_data=True,
        report_type="cluster",
        label="Pain Point Quadrant",
        label_zh="痛點四象限",
        source_table="",
        columns=(),
        supports_patent_ids=False,
        data_source_note="待市場線痛點資料（市場資料上傳→AI 摘要→使用者確認）；缺資料時痛點軸全標待調查，不假造",
    ),
}


# 預設批次排除「需市場資料」的報表（2026-07-29 使用者定案「整個藏起來」）。
# 市場線（上傳→AI 摘要→使用者確認）尚未實作，缺資料時痛點軸全是「待調查」，
# 產出的圖看不出不完整、匯進 PPT 會被誤讀。定義本身保留，市場線做好後移除本過濾即可。
# ⚠ 前端 REPORT_TYPES 另有一份清單也要同步（同一概念兩處落點，由
# tests/test_report_types_frontend_backend_parity.py 鎖住）。
DEFAULT_REPORT_NAMES: tuple[str, ...] = tuple(
    name for name, definition in REPORT_DEFINITIONS.items()
    if not definition.requires_market_data
)


ALLOWED_FILTER_COLUMNS = {
    "patent_id",
    "application_year",
    "publication_year",
    "application_date",
    "country_code",
    "Orig. IPC(Main)",
    "Orig. CPC(Main)",
    "applicant_display_name",
    "current_assignee_display_name",
    "recent_assignee_display_name",
}


def allowed_filter_columns_for_report(definition: ReportDefinition) -> set[str]:
    """回傳單一報表可接受的 filter 欄位，供 API 與 report engine 共用。"""
    if definition.allowed_filter_columns:
        return set(definition.allowed_filter_columns)
    return set(ALLOWED_FILTER_COLUMNS)
