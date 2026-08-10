"""餵了既有解讀，就必須看得出有沒有用它（2026-08-10 使用者裁決）。

## 為什麼是驗結構而不是驗內容

`316a958` 把 narratives 餵進規劃 prompt 並寫明「濃縮不是重寫」，但那條規則**只活在
提示裡**——CLI 收到素材後仍可無視它自己重寫，沒有東西擋得住。這正是本專案反覆
出現的「規則寫在提示裡等於沒有規則」。

兩個可行方向中，使用者裁決做**溯源**而非覆蓋率：

| 方向 | 驗什麼 | 為何沒選 |
|---|---|---|
| 具名對象覆蓋率 | narratives 的申請人／主題有多少比例出現在 SlidePlan | 濃縮本來就會捨棄部分對象，門檻訂多少會變成又一次「量完才訂判準」 |
| **evidence 溯源** ✅ | 要點引用既有解讀時，evidence 標 `source="narrative"` 並指出來源 report_key | 驗的是**結構**不是內容相似度，不需要模糊門檻，也不懲罰合理取捨 |

## 門檻刻意訂得低

有餵素材時**至少一筆** evidence 標 narrative 來源即可。目的是讓「有沒有用素材」
從「讀文字判斷」變成**可查的事實**，不是規定要用多少。
"""
from __future__ import annotations

import unittest

from backend.app.reports.planning_contracts import validate_evidence

SNAP = "v1"


def _plan(*refs):
    return {"slides": [{"slide_id": "s1", "narrative": [
        {"text": "曾晴 9 件", "evidence_ref": r} for r in refs]}]}


class NarrativeSourcingTests(unittest.TestCase):
    """有素材就要看得出用了沒。"""

    def test_narrative_source_accepted(self):
        """`source="narrative"` 是合法來源，且要能指出來自哪一份解讀。"""
        manifest = {"e1": {"source": "narrative", "snapshot_id": SNAP,
                           "report_key": "applicant_strength_profile"}}
        self.assertEqual(
            validate_evidence(_plan("e1"), manifest, snapshot_id=SNAP,
                              has_narratives=True),
            [],
        )

    def test_narrative_source_requires_report_key(self):
        """標了 narrative 卻不說來自哪一份＝無法追溯，等於沒標。"""
        manifest = {"e1": {"source": "narrative", "snapshot_id": SNAP}}
        errors = validate_evidence(_plan("e1"), manifest, snapshot_id=SNAP,
                                   has_narratives=True)
        self.assertTrue(errors)
        self.assertIn("report_key", errors[0])

    def test_rejects_when_narratives_fed_but_never_sourced(self):
        """餵了素材卻一筆都沒引用 → 不合格（這就是「重寫而非濃縮」的可查跡象）。"""
        manifest = {"e1": {"source": "selected_chart", "snapshot_id": SNAP,
                           "chart_identity": "x:default"}}
        errors = validate_evidence(_plan("e1"), manifest, snapshot_id=SNAP,
                                   has_narratives=True)
        self.assertTrue(errors)
        self.assertIn("濃縮", errors[-1])

    def test_no_narratives_no_requirement(self):
        """沒餵素材時不得要求溯源——解讀確實可能還沒產出。"""
        manifest = {"e1": {"source": "selected_chart", "snapshot_id": SNAP,
                           "chart_identity": "x:default"}}
        self.assertEqual(
            validate_evidence(_plan("e1"), manifest, snapshot_id=SNAP,
                              has_narratives=False),
            [],
        )

    def test_default_keeps_old_behaviour(self):
        """預設不帶參數＝維持既有行為（呼叫端沒改到的地方不受影響）。"""
        manifest = {"e1": {"source": "selected_chart", "snapshot_id": SNAP}}
        self.assertEqual(validate_evidence(_plan("e1"), manifest, snapshot_id=SNAP), [])


if __name__ == "__main__":
    unittest.main()
