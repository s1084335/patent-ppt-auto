"""專利分析報告 PPTX 產生器 v3（deterministic，不呼叫 AI）。

設計原則
--------
- **AI 只產文案，不碰數字、不碰排版**：所有數字取自 `report_data.json`，本程式不推算、
  不四捨五入、不捏造；版型由使用者經 `layout_overrides` 挑，AI 不生成版型。
- **`theme.json` 是版面座標、字級、配色的唯一來源**：renderer 內不得出現座標數字字面值
  （`tests/test_ppt_geometry_single_source.py` 以 AST 契約測試把關）。前端投影片縮圖
  預覽也讀同一份 geometry，避免座標分岔。
- **圖檔一律走 `artifact_manifest.json` 反查**：實際檔名（`ipc_main_distribution_L4.svg`、
  `jurisdiction_distribution.svg`、`annual_trend.svg`）與 report_key 不同名，用
  `{report_key}.svg` 猜必錯，故禁止猜檔名。
- **每頁必有視覺元素**：找不到圖就降級 `stat_callout`（用該報表 rows 取關鍵數字），
  不印「（圖檔待產出）」這類佔位文字。
- **成對報表不合成同一張圖**：IPC/CPC 的 L4 與 L5、機會矩陣的技術面與功效面，
  預設同頁左右並排（`comparison`），使用者可改成分頁。
- **產後自檢**：組完逐 shape 檢查超界、邊距不足、文字疊文字與文字裝不下，
  結果寫入 manifest `warnings[]`，不靜默。

可攜獨立執行（不 import 主專案任何模組）：
    uv run --no-project --with python-pptx --with pymupdf --python 3.12 \
        python build_ppt.py --report-dir <報表版本目錄> \
        --approvals <approvals.json> --output-dir <輸出目錄>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml import parse_xml
from pptx.oxml.ns import nsdecls, qn
from pptx.util import Emu, Inches, Pt

# ⚠ 同層模組匯入：本檔有兩種載入方式——直接執行（uv run 本檔）與以檔案路徑載入
# （backend 的 ai_report_ppt_runner._load_builder 用 spec_from_file_location）。
# 後者不會把本檔所在目錄放進 sys.path，`from starfield import ...` 會 ImportError。
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from starfield import starfield_png  # noqa: E402  （須在 sys.path 補完之後）

SKILL_ROOT = Path(__file__).resolve().parent.parent
THEME_PATH = SKILL_ROOT / "theme.json"

# 圖檔副檔名白名單：artifact_manifest 內混有 report_data／report_html，只取得出圖的。
IMAGE_SUFFIXES = (".svg", ".png", ".jpg", ".jpeg")


# --------------------------------------------------------------------------
# 內容設定表（非座標；座標一律在 theme.json）
# 報表增減、成對呈現、圖例編碼、警語只改本區的表，不動 renderer。
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class PageSpec:
    """單頁版型定義。

    page：頁碼（1-based，展開後重新連號）。
    kind：組版樣式，決定用哪個 renderer。
    title：實際印出的標題（判讀式，＝「{報表主題}：{headline}」）。
    topic：報表主題（標題前半，供缺 headline 時單獨使用）。
    report_keys：本頁引用的 report_data report_key，依序取用。
    charts：本頁配圖檔名，**只能來自 artifact_manifest 反查**，不得猜檔名。
    slots：本頁需 AI 產的文案槽；v3 只有 cover.title 與 direction.body。
    is_appendix：附錄段顯式旗標，動態插頁靠它找錨點，不用頁碼魔術數字。
    degraded_from：若因缺圖降級，記錄原本的 kind，寫進 manifest 供追溯。
    """

    page: int
    kind: str
    title: str
    topic: str = ""
    report_keys: tuple[str, ...] = ()
    charts: tuple[str, ...] = ()
    slots: tuple[str, ...] = ()
    subtitle: str = ""
    is_appendix: bool = False
    degraded_from: str = ""
    # 列過濾（P1-3）：成對報表以「rows 的欄位值」拆頁時（主題分布依 source_field
    # 分技術／功效兩張表），該頁只取匹配的列。空 dict＝不過濾。
    # ⚠ 用 tuple of pairs 保持 frozen dataclass 可雜湊；讀取端用 dict(spec.row_filter)。
    row_filter: tuple[tuple[str, str], ...] = ()


def _spec_with(spec: PageSpec, **changes: Any) -> PageSpec:
    """PageSpec 是 frozen，改欄位一律走這裡建新物件。"""
    fields = {
        "page": spec.page,
        "kind": spec.kind,
        "title": spec.title,
        "topic": spec.topic,
        "report_keys": spec.report_keys,
        "charts": spec.charts,
        "slots": spec.slots,
        "subtitle": spec.subtitle,
        "is_appendix": spec.is_appendix,
        "degraded_from": spec.degraded_from,
        "row_filter": spec.row_filter,
    }
    fields.update(changes)
    return PageSpec(**fields)


# 基礎大綱：報告敘事主線。其餘有資料的報表由 `_expand_page_layout` 自動插在附錄前。
# ⚠ 頁序即論證鏈（P1-6，2026-07-31 使用者定案）：範圍 → 證據（時間→空間→技術→競爭→
# 機會）→ **結論（研發方向建議）壓軸** → 附錄。研發方向是吸收全部證據後的收尾，
# 不是開場白；動態插頁也算證據，一律插在它之前。
PAGE_LAYOUT: tuple[PageSpec, ...] = (
    PageSpec(page=1, kind="cover", title="專利情報整合分析", topic="專利情報整合分析",
             report_keys=("application_trend", "country_distribution")),
    PageSpec(page=2, kind="chart_hero", title="申請趨勢", topic="申請趨勢",
             report_keys=("application_trend", "publication_trend")),
    PageSpec(page=3, kind="percentage_bars", title="保護地域分布", topic="保護地域分布",
             report_keys=("country_distribution",)),
    # IPC＋CPC 同頁對照（2026-07-31）：原本只掛 ipc，cpc 落到動態插頁、與 ipc 隔開
    # 好幾頁——註解寫著「IPC/CPC 維持同頁比較」但實作沒做到，這裡補齊。
    PageSpec(page=4, kind="comparison", title="技術分類布局", topic="技術分類布局",
             report_keys=("ipc_main_distribution", "cpc_main_distribution")),
    # 主題分布：rows 帶 source_field 兩通道，展開時依通道拆成兩張表格頁（P1-3）。
    PageSpec(page=5, kind="table_with_points", title="技術主題分布", topic="技術主題分布",
             report_keys=("cluster_topic_table",)),
    PageSpec(page=6, kind="chart_hero", title="競爭者佈局", topic="競爭者佈局",
             report_keys=("applicant_ranking", "owner_ranking")),
    PageSpec(page=7, kind="comparison", title="機會評估", topic="機會評估",
             report_keys=("opportunity_quadrant",)),
    PageSpec(page=8, kind="direction", title="研發方向建議", topic="研發方向建議",
             slots=("direction.body",)),
    PageSpec(page=9, kind="table", title="附錄1：全分類技術指標總表", topic="全分類技術指標總表",
             report_keys=("cluster_topic_table",), is_appendix=True),
    PageSpec(page=10, kind="table", title="附錄2：主要專利權人與申請人", topic="主要專利權人與申請人",
             report_keys=("applicant_ranking", "owner_ranking"), is_appendix=True),
)

# 論證順序（2026-07-31 使用者定案的 17 頁大綱）：範圍 → 時間 → 空間 → 技術 →
# 競爭 → 機會 → 結論 → 附錄。
#
# ⚠ 問題背景：`PAGE_LAYOUT` 原本只固定 8 個 report_key，介面上其餘 7 種報表
# 全部落入動態插頁，順序由 `_iter_report_entries` 決定＝**引擎輸出什麼順序就什麼
# 順序**，整批塞在結論前。使用者：「我沒有看到我們的內容大綱怎安排」——論證鏈
# 只有頭尾編排過，中段是未編排的填充（CPC 與 IPC 隔開、年度矩陣與競爭者佈局分家）。
#
# ⚠ 為什麼不是把這 7 張寫成 PAGE_LAYOUT 固定條目：那樣得連 `kind` 一起寫死，
# 就失去 `_kind_for_report` 依「報表型別＋實際圖檔數」判定版型的能力（同一張報表
# 有圖沒圖該用的版型不同）。故只編排**位置**，版型仍自動判定；未列在此的報表
# （日後新增的）照舊自動出頁，排在已知證據之後、結論之前，不會漏。
EVIDENCE_ORDER: tuple[str, ...] = (
    "application_trend", "publication_trend", "lifecycle",              # 時間
    "country_distribution", "applicant_country_distribution",           # 空間
    "family_country_layout",
    "ipc_main_distribution", "cpc_main_distribution", "cluster_topic_table",  # 技術
    "applicant_ranking", "owner_ranking",                               # 競爭
    "applicant_year_matrix", "owner_year_matrix",
    "opportunity_quadrant", "pain_point_quadrant",                      # 機會
)

# 不進 PPT 的報表（2026-07-31 使用者定案）：家族完整性明細屬資料品質稽核，
# 不是給決策者看的證據；對照 297 期論述與割草機範例，附錄也沒有這張。
# ⚠ 報表頁仍照常產出，只是不上簡報。
EXCLUDED_FROM_PPT = frozenset({"family_quality_detail"})

# 截斷時優先切在這些標點之後（見 `_truncate_to_width`）：斷在標點像「話沒說完」，
# 斷在字中間像「字被砍掉」，後者會讓讀者以為產檔壞了。
TRUNCATE_BREAK_MARKS = ("，", "、", "；", "：", "。", "）", ",", ";")

# 背景層（漸層底＋星空紋理）的 shape 名稱：版面自檢與 QA 用它排除全出血元素。
BACKGROUND_SHAPE_NAME = "space-background"

# 向量圖 part 的流水號：`PackURI` 必須唯一，同名會讓後插入的圖覆蓋前一張。
# 逐頁遞增、順序固定，故同一份報表重跑產出的檔案仍完全一致。
_VECTOR_INDEX = 0

# 依通道拆頁的報表（P1-3）：rows 的哪個欄位分通道、各通道的顯示名。
# ⚠ 主題分布不走「多圖拆頁」（它根本沒圖）——是**依列值**拆成兩張表格頁。
# 通道 → 解讀變體鍵（2026-07-31）：主題統計表拆成技術／功效兩頁後，各取各的解讀。
# ⚠ 上游 `chart_runner` 必須宣告同名 variant，否則這裡對不到、兩頁又會共用同一段。
CHANNEL_NARRATIVE_VARIANTS: dict[str, str] = {
    "wips_independent_claims": "topic_table_tech",
    "effect_summary": "topic_table_effect",
}

CHANNEL_SPLIT_REPORTS: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "cluster_topic_table": ("source_field", (
        ("wips_independent_claims", "技術主題分布"),
        ("effect_summary", "功效主題分布"),
    )),
}

# 表格欄位的顯示對照——⚠ **僅作舊報表版本的 fallback**（2026-07-31 起）。
#
# 正式來源是引擎寫進 `report_data.json["table_display"]` 的那份（欄名對照＋逐報表
# 排除欄）。本檔這份留著只為相容：`table_display` 是新加的鍵，先前已產出的報表版本
# 沒有它，缺了會讓簡報印出原始英文欄名。
#
# ⚠ 為什麼曾經對不上：這裡自建一份、引擎另有一份，兩邊各自演進。實測差異包括
#   `top3_share` 引擎叫「前三大占比(%)」這裡叫「前三大集中度(%)」、
#   `max_share` 引擎叫「最大一家(%)」這裡叫「最大占比(%)」、
#   `current_assignee_display_name` 這裡根本沒有（寫成 owner_display_name）→ 印英文；
#   排除欄更誇張：引擎排 4 欄、這裡只排 1 欄，使用者要求拿掉的「龍頭涉入」還在簡報上。
# 新增欄位請改引擎那份，不要往這裡加。
TABLE_COLUMN_LABELS: dict[str, str] = {
    "label": "主題",
    "source_field": "通道",
    "patent_count": "專利件數",
    "applicant_count": "申請人家數",
    "top3_share": "前三大集中度(%)",
    "max_share": "最大占比(%)",
    "top_applicants": "前三大申請人",
    "year_span": "年份跨度",
    "applicant_display_name": "申請人",
    "owner_display_name": "專利權人",
    "application_year": "申請年",
    "授權公告年": "授權公告年",
    "leading_applicant_count": "龍頭涉入數",
    "leading_applicants_involved": "龍頭涉入名單",
    "quadrant": "象限",
}
TABLE_EXCLUDED_COLUMNS = frozenset({"topic_code"})
TABLE_VALUE_LABELS: dict[str, dict[str, str]] = {
    "source_field": {"wips_independent_claims": "技術", "effect_summary": "功效"},
}

# narrative 別名（P1-2）：解讀掛點與 report_key 不同名時的對照。
# 機會矩陣的解讀由 ai:narrative 掛在 cluster section 的 opportunity_* 變體
# （sections 結構），而 PPT 端用 report bucket 的 opportunity_quadrant——兩個
# key 空間的歷史不一致，這裡以「report:variant」語法橋接。
NARRATIVE_ALIASES: dict[str, tuple[str, ...]] = {
    "opportunity_quadrant": ("cluster_topic_table:opportunity_tech",
                             "cluster_topic_table:opportunity_effect"),
    "opportunity_quadrant_tech": ("cluster_topic_table:opportunity_tech",),
    "opportunity_quadrant_effect": ("cluster_topic_table:opportunity_effect",),
}

# 成對報表預設呈現：預設同頁左右並排；列在這裡的改成分頁。
# - cluster_topic_table：表格內容多、並排讀不動（依通道列值拆）。
# - opportunity_quadrant：2026-07-31 使用者二輪回饋「同頁比較圖太小」——象限圖
#   資訊密度高，一圖一頁（chart_hero 大圖）才看得清。IPC/CPC 維持同頁比較。
SPLIT_PAIR_REPORTS = frozenset({"cluster_topic_table", "opportunity_quadrant"})

# 只上主圖的報表（2026-07-31 使用者定案）：年度矩陣只用前 10 名主表那張，
# 「更多」長尾圖不上 PPT（報表頁仍看得到，PPT 給決策者看主要玩家就夠）。
MAIN_CHART_ONLY_REPORTS = frozenset({"applicant_year_matrix", "owner_year_matrix"})


def _filter_report_charts(report_keys: tuple[str, ...], files: tuple[str, ...]) -> tuple[str, ...]:
    """套用「只上主圖」規則：MAIN_CHART_ONLY_REPORTS 的 `_more` 變體圖不上 PPT。"""
    if not any(key in MAIN_CHART_ONLY_REPORTS for key in report_keys):
        return files
    return tuple(name for name in files if "_more" not in name)

# 成對圖的左右順序偏好：L4 在 L5 前、技術面在功效面前；其餘照檔名排序保持 deterministic。
CHART_ORDER_HINTS = ("_L4", "_L5", "_tech", "_effect", "_more")

# 成對圖的子標，讓使用者一眼知道左右差在哪（禁止合成一張圖的配套說明）。
CHART_VARIANT_LABELS = {
    "_L4": "4 階細分類（L4）",
    "_L5": "5 階細分類（L5）",
    "_tech": "技術面",
    "_effect": "功效面",
    "_more": "（續）",
}

# 圖例編碼說明：放圖表右上一行小字，說明視覺編碼怎麼讀。
ENCODING_NOTES = {
    "application_trend": "條長＝當年申請件數｜橫軸＝申請年",
    "publication_trend": "條長＝當年公告件數｜橫軸＝公告年",
    "country_distribution": "條長＝件數佔比｜數值＝實際件數",
    "ipc_main_distribution": "條長＝件數｜左右為不同階層，非同圖合成",
    "cpc_main_distribution": "條長＝件數｜左右為不同階層，非同圖合成",
    "opportunity_quadrant": "橫軸＝申請人家數｜縱軸＝專利件數｜點＝技術主題",
    "cluster_topic_table": "條長＝主題件數｜家數＝投入該主題的申請人數",
    "applicant_ranking": "條長＝件數｜排序＝件數由高至低",
    "owner_ranking": "條長＝件數｜排序＝件數由高至低",
    "applicant_country_distribution": "格值＝件數｜列＝申請人、欄＝受理國",
    "applicant_year_matrix": "格值＝件數｜列＝申請人、欄＝申請年",
    "owner_year_matrix": "格值＝件數｜列＝專利權人、欄＝申請年",
    "lifecycle": "面積／條長＝當年件數｜橫軸＝申請年",
    "family_country_layout": "條長＝家族成員件數｜分組＝受理國",
}
DEFAULT_ENCODING_NOTE = "條長＝件數｜數值取自報表引擎"

# 警語在要點清單裡的固定 label。`_trim_blocks` 認這個字保護它不被裁切，
# 故必須是常數而非各處寫死的字面（改字面時裁切保護才不會默默失效）。
CAVEAT_LABEL = "判讀限制"

# 方法論警語：只寫判讀限制，不寫系統狀態；沒有列在這裡的頁面不硬生警語框。
CAVEATS = {
    "application_trend": "最近 1–2 個申請年件數偏低多為新案審查中的資料截止效應，非活動衰退。",
    "opportunity_quadrant": "象限以件數與申請人家數的相對門檻切分，屬相對位置判讀，不代表技術優劣；低密度區需人工覆核代表專利相關性。",
    "cluster_topic_table": "分群標籤僅供辨識，技術優劣與可商品化程度未經驗證，需人工覆核代表專利。",
    "applicant_ranking": "申請人家數只反映競爭者是否已進場，不等於市占、營收或產品核心度。",
    "owner_ranking": "名單反映權利持有結構，不代表合作、授權或併購關係。",
    "country_distribution": "受理國分布反映保護範圍，不能推論銷售市場或需求大小。",
}

# narrative fallback：舊格式只有長文 text，依這些段落標記切成可讀條列。
NARRATIVE_MARKERS = (
    "觀察", "現況", "意涵", "後續檢視點", "決策提醒", "後續", "結論邊界", "限制", "建議",
)

# 關鍵數字粗體：把數字（含常見量詞）從敘述中切出來單獨加粗。
NUMBER_PATTERN = re.compile(r"(\d[\d,]*(?:\.\d+)?\s*(?:%|件|家|年|項|個)?)")

# P1-10（2026-07-31 使用者定案）：來源註**只留可追溯資訊**（來源／期間／版本）。
# 舊版逐頁蓋「不保證」式免責章——數字是引擎確定性統計、預覽閘門本身就是人工定稿，
# 再蓋章等於否定定稿流程；不確定性提醒收斂到判讀限制框（該頁需要才出現）。


# 一條要點在版面上預估佔幾行（含 label 那截）。用來把「可用行數」換算成「可寫幾條」。
NARRATIVE_POINT_LINES = 2


def _points_area(theme: Theme, kind: str) -> tuple[float, float, int] | None:
    """該版型放要點的區域：(寬, 高, 欄數)；沒有要點區的版型回 None。"""
    if kind == "chart_hero":
        g = theme.geometry["chart_hero"]
        inset = g["panel_inset_in"]
        return (g["panel_width_in"] - inset - inset,
                g["panel_height_in"] - g["panel_text_top_offset_in"] - inset, 1)
    if kind == "table_with_points":
        g = theme.geometry["table_with_points"]
        inset = g["points_band_inset_in"]
        columns = int(g["points_band_columns"])
        gap = g["points_band_column_gap_in"]
        width = (g["points_band_width_in"] - inset - inset - gap * (columns - 1)) / columns
        return (width,
                g["points_band_height_in"] - g["points_band_text_top_offset_in"] - inset, columns)
    if kind == "comparison":
        g = theme.geometry["comparison"]
        return (g["column_width_in"] - g["points_inset_right_in"],
                g["points_height_in"] - g["points_top_offset_in"] - g["points_bottom_pad_in"], 1)
    # ⚠ `table`（附錄）不在此列：附錄頁**不放要點**（`_render_table` 沒有要點區），
    # 誤把它算進來會用附錄的幾何覆蓋掉同一張報表在內頁的真實容量。
    if kind in {"chart_with_points", "percentage_bars", "stat_callout"}:
        g = theme.geometry["points_panel"]
        return (g["width_in"] - g["text_inset_right_in"],
                g["height_in"] - g["text_top_offset_in"] - g["text_bottom_pad_in"], 1)
    return None


def narrative_capacity(theme: Theme | None = None) -> dict[str, dict[str, int]]:
    """每張報表的要點區實際容量：{report_key: {max_points, max_chars}}。

    ⚠ 為什麼要這個（2026-07-31）：解讀 CLI 原本只拿到一組全域上限（4–7 條 ×55 字），
    一刀切。但 `chart_hero` 的右側直欄與無圖表格頁的底部橫幅**可用空間差很多**，
    CLI 盲寫、`_trim_blocks` 事後裁切——截斷就是這樣來的。

    這裡由 `PAGE_LAYOUT`（哪張報表上哪種版型）＋ `theme.json` 幾何（那種版型的
    要點區多大）算出真實容量，**不新增任何常數**。撰寫（prompt）、驗證（validator）
    與裁切（_trim_blocks）三處因此吃同一份數字，不會再出現「說 55 字、實際只放得下
    40」的錯位。
    """
    theme = theme or Theme.load()
    size = theme.size("point_text_pt")
    capacity: dict[str, dict[str, int]] = {}
    for spec in PAGE_LAYOUT:
        if spec.is_appendix:
            continue
        # ⚠ 用**實際渲染時**的版型算，不是宣告的版型：列在 SPLIT_PAIR_REPORTS 的
        # comparison 頁會被 `_split_pairs_by_policy` 拆成一圖一頁的 chart_hero，
        # 拿 comparison 的窄長條去算會嚴重低估（實測只算得出 1 條）。
        kind = spec.kind
        if kind == "comparison" and any(key in SPLIT_PAIR_REPORTS for key in spec.report_keys):
            kind = "chart_hero"
        area = _points_area(theme, kind)
        if area is None:
            continue
        width_in, height_in, columns = area
        per_line, max_lines = _text_capacity(theme, width_in=width_in, height_in=height_in, size_pt=size)
        max_points = max(1, (max_lines * columns) // NARRATIVE_POINT_LINES)
        max_chars = max(1, per_line * NARRATIVE_POINT_LINES)
        for key in spec.report_keys:
            capacity[key] = {"max_points": max_points, "max_chars": max_chars}
    return capacity


def all_slot_keys() -> list[str]:
    """回傳 PPT 階段 AI 需要產的全部文案槽（唯一來源＝PAGE_LAYOUT）。

    v3 只剩 `cover.title` 與 `direction.body`：其餘頁面文字一律來自 narratives，
    不再讓 AI 為每頁另產一份，避免同一段判讀在兩處各寫一次而互相矛盾。
    """
    keys: list[str] = []
    for spec in PAGE_LAYOUT:
        keys.extend(spec.slots)
    return keys


# --------------------------------------------------------------------------
# 樣式
# --------------------------------------------------------------------------
@dataclass
class Theme:
    """外觀樣式；改配色、字級、座標只改 theme.json，不動本程式。"""

    font: dict[str, Any]
    color: dict[str, str]
    geometry: dict[str, Any]
    slide: dict[str, float]
    qa: dict[str, Any]
    # v3 深空主題：背景漸層兩色與角度、星空紋理生成參數、圖表轉色與裁切規則。
    gradient: dict[str, Any]
    starfield: dict[str, Any]
    chart_recolor: dict[str, Any]

    @classmethod
    def load(cls, path: Path | str = THEME_PATH) -> Theme:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            font=data["font"],
            color=data["color"],
            geometry=data["geometry"],
            slide=data["slide"],
            qa=data["qa"],
            gradient=data["gradient"],
            starfield=data["starfield"],
            chart_recolor=data["chart_recolor"],
        )

    def rgb(self, name: str) -> RGBColor:
        return RGBColor.from_string(self.color[name])

    def size(self, name: str) -> float:
        """取字級並套用 12pt 下限；下限在 theme 內宣告，程式只負責遵守。"""
        return max(float(self.font[name]), float(self.font["min_pt"]))


# --------------------------------------------------------------------------
# 文字量估算：文字框裝不裝得下要能事前判斷（fallback 截斷）與事後自檢（QA）
# --------------------------------------------------------------------------
def _text_capacity(theme: Theme, *, width_in: float, height_in: float, size_pt: float) -> tuple[int, int]:
    """回傳（每行字數, 可用行數）。中文字寬約等於字級，故以 pt/72 估字寬。"""
    char_in = size_pt / 72.0 * float(theme.qa["cjk_char_width_ratio"])
    line_in = size_pt / 72.0 * float(theme.qa["line_height_ratio"])
    # ⚠ 加 epsilon：1.5 / (40/72*1.35) 在浮點下是 1.9999999998，直接 int() 會少算一行，
    # 讓剛好兩行的標題被誤判成裝不下而截字（封面標題被切成「…」就是這樣來的）。
    epsilon = 1e-6
    per_line = max(1, int(width_in / char_in + epsilon))
    lines = max(1, int(height_in / line_in + epsilon))
    return per_line, lines


def _fit_text(theme: Theme, text: str, *, width_in: float, height_in: float, size_pt: float) -> tuple[str, bool]:
    """把文字截到框內裝得下，超出加「…」。回傳（文字, 是否截斷）。"""
    per_line, lines = _text_capacity(theme, width_in=width_in, height_in=height_in, size_pt=size_pt)
    budget = per_line * lines
    if len(text) <= budget:
        return text, False
    return text[: max(1, budget - 1)].rstrip("，、。；：") + "…", True


def _display_width(text: str) -> float:
    """字串的顯示寬度（em）。中文、全形符號約 1 em，半形英數約 0.55 em。

    表格欄位混排 `applicant_display_name` 與中文公司名，一律當全形算會把英文
    表頭砍成一半；一律當半形算又會讓中文撐爆欄寬。
    """
    return sum(0.55 if ord(ch) < 0x2E80 else 1.0 for ch in text)


def _truncate_to_width(text: str, width_in: float, size_pt: float) -> str:
    """把字串截到指定英吋寬度內（超出加「…」），用於表格儲存格避免自動換行撐高列。

    ⚠ 截點優先落在標點（2026-07-31）：原本切在剛好超寬的那個字，會產生
    「全數歸在 A…」這種斷在詞中間的句子。改成先找容量內最後一個標點，
    切在它之後——讀起來是「一句話沒講完」而不是「字被砍斷」。
    找不到標點（例如整串是公司名）才退回原本的硬切。
    """
    budget = width_in / (size_pt / 72.0)
    if _display_width(text) <= budget:
        return text
    used, kept = 0.0, []
    for ch in text:
        step = 0.55 if ord(ch) < 0x2E80 else 1.0
        if used + step > budget - 1:
            break
        used += step
        kept.append(ch)
    clipped = "".join(kept)
    cut = max((clipped.rfind(mark) for mark in TRUNCATE_BREAK_MARKS), default=-1)
    # 太靠前的標點不採用（只留半截等於沒講），門檻取容量的六成。
    if cut >= len(clipped) * 0.6:
        clipped = clipped[: cut + 1]
    return clipped + "…"


def _lines_needed(text: str, per_line: int) -> int:
    """多段文字所需行數：每段至少佔一行，不足一行不合併。"""
    return sum(max(1, math.ceil(len(line) / per_line)) for line in text.split("\n"))


# --------------------------------------------------------------------------
# 圖檔對照：唯一來源＝artifact_manifest.json（禁止用 report_key 猜檔名）
# --------------------------------------------------------------------------
class ChartIndex:
    """report_name → 圖檔清單的反查表，並負責 SVG 轉點陣與快取。

    實際檔名與 report_key 不同名（`country_distribution` 的圖叫
    `jurisdiction_distribution.svg`），且同一 report_name 可能對應多張圖
    （IPC 的 L4/L5、機會矩陣的技術面/功效面）——這正是成對呈現的來源。
    """

    def __init__(self, report_dir: Path, cache_dir: Path, manifest: dict[str, Any] | None = None,
                 theme: Theme | None = None) -> None:
        self.report_dir = report_dir
        self.cache_dir = cache_dir
        # theme 供圖表深色轉色與白邊裁切用；None＝維持原樣只轉檔（前端預覽走這條）。
        self.theme = theme
        self._by_report: dict[str, list[str]] = {}
        self._raster_cache: dict[str, Path | None] = {}
        self.manifest_found = bool(manifest)
        for artifact in (manifest or {}).get("artifacts", []) or []:
            if not isinstance(artifact, dict):
                continue
            # 上游欄位曾用 file／filename、sha256／hash 兩種寫法，兩種都吃。
            name = str(artifact.get("file") or artifact.get("filename") or "")
            if not name or not name.lower().endswith(IMAGE_SUFFIXES):
                continue
            if not (self.report_dir / name).exists():
                continue
            targets = list(artifact.get("report_names") or [])
            if artifact.get("report_name"):
                targets.append(str(artifact["report_name"]))
            for target in {str(t) for t in targets if t}:
                bucket = self._by_report.setdefault(target, [])
                if name not in bucket:
                    bucket.append(name)

    @staticmethod
    def _order_key(name: str) -> tuple[int, str]:
        for index, hint in enumerate(CHART_ORDER_HINTS):
            if hint in name:
                return index, name
        return len(CHART_ORDER_HINTS), name

    def files_for(self, report_keys: tuple[str, ...]) -> tuple[str, ...]:
        """依 report_key 順序取圖檔；同一 report_key 多張圖時保持穩定排序。"""
        found: list[str] = []
        for key in report_keys:
            for name in sorted(self._by_report.get(key, []), key=self._order_key):
                if name not in found:
                    found.append(name)
        return tuple(found)

    def owners_of(self, chart_name: str, keys: tuple[str, ...]) -> tuple[str, ...]:
        """某張圖對應到候選 report_key 中的哪幾個；拆頁時用來把 report_key 一起收窄。"""
        return tuple(key for key in keys if chart_name in self._by_report.get(key, []))

    def resolve(self, name: str) -> Path | None:
        """把圖檔轉成 python-pptx 吃得下的點陣檔；SVG 轉 PNG 只轉一次。"""
        if name in self._raster_cache:
            return self._raster_cache[name]
        source = self.report_dir / name
        if not source.exists():
            self._raster_cache[name] = None
            return None
        if source.suffix.lower() == ".svg":
            resolved = globals()["rasterize_svg"](source, self.cache_dir, self.theme)
        else:
            resolved = source
        self._raster_cache[name] = resolved
        return resolved


BACKGROUND_RECT_PATTERN = re.compile(
    r'<rect[^>]*?fill="(?:white|#fff|#ffffff|#FFF|#FFFFFF)"[^>]*?/>', re.I)
SVG_SIZE_PATTERN = re.compile(r'<svg[^>]*?width="(\d+(?:\.\d+)?)"[^>]*?height="(\d+(?:\.\d+)?)"', re.I)
RASTER_DPI = 150


def recolor_svg(svg_text: str, recolor: dict[str, Any]) -> str:
    """把引擎產的淺底圖表 SVG 換成深空配色（PPT 端轉色，不動引擎）。

    ⚠ 為什麼在這裡轉而不是讓引擎產深色：同一份 SVG 也內嵌在**網頁報表頁**
    （淺底），引擎改深色會讓網頁那邊變成深底深字。使用者 2026-07-31 選定此方案。
    ⚠ 只換顏色，不動 viewBox、不裁切內容、不拉伸——圖表形式一律照引擎產的樣子
    （使用者定案：「不能像 NotebookLM 把圖表改形式」）。
    """
    if recolor.get("strip_background"):
        # 整版白底矩形必須先拿掉，否則深色頁上會出現一塊白板。
        svg_text = BACKGROUND_RECT_PATTERN.sub("", svg_text, count=1)
    for old, new in (recolor.get("map") or {}).items():
        svg_text = re.sub(re.escape(f"#{old}"), f"#{new}", svg_text, flags=re.I)
    return svg_text


def _crop_box(png_path: Path, padding: int) -> tuple[int, int, int, int] | None:
    """圖檔實際內容的邊界（含安全邊）；整張都是空的回 None。

    去掉白邊是使用者 2026-07-31 授權的（「裁切就是邊邊白白的可以切」）——
    這不是改圖表形式，是不再把死空間當成圖的一部分塞進版面。實測一張趨勢圖
    左右上下共有 320 px 是空的，切掉後同樣的框內圖表內容線性大 25%。
    """
    try:
        from PIL import Image  # python-pptx 本來就依賴 Pillow，不是新增相依
    except ImportError:
        return None
    with Image.open(png_path) as image:
        box = image.getbbox()  # RGBA：回傳非透明區域
        if box is None:
            return None
        left, top, right, bottom = box
        width, height = image.size
    return (max(0, left - padding), max(0, top - padding),
            min(width, right + padding), min(height, bottom + padding))


def prepare_chart(svg_path: Path, cache_dir: Path, theme: Theme) -> tuple[Path | None, Path | None]:
    """圖表 SVG → (PNG, 轉色裁切後的 SVG)；轉不動時 PNG 回 None 由呼叫端降級。

    🔴 兩個回傳值必須**同源**：PNG 是後援與預覽用、SVG 供 PowerPoint 顯示向量。
    若 PNG 用轉色版、SVG 用原始版，會出現「縮圖淺色、放大變深色」的錯位，
    而且極難察覺（多數人不會放大到觸發 SVG 渲染）。故兩者都由同一份轉色＋
    同一個裁切框產出。
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    recolor = getattr(theme, "chart_recolor", {}) or {}
    source = svg_path.read_text(encoding="utf-8")
    dark = recolor_svg(source, recolor)
    digest = hashlib.sha256(dark.encode("utf-8")).hexdigest()[:16]
    png_path = cache_dir / f"{svg_path.stem}_{digest}.png"
    out_svg = cache_dir / f"{svg_path.stem}_{digest}.svg"
    if png_path.exists() and out_svg.exists():
        return png_path, out_svg

    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            return None, None

    staged = cache_dir / f"{svg_path.stem}_{digest}_src.svg"
    staged.write_text(dark, encoding="utf-8")
    try:
        doc = pymupdf.open(str(staged))
        # alpha=True：去掉白底後背景要**透明**才貼得上深色頁，也才裁得出內容邊界。
        doc[0].get_pixmap(dpi=RASTER_DPI, alpha=True).save(str(png_path))
        doc.close()
    except Exception:
        return None, None

    box = _crop_box(png_path, int(recolor.get("crop_padding_px") or 0))
    out_svg.write_text(_cropped_svg(dark, png_path, box), encoding="utf-8")
    if box is not None:
        try:
            from PIL import Image

            with Image.open(png_path) as image:
                image.crop(box).save(png_path)
        except Exception:
            pass  # 裁不動就用未裁的，畫面只是留白多一點，不該讓整份簡報失敗
    return png_path, out_svg


def rasterize_svg(svg_path: Path, cache_dir: Path, theme: Theme | None = None) -> Path | None:
    """SVG → PNG（python-pptx 不吃 SVG）。轉不動時回 None，由呼叫端降級處理。

    ⚠ 保留這個名字與單一回傳值是為了呼叫端（`ChartIndex.resolve`）與既有測試不必改。
    有 theme 時走 `prepare_chart`（深色轉色＋白邊裁切＋同時產向量版），
    沒有 theme（例如前端預覽只拿得到 report_data）就只轉檔、不改外觀。
    """
    if theme is not None:
        png_path, _ = prepare_chart(svg_path, cache_dir, theme)
        return png_path
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(svg_path.read_bytes()).hexdigest()[:16]
    png_path = cache_dir / f"{svg_path.stem}_{digest}_plain.png"
    if png_path.exists():
        return png_path
    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            return None
    try:
        doc = pymupdf.open(str(svg_path))
        doc[0].get_pixmap(dpi=RASTER_DPI).save(str(png_path))
        doc.close()
    except Exception:
        return None
    return png_path


def _cropped_svg(svg_text: str, png_path: Path, box: tuple[int, int, int, int] | None) -> str:
    """把裁切框換算回 SVG 使用者座標並改寫 viewBox，讓向量版與 PNG 切在同一處。"""
    size = SVG_SIZE_PATTERN.search(svg_text)
    if box is None or size is None:
        return svg_text
    try:
        from PIL import Image

        with Image.open(png_path) as image:
            png_width = image.size[0]
    except Exception:
        return svg_text
    svg_width = float(size.group(1))
    svg_height = float(size.group(2))
    if not png_width or not svg_width:
        return svg_text
    scale = png_width / svg_width
    left, top, right, bottom = (value / scale for value in box)
    width = max(1.0, right - left)
    height = max(1.0, bottom - top)
    header = size.group(0)
    replaced = re.sub(r'width="[\d.]+"', f'width="{width:.1f}"', header, count=1)
    replaced = re.sub(r'height="[\d.]+"', f'height="{height:.1f}"', replaced, count=1)
    replaced = f'{replaced} viewBox="{left:.1f} {top:.1f} {width:.1f} {height:.1f}"'
    # 原本就有 viewBox 的話先移除，避免同一個標籤出現兩個。
    svg_text = re.sub(r'\s+viewBox="[^"]*"', "", svg_text, count=1)
    _ = svg_height
    return svg_text.replace(header, replaced, 1)


# --------------------------------------------------------------------------
# report_data 取值
# --------------------------------------------------------------------------
def _iter_report_entries(report_data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    entries: list[tuple[str, dict[str, Any]]] = []
    for bucket in ("reports", "family_reports"):
        section = report_data.get(bucket) or {}
        if isinstance(section, dict):
            entries.extend((k, v) for k, v in section.items() if isinstance(v, dict))
    return entries


def _entry_of(report_data: dict[str, Any], report_key: str) -> dict[str, Any]:
    for bucket in ("reports", "family_reports"):
        entry = (report_data.get(bucket) or {}).get(report_key)
        if isinstance(entry, dict):
            return entry
    return {}


def _rows_of(report_data: dict[str, Any], report_key: str) -> list[dict[str, Any]]:
    rows = _entry_of(report_data, report_key).get("rows")
    return rows if isinstance(rows, list) else []


def _label_of(report_data: dict[str, Any], report_key: str, fallback: str = "") -> str:
    entry = _entry_of(report_data, report_key)
    return str(entry.get("label_zh") or entry.get("label") or fallback or report_key)


def _report_key_has_data(report_data: dict[str, Any], report_key: str) -> bool:
    entry = _entry_of(report_data, report_key)
    rows = entry.get("rows")
    if isinstance(rows, list) and rows:
        return True
    try:
        return int(entry.get("row_count") or 0) > 0
    except (TypeError, ValueError):
        return False


def _actual_report_keys(report_data: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(key for key in keys if _report_key_has_data(report_data, key))


def _numeric_column(rows: list[dict[str, Any]]) -> str:
    """挑出 rows 內代表件數的數值欄；優先 patent_count，其次第一個純數字欄。"""
    if not rows:
        return ""
    if "patent_count" in rows[0]:
        return "patent_count"
    for name, value in rows[0].items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(name)
    return ""


def _label_column(rows: list[dict[str, Any]], numeric: str) -> str:
    """挑出 rows 內代表項目名稱的欄。

    ⚠ 優先 `label`／顯示名欄，並跳過排除欄——舊版取「第一個非數值欄」，
    分群 rows 第一欄是 topic_code，降級要點就印出「T001 15 件」（代碼入文，
    2026-07-31 實機驗收抓到）。
    """
    if not rows:
        return ""
    for preferred in ("label", "applicant_display_name", "owner_display_name"):
        if preferred in rows[0]:
            return preferred
    for name in rows[0]:
        if str(name) != numeric and str(name) not in TABLE_EXCLUDED_COLUMNS:
            return str(name)
    return str(next(iter(rows[0])))


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _statistics_period(report_data: dict[str, Any]) -> str:
    """統計期間：先讀 parameters 明示值，沒有才由趨勢報表的年份區間推得。"""
    params = report_data.get("parameters") or {}
    for key in ("period", "date_range", "statistics_period"):
        if params.get(key):
            return str(params[key])
    years: list[int] = []
    for report_key in ("application_trend", "publication_trend", "lifecycle"):
        for row in _rows_of(report_data, report_key):
            for field in ("year", "application_year", "publication_year"):
                if field in row:
                    value = _as_int(row[field])
                    if value:
                        years.append(value)
    if not years:
        return ""
    low, high = min(years), max(years)
    return str(low) if low == high else f"{low}–{high}"


# --------------------------------------------------------------------------
# narrative：headline／points 三件套，缺就 fallback 並記 warning
# --------------------------------------------------------------------------
def _narrative_entry(narratives: dict[str, Any], candidates: tuple[str, ...]) -> tuple[str, dict[str, Any]]:
    """依候選鍵找 narrative；回傳（命中的鍵, variant 內容）。

    narratives 的鍵混用 report_key 與圖檔名（申請趨勢的解讀掛在 `annual_trend`），
    故候選鍵同時含 report_key、圖檔主檔名與去掉 _L4/_tech 等變體後綴的主檔名。
    """
    reports = narratives.get("reports") or {}
    for key in candidates:
        # 「report:variant」語法（P1-2 alias）：直接指定變體，不掃整個 entry。
        if ":" in key:
            report_key, _, variant_key = key.partition(":")
            variant = ((reports.get(report_key) or {}).get("variants") or {}).get(variant_key)
            if isinstance(variant, dict) and (variant.get("points") or variant.get("text") or variant.get("headline")):
                return key, variant
            continue
        entry = reports.get(key)
        if not isinstance(entry, dict):
            continue
        variants = entry.get("variants") or {}
        for variant_key in ("default", *variants):
            variant = variants.get(variant_key)
            if isinstance(variant, dict) and (variant.get("points") or variant.get("text") or variant.get("headline")):
                return key, variant
    return "", {}


def _split_legacy_text(text: str) -> list[dict[str, Any]]:
    """把舊格式長文依「觀察／意涵／決策提醒」等段落標記切成條列。

    這是 fallback 的可讀性補救：上游還沒升級 headline/points 契約前，
    直接把 400–500 字整段塞進版面就是字牆（實機驗收不合格的主因之一）。
    切分只依 AI 自己寫的段落標記，不新增、不改寫任何內容。
    """
    pattern = "|".join(NARRATIVE_MARKERS)
    chunks = [c.strip() for c in re.split(rf"(?=(?:{pattern})\s*[：:])", text) if c.strip()]
    points: list[dict[str, Any]] = []
    for chunk in chunks:
        match = re.match(rf"^({pattern})\s*[：:]\s*(.+)$", chunk, flags=re.DOTALL)
        if match:
            points.append({"label": match.group(1), "text": match.group(2).strip()})
        else:
            points.append({"label": "", "text": chunk})
    if len(points) > 1:
        return points
    # 找不到段落標記：依句號切成最多三條，仍好過單段字牆。
    sentences = [s.strip() for s in re.split(r"(?<=[。！？])", text) if s.strip()]
    if len(sentences) <= 1:
        return [{"label": "", "text": text.strip()}]
    size = math.ceil(len(sentences) / 3)
    return [
        {"label": "", "text": "".join(sentences[i : i + size])}
        for i in range(0, len(sentences), size)
    ]


HEADLINE_MAX_CHARS = 26


def _derive_headline(points: list[dict[str, Any]]) -> str:
    """缺 headline 時，從第一條要點取一個短句當判讀式標題。

    這是**選取**而不是生成：句子完全是 narrative 自己寫的，程式只切第一個句讀
    並檢查長度，切不出夠短的就不硬湊（標題退回報表主題）。裸名詞標題讀者看不出
    這頁在講什麼，但捏造判讀更糟——所以只在有現成句子可用時才升級標題，
    且一律寫 warning 讓人追得到這個標題是推導來的。
    """
    if not points:
        return ""
    text = str(points[0].get("text") or "").strip()
    for separator in ("。", "；", "，", "、"):
        head = text.split(separator)[0].strip()
        if head and len(head) <= HEADLINE_MAX_CHARS:
            return head
    return ""


def _normalize_narrative(variant: dict[str, Any]) -> tuple[str, list[dict[str, Any]], bool]:
    """回傳（headline, points, 是否走 fallback）。缺 headline 不自行編造。"""
    headline = str(variant.get("headline") or "").strip()
    raw_points = variant.get("points")
    if isinstance(raw_points, list) and raw_points:
        points = [
            {
                "label": str(p.get("label") or "").strip(),
                "text": str(p.get("text") or "").strip(),
                "emphasis": bool(p.get("emphasis")),
            }
            for p in raw_points
            if isinstance(p, dict) and str(p.get("text") or "").strip()
        ]
        if points:
            return headline, points, False
    text = str(variant.get("text") or "").strip()
    if not text:
        return headline, [], True
    return headline, [{**p, "emphasis": False} for p in _split_legacy_text(text)], True


# --------------------------------------------------------------------------
# 組版輔助
# --------------------------------------------------------------------------
def _set_font(run, theme: Theme, *, size: float, color: str, bold: bool) -> None:
    """套字體與字級。

    ⚠ 只設 `run.font.name` 只會寫 `a:latin`，中文會被 PowerPoint 退回新細明體；
    要全字元都是微軟正黑體必須同時寫 `a:ea`（東亞）與 `a:cs`（複雜文字）。
    """
    run.font.size = Pt(max(size, float(theme.font["min_pt"])))
    run.font.bold = bold
    run.font.color.rgb = theme.rgb(color)
    run.font.name = theme.font["family"]
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        element = rpr.find(qn(tag))
        if element is None:
            element = rpr.makeelement(qn(tag), {})
            rpr.append(element)
        element.set("typeface", theme.font["family"])


def _new_textbox(slide, *, left: float, top: float, width: float, height: float):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = box.text_frame
    frame.word_wrap = True
    return box, frame


def _add_text(
    slide,
    theme: Theme,
    text: str,
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    size: float,
    color: str = "ink",
    bold: bool = False,
    align=PP_ALIGN.LEFT,
    anchor=MSO_ANCHOR.TOP,
) -> None:
    """加入純文字框；換行字元切成獨立段落，維持行距一致。"""
    _, frame = _new_textbox(slide, left=left, top=top, width=width, height=height)
    frame.vertical_anchor = anchor
    for index, line in enumerate(str(text).split("\n")):
        para = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        para.alignment = align
        run = para.add_run()
        run.text = line
        _set_font(run, theme, size=size, color=color, bold=bold)


def _add_number_bold_text(
    slide,
    theme: Theme,
    blocks: list[tuple[str, str, str, bool]],
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    size: float,
) -> None:
    """逐條 bullet；每條由（標籤, 內文, 內文色, 標籤是否強調）構成。

    ⚠ v3 起**全部內文一律粗體**（2026-07-31 使用者定案「文字內容記得加粗體」）：
    深色底上細字會發灰，投影時尤其糊。

    但這樣一來「用粗體切出關鍵數字」的原設計就失效了——全部都粗，數字不再突出。
    故改以**顏色**承擔區分：數字改用 accent 青，非數字用原內文色。粗體管可讀性、
    顏色管重點，兩件事分開，不再互相搶同一個視覺通道。
    ⚠ 強調條（emphasis）的數字維持 alert 色不轉青，否則整條的警示語氣會被打斷。
    """
    _, frame = _new_textbox(slide, left=left, top=top, width=width, height=height)
    for index, (label, text, color, emphasized) in enumerate(blocks):
        para = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        if label:
            chip = para.add_run()
            chip.text = f"{label}｜"
            _set_font(chip, theme, size=size, color="alert" if emphasized else "accent", bold=True)
        for piece in NUMBER_PATTERN.split(text):
            if not piece:
                continue
            is_number = bool(NUMBER_PATTERN.fullmatch(piece))
            run = para.add_run()
            run.text = piece
            _set_font(run, theme, size=size,
                      color="accent" if (is_number and color != "alert") else color,
                      bold=True)


def _add_band(slide, theme: Theme, left, top, width, height, color: str, *, rounded: bool = False, line: str = "") -> None:
    """加入色塊／底板。rounded 用於圓角面板，方角用於裝飾條。

    🔴 2026-07-31 批2：**圓角一律補可見邊框**。獨立驗收實測 46 個填色面板中
    43 個是 `<a:ln><a:noFill/>`，面板底對背景只有 1.12–1.72，等於沒有邊界、
    整頁區塊糊成一片。

    ⚠ 治在**實作**而不是逐一改 43 個呼叫點：加參數會讓介面變寬、每個呼叫端與
    測試都得跟著改（同日 `rasterize_svg` 加第三個參數就是這樣害測試變紅）。
    判斷依據用既有語意——圓角＝面板（要邊界），方角＝標題底線／進度條等裝飾條
    （加框反而礙眼），不必新增旗標。
    """
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
        Inches(left), Inches(top), Inches(width), Inches(height),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = theme.rgb(color)
    if not line and rounded:
        line = "hairline"
    if line:
        shape.line.color.rgb = theme.rgb(line)
        shape.line.width = Pt(theme.font["panel_border_pt"])
    else:
        shape.line.fill.background()
    if rounded:
        shape.adjustments[0] = 0.08
    shape.shadow.inherit = False
    shape.text_frame.text = ""


def _add_background(slide, theme: Theme, cache_dir: Path, *, density: float) -> None:
    """深空背景層：全出血漸層矩形 ＋ 星空紋理疊圖（v3，2026-07-31）。

    分兩層是有原因的，不是為了好看才拆：
    - **漸層走 python-pptx 原生 gradFill**：向量，放大／投影／列印都不糊。
      ⚠ 不可改用 SVG 漸層——pymupdf 不渲染 SVG 漸層，實測整片變純黑。
    - **星點走圖片疊加**：星點數以百計，若逐顆畫成 shape 會讓檔案膨脹又拖慢
      PowerPoint 開檔；疊一張透明底 PNG 便宜得多。seed 固定＝每次都一樣。

    density＝紋理密度倍率（封面 1.0、內頁較淡）。紋理產不出來時**略過該層**
    而不是中斷組版：少一層星點只是樸素些，整份簡報產不出來才是嚴重的。
    """
    g = theme.geometry["background"]
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(g["left_in"]), Inches(g["top_in"]),
        Inches(theme.slide["width_in"]), Inches(theme.slide["height_in"]),
    )
    shape.line.fill.background()
    shape.shadow.inherit = False
    shape.text_frame.text = ""

    # 先讓 python-pptx 建出 gradFill 骨架，再把 stop 換成 theme 指定的兩色。
    # 直接改 XML 是因為 python-pptx 預設會塞多個 stop，逐一設色反而更繞。
    cfg = theme.gradient
    shape.fill.gradient()
    grad = shape._element.spPr.find(qn("a:gradFill"))
    stops = grad.find(qn("a:gsLst"))
    for gs in list(stops):
        stops.remove(gs)
    stops.append(parse_xml(
        f'<a:gs {nsdecls("a")} pos="0"><a:srgbClr val="{cfg["start"]}"/></a:gs>'))
    stops.append(parse_xml(
        f'<a:gs {nsdecls("a")} pos="100000"><a:srgbClr val="{cfg["end"]}"/></a:gs>'))
    # 線性漸層；若骨架給的是放射狀（a:path）就換掉，否則角度設定會無效。
    path = grad.find(qn("a:path"))
    if path is not None:
        grad.remove(path)
    lin = grad.find(qn("a:lin"))
    if lin is None:
        grad.append(parse_xml(
            f'<a:lin {nsdecls("a")} scaled="0" ang="{int(cfg["angle_ooxml"])}"/>'))
    else:
        lin.set("ang", str(int(cfg["angle_ooxml"])))
        lin.set("scaled", "0")

    shape.name = BACKGROUND_SHAPE_NAME
    texture = starfield_png(theme.starfield, cache_dir, density=density)
    if texture is None:
        return
    picture = slide.shapes.add_picture(
        str(texture), Inches(g["left_in"]), Inches(g["top_in"]),
        width=Inches(theme.slide["width_in"]), height=Inches(theme.slide["height_in"]),
    )
    # ⚠ 命名是為了讓版面自檢認得出它：背景是**刻意全出血**的，
    # 用「所有圖片都要在安全邊界內」去檢查它一定會誤報。
    picture.name = BACKGROUND_SHAPE_NAME


def _add_oval(slide, theme: Theme, left, top, width, height, color: str) -> None:
    """圓點裝飾（封面角落圓格網）。"""
    shape = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(left), Inches(top), Inches(width), Inches(height))
    shape.fill.solid()
    shape.fill.fore_color.rgb = theme.rgb(color)
    shape.line.fill.background()
    shape.shadow.inherit = False
    shape.text_frame.text = ""


def _attach_svg(picture, svg_path: Path, index: int) -> None:
    """把向量版掛到已插入的圖片上（OOXML 的 `asvg:svgBlip` 擴充）。

    PowerPoint 2016+ 顯示這份 SVG，舊版／網頁預覽／縮圖服務自動退回 `r:embed`
    指向的 PNG。⚠ 這不是變通做法——PowerPoint 自己插入 SVG 時產生的就是這個
    結構（點陣後援＋向量擴充），這裡只是用程式重現它。

    ⚠ `PackURI` 必須唯一：同名會讓後插入的圖覆蓋前一張，整份簡報的圖全變成同一張。
    """
    try:
        from pptx.opc.constants import RELATIONSHIP_TYPE as RT
        from pptx.opc.package import Part
        from pptx.opc.packuri import PackURI

        slide_part = picture.part
        partname = PackURI(f"/ppt/media/chart-vector-{index}.svg")
        svg_part = Part(partname, "image/svg+xml", slide_part.package, svg_path.read_bytes())
        rel_id = slide_part.relate_to(svg_part, RT.IMAGE)
        blip = picture._element.blipFill.find(qn("a:blip"))
        if blip is None:
            return
        blip.append(parse_xml(
            f'<a:extLst {nsdecls("a", "r")}>'
            '<a:ext uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}">'
            '<asvg:svgBlip xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main" '
            f'r:embed="{rel_id}"/>'
            "</a:ext></a:extLst>"
        ))
    except Exception:
        # 掛不上就維持純 PNG：圖會是點陣的（放大略糊），但簡報照樣產得出來。
        return


def _add_picture_fitted(slide, image_path: Path, *, left: float, top: float, width: float, height: float) -> None:
    """等比縮放塞進框內並置中。

    ⚠ 舊版只給 `width=` 讓高度自由伸展，遇到高瘦圖就往下溢出 3.5 吋衝出版面。
    這裡先用原生尺寸插入再依框的長寬比縮放，圖再怪也不會出框。

    ⚠ 同名 `.svg` 若存在（`prepare_chart` 產的轉色裁切版）就一併掛成向量：
    以副檔名推導而不是多傳一個參數，是為了讓每個 renderer 的呼叫點都不必改——
    兩份檔案本來就由同一次 `prepare_chart` 同時產出、同名同 digest，不會對錯。
    """
    picture = slide.shapes.add_picture(str(image_path), Inches(left), Inches(top))
    vector = image_path.with_suffix(".svg")
    if vector.exists():
        global _VECTOR_INDEX
        _VECTOR_INDEX += 1
        _attach_svg(picture, vector, _VECTOR_INDEX)
    box_w, box_h = Inches(width), Inches(height)
    if picture.width <= 0 or picture.height <= 0:
        picture.width, picture.height = box_w, box_h
        return
    scale = min(box_w / picture.width, box_h / picture.height)
    picture.width = Emu(int(picture.width * scale))
    picture.height = Emu(int(picture.height * scale))
    picture.left = Emu(int(box_w - picture.width) // 2 + int(Inches(left)))
    picture.top = Emu(int(box_h - picture.height) // 2 + int(Inches(top)))


def _variant_label(chart_name: str) -> str:
    for suffix, label in CHART_VARIANT_LABELS.items():
        if suffix in chart_name:
            return label
    return Path(chart_name).stem


def _encoding_note(spec: PageSpec) -> str:
    for key in spec.report_keys:
        if key in ENCODING_NOTES:
            return ENCODING_NOTES[key]
    return DEFAULT_ENCODING_NOTE


def _caveat_of(spec: PageSpec) -> str:
    for key in spec.report_keys:
        if key in CAVEATS:
            return CAVEATS[key]
    return ""


def _points_for(spec: PageSpec, ctx: dict[str, Any]) -> list[tuple[str, str, str, bool]]:
    """取本頁要點；缺 narrative 時退回引擎 rows 的關鍵數字，仍不留空框。"""
    _, points, _ = ctx["narratives_by_page"].get(spec.page, ("", [], False))
    if points:
        return [
            (
                str(p.get("label") or ""),
                str(p.get("text") or ""),
                "alert" if p.get("emphasis") else "ink",
                bool(p.get("emphasis")),
            )
            for p in points
        ]
    return [(label, text, "ink", False) for label, text in _row_highlights(spec, ctx)]


def _row_highlights(spec: PageSpec, ctx: dict[str, Any]) -> list[tuple[str, str]]:
    """從引擎 rows 取前幾名做要點，供缺 narrative 的頁面使用（只列數字，不下判讀）。"""
    for key in spec.report_keys:
        rows = _rows_of(ctx["report_data"], key)
        if not rows:
            continue
        numeric = _numeric_column(rows)
        if not numeric:
            continue
        label_col = _label_column(rows, numeric)
        ranked = sorted(rows, key=lambda r: _as_int(r.get(numeric)), reverse=True)[:4]
        return [(str(r.get(label_col, "")), f"{_as_int(r.get(numeric))} 件") for r in ranked]
    return []


# --------------------------------------------------------------------------
# 共用頁面區塊
# --------------------------------------------------------------------------
def _render_header(slide, theme: Theme, spec: PageSpec, ctx: dict[str, Any]) -> None:
    """內頁頁首：判讀式標題、accent 底線、右上頁碼。"""
    g = theme.geometry["header"]
    _add_text(slide, theme, spec.title,
              left=g["title_left_in"], top=g["title_top_in"],
              width=g["title_width_in"], height=g["title_height_in"],
              # v3：深底主題下 navy 已是面板底色，標題再用它等於隱形（實機轉圖驗到）。
              size=theme.size("title_pt"), color="ink", bold=True)
    _add_band(slide, theme, g["rule_left_in"], g["rule_top_in"],
              g["rule_width_in"], g["rule_height_in"], "accent")
    _add_text(slide, theme, f"{spec.page:02d}",
              left=g["page_number_left_in"], top=g["page_number_top_in"],
              width=g["page_number_width_in"], height=g["page_number_height_in"],
              size=theme.size("page_number_pt"), color="accent", bold=True, align=PP_ALIGN.RIGHT)


def _render_footnote(slide, theme: Theme, spec: PageSpec, ctx: dict[str, Any], extra: str = "") -> None:
    """頁底資料來源註：資料來源、統計期間、判讀限制聲明。"""
    g = theme.geometry["footnote"]
    sources = "、".join(_label_of(ctx["report_data"], key) for key in spec.report_keys) or "本次報表版本"
    period = ctx["period"] or "未標示"
    # ⚠ 2026-07-31 使用者定案：頁尾**不印報表版本**（「這種報表版本這種字不要有」）。
    # 原本印 report_trial_20260731_… 這種內部識別碼，對讀者毫無意義又佔掉頁尾寬度；
    # 可追溯性由 manifest 保留，不必寫在簡報上。
    text = f"資料來源：{sources}｜統計期間：{period}"
    if extra:
        text = f"{text}｜{extra}"
    text, _ = _fit_text(theme, text, width_in=g["width_in"], height_in=g["height_in"],
                        size_pt=theme.size("footnote_pt"))
    _add_text(slide, theme, text,
              left=g["left_in"], top=g["top_in"], width=g["width_in"], height=g["height_in"],
              size=theme.size("footnote_pt"), color="muted")


def _render_points_panel(slide, theme: Theme, spec: PageSpec, ctx: dict[str, Any]) -> None:
    """右側要點框（＋必要時的方法論警語框）。"""
    g = theme.geometry["points_panel"]
    caveat = _caveat_of(spec)
    panel_height = g["height_with_caveat_in"] if caveat else g["height_in"]
    _add_band(slide, theme, g["left_in"], g["top_in"], g["width_in"], panel_height, "panel", rounded=True)
    _add_text(slide, theme, "判讀要點",
              left=g["left_in"] + g["header_inset_left_in"], top=g["top_in"] + g["header_top_offset_in"],
              width=g["width_in"] - g["text_inset_right_in"], height=g["header_height_in"],
              size=theme.size("panel_header_pt"), color="accent", bold=True)

    text_width = g["width_in"] - g["text_inset_right_in"]
    text_height = panel_height - g["text_top_offset_in"] - g["text_bottom_pad_in"]
    size = theme.size("point_text_pt")
    blocks = _trim_blocks(theme, _points_for(spec, ctx), width_in=text_width, height_in=text_height, size_pt=size)
    _add_number_bold_text(slide, theme, blocks,
                          left=g["left_in"] + g["text_inset_left_in"], top=g["top_in"] + g["text_top_offset_in"],
                          width=text_width, height=text_height, size=size)

    if caveat:
        c = theme.geometry["caveat_panel"]
        _add_band(slide, theme, c["left_in"], c["top_in"], c["width_in"], c["height_in"], "panel", rounded=True)
        _add_text(slide, theme, "判讀限制",
                  left=c["left_in"] + c["title_inset_left_in"], top=c["top_in"] + c["title_top_offset_in"],
                  width=c["width_in"] - c["text_inset_right_in"], height=c["title_height_in"],
                  size=theme.size("caveat_title_pt"), color="accent", bold=True)
        body_width = c["width_in"] - c["text_inset_right_in"]
        body_height = c["height_in"] - c["text_top_offset_in"] - c["text_bottom_pad_in"]
        body, _ = _fit_text(theme, caveat, width_in=body_width, height_in=body_height,
                            size_pt=theme.size("caveat_text_pt"))
        _add_text(slide, theme, body,
                  left=c["left_in"] + c["text_inset_left_in"], top=c["top_in"] + c["text_top_offset_in"],
                  width=body_width, height=body_height,
                  size=theme.size("caveat_text_pt"), color="on_dark_soft")


def _visible_column_count(rows: list[dict[str, Any]], excluded: set[str]) -> int:
    """排除欄之後實際可顯示的欄數（截欄註記的分母）。"""
    if not rows:
        return 0
    return len([name for name in rows[0] if str(name) not in excluded])


def _render_points_band(slide, theme: Theme, spec: PageSpec, ctx: dict[str, Any],
                        *, top: float | None = None) -> None:
    """底部要點橫幅：無圖的表格頁專用，讓表格拿到滿版寬度（v3，2026-07-31）。

    ⚠ 橫幅容量比右側直欄小，所以**不是**把右欄內容原樣搬下來就好——
    該頁能寫幾條、每條幾字由 `narrative_capacity()` 依本區幾何算出後餵給解讀 CLI，
    上游照容量寫，這裡的 `_trim_blocks` 只當最後保底。
    """
    g = theme.geometry["table_with_points"]
    left = g["points_band_left_in"]
    top = g["points_band_top_in"] if top is None else top
    width = g["points_band_width_in"]
    height = g["points_band_height_in"]
    inset = g["points_band_inset_in"]
    _add_band(slide, theme, left, top, width, height, "panel", rounded=True)
    _add_text(slide, theme, "判讀要點",
              left=left + inset, top=top + g["points_band_header_top_offset_in"],
              width=width - inset - inset, height=g["points_band_header_height_in"],
              size=theme.size("panel_header_pt"), color="accent", bold=True)

    columns = int(g["points_band_columns"])
    gap = g["points_band_column_gap_in"]
    col_width = (width - inset - inset - gap * (columns - 1)) / columns
    text_top = top + g["points_band_text_top_offset_in"]
    text_height = height - g["points_band_text_top_offset_in"] - inset
    size = theme.size("point_text_pt")
    # 容量以「單欄高度 × 欄數」估：橫幅是多欄排版，只用單欄高度會低估一半。
    blocks = _trim_blocks(theme, _points_for(spec, ctx),
                          width_in=col_width, height_in=text_height * columns, size_pt=size)
    # 前半進左欄、後半進右欄——依序讀比蛇行（左右交錯）自然。
    per_column = max(1, math.ceil(len(blocks) / columns)) if blocks else 1
    for index in range(columns):
        chunk = blocks[index * per_column:(index + 1) * per_column]
        if not chunk:
            continue
        _add_number_bold_text(slide, theme, chunk,
                              left=left + inset + index * (col_width + gap), top=text_top,
                              width=col_width, height=text_height, size=size)


def _trim_blocks(
    theme: Theme,
    blocks: list[tuple[str, str, str, bool]],
    *,
    width_in: float,
    height_in: float,
    size_pt: float,
) -> list[tuple[str, str, str, bool]]:
    """把要點條列裁到框內裝得下。

    ⚠ 依序填到滿為止會讓第一條吃光版面、後面的「意涵」「決策提醒」整條消失——
    等於只給讀者半個判讀。故改成**按條數分配行數**：每條至少一行，各自截斷，
    寧可每條短一點，也要讓完整的判讀結構（現況→意涵→後續）都露出來。

    ⚠ `CAVEAT_LABEL` 那條**先扣足行數、不參與均分**（2026-07-31 實機第 3、5 頁）：
    判讀限制被切成「…多為新案審…」，而那段沒有標點可切，`_truncate_to_width`
    的「切在標點」邏輯救不了。警語講一半比不講更糟，故讓要點讓路而不是讓警語斷句。
    """
    if not blocks:
        return blocks
    per_line, lines = _text_capacity(theme, width_in=width_in, height_in=height_in, size_pt=size_pt)
    needs = [
        max(1, math.ceil(((len(label) + 1 if label else 0) + len(text)) / per_line))
        for label, text, _, _ in blocks
    ]
    if sum(needs) <= lines:
        return blocks

    protected = {index for index, block in enumerate(blocks) if block[0] == CAVEAT_LABEL}
    reserved = min(lines, sum(needs[index] for index in protected))
    others = [index for index in range(len(blocks)) if index not in protected]
    room = lines - reserved
    keep = min(len(others), room)
    share, extra = divmod(room, keep) if keep else (0, 0)
    ranks = {index: rank for rank, index in enumerate(others[:keep])}
    trimmed: list[tuple[str, str, str, bool]] = []
    for index, (label, text, color, emphasized) in enumerate(blocks):
        if index in protected:
            trimmed.append((label, text, color, emphasized))
            continue
        if index not in ranks:
            continue
        allowance = share + (1 if ranks[index] < extra else 0)
        budget = allowance * per_line - (len(label) + 1 if label else 0)
        if len(text) > budget:
            text = text[: max(1, budget - 1)].rstrip("，、。；：") + "…"
        trimmed.append((label, text, color, emphasized))
    return trimmed


# --------------------------------------------------------------------------
# 版型 renderer
# --------------------------------------------------------------------------
def _render_cover(slide, theme: Theme, spec: PageSpec, ctx: dict[str, Any]) -> None:
    """封面：主標＋統計期間副標＋統計卡＋分析框架條＋幾何裝飾（深底亮字）。

    ⚠ v3 起**不再自己畫整頁底色**：底色由 `_add_background` 的漸層＋星空負責，
    這裡若再鋪一張不透明矩形會把背景整個蓋掉（v2 是淺底主題才需要）。
    """
    g = theme.geometry["cover"]
    _add_band(slide, theme, g["accent_block_left_in"], g["accent_block_top_in"],
              g["accent_block_width_in"], g["accent_block_height_in"], "royal")

    # 角落圓格網與斜線紋：Slidesgo 的幾何裝飾語彙，純視覺、不承載資訊。
    for row in range(int(g["dots_rows"])):
        for col in range(int(g["dots_cols"])):
            _add_oval(slide, theme,
                      g["dots_left_in"] + col * g["dots_step_in"],
                      g["dots_top_in"] + row * g["dots_step_in"],
                      g["dots_size_in"], g["dots_size_in"], "accent")
    for index in range(int(g["stripe_count"])):
        stripe = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(g["stripe_left_in"] + index * g["stripe_step_in"]), Inches(g["stripe_top_in"]),
            Inches(g["stripe_width_in"]), Inches(g["stripe_height_in"]),
        )
        stripe.fill.solid()
        stripe.fill.fore_color.rgb = theme.rgb("royal")
        stripe.line.fill.background()
        stripe.shadow.inherit = False
        stripe.rotation = g["stripe_rotation_deg"]
        stripe.text_frame.text = ""

    _add_text(slide, theme, "專利情報整合分析",
              left=g["eyebrow_left_in"], top=g["eyebrow_top_in"],
              width=g["eyebrow_width_in"], height=g["eyebrow_height_in"],
              size=theme.size("cover_subtitle_pt"), color="accent", bold=True)

    title = _cover_title(ctx["report_data"], ctx["slots"])
    title, _ = _fit_text(theme, title, width_in=g["title_width_in"], height_in=g["title_height_in"],
                         size_pt=theme.size("cover_title_pt"))
    _add_text(slide, theme, title,
              left=g["title_left_in"], top=g["title_top_in"],
              width=g["title_width_in"], height=g["title_height_in"],
              size=theme.size("cover_title_pt"), color="ink", bold=True)
    _add_band(slide, theme, g["rule_left_in"], g["rule_top_in"], g["rule_width_in"], g["rule_height_in"], "accent")

    period = ctx["period"]
    # ⚠ 2026-07-31 使用者定案：封面也不印報表版本（內部識別碼對讀者無意義）。
    #    可追溯性由 manifest 保留；頁尾同步移除，見 _render_footnote。
    _add_text(slide, theme,
              f"統計期間 {period}" if period else "",
              left=g["period_left_in"], top=g["period_top_in"],
              width=g["period_width_in"], height=g["period_height_in"],
              size=theme.size("cover_subtitle_pt"), color="muted")

    for index, (value, unit, label) in enumerate(ctx["cover_stats"]):
        left = g["stat_left_in"] + index * g["stat_gap_in"]
        _add_band(slide, theme, left, g["stat_top_in"], g["stat_width_in"], g["stat_height_in"],
                  "panel", rounded=True)
        _add_band(slide, theme, left, g["stat_top_in"], g["stat_width_in"], g["stat_accent_height_in"], "accent")
        # 大數字分級：卡片寬度固定，值太長就降級字級，避免撐出卡片外（封面壓字的舊病）。
        if len(value) <= 4:
            value_size = theme.size("stat_value_pt")
        elif len(value) <= 8:
            value_size = theme.size("stat_value_medium_pt")
        else:
            value_size = theme.size("stat_value_small_pt")
        _add_text(slide, theme, value,
                  left=left, top=g["stat_value_top_in"],
                  width=g["stat_width_in"], height=g["stat_value_height_in"],
                  # v3：卡片底色就是 navy，數值再用 navy 等於隱形（實機轉圖驗到）。
                  size=value_size, color="on_dark", bold=True, align=PP_ALIGN.CENTER)
        _add_text(slide, theme, unit,
                  left=left, top=g["stat_unit_top_in"],
                  width=g["stat_width_in"], height=g["stat_unit_height_in"],
                  size=theme.size("stat_unit_pt"), color="blue", bold=True, align=PP_ALIGN.CENTER)
        _add_text(slide, theme, label,
                  left=left + g["stat_label_inset_in"], top=g["stat_label_top_in"],
                  width=g["stat_width_in"] - g["stat_label_inset_in"] * 2, height=g["stat_label_height_in"],
                  size=theme.size("stat_label_pt"), color="ink", align=PP_ALIGN.CENTER)

    _add_band(slide, theme, g["banner_left_in"], g["banner_top_in"],
              g["banner_width_in"], g["banner_height_in"], "panel", rounded=True)
    banner, _ = _fit_text(theme, ctx["framework_text"],
                          width_in=g["banner_text_width_in"], height_in=g["banner_text_height_in"],
                          size_pt=theme.size("cover_banner_pt"))
    _add_text(slide, theme, banner,
              left=g["banner_left_in"] + g["banner_text_inset_left_in"],
              top=g["banner_top_in"] + g["banner_text_top_offset_in"],
              width=g["banner_text_width_in"], height=g["banner_text_height_in"],
              size=theme.size("cover_banner_pt"), color="on_dark_soft")


def _render_section_divider(slide, theme: Theme, spec: PageSpec, ctx: dict[str, Any]) -> None:
    """章節隔頁：深色塊大字，只做段落分隔。

    ⚠ v3 起不再自畫整頁底色（同 `_render_cover` 的理由：會蓋掉漸層與星空）。
    ⚠ 目前大綱不使用本版型——使用者定案「不加章節號、不做章節隔頁」，
    論證鏈靠頁序本身表達。保留 renderer 是因為 layout_overrides 仍可指定它。
    """
    g = theme.geometry["section_divider"]
    _add_band(slide, theme, g["accent_left_in"], g["accent_top_in"],
              g["accent_width_in"], g["accent_height_in"], "accent")
    _add_text(slide, theme, spec.title,
              left=g["title_left_in"], top=g["title_top_in"],
              width=g["title_width_in"], height=g["title_height_in"],
              size=theme.size("section_title_pt"), color="on_dark", bold=True)
    if spec.subtitle:
        _add_text(slide, theme, spec.subtitle,
                  left=g["subtitle_left_in"], top=g["subtitle_top_in"],
                  width=g["subtitle_width_in"], height=g["subtitle_height_in"],
                  size=theme.size("section_subtitle_pt"), color="on_dark_soft")


def _conclusion_text(headline: str, points: list[dict[str, Any]]) -> str:
    """核心結論條的文字：emphasis 那條 → 「意涵」那條 → headline。

    NotebookLM 範例的手法：每頁底部一句話收束。優先取 narrative 自己標記
    最重要的（emphasis），其次判讀性最強的「意涵」，都沒有才退回標題句。
    """
    for point in points:
        if point.get("emphasis"):
            return str(point.get("text") or "")
    for point in points:
        if "意涵" in str(point.get("label") or ""):
            return str(point.get("text") or "")
    return headline


def _render_chart_hero(slide, theme: Theme, spec: PageSpec, ctx: dict[str, Any]) -> None:
    """大圖版型（P1-1，內容頁新預設）：圖佔主幅＋右側精簡註解卡＋底部核心結論條。

    「圖表為主、文字為輔」的落實：文字不是消失，而是（a）判讀進標題、
    （b）要點成右側小卡、（c）最重要一句進底部結論條。
    ⚠ 註解卡放固定區，不精準指向圖內資料點——不動 SVG 內部就拿不到錨點座標。
    """
    _render_header(slide, theme, spec, ctx)
    g = theme.geometry["chart_hero"]
    _add_text(slide, theme, _encoding_note(spec),
              left=g["encoding_left_in"], top=g["encoding_top_in"],
              width=g["encoding_width_in"], height=g["encoding_height_in"],
              size=theme.size("encoding_note_pt"), color="muted", align=PP_ALIGN.RIGHT)
    image = ctx["charts"].resolve(spec.charts[0]) if spec.charts else None
    if image is not None:
        _add_picture_fitted(slide, image,
                            left=g["image_left_in"], top=g["image_top_in"],
                            width=g["image_width_in"], height=g["image_height_in"])

    headline, points, _ = ctx["narratives_by_page"].get(spec.page, ("", [], False))
    conclusion = _conclusion_text(headline, points)
    # 右欄＝完整要點面板（2026-07-31 二輪回饋「字太省」：原本固定 3 張小卡
    # 塞不下 4–6 條判讀）。結論那條不重複（已在底部條）；判讀限制作尾條。
    listed = [p for p in points if str(p.get("text") or "") != conclusion]
    if not listed and not points:
        listed = [{"label": label, "text": text, "emphasis": False}
                  for label, text in _row_highlights(spec, ctx)]
    blocks = [(str(p.get("label") or ""), str(p.get("text") or ""),
               "alert" if p.get("emphasis") else "ink", bool(p.get("emphasis")))
              for p in listed]
    caveat = _caveat_of(spec)
    if caveat:
        blocks = blocks + [(CAVEAT_LABEL, caveat, "muted", False)]
    _add_band(slide, theme, g["panel_left_in"], g["panel_top_in"],
              g["panel_width_in"], g["panel_height_in"], "panel", rounded=True)
    _add_text(slide, theme, "判讀要點",
              left=g["panel_left_in"] + g["panel_inset_in"],
              top=g["panel_top_in"] + g["panel_header_top_offset_in"],
              width=g["panel_width_in"] - g["panel_inset_in"] * 2,
              height=g["panel_header_height_in"],
              size=theme.size("panel_header_pt"), color="accent", bold=True)
    text_width = g["panel_width_in"] - g["panel_inset_in"] * 2
    text_height = g["panel_height_in"] - g["panel_text_top_offset_in"] - g["panel_inset_in"]
    size = theme.size("point_text_pt")
    _add_number_bold_text(slide, theme,
                          _trim_blocks(theme, blocks, width_in=text_width,
                                       height_in=text_height, size_pt=size),
                          left=g["panel_left_in"] + g["panel_inset_in"],
                          top=g["panel_top_in"] + g["panel_text_top_offset_in"],
                          width=text_width, height=text_height, size=size)

    if conclusion:
        _add_band(slide, theme, g["conclusion_left_in"], g["conclusion_top_in"],
                  g["conclusion_width_in"], g["conclusion_height_in"], "panel_deep", rounded=True)
        text_width = g["conclusion_width_in"] - g["conclusion_inset_left_in"] * 2
        body, _ = _fit_text(theme, f"核心結論：{conclusion}", width_in=text_width,
                            height_in=g["conclusion_height_in"] - g["conclusion_text_top_offset_in"] * 2,
                            size_pt=theme.size("conclusion_pt"))
        _add_text(slide, theme, body,
                  left=g["conclusion_left_in"] + g["conclusion_inset_left_in"],
                  top=g["conclusion_top_in"] + g["conclusion_text_top_offset_in"],
                  width=text_width,
                  height=g["conclusion_height_in"] - g["conclusion_text_top_offset_in"] * 2,
                  size=theme.size("conclusion_pt"), color="on_dark", bold=True)
    _render_footnote(slide, theme, spec, ctx)


def _render_chart_with_points(slide, theme: Theme, spec: PageSpec, ctx: dict[str, Any]) -> None:
    """內容頁預設版型：左圖約 60% 寬，右側要點框（＋必要時警語框）。"""
    _render_header(slide, theme, spec, ctx)
    g = theme.geometry["chart_with_points"]
    _add_text(slide, theme, _encoding_note(spec),
              left=g["encoding_left_in"], top=g["encoding_top_in"],
              width=g["encoding_width_in"], height=g["encoding_height_in"],
              size=theme.size("encoding_note_pt"), color="muted", align=PP_ALIGN.RIGHT)
    image = ctx["charts"].resolve(spec.charts[0]) if spec.charts else None
    if image is not None:
        _add_picture_fitted(slide, image,
                            left=g["image_left_in"], top=g["image_top_in"],
                            width=g["image_width_in"], height=g["image_height_in"])
    _render_points_panel(slide, theme, spec, ctx)
    _render_footnote(slide, theme, spec, ctx)


def _render_comparison(slide, theme: Theme, spec: PageSpec, ctx: dict[str, Any]) -> None:
    """成對報表同頁左右並排：兩張圖各配子標、圖例編碼與要點。

    ⚠ 成對報表（IPC/CPC 的 L4 與 L5、機會矩陣的技術面與功效面）**禁止合成同一張圖**；
    合成會讓兩個不同母體的數字被讀成同一組，這裡只做並排比較。
    """
    _render_header(slide, theme, spec, ctx)
    g = theme.geometry["comparison"]
    note = _encoding_note(spec)
    blocks = _points_for(spec, ctx)
    for index, chart_name in enumerate(spec.charts[: len(g["column_left_in"])]):
        left = g["column_left_in"][index]
        width = g["column_width_in"]
        _add_text(slide, theme, _variant_label(chart_name),
                  left=left, top=g["caption_top_in"], width=width, height=g["caption_height_in"],
                  size=theme.size("chart_caption_pt"), color="accent", bold=True)
        _add_text(slide, theme, note,
                  left=left, top=g["encoding_top_in"], width=width, height=g["encoding_height_in"],
                  size=theme.size("encoding_note_pt"), color="muted")
        image = ctx["charts"].resolve(chart_name)
        if image is not None:
            _add_picture_fitted(slide, image,
                                left=left, top=g["image_top_in"], width=width, height=g["image_height_in"])
        _add_band(slide, theme, left, g["points_top_in"], width, g["points_height_in"], "panel", rounded=True)
        text_width = width - g["points_inset_right_in"]
        text_height = g["points_height_in"] - g["points_top_offset_in"] - g["points_bottom_pad_in"]
        size = theme.size("point_text_pt")
        half = blocks[index::2] or blocks[:1]
        _add_number_bold_text(slide, theme,
                              _trim_blocks(theme, half, width_in=text_width, height_in=text_height, size_pt=size),
                              left=left + g["points_inset_left_in"], top=g["points_top_in"] + g["points_top_offset_in"],
                              width=text_width, height=text_height, size=size)
    _render_footnote(slide, theme, spec, ctx)


def _render_stat_callout(slide, theme: Theme, spec: PageSpec, ctx: dict[str, Any]) -> None:
    """大數字焦點頁；同時是「圖檔缺失時的降級版型」——確保每頁都有視覺元素。"""
    _render_header(slide, theme, spec, ctx)
    g = theme.geometry["stat_callout"]
    _add_band(slide, theme, g["block_left_in"], g["block_top_in"],
              g["block_width_in"], g["block_height_in"], "panel", rounded=True)

    rows: list[dict[str, Any]] = []
    report_key = ""
    for key in spec.report_keys:
        rows = _rows_of(ctx["report_data"], key)
        if rows:
            report_key = key
            break
    numeric = _numeric_column(rows)
    label_col = _label_column(rows, numeric) if numeric else ""
    total = sum(_as_int(row.get(numeric)) for row in rows) if numeric else 0

    _add_text(slide, theme, f"{total:,}" if total else str(len(rows)),
              left=g["value_left_in"], top=g["value_top_in"],
              width=g["value_width_in"], height=g["value_height_in"],
              size=theme.size("callout_value_pt"), color="on_dark", bold=True)
    _add_text(slide, theme, "件" if total else "筆",
              left=g["unit_left_in"], top=g["unit_top_in"],
              width=g["unit_width_in"], height=g["unit_height_in"],
              size=theme.size("callout_unit_pt"), color="accent", bold=True)
    _add_text(slide, theme, _label_of(ctx["report_data"], report_key, spec.topic) if report_key else spec.topic,
              left=g["label_left_in"], top=g["label_top_in"],
              width=g["label_width_in"], height=g["label_height_in"],
              size=theme.size("callout_label_pt"), color="on_dark_soft")
    _add_band(slide, theme, g["rule_left_in"], g["rule_top_in"], g["rule_width_in"], g["rule_height_in"], "accent")

    ranked = sorted(rows, key=lambda r: _as_int(r.get(numeric)), reverse=True) if numeric else []
    for index, row in enumerate(ranked[: int(g["row_max"])]):
        _add_text(slide, theme,
                  f"{row.get(label_col, '')}　{_as_int(row.get(numeric)):,} 件",
                  left=g["row_left_in"], top=g["row_top_in"] + index * g["row_step_in"],
                  width=g["row_width_in"], height=g["row_height_in"],
                  size=theme.size("callout_row_pt"), color="on_dark_soft")

    panel = theme.geometry["points_panel"]
    caveat = _caveat_of(spec)
    _add_band(slide, theme, g["points_left_in"], g["points_top_in"],
              g["points_width_in"], g["points_height_in"], "panel", rounded=True)
    _add_text(slide, theme, "判讀要點",
              left=g["points_left_in"] + panel["header_inset_left_in"],
              top=g["points_top_in"] + panel["header_top_offset_in"],
              width=g["points_width_in"] - panel["text_inset_right_in"], height=panel["header_height_in"],
              size=theme.size("panel_header_pt"), color="accent", bold=True)
    text_width = g["points_width_in"] - panel["text_inset_right_in"]
    text_height = g["points_height_in"] - panel["text_top_offset_in"] - panel["text_bottom_pad_in"]
    size = theme.size("point_text_pt")
    blocks = _points_for(spec, ctx)
    if caveat:
        blocks = blocks + [(CAVEAT_LABEL, caveat, "muted", False)]
    _add_number_bold_text(slide, theme,
                          _trim_blocks(theme, blocks, width_in=text_width, height_in=text_height, size_pt=size),
                          left=g["points_left_in"] + panel["text_inset_left_in"],
                          top=g["points_top_in"] + panel["text_top_offset_in"],
                          width=text_width, height=text_height, size=size)
    _render_footnote(slide, theme, spec, ctx)


def _render_percentage_bars(slide, theme: Theme, spec: PageSpec, ctx: dict[str, Any]) -> None:
    """佔比條列（如受理國分布）：條長＝佔比，右側數值為實際件數。"""
    _render_header(slide, theme, spec, ctx)
    g = theme.geometry["percentage_bars"]
    rows: list[dict[str, Any]] = []
    for key in spec.report_keys:
        rows = _rows_of(ctx["report_data"], key)
        if rows:
            break
    numeric = _numeric_column(rows)
    label_col = _label_column(rows, numeric) if numeric else ""
    ranked = sorted(rows, key=lambda r: _as_int(r.get(numeric)), reverse=True) if numeric else []
    ranked = ranked[: int(g["row_max"])]
    top_value = max((_as_int(r.get(numeric)) for r in ranked), default=0)
    total = sum(_as_int(r.get(numeric)) for r in ranked) or 1

    # 條數少時整組垂直置中，避免條列擠在上方、下半頁一片空白。
    body = theme.geometry
    span = len(ranked) * g["row_step_in"]
    room = body["body_top_in"] + body["body_height_in"] - g["row_top_in"]
    first_top = g["row_top_in"] + max(0.0, (room - span) / 2)

    for index, row in enumerate(ranked):
        top = first_top + index * g["row_step_in"]
        value = _as_int(row.get(numeric))
        _add_text(slide, theme, str(row.get(label_col, "")),
                  left=g["row_left_in"], top=top, width=g["label_width_in"], height=g["label_height_in"],
                  size=theme.size("percent_label_pt"), color="ink", bold=True)
        _add_band(slide, theme, g["track_left_in"], top + g["track_top_offset_in"],
                  g["track_width_in"], g["track_height_in"], "bar_track", rounded=True)
        ratio = (value / top_value) if top_value else 0.0
        fill_width = max(g["track_height_in"], g["track_width_in"] * ratio)
        _add_band(slide, theme, g["track_left_in"], top + g["track_top_offset_in"],
                  # 🔴 批2：原本第一名用 royal、其餘用 blue——兩者都是**裝飾色**
                  # （色相距 accent 僅 0.6°／0.7°），使用者反映「資料看起來像裝飾」；
                  # 且 royal 比 blue 暗，等於**最大值最不顯眼**、語意反了。
                  # 改為資料暖色，第一名用主序列、其餘用淺階，明暗與大小一致。
                  fill_width, g["track_height_in"],
                  "series_primary" if index == 0 else "series_light", rounded=True)
        _add_text(slide, theme, f"{value:,} 件　{value / total:.0%}",
                  left=g["value_left_in"], top=top, width=g["value_width_in"], height=g["value_height_in"],
                  size=theme.size("percent_value_pt"), color="ink", bold=True)
    _render_points_panel(slide, theme, spec, ctx)
    _render_footnote(slide, theme, spec, ctx)


def _render_table(slide, theme: Theme, spec: PageSpec, ctx: dict[str, Any]) -> None:
    """全寬表格（附錄）：直接列引擎 rows，不加解讀。"""
    _render_header(slide, theme, spec, ctx)
    g = theme.geometry["table"]
    rows = _first_rows(spec, ctx)
    labels, excluded = _table_display(ctx, spec)
    shown = _add_table(slide, theme, rows,
                       left=g["left_in"], top=g["top_in"], width=g["width_in"], height=g["height_in"],
                       row_height=g["row_height_in"], max_columns=int(g["max_columns"]),
                       cell_margin_in=g["cell_margin_in"], cell_inset_in=g["cell_inset_in"],
                       labels=labels, excluded=excluded)
    _render_footnote(slide, theme, spec, ctx,
                     _rows_note(shown, rows, int(g["max_columns"]), _visible_column_count(rows, excluded)))


def _render_table_with_points(slide, theme: Theme, spec: PageSpec, ctx: dict[str, Any]) -> None:
    """表格＋底部要點橫幅：明細與判讀同頁（v3 改滿版，2026-07-31）。

    ⚠ 這類頁面（技術／功效主題分布）**沒有圖**，右欄本來就不必讓給要點卡。
    量測：舊版 8.05 in ÷ 4 欄＝每欄 2.01 in；改滿版 12.13 in ÷ 6 欄＝每欄 2.02 in
    ——欄寬幾乎不變，欄位卻從 4 欄變 6 欄全到齊。要點改放底部橫幅雙欄。
    """
    _render_header(slide, theme, spec, ctx)
    g = theme.geometry["table_with_points"]
    rows = _first_rows(spec, ctx)
    labels, excluded = _table_display(ctx, spec)
    shown = _add_table(slide, theme, rows,
                       left=g["left_in"], top=g["top_in"], width=g["width_in"], height=g["height_in"],
                       row_height=g["row_height_in"], max_columns=int(g["max_columns"]),
                       cell_margin_in=g["cell_margin_in"], cell_inset_in=g["cell_inset_in"],
                       labels=labels, excluded=excluded)
    # 要點橫幅跟著表格底緣走：表高會依實際列數收縮（主題常只有 5–8 列），
    # 橫幅若固定在「排滿 10 列」的位置，中間會空一大塊（實機轉圖驗到）。
    # ⚠ 只往上收、不往下移：theme 的 top 是**最低**位置，超過就會壓到頁尾。
    table_bottom = g["top_in"] + min(g["height_in"], (shown + 1) * g["row_height_in"])
    _render_points_band(slide, theme, spec, ctx,
                        top=min(g["points_band_top_in"], table_bottom + theme.geometry["column_gap_in"]))
    _render_footnote(slide, theme, spec, ctx,
                     _rows_note(shown, rows, int(g["max_columns"]), _visible_column_count(rows, excluded)))


def _parse_direction_body(body: str) -> dict[str, Any] | None:
    """解析結構化 direction.body（P1-7 合併版契約）；不是合法 JSON 就回 None。

    契約形狀（content_rules 同步定義；AI 產 JSON 字串塞進 slot）：
        {"situation": [..], "opportunity": [..], "direction": [..],
         "topics": [{"name","basis","action"}, ..], "conclusion": ".."}
    ⚠ 舊純文字要能過渡：回 None 讓 renderer 走舊的條列版面，不炸、不硬轉。
    """
    text = str(body or "").strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return {
        "situation": [str(x) for x in parsed.get("situation") or []],
        "opportunity": [str(x) for x in parsed.get("opportunity") or []],
        "direction": [str(x) for x in parsed.get("direction") or []],
        "topics": [t for t in parsed.get("topics") or [] if isinstance(t, dict)],
        "conclusion": str(parsed.get("conclusion") or ""),
    }


def _render_direction_flow(slide, theme: Theme, spec: PageSpec, ctx: dict[str, Any],
                           parsed: dict[str, Any]) -> None:
    """合併版（2026-07-31 使用者經表單選定）：
    上半「態勢→機會→方向」三步色塊流程、下半研發題目卡片、底部核心結論條。
    python-pptx 畫不了原生 SmartArt——色塊＋箭頭是同等效果的確定性圖形。
    """
    g = theme.geometry["direction_flow"]
    # 🔴 2026-07-31 批2：三步原本用 royal／blue／navy，明暗完全不一致——
    # `blue`(4FC3F7) 是亮青，配 on_dark_soft 深灰字等於讀不出來（實機第 17 頁）。
    # 改為三階**都夠深**的同族藍，靠由深到淺表達流程推進，文字一律亮字。
    # ⚠ 三張卡是**並列的同階資訊**（y/w/h 完全相同），不是有大小的序階，
    # 故底色一律相同——流程推進由中間的箭頭表達。批2 初版讓三者由暗到亮遞增，
    # 獨立驗收指出那是語意錯亂，且最暗的「態勢」卡對背景只有 1.343 不達標。
    steps = (("態勢", parsed["situation"], "panel"),
             ("機會", parsed["opportunity"], "panel"),
             ("方向", parsed["direction"], "panel"))
    for index, (label, lines, color) in enumerate(steps):
        left = g["step_left_in"] + index * g["step_gap_in"]
        _add_band(slide, theme, left, g["step_top_in"], g["step_width_in"], g["step_height_in"],
                  color, rounded=True, line="royal")
        _add_text(slide, theme, label,
                  left=left + g["step_inset_in"], top=g["step_top_in"] + g["step_label_top_offset_in"],
                  width=g["step_width_in"] - g["step_inset_in"] * 2, height=g["step_label_height_in"],
                  size=theme.size("flow_label_pt"), color="accent", bold=True)
        body_width = g["step_width_in"] - g["step_inset_in"] * 2
        body_height = g["step_height_in"] - g["step_text_top_offset_in"] - g["step_inset_in"]
        body, _ = _fit_text(theme, "\n".join(lines), width_in=body_width, height_in=body_height,
                            size_pt=theme.size("flow_text_pt"))
        _add_text(slide, theme, body,
                  left=left + g["step_inset_in"], top=g["step_top_in"] + g["step_text_top_offset_in"],
                  width=body_width, height=body_height,
                  # 批2：on_dark_soft 在最亮的 panel_deep 上只有 3.28，改亮字。
                  size=theme.size("flow_text_pt"), color="on_dark")
        if index < len(steps) - 1:
            _add_text(slide, theme, "→",
                      left=left + g["step_width_in"] + g["arrow_left_offset_in"],
                      top=g["arrow_top_in"], width=g["arrow_width_in"], height=g["arrow_height_in"],
                      size=theme.size("flow_arrow_pt"), color="accent", bold=True,
                      align=PP_ALIGN.CENTER)

    topics = parsed["topics"][: int(g["topic_max"])]
    for index, topic in enumerate(topics):
        left = g["topic_left_in"] + index * g["topic_gap_in"]
        _add_band(slide, theme, left, g["topic_top_in"], g["topic_width_in"], g["topic_height_in"],
                  "panel", rounded=True)
        _add_band(slide, theme, left, g["topic_top_in"], g["topic_width_in"],
                  g["topic_accent_height_in"], "accent")
        _add_text(slide, theme, str(topic.get("name") or ""),
                  left=left + g["topic_inset_in"], top=g["topic_top_in"] + g["topic_name_top_offset_in"],
                  width=g["topic_width_in"] - g["topic_inset_in"] * 2, height=g["topic_name_height_in"],
                  size=theme.size("topic_name_pt"), color="ink", bold=True)
        detail_lines = []
        if topic.get("basis"):
            detail_lines.append(f"依據｜{topic['basis']}")
        if topic.get("action"):
            detail_lines.append(f"行動｜{topic['action']}")
        body_width = g["topic_width_in"] - g["topic_inset_in"] * 2
        body_height = g["topic_height_in"] - g["topic_text_top_offset_in"] - g["topic_inset_in"]
        body, _ = _fit_text(theme, "\n".join(detail_lines), width_in=body_width,
                            height_in=body_height, size_pt=theme.size("topic_text_pt"))
        _add_text(slide, theme, body,
                  left=left + g["topic_inset_in"], top=g["topic_top_in"] + g["topic_text_top_offset_in"],
                  width=body_width, height=body_height,
                  size=theme.size("topic_text_pt"), color="ink")

    if parsed["conclusion"]:
        _add_band(slide, theme, g["conclusion_left_in"], g["conclusion_top_in"],
                  g["conclusion_width_in"], g["conclusion_height_in"], "panel_deep", rounded=True)
        text_width = g["conclusion_width_in"] - g["conclusion_inset_in"] * 2
        body, _ = _fit_text(theme, f"核心結論：{parsed['conclusion']}", width_in=text_width,
                            height_in=g["conclusion_height_in"] - g["conclusion_text_top_offset_in"] * 2,
                            size_pt=theme.size("conclusion_pt"))
        _add_text(slide, theme, body,
                  left=g["conclusion_left_in"] + g["conclusion_inset_in"],
                  top=g["conclusion_top_in"] + g["conclusion_text_top_offset_in"],
                  width=text_width,
                  height=g["conclusion_height_in"] - g["conclusion_text_top_offset_in"] * 2,
                  size=theme.size("conclusion_pt"), color="on_dark", bold=True)


def _render_direction(slide, theme: Theme, spec: PageSpec, ctx: dict[str, Any]) -> None:
    """研發方向建議（結論頁，壓軸）。

    結構化 direction.body → 合併版（色塊流程＋題目卡＋結論條）；
    舊純文字 → 走原條列版面過渡，並由 build_ppt 記 warning（不靜默）。
    """
    _render_header(slide, theme, spec, ctx)
    parsed = _parse_direction_body(ctx["slots"].get("direction.body") or "")
    if parsed is not None:
        _render_direction_flow(slide, theme, spec, ctx, parsed)
        _render_footnote(slide, theme, spec, ctx)
        return
    g = theme.geometry["direction"]
    _add_band(slide, theme, g["body_left_in"], g["body_top_in"],
              g["body_width_in"], g["body_height_in"], "panel", rounded=True)
    _add_text(slide, theme, "研發方向與具體題目",
              left=g["body_left_in"] + g["body_header_inset_left_in"],
              top=g["body_top_in"] + g["body_header_top_offset_in"],
              width=g["body_width_in"] - g["body_text_inset_right_in"], height=g["body_header_height_in"],
              size=theme.size("panel_header_pt"), color="accent", bold=True)
    text_width = g["body_width_in"] - g["body_text_inset_right_in"]
    text_height = g["body_height_in"] - g["body_text_top_offset_in"] - g["body_text_bottom_pad_in"]
    size = theme.size("body_pt")
    body = ctx["slots"].get("direction.body") or ""
    blocks = [("", line.strip(), "ink", False) for line in body.split("\n") if line.strip()]
    if not blocks:
        blocks = [(label, text, "ink", False) for label, text in _row_highlights(spec, ctx)]
    _add_number_bold_text(slide, theme,
                          _trim_blocks(theme, blocks, width_in=text_width, height_in=text_height, size_pt=size),
                          left=g["body_left_in"] + g["body_text_inset_left_in"],
                          top=g["body_top_in"] + g["body_text_top_offset_in"],
                          width=text_width, height=text_height, size=size)

    _add_band(slide, theme, g["basis_left_in"], g["basis_top_in"],
              g["basis_width_in"], g["basis_height_in"], "panel", rounded=True)
    _add_text(slide, theme, "專利地圖依據",
              left=g["basis_left_in"] + g["basis_header_inset_left_in"],
              top=g["basis_top_in"] + g["basis_header_top_offset_in"],
              width=g["basis_item_width_in"], height=g["basis_header_height_in"],
              size=theme.size("panel_header_pt"), color="accent", bold=True)
    for index, label in enumerate(ctx["included_report_labels"][: int(g["basis_item_max"])]):
        text, _ = _fit_text(theme, f"・{label}", width_in=g["basis_item_width_in"],
                            height_in=g["basis_item_height_in"], size_pt=theme.size("point_label_pt"))
        _add_text(slide, theme, text,
                  left=g["basis_left_in"] + g["basis_item_inset_left_in"],
                  top=g["basis_item_top_in"] + index * g["basis_item_step_in"],
                  width=g["basis_item_width_in"], height=g["basis_item_height_in"],
                  size=theme.size("point_label_pt"), color="on_dark_soft")
    _render_footnote(slide, theme, spec, ctx)


def _table_display(ctx: dict[str, Any], spec: PageSpec) -> tuple[dict[str, str], set[str]]:
    """本頁表格的欄名對照與排除欄：引擎那份優先，缺鍵才用本檔 fallback。

    排除欄是**逐報表**的（同一欄在 A 報表要藏、在 B 報表要顯示），故依本頁掛的
    report_keys 逐一併集。
    """
    display = ctx["report_data"].get("table_display") or {}
    labels = {**TABLE_COLUMN_LABELS, **(display.get("column_labels") or {})}
    excluded = set(TABLE_EXCLUDED_COLUMNS)
    per_report = display.get("excluded_columns") or {}
    for key in spec.report_keys:
        excluded.update(per_report.get(key) or ())
    return labels, excluded


def _rows_note(shown: int, rows: list[dict[str, Any]], max_columns: int, visible_columns: int) -> str:
    """表格截列／截欄時據實說明，避免讀者把前 N 筆當成全部。

    ⚠ `visible_columns` 是**排除欄之後**的欄數：拿原始欄數去比會把引擎刻意藏起來
    的欄（topic_code、龍頭涉入等）算進分母，印出「前 6/10 欄」這種嚇人又不實的註記。
    """
    notes = []
    if rows and shown < len(rows):
        notes.append(f"顯示前 {shown}/{len(rows)} 筆")
    if visible_columns > max_columns:
        notes.append(f"前 {max_columns}/{visible_columns} 欄，完整欄位見附錄")
    return "、".join(notes)


def _first_rows(spec: PageSpec, ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """取本頁表格要印的 rows；帶 row_filter 的頁（依通道拆頁）只留匹配的列。

    ⚠ 優先取引擎寫在 `table_display.display_rows` 的**呈現字串**：`top_applicants`
    這類欄的原始值是物件陣列，直接印會變成 `{'name': '祺驊', ...`（2026-07-31 實機
    第 9、10、18 頁）。呈現規則的唯一來源在引擎（`chart_runner._humanize_cell`），
    本檔不自建第二份；舊報表版本沒有這個鍵時退回原始 rows。
    """
    display = (ctx["report_data"].get("table_display") or {}).get("display_rows") or {}
    filters = dict(spec.row_filter)
    for key in spec.report_keys:
        rows = display.get(key) or _rows_of(ctx["report_data"], key)
        if filters:
            rows = [r for r in rows
                    if all(str(r.get(col)) == value for col, value in filters.items())]
        if rows:
            return rows
    return []


def _add_table(
    slide,
    theme: Theme,
    rows: list[dict[str, Any]],
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    row_height: float,
    max_columns: int,
    cell_margin_in: float,
    cell_inset_in: float,
    labels: dict[str, str],
    excluded: set[str],
) -> int:
    """把引擎 rows 畫成表格。

    列數依框高與列高換算後截斷（PowerPoint 的表格列高只增不減，塞太多必溢出），
    儲存格字數也依欄寬截斷，避免自動換行把列撐高。
    """
    if not rows:
        g = theme.geometry["table"]
        _add_band(slide, theme, left, top, width, height, "panel", rounded=True)
        _add_text(slide, theme, "本頁報表無資料列",
                  left=g["empty_text_left_in"], top=g["empty_text_top_in"],
                  width=g["empty_text_width_in"], height=g["empty_text_height_in"],
                  size=theme.size("table_body_pt"), color="muted", align=PP_ALIGN.CENTER)
        return 0

    # 欄位顯示規則：排除欄與中文欄名以引擎那份為準（labels／excluded 由呼叫端備妥），
    # 欄值轉譯仍在本檔（source_field 的原始欄值不得入畫面，轉「技術／功效」）。
    columns = [name for name in rows[0] if str(name) not in excluded][:max_columns]
    max_rows = max(1, int(height / row_height) - 1)
    display = rows[:max_rows]
    # 表高依實際列數收縮：宣告高度是**上限**不是固定值，列少時下半截留白會很難看
    # （主題分布通常只有 8–12 列，舊版固定 4.86 in 有一半是空的）。
    used_height = min(height, (len(display) + 1) * row_height)
    table = slide.shapes.add_table(
        len(display) + 1, len(columns), Inches(left), Inches(top), Inches(width), Inches(used_height)
    ).table
    for row in table.rows:
        row.height = Inches(row_height)

    # 扣掉左右內距後才是真正可用的文字寬度；截字到這個寬度內，儲存格就不會自動換行把列撐高。
    text_width = width / len(columns) - cell_inset_in * 2
    for index, name in enumerate(columns):
        cell = table.cell(0, index)
        shown = labels.get(str(name), str(name))
        cell.text = _truncate_to_width(shown, text_width, theme.size("table_header_pt"))
        # v3 深空：表頭深藍底＋accent 青字（原白字在深底上與內文分不出層次）。
        _style_cell(cell, theme, size=theme.size("table_header_pt"), color="accent", bold=True,
                    fill="navy", margin_in=cell_margin_in, inset_in=cell_inset_in)
    for r, row in enumerate(display, start=1):
        for c, name in enumerate(columns):
            value = row.get(name)
            mapped = TABLE_VALUE_LABELS.get(str(name), {})
            if isinstance(value, list):
                value = "、".join(str(v) for v in value)
            value = mapped.get(str(value), value) if mapped else value
            cell = table.cell(r, c)
            cell.text = "" if value is None else _truncate_to_width(
                str(value), text_width, theme.size("table_body_pt")
            )
            # bold=True：v3 使用者定案「文字內容加粗體」，深底細字會發灰。
            _style_cell(cell, theme, size=theme.size("table_body_pt"), color="ink", bold=True,
                        fill="paper" if r % 2 else "panel_alt",
                        margin_in=cell_margin_in, inset_in=cell_inset_in)
    return len(display)


def _set_cell_borders(cell, theme: Theme) -> None:
    """給儲存格四邊套主題細線。

    ⚠ python-pptx 沒有儲存格框線 API，不寫就會沿用 PowerPoint **預設表格樣式**的
    淺灰線（實測約 A8B3BD–DFE0E3），與全簡報統一的細線不一致——規格 S1-6 寫了
    「外框細線」卻一直沒實作，獨立驗收在 p9/p10/p18/p19 抓到。
    ⚠ 四邊都要寫：只寫左與上，相鄰儲存格之間會留下缺口。
    """
    colour = theme.color["hairline"]
    width = int(Pt(theme.font["panel_border_pt"]))
    props = cell._tc.get_or_add_tcPr()
    for tag in ("a:lnL", "a:lnR", "a:lnT", "a:lnB"):
        existing = props.find(qn(tag))
        if existing is not None:
            props.remove(existing)
        props.append(parse_xml(
            f'<{tag} {nsdecls("a")} w="{width}" cap="flat" cmpd="sng" algn="ctr">'
            f'<a:solidFill><a:srgbClr val="{colour}"/></a:solidFill>'
            f'</{tag}>'
        ))


def _style_cell(
    cell, theme: Theme, *, size: float, color: str, bold: bool = False, fill: str = "paper",
    margin_in: float = 0.0, inset_in: float = 0.0,
) -> None:
    """套儲存格底色與字體。

    ⚠ python-pptx 的儲存格預設上下內距 0.05 in，PowerPoint 的列高只增不減——
    預設內距會讓實際列高遠大於宣告值，整張表往下溢出版面。故一律壓縮內距。
    """
    cell.fill.solid()
    cell.fill.fore_color.rgb = theme.rgb(fill)
    cell.margin_top = Inches(margin_in)
    cell.margin_bottom = Inches(margin_in)
    cell.margin_left = Inches(inset_in)
    cell.margin_right = Inches(inset_in)
    for para in cell.text_frame.paragraphs:
        for run in para.runs:
            _set_font(run, theme, size=size, color=color, bold=bold)
    _set_cell_borders(cell, theme)


RENDERERS = {
    "cover": _render_cover,
    "section_divider": _render_section_divider,
    "chart_hero": _render_chart_hero,
    "chart_with_points": _render_chart_with_points,
    "comparison": _render_comparison,
    "stat_callout": _render_stat_callout,
    "percentage_bars": _render_percentage_bars,
    "table": _render_table,
    "table_with_points": _render_table_with_points,
    "direction": _render_direction,
}

# 需要圖才成立的版型：解析不到圖就降級 stat_callout，不留佔位文字。
CHART_DEPENDENT_KINDS = frozenset({"chart_hero", "chart_with_points", "comparison"})
# 單圖版型：被指定給多圖頁面時要拆成多頁（成對報表的「分頁」呈現）。
SINGLE_CHART_KINDS = frozenset({"chart_hero", "chart_with_points", "stat_callout"})


# --------------------------------------------------------------------------
# 頁面展開
# --------------------------------------------------------------------------
def _kind_for_report(report: dict[str, Any], chart_count: int) -> str:
    """動態頁的預設版型：明細／矩陣走表格，多圖走並排，單圖走大圖，沒圖走大數字。

    單圖預設 `chart_hero`（P1-1）：大圖＋底部核心結論條——「圖表為主、文字為輔」
    的預設呈現；要點框版（chart_with_points）保留給版型下拉切換。
    """
    if str(report.get("report_type") or "").lower() in {"detail", "table"}:
        return "table"
    if chart_count >= 2:
        return "comparison"
    if chart_count == 1:
        return "chart_hero"
    return "stat_callout"


def _page_should_render(report_data: dict[str, Any], spec: PageSpec) -> bool:
    """cover／direction 恆出；其餘頁面必須至少有一個實際有資料的 report_key。"""
    if spec.kind in {"cover", "direction", "section_divider"}:
        return True
    return bool(_actual_report_keys(report_data, spec.report_keys))


def _evidence_rank(spec: PageSpec) -> int:
    """證據頁在論證鏈上的位置；未列名者排在已知證據之後（仍在結論之前）。

    取第一個命中 EVIDENCE_ORDER 的 report_key——成對報表（如申請人／專利權人
    排名同頁）以先列者定位即可，兩者在論證上本來就相鄰。
    """
    for key in spec.report_keys:
        if key in EVIDENCE_ORDER:
            return EVIDENCE_ORDER.index(key)
    return len(EVIDENCE_ORDER)


def _expand_page_layout(report_data: dict[str, Any], charts: ChartIndex | None = None) -> list[PageSpec]:
    """選擇驅動出頁：把有資料的報表展開成頁面清單，頁碼重新連號。

    `charts` 省略時（前端縮圖預覽只拿得到 report_data）僅回頁面骨架，
    圖檔欄位留空——留空是誠實的，猜檔名才是錯的。
    """
    base: list[PageSpec] = []
    for spec in PAGE_LAYOUT:
        if not _page_should_render(report_data, spec):
            continue
        keys = spec.report_keys if spec.kind in {"cover", "direction"} else _actual_report_keys(report_data, spec.report_keys)
        # 封面／隔頁／方向頁不擺報表圖，charts 留空才不會讓 manifest 反查出誤導的對照。
        files = _filter_report_charts(keys, charts.files_for(keys)) if charts and spec.kind not in {"cover", "direction", "section_divider"} else ()
        kind = spec.kind
        if kind == "comparison" and len(files) < 2:
            kind = "chart_hero"
        resolved = _spec_with(spec, report_keys=keys, charts=files, kind=kind)
        # 依通道拆頁（P1-3）：主題分布 rows 帶兩通道，各自成一張表格頁；
        # 單一通道時維持一頁、不加通道字樣。
        base.extend(_split_by_channel(resolved, report_data))

    covered = {key for spec in PAGE_LAYOUT for key in spec.report_keys}
    extra: list[PageSpec] = []
    for report_key, report in _iter_report_entries(report_data):
        if report_key in covered or report_key in EXCLUDED_FROM_PPT:
            continue
        if not _report_key_has_data(report_data, report_key):
            continue
        files = _filter_report_charts((report_key,), charts.files_for((report_key,))) if charts else ()
        topic = _label_of(report_data, report_key)
        extra.append(
            PageSpec(page=0, kind=_kind_for_report(report, len(files)), title=topic, topic=topic,
                     report_keys=(report_key,), charts=files)
        )

    # ⚠ 錨點＝direction 或第一個附錄，取先出現者：動態插頁也是證據，
    #   必須排在結論（研發方向建議）之前——結論永遠壓軸（P1-6）。
    anchor = next((i for i, spec in enumerate(base)
                   if spec.is_appendix or spec.kind == "direction"), len(base))
    # 證據段依 EVIDENCE_ORDER 重排（2026-07-31）：固定條目與動態插頁**混在一起**
    # 排序，而不是「固定的在前、動態的在後」——否則 CPC 仍會離 IPC 很遠。
    # sort 是穩定的，故未列名的報表維持引擎輸出的相對順序，只是整批落在最後。
    head = [spec for spec in base[:anchor] if spec.kind == "cover"]
    evidence = [spec for spec in base[:anchor] if spec.kind != "cover"] + extra
    evidence.sort(key=_evidence_rank)
    merged = head + evidence + base[anchor:]
    merged = _split_pairs_by_policy(merged, charts)
    return [_spec_with(spec, page=index) for index, spec in enumerate(merged, start=1)]


def _split_by_channel(spec: PageSpec, report_data: dict[str, Any]) -> list[PageSpec]:
    """rows 含多通道的報表依通道拆頁（每通道一張表；附錄總表不拆）。"""
    if spec.is_appendix or not spec.report_keys:
        return [spec]
    config = CHANNEL_SPLIT_REPORTS.get(spec.report_keys[0])
    if config is None:
        return [spec]
    column, channels = config
    rows = _rows_of(report_data, spec.report_keys[0])
    present = [(value, topic) for value, topic in channels
               if any(str(r.get(column)) == value for r in rows)]
    if len(present) <= 1:
        return [spec]
    return [
        _spec_with(spec, topic=topic, title=topic, row_filter=((column, value),))
        for value, topic in present
    ]


def _split_pairs_by_policy(layout: list[PageSpec], charts: ChartIndex | None = None) -> list[PageSpec]:
    """列在 SPLIT_PAIR_REPORTS 的成對報表改成分頁（表格內容多，並排讀不動）。"""
    result: list[PageSpec] = []
    for spec in layout:
        if spec.kind == "comparison" and any(key in SPLIT_PAIR_REPORTS for key in spec.report_keys):
            # 拆頁後用大圖版型（chart_hero）——分頁的動機就是圖要大。
            result.extend(_split_multi_chart_page(_spec_with(spec, kind="chart_hero"), charts))
        else:
            result.append(spec)
    return result


def _split_multi_chart_page(spec: PageSpec, charts: ChartIndex | None = None) -> list[PageSpec]:
    """單圖版型碰到多圖頁面：一圖一頁。

    拆頁時把 `report_keys` 一併收窄到該圖真正對應的報表——否則兩頁都掛著全部
    report_key，會抓到同一段 narrative、印出兩張一模一樣的標題與註腳。
    """
    if len(spec.charts) <= 1:
        return [spec]
    pages: list[PageSpec] = []
    for name in spec.charts:
        owners = charts.owners_of(name, spec.report_keys) if charts else ()
        pages.append(_spec_with(spec, charts=(name,), report_keys=owners or spec.report_keys))
    return pages


def _clean_layout_overrides(value: Any) -> dict[str, str]:
    """只接受 renderer 支援的版型名稱，無效覆寫忽略而非讓產檔失敗。"""
    if not isinstance(value, dict):
        return {}
    return {str(page): str(kind) for page, kind in value.items() if str(kind) in RENDERERS}


def _apply_layout_overrides(
    layout: list[PageSpec], overrides: dict[str, str], charts: ChartIndex | None = None
) -> list[PageSpec]:
    """套用使用者挑的版型；單圖版型碰到多圖頁面會自動拆頁，頁碼重新連號。"""
    result: list[PageSpec] = []
    for spec in layout:
        kind = overrides.get(str(spec.page), spec.kind)
        target = _spec_with(spec, kind=kind)
        if kind in SINGLE_CHART_KINDS and len(spec.charts) > 1:
            result.extend(_split_multi_chart_page(target, charts))
        elif kind == "comparison" and len(spec.charts) < 2:
            result.append(_spec_with(target, kind="chart_with_points"))
        else:
            result.append(target)
    return [_spec_with(spec, page=index) for index, spec in enumerate(result, start=1)]


def _apply_chart_degradation(layout: list[PageSpec], charts: ChartIndex) -> list[PageSpec]:
    """圖檔缺失／轉檔失敗 → 降級 stat_callout，確保每頁都有視覺元素。"""
    result: list[PageSpec] = []
    for spec in layout:
        if spec.kind not in CHART_DEPENDENT_KINDS:
            result.append(spec)
            continue
        usable = tuple(name for name in spec.charts if charts.resolve(name) is not None)
        if usable:
            result.append(_spec_with(spec, charts=usable))
        else:
            result.append(_spec_with(spec, kind="stat_callout", charts=(), degraded_from=spec.kind))
    return result


# --------------------------------------------------------------------------
# 封面統計卡與分析框架
# --------------------------------------------------------------------------
def _cover_title(report_data: dict[str, Any], slots: dict[str, str]) -> str:
    """封面主標＝workspace 顯示名稱（P1-8，確定性組成；cover.title AI slot 已退場）。

    ⚠ slots 參數保留：使用者若日後經 approvals 明確給了標題仍尊重（人工定稿
    優先於推導），但**不再請 AI 產**——AI 只剩 direction.body 一個 slot。
    parameters 缺 workspace 名稱時退回通用標題，不硬湊。
    """
    manual = str(slots.get("cover.title") or "").strip()
    if manual:
        return manual
    params = report_data.get("parameters") or {}
    for key in ("workspace_name", "workspace_display_name", "workspace"):
        value = str(params.get(key) or "").strip()
        if value:
            # 2026-07-31 使用者定案：「封面頁主題要顯示成 workspace 名稱配上專利分析」
            # ——單獨一個「滑雪機」不像簡報標題，補上主題詞才成句。
            return value if value.endswith("專利分析") else f"{value}專利分析"
    return "專利情報整合分析"


def _cover_stats(report_data: dict[str, Any]) -> list[tuple[str, str, str]]:
    """封面統計卡；資料不足就少一格，不硬湊低價值指標。"""
    stats: list[tuple[str, str, str]] = []
    trend_rows = _rows_of(report_data, "application_trend")
    if trend_rows:
        total = sum(_as_int(row.get("patent_count")) for row in trend_rows)
        stats.append((f"{total:,}", "件", "專利總數"))
    country_rows = _rows_of(report_data, "country_distribution")
    if country_rows:
        numeric = _numeric_column(country_rows)
        label_col = _label_column(country_rows, numeric)
        top = sorted(country_rows, key=lambda r: _as_int(r.get(numeric)), reverse=True)[:2]
        stats.append((
            " ｜ ".join(str(_as_int(r.get(numeric))) for r in top),
            " ｜ ".join(str(r.get(label_col, "-")) for r in top),
            "地域分布（件數）",
        ))
    period = _statistics_period(report_data)
    if period:
        stats.append((period, "年", "年份區間"))
    # 第 4 格由資料現有欄位組成；都沒有就只出 3 格。
    for report_key, unit, label in (
        ("applicant_ranking", "家", "申請人家數"),
        ("ipc_main_distribution", "類", "IPC 主分類數"),
        ("cluster_topic_table", "群", "技術主題數"),
    ):
        rows = _rows_of(report_data, report_key)
        if rows:
            stats.append((str(len(rows)), unit, label))
            break
    return stats[:4]


FRAMEWORK_TOPIC_LIMIT = 5


def _framework_text(layout: list[PageSpec]) -> str:
    """分析框架條：用本次實際出頁的內容頁主題串成閱讀動線。

    只列前幾個主題再收「等 N 項」——列滿十幾個會被單行版面截成「…」，
    反而看不出動線。
    """
    topics = list(dict.fromkeys(
        spec.topic or spec.title
        for spec in layout
        if spec.kind not in {"cover", "direction", "section_divider"} and not spec.is_appendix
    ))
    if not topics:
        return "分析框架：本次僅含封面與研發方向建議"
    head = " → ".join(topics[:FRAMEWORK_TOPIC_LIMIT])
    rest = len(topics) - FRAMEWORK_TOPIC_LIMIT
    return f"分析框架：{head}" + (f" → 等共 {len(topics)} 項分析" if rest > 0 else "")


# --------------------------------------------------------------------------
# 產後自檢（QA）：溢出、邊距、重疊、文字裝不下
# --------------------------------------------------------------------------
def _shape_font_pt(shape) -> float:
    sizes = [
        run.font.size.pt
        for para in shape.text_frame.paragraphs
        for run in para.runs
        if run.font.size is not None
    ]
    return max(sizes) if sizes else 0.0


def audit_layout(prs: Presentation, theme: Theme) -> list[dict[str, Any]]:
    """逐頁逐 shape 檢查版面問題，回傳 warnings（不修改簡報，只回報）。

    檢四項：超出版面邊界、邊距不足、文字疊文字、文字估算裝不下。
    最後一項用字級與框大小估算——PowerPoint 的文字溢出不會改變 shape 尺寸，
    只看座標抓不到「字太多」，而字牆正是這次重建要解決的問題。
    """
    warnings: list[dict[str, Any]] = []
    slide_w = float(theme.slide["width_in"])
    slide_h = float(theme.slide["height_in"])
    margin = float(theme.qa["min_margin_in"])
    bounds_tol = float(theme.qa["bounds_tolerance_in"])
    overlap_tol = float(theme.qa["overlap_tolerance_in"])
    slack = float(theme.qa["capacity_slack"])

    for page, slide in enumerate(prs.slides, start=1):
        boxes: list[tuple[str, float, float, float, float]] = []
        for shape in slide.shapes:
            if shape.left is None or shape.top is None:
                continue
            left = shape.left / 914400
            top = shape.top / 914400
            right = left + (shape.width or 0) / 914400
            bottom = top + (shape.height or 0) / 914400
            text = shape.text_frame.text.strip() if shape.has_text_frame else ""
            name = f"{shape.shape_type}｜{text[:18]}" if text else str(shape.shape_type)

            # 全出血底板（封面／隔頁背景）本來就貼齊版面，不算違規。
            full_bleed = left <= bounds_tol and top <= bounds_tol and right >= slide_w - bounds_tol
            # 邊距規則保護的是「內容不要貼邊」；色塊、圓點、斜線這類裝飾出血是設計語彙，
            # 只受版面邊界（out_of_bounds）約束，不受安全區約束。
            is_content = bool(text) or shape.has_table or shape.shape_type == MSO_SHAPE_TYPE.PICTURE

            if not full_bleed and (
                left < -bounds_tol or top < -bounds_tol
                or right > slide_w + bounds_tol or bottom > slide_h + bounds_tol
            ):
                warnings.append({
                    "type": "out_of_bounds", "page": page, "shape": name,
                    "overflow_in": round(max(-left, -top, right - slide_w, bottom - slide_h), 3),
                })
            elif is_content and not full_bleed and (
                left < margin - bounds_tol or top < margin - bounds_tol
                or right > slide_w - margin + bounds_tol or bottom > slide_h - margin + bounds_tol
            ):
                warnings.append({
                    "type": "margin_violation", "page": page, "shape": name,
                    "margin_in": round(min(left, top, slide_w - right, slide_h - bottom), 3),
                })

            if not text:
                continue
            boxes.append((name, left, top, right, bottom))

            size_pt = _shape_font_pt(shape)
            if size_pt:
                per_line, lines = _text_capacity(
                    theme, width_in=right - left, height_in=bottom - top, size_pt=size_pt
                )
                needed = _lines_needed(text, per_line)
                if needed > lines * slack:
                    warnings.append({
                        "type": "text_overflow_estimated", "page": page, "shape": name,
                        "lines_needed": needed, "lines_available": lines,
                    })

        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                overlap_w = min(a[3], b[3]) - max(a[1], b[1])
                overlap_h = min(a[4], b[4]) - max(a[2], b[2])
                if overlap_w > overlap_tol and overlap_h > overlap_tol:
                    warnings.append({
                        "type": "text_overlap", "page": page,
                        "shapes": [a[0], b[0]],
                        "overlap_in": [round(overlap_w, 3), round(overlap_h, 3)],
                    })
    return warnings


# --------------------------------------------------------------------------
# 產出
# --------------------------------------------------------------------------
def _next_available_path(output_dir: Path, version: str) -> Path:
    """同版本重跑不覆蓋舊檔，改產 `_r2`、`_r3`。"""
    base = output_dir / f"{version}.pptx"
    if not base.exists():
        return base
    index = 2
    while (output_dir / f"{version}_r{index}.pptx").exists():
        index += 1
    return output_dir / f"{version}_r{index}.pptx"


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _narrative_candidates(spec: PageSpec) -> tuple[str, ...]:
    """narrative 查找鍵：report_key → 圖檔主檔名 → 去後綴主檔名 → alias。

    ⚠ alias（NARRATIVE_ALIASES）解「兩個 key 空間不同名」：機會矩陣的解讀
    掛在 `cluster_topic_table:opportunity_*`（sections 結構），report bucket
    卻叫 `opportunity_quadrant`——沒有 alias 這頁永遠 narrative_missing
    （2026-07-31 實機 P10）。
    """
    # 🔴 順序＝精確度，不是「先 report_key 再圖檔」（2026-07-31 修）。
    # 症狀：機會評估拆成技術／功效兩頁後，兩頁印出同一段解讀（圖是功效、文字是技術）。
    # 根因：拆頁時 report_keys 被收窄成同一個 `opportunity_quadrant`，兩頁完全相同；
    # 而 alias 依 report_key 層先展開，它同時對到 tech 與 effect 兩個變體，
    # 於是兩頁都命中先列的 tech。圖檔主檔名（`opportunity_quadrant_effect`）才是
    # 能區分兩頁的唯一線索，必須先查。
    # 通道拆頁（主題統計表）最精確：該頁只呈現一個通道的列，解讀也該只取那個通道。
    # ⚠ 這頁沒有圖檔，所以圖檔主檔名那條線索不存在，只能靠 row_filter 認。
    channel: list[str] = []
    for column, value in spec.row_filter:
        variant = CHANNEL_NARRATIVE_VARIANTS.get(str(value))
        if not variant:
            continue
        for key in spec.report_keys:
            candidate = f"{key}:{variant}"
            if candidate not in channel:
                channel.append(candidate)

    specific: list[str] = []
    for name in spec.charts:
        stem = Path(name).stem
        if stem not in specific:
            specific.append(stem)
        for suffix in CHART_ORDER_HINTS:
            if stem.endswith(suffix):
                base = stem[: -len(suffix)]
                if base not in specific:
                    specific.append(base)
    generic = [key for key in spec.report_keys if key not in specific]

    keys: list[str] = channel + list(specific) + generic
    # alias 也照同一順序展開：圖檔層 alias 一律排在 report_key 層 alias 之前。
    for key in specific + generic:
        for alias in NARRATIVE_ALIASES.get(key, ()):
            if alias not in keys:
                keys.append(alias)
    return tuple(keys)


def build_ppt(
    *,
    report_dir: Path | str,
    approvals_path: Path | str | None = None,
    output_dir: Path | str | None = None,
    theme_path: Path | str = THEME_PATH,
) -> dict[str, Any]:
    """依版型表組出報告 PPTX，回傳輸出路徑、manifest 路徑與 manifest 內容。"""
    report_dir = Path(report_dir)
    report_data = _load_json(report_dir / "report_data.json", {})
    narratives = _load_json(report_dir / "narratives.json", {})
    artifact_manifest = _load_json(report_dir / "artifact_manifest.json", {})
    approvals = _load_json(Path(approvals_path), {}) if approvals_path else {}
    slots: dict[str, str] = approvals.get("slots") or {}

    version = (
        (report_data.get("parameters") or {}).get("version")
        or approvals.get("report_version")
        or report_dir.name
    )
    output_dir = Path(output_dir) if output_dir else Path("data/report_artifacts/ppt")
    output_dir.mkdir(parents=True, exist_ok=True)

    theme = Theme.load(theme_path)
    charts = ChartIndex(report_dir, output_dir / ".cache", artifact_manifest, theme)

    warnings: list[dict[str, Any]] = []
    if not charts.manifest_found:
        warnings.append({
            "type": "artifact_manifest_missing",
            "detail": "找不到 artifact_manifest.json；本次無法對照圖檔，含圖頁面一律降級為 stat_callout。",
        })

    direction_body = str(slots.get("direction.body") or "").strip()
    if direction_body and _parse_direction_body(direction_body) is None:
        warnings.append({
            "type": "direction_unstructured",
            "detail": "direction.body 非結構化 JSON（舊契約純文字），已用條列版面過渡；"
                      "重跑 ai:report_ppt 產新契約後將呈現色塊流程＋題目卡。",
        })

    layout = _expand_page_layout(report_data, charts)
    layout = _apply_layout_overrides(layout, _clean_layout_overrides(approvals.get("layout_overrides")), charts)
    layout = _apply_chart_degradation(layout, charts)

    # 逐頁備妥 narrative（判讀式標題＋要點），fallback 一律寫 warning，不靜默。
    narratives_by_page: dict[int, tuple[str, list[dict[str, Any]], bool]] = {}
    titled: list[PageSpec] = []
    for spec in layout:
        if spec.kind in {"cover", "direction", "section_divider"} or spec.is_appendix:
            # 附錄頁沒有文案 slot、只渲染表格，標題保留「附錄N：…」的定位不套判讀式標題。
            titled.append(spec)
            continue
        if spec.kind == "table":
            # 純表格頁（明細類，如家族完整性明細）本來就不配解讀——查了也是空，
            # 誤報 narrative_missing 會讓人白跑一趟「補解讀」（P1-4，實機 P17）。
            titled.append(spec)
            continue
        matched, variant = _narrative_entry(narratives, _narrative_candidates(spec))
        headline, points, fell_back = _normalize_narrative(variant) if variant else ("", [], False)
        narratives_by_page[spec.page] = (headline, points, fell_back)
        if not variant:
            warnings.append({
                "type": "narrative_missing", "page": spec.page,
                "report_key": ",".join(spec.report_keys),
                "detail": "找不到對應 narrative；本頁要點改用引擎 rows 的關鍵數字。",
            })
        elif fell_back:
            warnings.append({
                "type": "narrative_fallback", "page": spec.page,
                "report_key": matched or ",".join(spec.report_keys),
                "detail": "narrative 缺 headline／points（舊格式只有 text），已切段落並依版面截斷。",
            })
        if not headline and points:
            headline = _derive_headline(points)
            if headline:
                warnings.append({
                    "type": "headline_derived", "page": spec.page,
                    "report_key": matched or ",".join(spec.report_keys),
                    "detail": f"narrative 未提供 headline，標題取自要點首句：「{headline}」。",
                })
        titled.append(_spec_with(spec, title=f"{spec.topic}：{headline}" if headline else spec.topic))
    layout = titled

    included_labels = [
        _label_of(report_data, key)
        for spec in layout
        for key in spec.report_keys
        if _report_key_has_data(report_data, key)
    ]
    cover_stats = _cover_stats(report_data)
    ctx: dict[str, Any] = {
        "report_data": report_data,
        "narratives": narratives,
        "narratives_by_page": narratives_by_page,
        "slots": slots,
        "version": version,
        "charts": charts,
        "period": _statistics_period(report_data),
        "cover_stats": cover_stats,
        "framework_text": _framework_text(layout),
        "included_report_labels": list(dict.fromkeys(included_labels)),
    }

    prs = Presentation()
    prs.slide_width = Inches(theme.slide["width_in"])
    prs.slide_height = Inches(theme.slide["height_in"])
    blank = prs.slide_layouts[6]

    pages: list[dict[str, Any]] = []
    inner_density = float(theme.starfield["inner_page_density"])
    for spec in layout:
        slide = prs.slides.add_slide(blank)
        # 背景先畫＝壓在最底層；封面用滿密度，內頁調淡避免星點被當成圖表資料點。
        _add_background(slide, theme, charts.cache_dir,
                        density=1.0 if spec.kind in {"cover", "section_divider"} else inner_density)
        RENDERERS[spec.kind](slide, theme, spec, ctx)
        page_info: dict[str, Any] = {
            "page": spec.page,
            "kind": spec.kind,
            "title": spec.title,
            "topic": spec.topic,
            "report_keys": list(spec.report_keys),
            "charts": list(spec.charts),
            "is_appendix": spec.is_appendix,
            "degraded_from": spec.degraded_from,
            "filled_slots": [s for s in spec.slots if slots.get(s)],
            "missing_slots": [s for s in spec.slots if not slots.get(s)],
            "missing_reports": [k for k in spec.report_keys if not _report_key_has_data(report_data, k)],
        }
        if spec.kind == "cover":
            page_info["stat_cards"] = len(cover_stats)
        if spec.degraded_from:
            warnings.append({
                "type": "chart_missing_degraded", "page": spec.page,
                "report_key": ",".join(spec.report_keys),
                "detail": f"artifact_manifest 內找不到可用圖檔，{spec.degraded_from} 已降級為 stat_callout。",
            })
        pages.append(page_info)

    warnings.extend(audit_layout(prs, theme))

    pptx_path = _next_available_path(output_dir, version)
    prs.save(str(pptx_path))

    selected = (report_data.get("parameters") or {}).get("reports_selected") or []
    rendered_keys = {key for page in pages for key in page["report_keys"]}
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "builder_version": "v3",
        "source_report_version": version,
        "source_report_dir": str(report_dir),
        "pptx_file": pptx_path.name,
        "sha256": _sha256_of(pptx_path),
        "slot_total": len(all_slot_keys()),
        "slot_filled": sum(len(p["filled_slots"]) for p in pages),
        "missing_slots": sorted({s for p in pages for s in p["missing_slots"]}),
        "missing_reports": sorted(
            {str(key) for key in selected if not _report_key_has_data(report_data, str(key))}
            | {key for p in pages for key in p["missing_reports"]}
            | {str(key) for key in selected if str(key) not in rendered_keys and _report_key_has_data(report_data, str(key))}
        ),
        "metadata": {
            key: (report_data.get("parameters") or {}).get(key)
            for key in ("topic_run_id", "topic_state_version")
            if (report_data.get("parameters") or {}).get(key) is not None
        },
        "warnings": warnings,
        "pages": pages,
    }
    manifest_path = pptx_path.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"pptx_path": str(pptx_path), "manifest_path": str(manifest_path), "manifest": manifest}


def write_approval_template(path: Path) -> Path:
    """產出確認槽範本供填入定稿文案；v3 只有兩個槽。"""
    payload = {
        "report_version": "<報表版本>",
        "slots": {slot: "" for slot in all_slot_keys()},
        "layout_overrides": {},
    }
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return Path(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="專利分析報告 PPTX 產生器 v3（deterministic）")
    parser.add_argument("--report-dir", help="報表版本目錄（含 report_data.json、artifact_manifest.json）")
    parser.add_argument("--approvals", help="確認槽定稿文案 JSON")
    parser.add_argument("--output-dir", default="data/report_artifacts/ppt", help="輸出目錄")
    parser.add_argument("--init-approvals", help="產出確認槽範本到指定路徑後結束")
    args = parser.parse_args()

    if args.init_approvals:
        print(f"approval template: {write_approval_template(Path(args.init_approvals))}")
        return
    if not args.report_dir:
        parser.error("--report-dir is required unless --init-approvals is used")

    result = build_ppt(
        report_dir=args.report_dir, approvals_path=args.approvals, output_dir=args.output_dir
    )
    manifest = result["manifest"]
    print(f"pptx: {result['pptx_path']}")
    print(f"manifest: {result['manifest_path']}")
    print(f"sha256: {manifest['sha256']}")
    print(f"pages: {len(manifest['pages'])}")
    print(f"warnings: {len(manifest['warnings'])}")
    for warning in manifest["warnings"]:
        print(f"  - [{warning['type']}] {warning.get('page', '-')} {warning.get('detail', '')}".rstrip())


if __name__ == "__main__":
    main()
