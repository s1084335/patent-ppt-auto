"""分群雙通道的固定資料來源與向量表 registry。"""

from __future__ import annotations

from dataclasses import dataclass


SOURCE_FIELD_TECHNICAL = "wips_independent_claims"
SOURCE_FIELD_EFFECT = "effect_summary"


@dataclass(frozen=True)
class ClusteringSourceSpec:
    """描述一個分群通道的文本位置、向量表與前處理方式。"""

    source_field: str
    label_zh: str
    embedding_table: str
    source_table: str
    source_column: str
    claim_aware_chunking: bool


SOURCE_SPECS = {
    SOURCE_FIELD_TECHNICAL: ClusteringSourceSpec(
        source_field=SOURCE_FIELD_TECHNICAL,
        label_zh="技術",
        embedding_table="core_layer.patent_technical_embeddings",
        source_table="core_layer.patents",
        source_column="獨立項[KR,JP,US,CN,EP,IN]",
        claim_aware_chunking=True,
    ),
    SOURCE_FIELD_EFFECT: ClusteringSourceSpec(
        source_field=SOURCE_FIELD_EFFECT,
        label_zh="功效",
        embedding_table="core_layer.patent_effect_embeddings",
        source_table="core_layer.patents",
        source_column="效果 摘要[US,EP,PCT,JP,KR,CN,TW]",
        claim_aware_chunking=False,
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
