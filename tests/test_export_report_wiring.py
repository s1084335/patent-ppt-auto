"""匯出報告全線接線體檢後的修正（2026-07-29 使用者定「全部修好」）。

體檢發現七項（詳見 work-log 2026-07-29），本檔鎖其中前端與 API 可靜態驗證者：

## ③ 匯出頁「重新產製報表資料」沒帶 workspace_id

`triggerExport` 送空 body → worker `handle_report_generate` 的
`workspace_id=None → return None` → **三份分群報表靜默跳過**。
07-28 修報表種類頁的 `submitReports`（呼叫點①）時，同檔案的呼叫點②漏掉
——同一個 bug 在兩個呼叫點，只修了一個。

## ④ requestExportPpt 頂層冗餘參數

params 頂層送 `slots`／`layout_overrides`／`position_overrides`，但派工端
（ai_bridge `_run_ai_report_ppt_job`）只讀 `approval_overrides`（內含同一份）。
頂層三個 key **不生效**，留著會誤導維護者以為它們有作用。

## ①② 頁面展開唯一實作 ＋ reports.py 去重

「PPT 頁面展開」原本三份實作（reports.py 兩份 ＋ build_ppt 一份），
且 API 端與 build_ppt 端的展開規則不同（動態頁來源／插入錨點／順序全不一致）
——覆寫以頁碼為 key，預覽頁碼與產檔頁碼錯位＝拖曳與版型套錯頁。
收斂為：**build_ppt._expand_page_layout 是唯一展開實作**，API 載入該版
report_data 後呼叫它，保證預覽頁碼＝產檔頁碼。

## ⑦ 匯出頁可選版本

原本 `loadExportPreview` 寫死 `/report-latest/content`——只能匯最新版。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "backend" / "app" / "static" / "index.html"


def _js_function(html: str, name: str) -> str:
    """抓 top-level function 本體（沿 test_api_frontend 的做法）。"""
    m = re.search(
        r"^(async\s+)?function " + re.escape(name) + r"\([^)]*\) \{(.*?)^\}",
        html, re.S | re.M,
    )
    assert m, f"找不到函式 {name}"
    return m.group(2)


class TriggerExportSendsWorkspaceTests(unittest.TestCase):
    """③：匯出頁重產報表必須帶 workspace_id。"""

    def test_trigger_export_sends_workspace_id(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        body = _js_function(html, "triggerExport")
        self.assertIn("workspace_id", body,
                      "triggerExport 沒帶 workspace_id——三份分群報表會被靜默跳過"
                      "（07-28 已在 submitReports 修過同一 bug，此為漏掉的呼叫點②）")
        self.assertIn("state.workspaceId", body)


class ExportPptParamsTests(unittest.TestCase):
    """④：params 只送 approval_overrides，不再頂層重複三個 key。"""

    def test_no_redundant_top_level_override_keys(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        body = _js_function(html, "requestExportPpt")
        self.assertIn("approval_overrides", body)
        # 頂層的 slots:／layout_overrides:／position_overrides: 是派工端不讀的死參數
        for key in ("slots:", "layout_overrides:", "position_overrides:"):
            with self.subTest(key=key):
                self.assertNotIn(
                    key, body,
                    f"params 頂層的 {key} 派工端不讀（只讀 approval_overrides），"
                    "留著會誤導維護者以為它有作用",
                )


class LayoutEndpointSingleImplTests(unittest.TestCase):
    """①②：ppt-layout 收斂為單一實作、單一路由（main.py），展開委派 build_ppt。

    舊狀態＝reports.py 同檔兩份（helper 三支＋路由各兩份）：FastAPI 路由先註冊者贏、
    Python 函式後定義者贏，實際行為是兩份的混種，第二個端點是永遠打不到的死碼。
    """

    @staticmethod
    def _reports_src() -> str:
        return (PROJECT_ROOT / "backend" / "app" / "api" / "reports.py").read_text(
            encoding="utf-8")

    @staticmethod
    def _main_src() -> str:
        return (PROJECT_ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")

    def test_single_route_registration(self):
        """全 backend 只能有一處註冊 `/reports/ppt-layout`。"""
        n_reports = self._reports_src().count('"/reports/ppt-layout"')
        n_main = self._main_src().count('@report_versions_router.get("/reports/ppt-layout")')
        self.assertEqual(n_reports, 0,
                         "reports.py 不得再有 ppt-layout（已搬 main.py versions router）")
        self.assertEqual(n_main, 1, f"main.py 應恰好註冊一次，實得 {n_main}")

    def test_no_duplicate_helper_definitions(self):
        """舊 helper 三支必須整組消失——它們是與 build_ppt 平行的第二套展開。"""
        src = self._reports_src() + self._main_src()
        for fn in ("_ppt_page_spec_to_dict", "_ppt_kind_for_report",
                   "_expand_ppt_pages_with_active_reports"):
            with self.subTest(fn=fn):
                self.assertEqual(
                    len(re.findall(rf"^def {fn}\(", src, re.M)), 0,
                    f"{fn} 仍存在——展開只能有 build_ppt 一份實作")

    def test_expansion_delegates_to_build_ppt(self):
        """main.py 的頁面組裝必須委派 build_ppt 的 `_expand_page_layout`。"""
        self.assertIn("_expand_page_layout", self._main_src(),
                      "API 沒委派 build_ppt 的展開實作")

    def test_pages_match_build_ppt_expansion(self):
        """行為驗證：API 頁面序列＝build_ppt 對同一份 report_data 的展開。"""
        import importlib.util
        import sys

        from backend.app.worker.ai_report_ppt_runner import BUILD_PPT_PATH

        if not BUILD_PPT_PATH.exists():
            raise unittest.SkipTest("本機無 skill 檔案（容器情境走 503，另有測試）")

        spec = importlib.util.spec_from_file_location("build_ppt_t", BUILD_PPT_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules["build_ppt_t"] = module
        spec.loader.exec_module(module)

        # 模擬「只產了部分報表」的版本——舊 API 實作（展開全部報表定義）在此情境必然錯位
        report_data = {"reports": {"application_trend": {"label_zh": "申請趨勢",
                                                         "rows": [{"y": 1}]}}}
        expected = [(p.page, p.title) for p in module._expand_page_layout(report_data)]

        from backend.app.main import _pages_for_report_data

        actual = [(p["page"], p["title"]) for p in _pages_for_report_data(report_data)]
        self.assertEqual(actual, expected,
                         "API 頁面與 build_ppt 展開不一致——覆寫將套錯頁")


class ExportVersionSelectTests(unittest.TestCase):
    """⑦：匯出頁要能選報表版本，不再寫死最新版。"""

    def test_load_preview_accepts_version(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        body = _js_function(html, "loadExportPreview")
        self.assertIn("/reports/versions/", body,
                      "loadExportPreview 應能載入指定版本的 content")

    def test_layout_endpoint_not_fetched(self):
        """⚠ 2026-07-30 起前端不再拉版型端點——CSS 模擬版面移除後沒有消費者。

        舊測試驗「loadPptLayout 帶 version」；模擬版面移除後該函式整支退場，
        本測試改釘「前端不得再 fetch /reports/ppt-layout」。
        ⚠ 後端端點本身先留著（若第二步編輯模式選 E2 疊層式，座標還會用到）。
        """
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotIn("'/reports/ppt-layout'", html,
                         "前端仍在拉版型端點——模擬版面已移除，沒有消費者")


if __name__ == "__main__":
    unittest.main()


class SkillShipsWithRepoTests(unittest.TestCase):
    """🔴 部署斷鏈（體檢第 4 紅項）：skill 必須隨專案 repo 進容器。

    build_ppt.py＋theme.json 原本只在中央 `D:\力山\.agents\skills\`——
    不在專案 repo、Dockerfile 也沒 COPY。Lightning 容器裡 `_load_builder()`
    必炸 → `/reports/ppt-layout` 503、匯出頁預覽在正式部署起不來。
    本機開發時 `_resolve_skill_dir` 掃祖先目錄掃得到中央 .agents，
    把問題完全掩蓋——部署才爆（與 9d 跨容器斷鏈同類）。

    2026-07-29 使用者定案：**skill 搬進專案 repo 當唯一來源**（backend／worker／
    Companion 的程式都依賴它，部署單位是專案 repo；「同一資訊只有一份唯一來源」），
    中央 .agents 那份移除。路徑用 `skills/`（非 `.agents/`——AGENTS.md 明定
    本 repo 不建 .agents/ 目錄，那是工作紀錄與 context 的集中規範）。
    """

    SKILL_DIR = PROJECT_ROOT / "skills" / "patent-report-ppt"

    def test_skill_files_in_repo(self):
        for rel in ("scripts/build_ppt.py", "theme.json", "SKILL.md"):
            with self.subTest(file=rel):
                self.assertTrue((self.SKILL_DIR / rel).is_file(),
                                f"skill 檔案 {rel} 不在專案 repo——容器裡載不到")

    def test_dockerfile_copies_skills(self):
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        copies = [l for l in dockerfile.splitlines()
                  if l.strip().startswith("COPY") and "skills" in l]
        self.assertTrue(copies,
                        "Dockerfile 沒 COPY skills/——repo 有檔也進不了 image")

    def test_runner_resolves_repo_local_first(self):
        """runner 的 SKILL_DIR 必須解析到專案內副本（唯一來源）。"""
        from backend.app.worker import ai_report_ppt_runner as r

        resolved = r._resolve_skill_dir()
        self.assertEqual(resolved, self.SKILL_DIR,
                         f"SKILL_DIR 解析到 {resolved}——應優先取專案內 skills/")
