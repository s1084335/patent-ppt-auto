"""色票唯一定義處與媒介對照（tasks §6.3／§6.5／§6.2a）。

## 為什麼要有這個

實查（§6.0）：chart 側 48 種顏色、49 處散落，**24 種完全沒有具名常數**；
deck 側 14 種。兩側各自維護，於是同一個深藍有兩個值（`#00094A` vs `#0B2545`）
而**沒有任何東西會報錯**。

## 使用者裁決（§6.2，2026-08-19）

兩套深藍**都留，但不得同頁**。分界從「哪個模組」改成「哪個媒介」：
HTML 報表全用 `#00094A`、PPTX 簡報全用 `#0B2545`（含進 deck 的那份圖），
同一份 SVG 進 deck 時整批換色，任一頁只會出現一種深藍。

## 落點：`chart_sizing`（沿用字型的既有先例）

⚠ 我先前在 §6.2a 寫「跨部署單元不能 import，要走資料流」——**那是錯的**。
`deck_layout.py:27` 與 `rebuild_chip_chart.py` **現在就 import**
`backend.app.reports.chart_sizing`（字型 2026-08-13 使用者裁決選項 A）。
色票沿用同一條路，少一個序列化層、也少一份可能分岔的副本。

## 這個測試守什麼

1. 色票是唯一定義處：`chart_runner` 不得自己再寫一份 hex
2. 每個色都要有**語意用途**（§6.5 的軟揭露，空字串不算）
3. 每個色都要標**媒介**（report／deck／both）——4 種兩側共用的色裡，
   前三個是真共用、第四個（`#00094A`）是待換掉的，不分開就會把 bug 當成設計
4. 對照表兩端都必須在色票內——指到色票外的值等於又開了一個定義處
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = ROOT / "skills" / "html-report-to-deck" / "scripts"
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

HEX = re.compile(r"#[0-9A-Fa-f]{6}")
MEDIA = ("report", "deck", "both")


class PaletteRegistryTests(unittest.TestCase):
    """色票登記表本身的形狀。"""

    @classmethod
    def setUpClass(cls):
        from backend.app.reports import chart_sizing

        cls.mod = chart_sizing
        cls.palette = chart_sizing.PALETTE

    def test_every_entry_declares_purpose(self):
        """§6.5：新增顏色必須填語意用途。⚠ 空字串不算填。"""
        missing = [name for name, e in self.palette.items()
                   if not str(getattr(e, "purpose", "")).strip()]
        self.assertEqual(
            missing, [],
            f"這些色沒有語意用途：{missing}——"
            "沒有用途的色，下一個人只能靠猜要不要沿用")

    def test_every_entry_declares_medium(self):
        """媒介是 §6.2「不得同頁」的依據，不能缺。"""
        bad = [(n, getattr(e, "medium", None)) for n, e in self.palette.items()
               if getattr(e, "medium", None) not in MEDIA]
        self.assertEqual(bad, [], f"媒介欄不合法（只能是 {MEDIA}）：{bad}")

    def test_hex_values_are_normalised(self):
        """一律大寫六碼——大小寫不一致會讓後面的字串比對閘門漏掉。"""
        bad = [(n, e.hex) for n, e in self.palette.items()
               if not re.fullmatch(r"#[0-9A-F]{6}", e.hex)]
        self.assertEqual(bad, [], f"色值格式不合（需 #RRGGBB 大寫）：{bad}")

    def test_no_duplicate_hex_within_same_medium(self):
        """⚠ 同一個媒介裡兩個名字指同一個色＝那就是同一份知識兩個落點。

        🔴 **本條的適用範圍在 §6.5 收色時必須先裁決**（2026-08-19 實查發現）：
        `chart_runner` 有**四套獨立色階恰好共用色值**——

        | 色階 | 內容 |
        |---|---|
        | `STATUS_COLORS`（生命週期） | 申請 #93C5FD／公開 #60A5FA／授權 #10B981／放棄 #9CA3AF |
        | `YEAR_BUBBLE_COLOR_BANDS`（強度） | 低 #93C5FD／中 #14B8A6／高 #F59E0B／最高 #DC2626 |
        | `_TIER_COLORS`（龍頭涉入） | lead≥2 #DC2626／lead=1 #F59E0B／lead=0 #9CA3AF |
        | `qcolors`（象限） | q1 #10B981／q2 #3B82F6／q3 #9CA3AF／q4 #F59E0B |

        它們是**不同的知識**（改「授權」的綠不該連帶改掉象限 q1），只是恰好
        撞到同一個 hex。若照本條把它們合併成單一條目，就是把四套獨立設計綁死
        ——**方向與本條想防的完全相反**。

        ⚠ 目前 PALETTE 只收 11 個各自唯一的色，本條成立且有價值；
        但 §6.5 把那四套色階收進來之前，必須先決定「同色多語意」怎麼表達
        （獨立條目？收斂成具名色階？逐組判斷？），不得為了讓本條通過而硬併。
        """
        seen: dict[tuple[str, str], str] = {}
        dupes = []
        for name, e in self.palette.items():
            key = (e.medium, e.hex)
            if key in seen:
                dupes.append((seen[key], name, e.hex, e.medium))
            seen[key] = name
        self.assertEqual(dupes, [], f"同媒介重複色：{dupes}")


class ColorScaleTests(unittest.TestCase):
    """🔴 色票的單位不只是「一個色」，還有「一套色階」（2026-08-19 使用者裁決）。

    實查發現四套**獨立**色階恰好共用色值——`#9CA3AF` 同時是法律狀態「放棄」、
    龍頭涉入 `lead=0`、象限 q3 與註記文字色。它們是不同的知識
    （改「授權」的綠不該連帶改掉象限 q1），只是撞到同一個 hex。

    若把它們拆成一色一條目並禁止重複，會被迫合併＝把四套獨立設計綁死，
    **方向與「同一份知識一個落點」想防的完全相反**。故色票收「色階」為單位。
    """

    @classmethod
    def setUpClass(cls):
        from backend.app.reports import chart_sizing

        cls.scales = chart_sizing.SCALES

    def test_expected_scales_registered(self):
        for key in ("STATUS", "INTENSITY", "TIER", "QUADRANT"):
            with self.subTest(scale=key):
                self.assertIn(key, self.scales, f"色階 {key} 沒登記")

    def test_each_scale_declares_purpose_and_medium(self):
        bad = [k for k, s in self.scales.items()
               if not str(s.purpose).strip() or s.medium not in MEDIA]
        self.assertEqual(bad, [], f"色階缺用途或媒介不合法：{bad}")

    def test_steps_carry_meaning_not_just_hex(self):
        """⚠ 只存 hex 序列＝下一個人不知道第三格是什麼意思，等於沒有語意。"""
        for key, s in self.scales.items():
            with self.subTest(scale=key):
                self.assertTrue(s.steps, f"{key} 是空的")
                for label, hexv in s.steps:
                    self.assertTrue(str(label).strip(), f"{key} 有一階沒有語意標籤")
                    self.assertRegex(hexv, r"^#[0-9A-F]{6}$", f"{key} 色值格式不合")

    def test_no_duplicate_within_one_scale(self):
        """⚠ **同一套**色階裡重複＝那兩階讀者分不出來，是真的 bug。

        跨色階重複則是允許的（那正是本類別存在的理由）。
        """
        for key, s in self.scales.items():
            hexes = [h for _lbl, h in s.steps]
            with self.subTest(scale=key):
                self.assertEqual(len(hexes), len(set(hexes)),
                                 f"{key} 內有重複色值：{hexes}")

    def test_status_scale_matches_engine(self):
        """色階不得與引擎現行值分岔——分岔的症狀是圖與表對不上，不是報錯。"""
        from backend.app.reports.chart_runner import STATUS_COLORS

        registered = {lbl: h for lbl, h in self.scales["STATUS"].steps}
        self.assertEqual(registered, STATUS_COLORS,
                         "STATUS 色階與 chart_runner.STATUS_COLORS 不一致")


class NotSamePageTests(unittest.TestCase):
    """🔴 同頁互斥色對（§6.2 深藍、§6.5 兩個紅，使用者裁決「都留但不同頁」）。"""

    @classmethod
    def setUpClass(cls):
        from backend.app.reports import chart_sizing

        cls.pairs = chart_sizing.NOT_SAME_PAGE

    def test_navy_pair_declared(self):
        self.assertIn(("#00094A", "#0B2545"), self.pairs,
                      "兩套深藍沒登記為同頁互斥")

    def test_red_pair_declared(self):
        """實測 ΔE2000 = 4.59（並置可辨）——與深藍同一種病，只是小一號。"""
        self.assertIn(("#C62828", "#DC2626"), self.pairs,
                      "兩個紅沒登記為同頁互斥")

    def test_pairs_are_ordered_and_distinct(self):
        """⚠ 左右相同的「互斥對」永遠成立，會讓閘門顯示已處理。"""
        for a, b in self.pairs:
            with self.subTest(pair=(a, b)):
                self.assertNotEqual(a, b)


class MediumRemapTests(unittest.TestCase):
    """§6.2：報表色 → deck 色的對照表。"""

    @classmethod
    def setUpClass(cls):
        from backend.app.reports import chart_sizing

        cls.mod = chart_sizing
        cls.remap = chart_sizing.REPORT_TO_DECK
        cls.palette = chart_sizing.PALETTE

    def test_navy_pair_is_declared(self):
        """使用者裁決的那一對必須在表上（§6.2 實查落點）。"""
        self.assertEqual(
            self.remap.get("#00094A"), "#0B2545",
            "兩套深藍的對照沒宣告——換色步驟無從得知要換什麼")

    def test_both_ends_are_in_palette(self):
        """⚠ 指到色票外的值＝又開了一個定義處，而且是隱形的。"""
        known = {e.hex for e in self.palette.values()}
        stray = [(k, v) for k, v in self.remap.items()
                 if k not in known or v not in known]
        self.assertEqual(stray, [], f"對照表指到色票外的值：{stray}")

    def test_source_side_is_report_target_side_is_deck(self):
        """方向不能反：左欄必須是報表側的色，右欄必須是 deck 側的色。"""
        by_hex: dict[str, list] = {}
        for e in self.palette.values():
            by_hex.setdefault(e.hex, []).append(e.medium)
        for src, dst in self.remap.items():
            with self.subTest(pair=(src, dst)):
                self.assertIn("report", by_hex.get(src, []),
                              f"{src} 不是報表側的色，不該出現在對照表左欄")
                self.assertIn("deck", by_hex.get(dst, []),
                              f"{dst} 不是 deck 側的色，不該出現在對照表右欄")

    def test_remap_is_not_identity(self):
        """⚠ 左右相同＝這條對照什麼都沒做，卻會讓閘門顯示「已處理」。"""
        noop = [k for k, v in self.remap.items() if k == v]
        self.assertEqual(noop, [], f"對照表有左右相同的項：{noop}")


class ChartRunnerConsumesPaletteTests(unittest.TestCase):
    """chart_runner 不得自己再寫一份色。"""

    def test_named_colors_come_from_palette(self):
        """既有具名色（COLOR_TEXT 等）必須是從色票取，不是自己寫 hex。"""
        from backend.app.reports import chart_runner

        src = (ROOT / "backend/app/reports/chart_runner.py").read_text(
            encoding="utf-8")
        for const in ("COLOR_TEXT", "COLOR_APPLICATION", "COLOR_GRID"):
            with self.subTest(const=const):
                m = re.search(rf"^{const}\s*=\s*(.+)$", src, re.M)
                self.assertIsNotNone(m, f"{const} 不見了")
                self.assertNotRegex(
                    m.group(1), HEX,
                    f"{const} 仍自己寫 hex——色票不是唯一定義處")
        # 值仍要正確（不是把常數改成空殼就算過）
        self.assertEqual(chart_runner.COLOR_TEXT, "#00094A")


class DeckLayoutConsumesPaletteTests(unittest.TestCase):
    """deck 側的主文字色也必須來自色票（§6.3b）。"""

    def test_deck_text_comes_from_palette(self):
        """⚠ `deck_layout.TEXT` 原本自己寫 `RGBColor(0x0B, 0x25, 0x45)`。

        它與 `chart_runner.COLOR_TEXT` 是「改一邊就得改另一邊」的同一份知識
        （§6.2 的媒介對照表就是在描述這個關係），所以只能有一處。
        """
        code = (SKILL_SCRIPTS / "deck_layout.py").read_text(encoding="utf-8")
        m = re.search(r"^TEXT\s*=\s*(.+)$", code, re.M)
        self.assertIsNotNone(m, "deck_layout.TEXT 不見了")
        self.assertNotRegex(
            m.group(1), r"RGBColor\(\s*0x0B",
            "deck_layout.TEXT 仍自己寫色值——色票不是唯一定義處")

    def test_deck_text_value_unchanged(self):
        """⚠ 接色票不得順手改掉值：§6.2 裁決是「都留」，不是收斂成一個。"""
        import deck_layout

        self.assertEqual(
            (deck_layout.TEXT[0], deck_layout.TEXT[1], deck_layout.TEXT[2]),
            (0x0B, 0x25, 0x45),
            "deck 主文字色被改動了——使用者裁決是兩套都留")

    def test_hex_to_rgbcolor_helper_is_shared(self):
        """hex → RGBColor 的換算只能有一份（兩份會在大小寫／前綴上分岔）。"""
        import deck_layout

        self.assertTrue(
            hasattr(deck_layout, "rgb"),
            "缺 hex→RGBColor 的共用換算——每個用到的地方各寫一次必然分岔")
        self.assertEqual(
            tuple(deck_layout.rgb("#0B2545")), (0x0B, 0x25, 0x45))


if __name__ == "__main__":
    unittest.main()
