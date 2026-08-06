"""分群雙通道的固定資料來源與向量表 registry。"""

from __future__ import annotations

from dataclasses import dataclass


SOURCE_FIELD_TECHNICAL = "wips_independent_claims"
SOURCE_FIELD_EFFECT = "effect_summary"

# 通道短名（唯一定義處）。用於分群產物的檔名後綴（`opportunity_quadrant_tech.svg`）
# 與母體註記的鍵（`cluster_topic_table:tech`）。
# ⚠ 2026-08-06 從 `chart_runner` 搬來：`population.py` 也需要它，而 chart_runner
# 反過來 import population，直接引用會成環。更根本的理由是——**通道是什麼**屬於
# clustering 的定義，不屬於畫圖那一層；放這裡兩邊都能取到同一份，不會漂移。


@dataclass(frozen=True)
class ClusteringSourceSpec:
    """描述一個分群通道的文本位置、向量表、前處理方式與 LLM 命名導向。"""

    source_field: str
    label_zh: str
    embedding_table: str
    source_table: str
    source_column: str
    claim_aware_chunking: bool
    # 給 topic_labeling_payload 的 instruction 用：告訴 LLM 這個通道的
    # label 該以什麼角度命名，避免技術通道出現功效式名稱（或相反）。
    naming_hint: str


SOURCE_SEGMENT_SLUGS = {SOURCE_FIELD_TECHNICAL: "tech", SOURCE_FIELD_EFFECT: "effect"}

SOURCE_SPECS = {
    SOURCE_FIELD_TECHNICAL: ClusteringSourceSpec(
        source_field=SOURCE_FIELD_TECHNICAL,
        label_zh="技術",
        embedding_table="core_layer.patent_technical_embeddings",
        source_table="core_layer.patents",
        source_column="獨立項[KR,JP,US,CN,EP,IN]",
        claim_aware_chunking=True,
        naming_hint=(
            "本通道為技術分群：label 必須以技術手段命名，"
            "聚焦結構、機構、裝置、控制或方法等技術特徵，"
            "例如「阻力調節機構」；不要以功效或效果命名。"
        ),
    ),
    SOURCE_FIELD_EFFECT: ClusteringSourceSpec(
        source_field=SOURCE_FIELD_EFFECT,
        label_zh="功效",
        embedding_table="core_layer.patent_effect_embeddings",
        source_table="core_layer.patents",
        source_column="效果 摘要[US,EP,PCT,JP,KR,CN,TW]",
        claim_aware_chunking=False,
        naming_hint=(
            "本通道為功效分群：label 必須以達成的功效命名，"
            "聚焦效果、優點或解決的問題，"
            "例如「降低運轉噪音」；不要以結構或機構命名。"
        ),
    ),
}


# 文獻備註的來源欄順位（2026-07-28 使用者定案，唯一定義處）。
#
# 為什麼與分群不同：分群技術通道**固定只讀獨立項、不 fallback**，維持主題切分的純淨；
# 備註則要盡量全覆蓋——它同時是「給人看的說明」與「無獨立項專利的 AI 補分輸入」，
# 取不到值等於那些專利兩邊皆空、補分機制自我堵死。
#
# 三級的實測依據（60 筆滑雪機資料）：
#   ① 獨立項        CN 28／US 9／EP 3／TW 0   ← 與分群同源，有就用
#   ② 所有權利要求   TW 9 由此救回（該欄名雖寫 [JP,KR,CN]，TW 實際有值）
#   ③ abstract      CN 外觀設計 11 筆由此救回（權利要求四欄全空、摘要 530 字）
# 合計 60/60。⚠ 明確排除「主權項」——它涵蓋附屬項，語意比獨立項雜（使用者定）。
#
# 第一級不寫死字面，直接取分群技術通道的來源欄：日後 WIPS 換欄名時只改 SOURCE_SPECS
# 一處，備註自動跟上，不會兩處分岔（本專案已多次因此靜默失敗）。
PATENT_NOTE_SOURCE_COLUMNS: tuple[str, ...] = (
    SOURCE_SPECS[SOURCE_FIELD_TECHNICAL].source_column,
    "所有權利要求[JP,KR,CN]",
    "abstract",
)


def get_source_spec(source_field: str) -> ClusteringSourceSpec:
    """取得受信任來源設定；未知來源不得進入動態 SQL。"""
    try:
        return SOURCE_SPECS[source_field]
    except KeyError as exc:
        raise ValueError(f"unsupported source_field: {source_field}") from exc


def source_fields() -> tuple[str, ...]:
    """回傳正式第一版固定的技術與功效通道。"""
    return tuple(SOURCE_SPECS)
