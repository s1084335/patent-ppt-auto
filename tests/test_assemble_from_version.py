"""版本目錄 → deck 中間格式的 intake 契約（add-deck-delivery-line tasks 1.4）。

## 為什麼要換 intake

deck 第 1 步原本是 `extract_report.py`：把**產好的 HTML** 再解析回結構。
那是繞路——引擎本來就有 `report_data.json`，HTML 只是它的一種呈現。
繞這一圈會付兩次代價：解析器要跟著 HTML 版面走（線一改章節式就是一例），
而且 HTML 丟失了 `report_key`／`variant_key` 這類引擎原生的識別。

2026-08-12 定案改由版本目錄直接組中間格式，`extract_report.py` 降為 HTML fallback。

## 契約＝與 `extract_report.py` **相同的輸出形狀**

下游（`plan_deck`／`check_content`／`make_deck`）一律吃 `report.json`＋`charts/`，
換 intake 不得改變它們看到的東西。形狀見 `extract_report.py` 的 `report` dict。

## 五個實測陷阱（2026-08-13 自 `report_trial_20260812_133901` 反解）

1. `variant.file` **可能是空字串**（主題統計表的兩個 variant）——那是解讀落點，
   不是圖，不得當成圖表檔進 charts。
2. `applicant_year_matrix` 帶 `more_variants`（第 11–20 名）——排頁規則明訂剔除。
3. rows 散在**三處**：`section.rows`／`variant.rows`／`reports[report_key].rows`，
   不同報表放不同地方，只讀一處會靜默少表。
4. `notes` 要併**三個來源**：`section.note`、`table_display.encoding_notes[key]`、
   `table_display.reader_guide`（後者是 list of {title, body}，前者是 dict）。
5. **`narratives.json` 可能不存在**（沒跑解讀的版本）——不得因此失敗，texts 留空。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "skills" / "html-report-to-deck" / "scripts"


def _load_assembler():
    """從 skill 的 scripts 目錄載入模組（它不在 package path 上）。"""
    path = SCRIPTS / "assemble_from_version.py"
    spec = importlib.util.spec_from_file_location("assemble_from_version", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["assemble_from_version"] = module
    spec.loader.exec_module(module)
    return module


def _write_version_dir(root: Path, *, with_narratives: bool = False) -> Path:
    """合成一個版本目錄，涵蓋上述五個陷阱。

    ⚠ 合成而非複製真實產物：不把某批專利釘進測試，換批也不會失效
    （沿 `regression.py` 同一個原則）。
    """
    d = root / "report_trial_20260101_000000"
    d.mkdir(parents=True)
    (d / "version_meta.json").write_text(json.dumps({
        "version": "report_trial_20260101_000000",
        "generated_at": "2026-01-01T00:00:00",
        "workspace_id": 7,
        "workspace_name": "自走式割草機",
    }, ensure_ascii=False), encoding="utf-8")

    report_data = {
        "parameters": {"ranking_limit": 10, "ipc_levels": [4, 5], "cpc_levels": [4, 5]},
        "reports": {
            "application_trend": {
                "report_name": "application_trend",
                "rows": [{"application_year": 2020, "patent_count": 3}],
                "row_count": 1,
            },
        },
        "sections": [
            # ① 一般章節：圖與 rows 都在 reports 裡
            {"title": "專利申請趨勢", "report_key": "application_trend",
             "variants": [{"label": "Trend", "file": "annual_trend.svg",
                           "variant_key": "default",
                           # 真實產物的純圖表 variant 帶這個鍵，narratives 靠它對回
                           "narrative_key": "application_trend:default"}]},
            # ② 陷阱 1：variant.file 是空字串（解讀落點，不是圖）
            #    陷阱 3：rows 掛在 section 上
            {"title": "主題分析", "report_key": "cluster_topic_table",
             "note": "主題數依分群結果而變",
             "rows": [{"topic": "割草路徑規劃", "patent_count": 5}],
             "variants": [
                 {"label": "主題統計表（技術）", "file": "", "variant_key": "topic_table_tech"},
                 {"label": "機會矩陣", "file": "opportunity_quadrant_tech.svg",
                  "variant_key": "opportunity_tech",
                  # 陷阱 3：rows 掛在 variant 上
                  "rows": [{"label": "割草路徑規劃", "patent_count": 5}]},
             ]},
            # ③ 陷阱 2：more_variants（第 11–20 名）必須剔除
            {"title": "申請人年度分布", "report_key": "applicant_year_matrix",
             "more_label": "第 11–20 名",
             "more_variants": [{"label": "11–20", "file": "applicant_year_matrix_more.svg",
                                "variant_key": "more"}],
             "variants": [{"label": "Top 10", "file": "applicant_year_matrix.svg",
                           "variant_key": "default"}]},
        ],
        # 陷阱 4：notes 的兩個來源
        "table_display": {
            "encoding_notes": {"application_trend": "兩線分別為申請與授權公告（同尺）"},
            "reader_guide": [{"title": "計數單位", "body": "全報告只有兩個單位：件與主題。"}],
        },
    }
    (d / "report_data.json").write_text(
        json.dumps(report_data, ensure_ascii=False), encoding="utf-8")

    for name in ("annual_trend", "opportunity_quadrant_tech",
                 "applicant_year_matrix", "applicant_year_matrix_more"):
        (d / f"{name}.svg").write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50">'
            f'<text>{name}</text></svg>', encoding="utf-8")

    if with_narratives:
        (d / "narratives.json").write_text(json.dumps({
            "application_trend:default": {
                "text": "申請量自 2020 年起穩定成長。",
                "headline": "穩定成長",
                "points": ["2020 年 3 件"],
            },
        }, ensure_ascii=False), encoding="utf-8")
    return d


class OutputShapeMatchesHtmlIntakeTests(unittest.TestCase):
    """輸出形狀必須與 `extract_report.py` 相同——下游不該知道換了 intake。"""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_assembler()

    def _run(self, **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        version_dir = _write_version_dir(root, **kwargs)
        out = root / "work"
        self.mod.assemble(version_dir, out)
        return out, json.loads((out / "report.json").read_text(encoding="utf-8"))

    def test_top_level_keys_match(self):
        _, report = self._run()
        self.assertEqual(set(report), {"report_meta", "sections", "chart_manifest"})

    def test_section_keys_match(self):
        _, report = self._run()
        for section in report["sections"]:
            with self.subTest(title=section.get("title")):
                self.assertEqual(set(section),
                                 {"title", "notes", "texts", "charts", "tables"})

    def test_chart_entry_keys_match(self):
        _, report = self._run()
        charts = [c for s in report["sections"] for c in s["charts"]]
        self.assertTrue(charts, "應該有圖表")
        for c in charts:
            with self.subTest(file=c.get("file")):
                self.assertEqual(set(c), {"file", "alt", "dup"})

    def test_svg_copied_into_charts_dir(self):
        out, report = self._run()
        for c in (c for s in report["sections"] for c in s["charts"]):
            with self.subTest(file=c["file"]):
                self.assertTrue((out / "charts" / c["file"]).is_file(),
                                f"{c['file']} 沒被複製進 charts/")


class TrapTests(unittest.TestCase):
    """五個自真實產物反解出來的陷阱。"""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_assembler()

    def _run(self, **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        version_dir = _write_version_dir(root, **kwargs)
        out = root / "work"
        self.mod.assemble(version_dir, out)
        return out, json.loads((out / "report.json").read_text(encoding="utf-8"))

    def test_empty_file_variant_is_not_a_chart(self):
        """陷阱 1：`file` 空字串是解讀落點，不是圖。"""
        _, report = self._run()
        files = [c["file"] for s in report["sections"] for c in s["charts"]]
        self.assertNotIn("", files, "空檔名被當成圖表了")

    def test_more_variants_excluded(self):
        """陷阱 2：第 11–20 名不進 deck（排頁規則明訂剔除）。"""
        out, report = self._run()
        files = [c["file"] for s in report["sections"] for c in s["charts"]]
        self.assertNotIn("applicant_year_matrix_more.svg", files)
        self.assertIn("applicant_year_matrix.svg", files)

    def test_rows_collected_from_all_three_places(self):
        """陷阱 3：rows 散在 reports／section／variant 三處，缺一就靜默少表。"""
        _, report = self._run()
        by_title = {s["title"]: s for s in report["sections"]}
        self.assertTrue(by_title["專利申請趨勢"]["tables"],
                        "reports[key].rows 沒被收進來")
        self.assertTrue(by_title["主題分析"]["tables"],
                        "section.rows／variant.rows 沒被收進來")

    def test_tables_use_head_rows_shape(self):
        """表格形狀沿用 HTML intake 的 {head, rows}，下游才不用改。"""
        _, report = self._run()
        for s in report["sections"]:
            for t in s["tables"]:
                with self.subTest(title=s["title"]):
                    self.assertEqual(set(t), {"head", "rows"})
                    self.assertTrue(all(isinstance(r, list) for r in t["rows"]),
                                    "rows 應為逐列的值陣列（與 HTML intake 一致）")

    def test_notes_merge_three_sources(self):
        """陷阱 4：section.note ＋ encoding_notes ＋ reader_guide 都要進 notes。"""
        _, report = self._run()
        by_title = {s["title"]: s for s in report["sections"]}
        joined_all = " ".join(n for s in report["sections"] for n in s["notes"])
        self.assertIn("主題數依分群結果而變", by_title["主題分析"]["notes"],
                      "section.note 漏了")
        self.assertIn("兩線分別為申請與授權公告（同尺）",
                      by_title["專利申請趨勢"]["notes"], "encoding_notes 漏了")
        self.assertIn("計數單位", joined_all, "reader_guide 漏了")

    def test_runs_without_narratives(self):
        """陷阱 5：沒跑解讀的版本也要能組，texts 留空而非炸掉。"""
        _, report = self._run(with_narratives=False)
        self.assertTrue(report["sections"])
        self.assertEqual([t for s in report["sections"] for t in s["texts"]], [])

    def test_narratives_fill_texts_when_present(self):
        """有 narratives.json 時，texts 以 narrative_key 對回各章節。"""
        _, report = self._run(with_narratives=True)
        by_title = {s["title"]: s for s in report["sections"]}
        self.assertTrue(by_title["專利申請趨勢"]["texts"], "narratives 沒對回章節")

    def test_narrative_key_falls_back_to_report_and_variant(self):
        """⚠ 真實產物中，帶 `rows` 的 variant（Key Players、機會矩陣）**沒有**
        `narrative_key`。缺這個鍵時要用 `report_key:variant_key` 推出來，
        否則那些章節的解讀會靜默消失——而且沒有任何閘門會提醒。
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        version_dir = _write_version_dir(root, with_narratives=True)
        # 補一則掛在「沒有 narrative_key 的 variant」上的解讀
        narratives_path = version_dir / "narratives.json"
        data = json.loads(narratives_path.read_text(encoding="utf-8"))
        data["cluster_topic_table:opportunity_tech"] = {"text": "機會集中在路徑規劃。"}
        narratives_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        out = root / "work"
        self.mod.assemble(version_dir, out)
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        by_title = {s["title"]: s for s in report["sections"]}
        self.assertIn("機會集中在路徑規劃。", by_title["主題分析"]["texts"],
                      "缺 narrative_key 時沒有用 report_key:variant_key 推導")


class ReportMetaTests(unittest.TestCase):
    """封面素材：design 4b 定案技術名稱＝workspace 名稱。"""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_assembler()

    def test_workspace_name_available_for_cover(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        out = root / "work"
        self.mod.assemble(_write_version_dir(root), out)
        meta = json.loads((out / "report.json").read_text(encoding="utf-8"))["report_meta"]
        self.assertEqual(meta["source_file"], "report_trial_20260101_000000")
        self.assertIn("自走式割草機", json.dumps(meta, ensure_ascii=False),
                      "封面技術名稱＝workspace 名稱（design 4b）")


if __name__ == "__main__":
    unittest.main()
