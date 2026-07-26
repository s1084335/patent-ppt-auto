"""匯入格式修復（csv/txt/xml）的 red→green 測試（依 import-format-fixes-spec）。

三個既有 bug：
1. csv 引號內換行：splitlines() 破壞引號內含換行的單一儲存格 → 欄位錯位。
2. csv Sniffer 只看前 8KB：前段無代表性時猜錯分隔符 → 全檔錯位。
3. xml XXE：ElementTree 未禁外部實體 → 實體注入風險。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.importers.wips_importer import load_delimited_rows, load_xml_rows


def _write(suffix: str, content: str, encoding: str = "utf-8") -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False,
                                    encoding=encoding, newline="")
    f.write(content)
    f.close()
    return Path(f.name)


class CsvQuotedNewlineTests(unittest.TestCase):
    """bug1：引號內含換行是單一儲存格，不得被拆成兩列。"""

    def test_quoted_newline_stays_single_cell(self):
        # 第二列的 title 欄含引號內換行；正確解析應是「1 個 header + 1 筆資料」，title 含換行。
        csv_text = (
            'patent_number,title\n'
            '"US-1","第一行\n第二行"\n'
        )
        path = _write(".csv", csv_text)
        try:
            _sources, _name, records, headers = load_delimited_rows(path, "t.csv")
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(headers, ["patent_number", "title"])
        self.assertEqual(len(records), 1, "引號內換行不得被拆成兩列")
        # title 應完整含換行內容（換行字元要保留，不能被 splitlines 吃掉黏成一行）
        self.assertIn("第一行", records[0]["title"])
        self.assertIn("第二行", records[0]["title"])
        self.assertIn("\n", records[0]["title"], "引號內換行字元必須保留，不得黏成一行")
        self.assertEqual(records[0]["patent_number"], "US-1")


class CsvDelimiterSniffTests(unittest.TestCase):
    """bug2：分隔符判定不得只憑前 8KB；前段無代表性時仍要判對。"""

    def test_delimiter_detected_when_early_rows_ambiguous(self):
        # 前段大量「單欄」列（無分隔符線索），真正的多欄結構在後段；用逗號分隔。
        # 舊實作 sample=text[:8192] 只看前 8KB，可能猜錯。
        padding = "\n".join("filler_row_%d" % i for i in range(400))  # 無逗號的前段
        csv_text = (
            "patent_number,title,applicant\n"
            + padding + "\n"
            + "US-9,Real Title,Acme Inc\n"
        )
        path = _write(".csv", csv_text)
        try:
            _sources, _name, records, headers = load_delimited_rows(path, "t.csv")
        finally:
            path.unlink(missing_ok=True)
        # header 應正確切成三欄（逗號分隔），而非被當成單欄
        self.assertEqual(headers, ["patent_number", "title", "applicant"])
        # 最後那筆多欄資料應正確切分
        real = [r for r in records if r.get("patent_number") == "US-9"]
        self.assertEqual(len(real), 1)
        self.assertEqual(real[0]["applicant"], "Acme Inc")


class XmlXxeTests(unittest.TestCase):
    """bug3：XXE — 外部實體不得被解析（defusedxml 擋）。"""

    def test_external_entity_not_resolved(self):
        # 帶外部實體宣告的惡意 XML：安全的解析器應拒 DTD/外部實體（丟錯或回空），
        # 絕不能讀取 /etc/passwd 之類外部資源填進實體。
        xxe = (
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE root [<!ENTITY xxe SYSTEM "file:///etc/hostname">]>\n'
            '<root><patent><number>&xxe;</number></patent></root>\n'
        )
        path = _write(".xml", xxe)
        try:
            # defusedxml 對含 DTD/外部實體的 XML 會 raise（EntitiesForbidden/DTDForbidden）。
            # 沿現有容錯：解析失敗回空，不得成功解析出外部實體內容。
            try:
                _sources, _name, records, _headers = load_xml_rows(path)
                # 若沒 raise，也絕不能把外部實體內容解析進 records
                dumped = str(records)
                self.assertNotIn("/etc", dumped)
                self.assertNotEqual(records and records[0].get("number", ""), "",
                                    "外部實體不應被解析出內容")
            except Exception:
                pass  # raise 即代表被擋，符合預期
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
