"""已移出 active chart registry 的引用／研發能量圖表 builder 留存。

本檔只供歷史追溯，不應由 production code import。
"""

from __future__ import annotations

from typing import Any


def build_top_cited_section(ctx: Any) -> None:
    """舊版高被引用專利排名 section builder。"""
    report = ctx.report("top_cited_patents")
    cited_rows = [
        {
            **row,
            "cite_label": f'{row.get("授權公告號") or row.get("未審查的公開號(轉換後)") or row["patent_id"]}'
            f'（{str(row.get("applicant_display_name") or "?")[:14]}）',
        }
        for row in report["rows"]
    ]
    render_bar_chart(
        ctx.run_dir / "top_cited_patents.svg",
        report["label_zh"],
        cited_rows,
        "cite_label",
        value_key="(F1)引用文獻數",
    )
    ctx.sections.append({
        "title": report["label_zh"],
        "variants": [{"label": "Top Cited", "file": "top_cited_patents.svg"}],
        "note": "被引用數（F1）是資料下載時點的快照；無引用欄的批次（精簡匯出）不在排名內。",
    })


def build_rd_energy_section(ctx: Any) -> None:
    """舊版企業研發能量氣泡圖 section builder。"""
    report = ctx.report("company_rd_energy")
    energy_rows = report["rows"]
    energy_plot = [row for row in energy_rows if int(row.get("cited_rows") or 0) > 0]
    energy_skipped = [row for row in energy_rows if int(row.get("cited_rows") or 0) == 0]
    render_bubble_chart(
        ctx.run_dir / "company_rd_energy.svg",
        report["label_zh"],
        energy_plot,
        x_key="cited_total",
        y_key="patent_count",
        size_key="inventor_total",
        label_key="applicant_display_name",
    )
    energy_note = "X＝被引用總數（下載時點快照）、Y＝申請量、泡泡＝發明人數合計。"
    if energy_skipped:
        skipped_names = "、".join(str(row["applicant_display_name"])[:20] for row in energy_skipped[:5])
        suffix = "…" if len(energy_skipped) > 5 else ""
        energy_note += f" 無引用資料（精簡匯出批）未入圖 {len(energy_skipped)} 家：{skipped_names}{suffix}。"
    ctx.sections.append({
        "title": report["label_zh"],
        "variants": [{"label": "Bubble", "file": "company_rd_energy.svg"}],
        "note": energy_note,
    })
