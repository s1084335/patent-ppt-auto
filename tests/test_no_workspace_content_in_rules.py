"""CLI 會讀的規則與 prompt 不得寫死特定 workspace 的內容（2026-08-10 使用者震怒版）。

## 為什麼需要這支測試

`content_standard.md` 的通用性一節自己寫著「任何寫死特定 workspace 名稱、
特定技術詞彙、特定公司名的規則都是缺陷」——但 2026-08-10 實測發現：

- `content_standard.md` 本身違反六處（「馬達自鎖：法蘭擋板＋電磁鐵解鎖，
  帝瑪斯 5 件同架構」直接寫在示例表裡）
- 規劃 prompt（`report_planning_runner.py`）的機構層示範表也寫死同一批名詞
- ⚠ 而那批資料**正好就是當時在跑的 workspace**——CLI 讀到現成句子會直接抄，
  抄出來的句子看起來完全合格，驗收根本分不出是查證寫的還是抄的。

免責聲明（「這是標杆不是模板」）擋不住照抄；**把具體名詞拿掉**才擋得住。
規則寫在檔案裡沒有程式驗證＝等於沒有規則（known-issues C-1），故立此測試。

## 範圍界線

- **要掃**：skill 的 .md（CLI 規劃與解讀時讀）、兩個 runner 組出的 prompt 字串。
- **不掃**：測試檔（測試用真實資料是正常的）、程式註解（記錄實機案例是歷史）、
  openspec（決策紀錄）。
- 詞表是**已知曾寫死過的詞**，不是完備清單——新 workspace 的詞不會在這裡，
  防的是「已清掉的又長回來」。範例文件的頁碼引用（「滑雪機 p8」＝出處標注）
  不在禁止之列，故詞表不含單獨的「滑雪機」「割草機」。
"""
from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 曾被寫死進規則/prompt 的 workspace 專屬詞（主題名、公司名、機構描述）
FORBIDDEN_TERMS = (
    "法蘭擋板", "電磁鐵解鎖", "帝瑪斯", "曾晴", "祺驊", "孟喬", "南通鐵人",
    "馬達自鎖", "捲輪回捲", "風磁", "拉繩滑雪", "立柱滑輪", "虛擬互動",
    "反向捲繞", "渦電流",
)

# CLI 會讀的 skill 文件（版型庫、內容標準、資料取用、解讀流程）
SKILL_DIR = PROJECT_ROOT / "skills" / "patent-report-ppt"
SKILL_DOCS = sorted(SKILL_DIR.glob("*.md"))


def _violations(text: str, source: str) -> list[str]:
    out = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for term in FORBIDDEN_TERMS:
            if term in line:
                out.append(f"{source}:{lineno} 含「{term}」：{line.strip()[:70]}")
    return out


class SkillDocsTests(unittest.TestCase):
    """skill 的 .md 全部掃過——CLI 規劃與解讀時逐份讀它們。"""

    def test_skill_docs_have_no_workspace_content(self):
        self.assertTrue(SKILL_DOCS, "skill 目錄下找不到 .md，掃描範圍失效")
        found: list[str] = []
        for doc in SKILL_DOCS:
            found += _violations(doc.read_text(encoding="utf-8"), doc.name)
        self.assertEqual(found, [], "skill 規則寫死了 workspace 內容：\n" + "\n".join(found))


class PromptTests(unittest.TestCase):
    """兩個 runner 實際組出的 prompt——這才是 CLI 眼睛真正看到的字串。"""

    def test_planning_prompt_clean(self):
        """規劃 prompt（無解讀的路徑）不得含任何 workspace 詞。

        ⚠ 只驗**規則文字**：不帶 narratives——實際跑時解讀素材裡出現公司名
        是正常的（那是查證產物），規則自帶名詞才是缺陷。
        """
        from backend.app.reports.planning_defaults import build_brief
        from backend.app.worker.report_planning_runner import build_prompt

        brief = build_brief(
            snapshot_id="v1", workspace_id=3, north_star_goal="找空白區",
            audience="研發主管", page_budget=11,
            selected_charts=[{
                "chart_identity": "applicant_strength_profile:default",
                "title": "Key Players", "image_path": "kp.svg",
                "data_rows": [{"applicant_display_name": "甲", "patent_count": 9}],
                "population_note": "", "version": "v1", "checksum": "abc",
            }],
        )
        found = _violations(build_prompt(brief), "planning_prompt")
        self.assertEqual(found, [], "\n".join(found))

    def test_narrative_prompt_clean(self):
        """解讀 prompt 同樣不得含 workspace 詞。

        ⚠ 2026-08-10 實例：公司短稱示例寫了「廈門帝瑪斯健康科技→帝瑪斯」，
        已改為〈城市〉〈字號〉佔位符。
        """
        import inspect

        from backend.app.worker import ai_narrative_runner as nr

        # 解讀 prompt 由模組內字串常數＋組裝函式構成；直接掃模組原始碼中的
        # **字串字面值**會把註解也算進去（註解記錄實機案例是允許的），
        # 故解析出所有 str 常數再掃。
        import ast

        source = inspect.getsource(nr)
        tree = ast.parse(source)
        found: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for term in FORBIDDEN_TERMS:
                    if term in node.value:
                        found.append(
                            f"ai_narrative_runner.py:{node.lineno} 字串含「{term}」")
        self.assertEqual(found, [], "\n".join(found))


if __name__ == "__main__":
    unittest.main()
