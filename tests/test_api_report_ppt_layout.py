"""PPT 版型端點契約測試（2026-07-29 改版）。

## 契約（收斂後）

- 路由掛 main.py 的 report_versions_router（該組路由被搬到 app.routes 最前，
  天然避開 `/reports/{job_id}` 把 `ppt-layout` 吃成 int 的 422）。
- **頁面展開委派 build_ppt._expand_page_layout**——依「該版 report_data 實際有的
  報表」展開，不是全部報表定義。舊契約（本檔前版所鎖）正是錯誤行為：
  預覽列出該版沒產的報表頁、頁碼與產檔不同步，以頁碼為 key 的覆寫全數錯位。
- `?version=` 指定版本；未給＝最新版。404＝版本不存在；503＝缺 skill 檔案。
- `kinds`＝build_ppt 全部 renderer（換版型下拉的合法值域），不是「已用到的集合」。

測試以假 run source 注入（mock _resolve_run_dir／_latest_run_dir），
不依賴本機 output 目錄有沒有報表版本；theme／build_ppt 需 skill 檔案，
無 skill 的環境整檔 skip（容器 503 行為由 mock 載入器另測）。
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app.main import app
from backend.app.worker.ai_report_ppt_runner import BUILD_PPT_PATH


client = TestClient(app)

# 只產兩種報表的版本——舊實作（展開全部定義）在此情境的頁數／頁碼必然與新契約不同
_FAKE_REPORT_DATA = {
    "reports": {
        "application_trend": {"label_zh": "申請趨勢", "rows": [{"year": 2024, "count": 3}]},
    },
}


class _FakeSource:
    """最小 run source 替身（同 main._DirRunSource 介面）。"""

    def __init__(self, name: str, files: dict[str, bytes]):
        self.name = name
        self._files = files

    def read_bytes(self, filename: str):
        return self._files.get(filename)

    def exists(self, filename: str) -> bool:
        return filename in self._files


def _fake_source(name: str = "report_trial_20260729_010101") -> _FakeSource:
    return _FakeSource(name, {
        "report_data.json": json.dumps(_FAKE_REPORT_DATA, ensure_ascii=False).encode("utf-8"),
    })


@unittest.skipUnless(BUILD_PPT_PATH.exists(), "需要 skill 檔案（build_ppt.py／theme.json）")
class ReportPptLayoutApiTests(unittest.TestCase):
    """驗證 PPT 版型 API 是前端預覽的單一來源，且與產檔展開一致。"""

    def test_route_returns_200_with_theme(self):
        """路由回 200 且帶 theme——排在 `/reports/{job_id}` 後會變 422。"""
        with mock.patch.object(main_module, "_latest_run_dir", return_value=_fake_source()):
            resp = client.get("/api/v1/reports/ppt-layout")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["theme"]["slide"]["width_in"], 13.333)
        self.assertEqual(body["theme"]["slide"]["height_in"], 7.5)
        self.assertIn("geometry", body["theme"])
        self.assertEqual(body["version"], "report_trial_20260729_010101")

    def test_pages_match_build_ppt_expansion_not_all_definitions(self):
        """頁面＝build_ppt 對該版 report_data 的展開；沒產的報表不得出現。"""
        with mock.patch.object(main_module, "_latest_run_dir", return_value=_fake_source()):
            body = client.get("/api/v1/reports/ppt-layout").json()

        pages = body["pages"]
        self.assertEqual([p["page"] for p in pages], list(range(1, len(pages) + 1)),
                         "頁碼必須連號（覆寫以頁碼為 key）")
        self.assertEqual(pages[0]["kind"], "cover")
        covered = {key for p in pages for key in p["report_keys"]}
        # ⚠ 基礎版型 12 頁的 report_keys（owner_ranking 等）恆在——那些是固定頁，
        # 缺資料時佔位顯示，不是錯。資料驅動的差異要用**不在基礎版型**的報表驗：
        # recent_assignee_ranking 不在基礎版型、也不在本版 report_data → 不得佔頁
        # （舊實作展開全部定義，它必然出現）。
        self.assertNotIn("recent_assignee_ranking", covered,
                         "沒產的報表出現在預覽＝舊的錯誤行為（展開全部定義）")

    def test_report_in_data_but_not_template_gets_a_page(self):
        """report_data 有、基礎版型沒有的報表 → 必須補頁（資料驅動的另一半）。"""
        source = _fake_source()
        data = {"reports": dict(_FAKE_REPORT_DATA["reports"])}
        data["reports"]["recent_assignee_ranking"] = {
            "label_zh": "最近受讓人排行", "rows": [{"name": "A", "count": 1}]}
        source._files["report_data.json"] = json.dumps(
            data, ensure_ascii=False).encode("utf-8")

        with mock.patch.object(main_module, "_latest_run_dir", return_value=source):
            pages = client.get("/api/v1/reports/ppt-layout").json()["pages"]

        covered = {key for p in pages for key in p["report_keys"]}
        self.assertIn("recent_assignee_ranking", covered,
                      "該版實際產出的報表必須有對應頁")

    def test_version_param_and_unknown_version(self):
        """?version= 指定版本；未知版本 404。"""
        def resolver(version):
            return _fake_source(version) if version == "report_trial_x" else None

        with mock.patch.object(main_module, "_resolve_run_dir", side_effect=resolver):
            ok = client.get("/api/v1/reports/ppt-layout?version=report_trial_x")
            missing = client.get("/api/v1/reports/ppt-layout?version=report_trial_nope")

        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["version"], "report_trial_x")
        self.assertEqual(missing.status_code, 404)

    def test_kinds_are_full_renderer_domain(self):
        """kinds＝全部 renderer——只回「已用到的」會讓使用者換不到未用的版型。"""
        with mock.patch.object(main_module, "_latest_run_dir", return_value=_fake_source()):
            body = client.get("/api/v1/reports/ppt-layout").json()

        kinds = body["kinds"]
        self.assertEqual(kinds, sorted(set(kinds)))
        used = {p["kind"] for p in body["pages"]}
        self.assertTrue(used.issubset(set(kinds)))
        self.assertIn("section_divider", kinds)

    def test_no_report_version_returns_404(self):
        """沒有任何報表版本＝404 帶可行動訊息，不是 500。"""
        with mock.patch.object(main_module, "_latest_run_dir", return_value=None):
            resp = client.get("/api/v1/reports/ppt-layout")

        self.assertEqual(resp.status_code, 404)
        self.assertIn("產製報表", resp.json()["detail"])


class SkillMissingTests(unittest.TestCase):
    """容器缺 skill 檔案（build_ppt 載不到）→ 503 帶可行動訊息，不是 500 追蹤。"""

    def test_builder_failure_returns_503(self):
        with mock.patch.object(main_module, "_latest_run_dir", return_value=_fake_source()), \
                mock.patch("backend.app.worker.ai_report_ppt_runner._load_builder",
                           side_effect=RuntimeError("no skill files")):
            resp = client.get("/api/v1/reports/ppt-layout")

        self.assertEqual(resp.status_code, 503)
        self.assertIn("skill", resp.json()["detail"])
