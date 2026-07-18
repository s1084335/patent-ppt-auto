"""分群雙通道的固定資料來源與向量表 registry。"""

from __future__ import annotations

from dataclasses import dataclass


SOURCE_FIELD_TECHNICAL = "wips_independent_claims"
SOURCE_FIELD_EFFECT = "effect_summary"


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


def get_source_spec(source_field: str) -> ClusteringSourceSpec:
    """取得受信任來源設定；未知來源不得進入動態 SQL。"""
    try:
        return SOURCE_SPECS[source_field]
    except KeyError as exc:
        raise ValueError(f"unsupported source_field: {source_field}") from exc


def source_fields() -> tuple[str, ...]:
    """回傳正式第一版固定的技術與功效通道。"""
    return tuple(SOURCE_SPECS)
