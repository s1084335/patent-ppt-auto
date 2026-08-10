"""報表解讀 headless CLI runner（把 scripts/run_narrative_task.ps1 的核心系統化）。

用途：worker 的 'ai:narrative' handler 呼叫此模組，把「組 headless CLI 提示 → 執行
`claude -p --output-format json` → 驗收 narratives.json → 觸發 --refresh-index」整條
確定性流程收進 Python，供 background worker 消費，並保留給 Patent Companion 對接。

設計重點：
- CLI 呼叫抽成可注入的 `cli_runner`（測試環境無 claude CLI，故 handler／單元測試餵 fake runner，
  不真跑二進位）。
- 雙 CLI 可換（2026-07-21 定案）：`cli_kind` 參數選 claude／opencode，預設 claude；不把
  'claude' 寫死到無法替換。指令組裝集中在 build_cli_command，換 CLI 只改此處對照表。
- narratives 落點沿用 run_narrative_task.ps1 現行落點（報表輸出目錄下 narratives.json），
  不新增落點語意；DB 敘述型回存另由 MCP save_analysis_narrative 負責，不在此重複。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Sequence

# 專案根目錄（此檔位於 backend/app/worker/ai_narrative_runner.py，往上 3 層）。
PROJECT_ROOT = Path(__file__).resolve().parents[3]
# full_report_latest 為報表引擎慣用輸出根；各版本為其下 report_trial_/analysis_ 子目錄。
FULL_REPORT_LATEST = PROJECT_ROOT / "output" / "full_report_latest"


def _resolve_skill_path() -> Path:
    """解讀規格來源（prompt 模板、narratives.json 契約、based_on_version 規則）。

    預設只取專案 repo 內 `skills/patent-report-ppt/report-narrative-flow.md`。正式部署若把
    規格掛載到其他位置，可用 `REPORT_NARRATIVE_FLOW_PATH` 覆寫。不得 fallback 到本機
    `.agents`；舊規格檔會掩蓋 Docker／公司伺服器缺檔問題。
    """
    configured = os.environ.get("REPORT_NARRATIVE_FLOW_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return PROJECT_ROOT / "skills" / "patent-report-ppt" / "report-narrative-flow.md"


SKILL_PATH = _resolve_skill_path()
# 解讀規格版本；隨 report-narrative-flow.md 模板升版而變。
# v3（2026-07-27）：prompt 納入使用者 instruction（原本 payload 有存但零消費）。
# v4（2026-07-31）：三件套契約——variant 加 headline／points（PPT 用要點，text 留給
# 報表頁長文）。動因：實機 PPT 每頁 1 段 400–500 字字牆，⚠ AI 沒有違規，是舊規則
# 教它寫散文（「標準寫法是：先點出數據現象…」）。
# v5（2026-07-31）：撰寫程序翻轉（points→headline→text）＋三道形狀鎖
# ＋逐報表版面容量。契約實質改變，故升版供產出追溯。
# v6（2026-08-02）：放寬形式、收緊內容。⚠ v5 的「不得含句號、逗號至多一個」
# 讓 CLI 只能砍掉數字（實測 points 只用 4 條 × 19 字，容量卻有 8 條 × 54 字）——
# 規則本身是 W-1 的根因。改為句號 ≤1／逗號 ≤2，同時新增三道內容鎖：
# 現況必須帶數字、每頁至少一條意涵、CPC 必須與 IPC 對照。
# v7（2026-08-03）：版面用量下限（要濃縮不要丟棄）＋敘述措辭四層
# （客觀描述 → 專利數據解讀 → 合理推論 → 分析限制）＋結論回到要點（標 emphasis）。
# v8（2026-08-04）：**移除版面用量下限**（v7 加的那道鎖是丟棄要點的根因，見下方
# 說明）＋ `max_chars` 改為扣掉標籤成本後的正文字數。契約實質改變，故升版供追溯。
# v10（2026-08-07 使用者指正）：拿掉標籤後格式仍固定＝只是換皮——取消「後續」
# 固定句型與逐條角色公式，改「格式完全不固定，但要講得出原因和結論」；並把本
# 字串注入 prompt（v9 沒注入，模型自己編了 report_narrative_v3 的假版本號）。
PROMPT_VERSION = "report_narrative_v10"

# ── 三件套契約上限（v4；單一來源，skill 條文與驗證都以此為準）──
# ⚠ 暫定值：理想上由 theme.json v2 的要點框尺寸換算，v2（skill creator 重建中）
#   落地後對框尺寸驗算；要調整只改這裡。
NARRATIVE_HEADLINE_MAX = 20   # 一句判讀結論（PPT 標題「{主題}：{headline}」）
# 以下三個是**全域上限**，實際能寫多少以 build_ppt.narrative_capacity() 逐報表算出的
# 版面容量為準（同一份數字同時餵給 prompt、validator 與裁切）。
NARRATIVE_POINT_TEXT_MAX = 55  # 每條要點的字數（2026-07-31 50→55，使用者選定）
# 🔴 2026-08-07 使用者定案：要點**取消固定標籤**（推翻 08-04 三層定案）——
# 「ppt解讀格式不用再定標籤，但一樣要能解釋現象背後的原因」。
# 條數上限維持 3（版面容量不變，容量另由 build_ppt.narrative_capacity 逐頁算）；
# 標籤欄位對舊檔**容忍**（照渲染），新產出不再要求。
# ⚠ 拿掉的是標籤形式，不是品質要求——數字事實與成因解釋改為頁級檢查（見鎖四／鎖五）。
NARRATIVE_LAYER_LABELS = ("現況", "意涵", "後續")   # legacy：僅供舊檔容忍與文件對照
NARRATIVE_POINTS_MIN = 2       # 2026-08-07 固定 3→2–3（自由條列，依內容）
NARRATIVE_POINTS_MAX = 3

# 要點與長文的數字一致性檢查用（含小數、百分比與千分位）。
_NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)*%?")

# 🔴 M-3（2026-08-04）：鎖八用的**統計數字**抽取——排除代碼型 token。
# 分類代碼（A63B-005、F03G-005）與專利號（2024-0173588、U+2011 版）的數字片段
# 不是統計數字：緊鄰字母或連字號（含 U+2011）的數字一律不取。
# ⚠ 只給鎖八用：鎖二（數字要出現在長文）與鎖四（現況要有數字）仍用寬鬆版
# ——代碼也該在長文出現、代碼也算「有數據依據」的一部分。
_STAT_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9\-\u2011])\d+(?:[.,]\d+)*%?(?![A-Za-z\-\u2011])"
)


def effective_max_chars(limits: dict[str, int] | None) -> int:
    """單一夾限出口（M-2，2026-08-04）：版面容量與全域可讀上限取小。

    🔴 曾兩處落點：validator 夾了 55、build_prompt 沒夾——L4 寬幅頁容量 97 字
    照原值進 prompt，CLI 守 prompt 寫 93 字仍被驗紅（規則自相矛盾，紅字全是噪音）。
    prompt 告知與 contract 驗證**必須同一個數字**，都走這裡。
    """
    raw = int((limits or {}).get("max_chars") or NARRATIVE_POINT_TEXT_MAX)
    return min(raw, NARRATIVE_POINT_TEXT_MAX)

# ── C-6：把「文字要有意義」從提示變成檢查（2026-08-02）──
#
# 使用者定調：報告文字要從「看到什麼數據 → 描述數據」提升為
# 「數據代表什麼 → 為何重要 → 對技術布局有何意義」。
# ⚠ 只寫在給 AI 看的提示、沒有程式驗證的規則，等於沒有規則
#   （known-issues-optimization C-1 的教訓）。以下三件是可程式化的部分。
# 2026-08-07 起標籤不再是檢查對象；兩個語意鎖改**頁級**：
# 至少一條帶統計數字（原鎖四）、至少一條解釋成因（原鎖五）。
# ⚠ 成因檢查是啟發式（找因果語彙）：驗得到「完全沒解釋」，驗不出「解釋得對不對」
# ——後者從標籤時代起就靠 prompt 與人工，這裡沒有變弱。
NARRATIVE_CAUSAL_MARKERS = ("因", "由", "來自", "反映", "顯示", "導致", "意味",
                            "使得", "代表", "屬", "源於", "隨", "受")

# 🔴 濃縮五規則的可程式化部分（2026-08-04 使用者定案「不走硬性字數路線」）：
# 填充詞禁詞——刪掉後句意不變的詞，出現即代表還有濃縮空間。
# ⚠ 清單是定案內容，增刪要先過使用者（test_narrative_condense_locks 鎖著）。
NARRATIVE_FILLER_WORDS = ("值得注意的是", "整體而言", "此外", "同時", "進行", "相關", "方面")

# 需要與另一張圖對照著講的報表。
# 🔴 參考報告（附件3 電輔自行車）的 CPC 段落寫的是「**與 IPC 分布圖不同的是**…
# Y02T（交通運輸的減排技術）…」——CPC 不重講一次 IPC，而是講 CPC 有而 IPC
# 沒有的分類及其意義。實機 p10／p11 與 p8／p9 逐字相同，F-4 修好的只是
# 「取到對的 variant」這個機制，內容上該講什麼差異是這一層的事。
NARRATIVE_CONTRAST_WITH = {"cpc_main_distribution": "IPC"}

# 🔴 2026-08-04：**版面用量下限（原 C-9 鎖七）已移除**，不要再加回來。
#
# 它 08-03 加入時的理由是 IPC L4 只寫了 81/432 字（18.8%）。但它造成的後果
# 比原問題更糟：**逼 CLI 寫到接近版面上限** → 必然踩到邊界 → 尾端整條被丟，
# 而丟的都是排在後面的「意涵」「後續」，正是價值最高的幾條
# （第五輪實機丟 5 條，contract warnings 卻是 0）。
#
# 🔴 使用者定案原話：「拿掉字數下限，但可以給他格式，畢竟現在版面是符合目標的
# 只是資訊被丟棄要修」。
#
# ⚠ 拿掉的只有**字數下限**。內容完整性由別的鎖守著，它們都還在：
#   - 鎖三：要覆蓋圖上主要事實
#   - 鎖五：至少要有一條「意涵」（不能只複述數據）
#   - 每條字數**上限**與條數上限（見下方 max_chars／max_points）
# 「寫得夠不夠」是內容問題，不是字數問題——用字數當代理指標就會逼出灌水。


def _point_text(point: Any) -> str:
    """取一條要點的文字，**任何形狀都不崩潰**。

    🔴 2026-08-10 實機 job 284：CLI 回了 `["文字", …]` 而非契約的 `[{"text": …}]`，
    原本四處都寫 `(point or {}).get("text")`——`or {}` 只防 None 與空值，
    對**非空字串**回傳字串本身，`.get` 直接 AttributeError。
    878 秒的解讀連同 CLI 成本一起丟掉，使用者只看到一行 traceback。

    ⚠ 四處各自修等於留四個會再漂移的落點；取文字的規則收斂到本函式，
    形狀是否合契約由 `validate_narrative_contract` 主迴圈**發一次**警告。
    """
    if isinstance(point, dict):
        return str(point.get("text") or "")
    if isinstance(point, str):
        return point
    return ""


def validate_narrative_contract(
    narratives: dict[str, Any],
    capacity: dict[str, dict[str, int]] | None = None,
    subjects: dict[str, list[str]] | None = None,
) -> list[str]:
    """驗三件套契約，回傳警告清單（合規＝空）。

    `capacity`＝各報表要點區的實際版面容量（`build_ppt.narrative_capacity()`）。
    給了就以它為準，沒給退回全域上限——撰寫（prompt）、驗證（本函式）與裁切
    （`_trim_blocks`）三處吃同一份數字，才不會出現「說 55 字、版面只放得下 26」。

    ⚠ 只警告、不 raise、不截斷：narrative 是報表頁與 PPT 的共同資料來源，
    截了就毀；截斷是 PPT 消費端 fallback 的職責。舊格式（只有 text）要能
    過渡期照跑，故缺 headline／points 也只標記。警告進 summary 的
    contract_warnings，前端任務進度看得到——違規不得靜默。
    """
    capacity = capacity or {}
    # subjects＝各變體可具名對象（Q14／RPT-012）：判讀要指名，不得只講泛稱。
    subjects = subjects or {}
    warnings: list[str] = []
    for report_key, report in (narratives.get("reports") or {}).items():
        base_limits = capacity.get(report_key) or {}
        for variant_key, entry in (report.get("variants") or {}).items():
            where = f"{report_key}:{variant_key}"
            # 🔴 I-1（2026-08-03）：容量**逐 variant**取，取不到才退回 report_key 層。
            # 同一報表的 L4（扁圖、底部寬橫幅）與 L5（一般圖、右側窄欄）版面差一倍以上，
            # 用同一個上限驗證的結果是：CLI 照寬的寫、驗證也照寬的驗，兩邊一致地錯，
            # 直到組版端才發現放不下——實機 #166 因此丟了 10 條要點。
            limits = capacity.get(where) or base_limits
            max_points = int(limits.get("max_points") or NARRATIVE_POINTS_MAX)
            # 版面容量比全域上限嚴時以版面為準；比全域寬時仍守全域（單一夾限出口）。
            max_chars = effective_max_chars(limits)
            min_points = min(NARRATIVE_POINTS_MIN, max_points)
            headline = entry.get("headline")
            points = entry.get("points")
            if not headline or not isinstance(points, list) or not points:
                warnings.append(
                    f"{where} 缺 headline/points（舊格式，PPT 端將以長文截斷 fallback）")
                continue
            if len(str(headline)) > NARRATIVE_HEADLINE_MAX:
                warnings.append(
                    f"{where} headline 超限（{len(str(headline))} 字 > "
                    f"{NARRATIVE_HEADLINE_MAX}）")
            if not (min_points <= len(points) <= max_points):
                warnings.append(
                    f"{where} points 條數 {len(points)} 不在 {min_points}–{max_points}"
                    f"（該頁版面容量）")
            body = str(entry.get("text") or "")
            for i, point in enumerate(points):
                # 🔴 2026-08-10 實機 job 284：CLI 回了字串陣列 `["文字", …]` 而非
                # 契約的物件陣列，`(point or {}).get` 對**非空字串**直接 AttributeError
                # ——878 秒的解讀連同 CLI 成本一起丟掉，使用者只看到一行 traceback。
                # ⚠ 本函式的職責是「把不合契約處列成 warnings」，不該用崩潰回應
                # 不合契約的輸入。形狀不符要**現形並繼續量**，不是中止。
                # ⚠ `(x or {}).get(...)` 只防 None 與空值，防不了型別——同型寫法要一起看。
                text = _point_text(point)
                if isinstance(point, str):
                    warnings.append(
                        f"{where} points[{i}] 是字串，契約要求 {{\"text\": …}} 物件"
                        "（已當作 text 續驗，PPT 端消費前需確認形狀）")
                elif not isinstance(point, dict):
                    warnings.append(
                        f"{where} points[{i}] 型別異常（{type(point).__name__}），無法取文字")
                if len(text) > max_chars:
                    warnings.append(
                        f"{where} points[{i}] 超限（{len(text)} 字 > {max_chars}）")
                # 鎖一·一條只講一個論點（🔴 2026-08-02 放寬形式）。
                #
                # 原規則是「不得含句號、逗號至多一個」。實測那讓 CLI **只能砍掉數字**：
                #   text（報表頁，過關）：「在 subclass 層級，A63B 達 47 件，是絕對主體。」
                #   points（PPT，太少）：「IPC大方向幾乎全落在運動訓練器材領域」（零數字）
                # 「A63B 達 47 件，是絕對主體」已用掉唯一的逗號，再加依據就違規——
                # 不是 CLI 偷懶，是規則逼它二選一。容量給到 8 條 × 54 字，只用了 4 條 × 19 字。
                #
                # ⚠ 放寬不等於取消：本意是「不要串接多個論點」，那由**上限**表達
                # （句號 ≤1、逗號 ≤2），不是禁止標點。
                if text.count("。") > 1:
                    warnings.append(f"{where} points[{i}] 句號過多（一條只講一個論點）")
                if text.count("，") > 2:
                    warnings.append(f"{where} points[{i}] 逗號過多（一條只講一個論點）")
                # 鎖二·數字一致：要點裡的數字必須在長文也出現，否則兩邊會漂移
                # （網頁報表頁讀 text、PPT 讀 points，讀者會看到互相對不上的數字）。
                for number in _NUMBER_PATTERN.findall(text):
                    if body and number not in body:
                        warnings.append(
                            f"{where} points[{i}] 的數字 {number} 未出現在長文——兩邊會對不上")
                # 鎖九·填充詞（濃縮五規則，2026-08-04）：刪掉後句意不變的詞
                # 不准出現——版面以字算租金，「進行維護」與「維護」是同一句話。
                for filler in NARRATIVE_FILLER_WORDS:
                    if filler in text:
                        warnings.append(
                            f"{where} points[{i}] 含填充詞「{filler}」——刪掉後句意不變，"
                            f"改寫更短的說法")
            # 鎖八·同頁數字不重複（濃縮五規則，2026-08-04）：意涵／後續複述
            # 現況的數字＝同一份資訊佔兩份版面。現況帶數字（鎖四），其他段講
            # 意義與行動，不再抄數字。
            # ⚠ M-3 修訂：①用 _STAT_NUMBER_PATTERN（分類代碼／專利號的數字片段
            # 不算統計數字）②每 point 先去重、只算**跨 point** 的重複——
            # 同段內重講是鎖一（一條只講一個論點）的範疇，不在此自指誤報。
            seen_numbers: dict[str, int] = {}
            for i, point in enumerate(points):
                text_i = _point_text(point)
                for number in dict.fromkeys(_STAT_NUMBER_PATTERN.findall(text_i)):
                    if number in seen_numbers and seen_numbers[number] != i:
                        warnings.append(
                            f"{where} points[{i}] 重複了 points[{seen_numbers[number]}] 的"
                            f"數字 {number}——同頁數字只寫一次，其他段講意義不抄數字")
                    else:
                        seen_numbers.setdefault(number, i)
            # 鎖四（2026-08-07 頁級版）·至少一條帶統計數字——標籤沒了，
            # 「數據依據」的要求不跟著消失；整頁零數字＝只剩形容詞。
            all_texts = [_point_text(p) for p in points]
            if points and not any(_NUMBER_PATTERN.search(t) for t in all_texts):
                warnings.append(
                    f"{where} 整頁沒有任何數字——要點必須有數據依據")
            # 鎖五（2026-08-07 無標籤版）·至少一條解釋成因（使用者：「一樣要能
            # 解釋現象背後的原因」）。啟發式找因果語彙；只描述現象不說為什麼＝
            # 停在「看到什麼數據」那一層。
            if points and not any(
                    any(m in t for m in NARRATIVE_CAUSAL_MARKERS) for t in all_texts):
                warnings.append(
                    f"{where} 沒有任何一條解釋成因——只描述現象，"
                    "要說出為什麼會這樣（背後的驅動、結構或機制）")
            # 鎖七·具名（Q14／RPT-012）：整頁至少點到一個具名對象。
            page_subjects = [n for n in (subjects.get(where) or []) if n]
            if points and page_subjects and not any(
                    any(name in t for name in page_subjects) for t in all_texts):
                warnings.append(
                    f"{where} 整頁沒有點到任何具名對象——判讀要指名"
                    f"（{'、'.join(page_subjects[:3])} 等），不能只說「主要申請人」")
            # 鎖六·該對照的要對照著講（CPC vs IPC）。
            counterpart = NARRATIVE_CONTRAST_WITH.get(report_key)
            if counterpart:
                joined = " ".join(_point_text(p) for p in points) + body
                if counterpart not in joined:
                    warnings.append(
                        f"{where} 未與 {counterpart} 對照——這一頁要講的是與 {counterpart} 的"
                        f"差異，不是把 {counterpart} 那段重講一次")
            # 鎖三·覆蓋：長文由要點逐條展開，段落數不該少於要點數。
            if body:
                paragraphs = [p for p in re.split(r"\n\s*\n|\n", body) if p.strip()]
                if len(paragraphs) < len(points):
                    warnings.append(
                        f"{where} 長文 {len(paragraphs)} 段 < 要點 {len(points)} 條"
                        f"——長文應由要點逐條展開，不得漏掉判讀")
    return warnings


# 預設 headless CLI 逾時（秒）；解讀多卡多變體可能久，給足時間但避免無限卡住。
DEFAULT_CLI_TIMEOUT_SECONDS = 1800.0

# 雙 CLI 指令對照（headless 非互動、只讀寫 narratives.json）。換 CLI 只改此表。
# 各值為「除提示字串外」的固定 argv 尾段；提示由 build_cli_command 插在二進位之後。
# 🔴 2026-08-09：`_CLI_SPECS`／`build_cli_command`／`CliResult`／`parse_cli_result`
# 已收斂到 `cli_gateway`（使用者定案「能整合的都要整合」）。七個 runner 各存一份
# 時，加 MCP 取證白名單要改七處——漏一處那條線就查不到資料庫，而且不會報錯。
#
# ⚠ 取證通道同時由 `Bash(uv run:*)`＋query_patents.py 改為 MCP 唯讀工具
# （RESEARCH_TOOLS）：工具清單即能力清單，不必靠提示詞約束「該查哪張表」。
import functools

from .cli_gateway import (  # 模組中段 re-export，維持既有 import 路徑
    _CLI_SPECS,  # noqa: F401  （re-export：既有呼叫端仍由本模組取用）
    RESEARCH_TOOLS,
    CliGatewayError,
    CliResult,
    parse_cli_result,
)
from .cli_gateway import build_cli_command as _gw_build_cli_command
from .cli_gateway import run_cli as _subprocess_cli_runner

# headless 解讀流程失敗（CLI 不存在、非零退出、產物缺失或版本不符）。
# ⚠ 2026-08-09 起是 CliGatewayError 的**別名**而非獨立類別：CLI 呼叫收斂到
# cli_gateway 後，「起不來／非零退出／輸出非 JSON」由它拋——別名讓既有
# `except NarrativeRunnerError` 仍然攔得到，不必逐處改捕捉型別。
NarrativeRunnerError = CliGatewayError


def _narrative_report_keys(narratives_path: Path) -> set[str]:
    """讀 narratives.json 現有的 report_key 集合；檔案不存在或壞掉回空集合。

    只用於「重產前後比對」，壞檔等同沒有既有解讀——這一步不該把讀檔問題
    誤報成資料遺失。
    """
    if not narratives_path.exists():
        return set()
    try:
        data = json.loads(narratives_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    reports = data.get("reports")
    return set(reports) if isinstance(reports, dict) else set()


# cli_runner 介面：收 (argv, timeout) 回 CliResult；預設 subprocess 實作，測試可注入 fake。
CliRunner = Callable[[Sequence[str], float], CliResult]


def materialize_report_version(version: str) -> Path:
    """把 DB 內的報表版本落地到本機暫存目錄，回傳該目錄（跨容器讀那一段）。

    延遲 import：worker 匯入本模組時不必拉進 DB 層；且測試可直接 patch 本函式。
    落點放 var/report_cache，與 ai_payloads 同層（皆為本機暫存，不進版控）。
    """
    from backend.app.db.report_artifact_store import materialize_version

    return materialize_version(version, PROJECT_ROOT / "var" / "report_cache")


def resolve_run_dir(based_on_version: str | None, *, root: Path | None = None) -> Path:
    """解析要解讀的報表版本目錄；本機沒有時從 DB 落地（2026-07-27 待辦 9d）。

    based_on_version 給定時＝full_report_latest 下該版本子目錄（目錄名即版本，對齊 PS1
    Split-Path -Leaf 規則）；未給時取 full_report_latest 下最新的 report_trial_ 目錄。

    ⚠ **本機優先、DB 補位**：報表由容器內 worker 產出、只存在 report_artifacts 表，
    而本函式在使用者本機 Companion 執行——只找本機必然落空（實機 job 95 即此，
    解讀從來沒成功過）。故本機目錄不存在時改從 DB 落地整包再讀。
    本機開發（backend 與報表同一台）時目錄真的存在，走原路徑、不繞 DB。
    """
    base = root if root is not None else FULL_REPORT_LATEST
    if based_on_version:
        run_dir = base / based_on_version
        if not (run_dir / "report_data.json").exists():
            # 本機沒有＝報表在容器裡產的，改從 report_artifacts 落地。
            try:
                return materialize_report_version(based_on_version)
            except Exception as exc:  # noqa: BLE001 - 兩邊都沒有才是真的找不到
                raise NarrativeRunnerError(
                    f"找不到報表版本 {based_on_version}：本機 {run_dir} 無 report_data.json，"
                    f"DB report_artifacts 也取不到（{type(exc).__name__}: {exc}）"
                ) from exc
        return run_dir
    # 未指定版本：本機取最新；本機一份都沒有時（容器產的報表）改問 DB 要最新版本。
    candidates = sorted(
        (p for p in base.glob("report_trial_*") if (p / "report_data.json").exists()),
        key=lambda p: p.name,
    )
    if candidates:
        return candidates[-1]
    try:
        from backend.app.db.report_artifact_store import list_versions

        versions = list_versions()   # 已依 version DESC 排序，且只含有 report_data.json 者
        if versions:
            return materialize_report_version(versions[0]["version"])
    except Exception as exc:  # noqa: BLE001 - DB 取不到就落到下面的統一錯誤
        raise NarrativeRunnerError(
            f"找不到可解讀的報表版本：本機 {base} 無 report_trial_ 目錄，"
            f"DB report_artifacts 也取不到（{type(exc).__name__}: {exc}）"
        ) from exc
    raise NarrativeRunnerError(
        f"找不到可解讀的報表版本：本機 {base} 與 DB report_artifacts 都沒有已產製的報表。"
        "請先在「報表種類」頁按「產製選定報表」。"
    )


def load_narrative_subjects(run_dir: Path | None = None) -> dict[str, list[str]]:
    """各變體「可具名對象」清單（Q14／RPT-012 具名鎖的比對集）。

    來源＝report_data.json 的 chart_rows：取每列的申請人／主題／專利號等
    名稱型欄位。⚠ 只給**已在該頁資料裡**的名字，AI 提到別頁的公司不算命中——
    具名要具體且該頁查得到。取不到就回空（該頁跳過鎖，不誤報）。
    """
    if run_dir is None:
        return {}
    try:
        data = json.loads((run_dir / "report_data.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    name_keys = ("applicant_display_name", "label", "topic_label",
                 "current_assignee_display_name", "patent_number")
    out: dict[str, list[str]] = {}
    for section in data.get("sections") or []:
        report_key = str(section.get("report_key") or "")
        rows = (data.get("chart_rows") or {}).get(report_key) or []
        names = [str(r[k]).strip() for r in rows if isinstance(r, dict)
                 for k in name_keys if r.get(k) and str(r[k]).strip()]
        if not names:
            continue
        seen = list(dict.fromkeys(names))
        for variant in section.get("variants") or [{}]:
            out[f"{report_key}:{variant.get('variant_key', 'default')}"] = seen
    return out


def load_narrative_capacity(run_dir: Path | None = None) -> dict[str, dict[str, int]]:
    """各報表要點區的實際版面容量；取不到回空 dict。

    ⚠ 延後匯入 `ai_report_ppt_runner`：那支模組在 import 期就從本模組取東西，
    寫成模組層 import 會循環。
    ⚠ 失敗只降級不中斷：容量是「讓 CLI 寫得剛好」的優化，拿不到就退回全域上限，
    不該讓整個解讀任務產不出來。
    """
    try:
        from .ai_report_ppt_runner import _load_builder

        builder = _load_builder()
        if run_dir is None:
            return builder.narrative_capacity()
        # 帶 run_dir 才算得出扁圖頁的真實容量（版型依圖的長寬比在執行時決定）。
        import json as _json

        manifest_path = run_dir / "artifact_manifest.json"
        manifest = _json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        theme = builder.Theme.load()
        charts = builder.ChartIndex(run_dir, run_dir / ".cache", manifest, theme)
        # K-1：帶 report_data 讓動態插頁也拿到容量（見 build_ppt.narrative_capacity）。
        rd_path = run_dir / "report_data.json"
        report_data = _json.loads(rd_path.read_text(encoding="utf-8")) if rd_path.exists() else None
        return builder.narrative_capacity(theme, charts, report_data)
    except Exception:
        return {}


def build_prompt(
    run_dir: Path,
    version: str,
    *,
    skill_path: Path | None = None,
    instruction: str | None = None,
    report_keys: list[str] | None = None,
) -> str:
    """組 headless 解讀任務提示：只指示 CLI 讀 skill 全文並遵守，不複製規格內文。

    instruction＝使用者在報表旁「重產解讀」時輸入的附加需求（可為空）。
    2026-07-27 前 payload 有存但這裡零消費，使用者打了完全沒作用——
    比失敗更誤導（看似成功卻沒照要求做），故納入 prompt。
    附加需求**不得凌駕輸出契約**：仍只寫 narratives.json、維持 v3 三件套兩層結構。

    report_keys＝只重產這幾張報表的解讀（2026-07-29 使用者定案「報表要能各自
    獨立重產解釋」）。不給＝整份重跑（原行為）。
    ⚠ 限定範圍時**必須明確要求保留其他報表的既有解讀**——否則 CLI 會寫出只含
    這幾張的 narratives.json，其餘解讀全部消失，且檔案結構合法、驗不出來
    （靜默資料損失）。
    """
    skill = skill_path if skill_path is not None else SKILL_PATH
    narratives_path = run_dir / "narratives.json"
    # 逐報表的真實版面容量：大圖頁的右側直欄與無圖表格頁的底部橫幅差很多，
    # 只給一組全域上限會讓 CLI 盲寫、事後被裁掉。取不到就不寫這段（退回全域上限）。
    capacity = load_narrative_capacity(run_dir)
    capacity_note = ""
    if capacity:
        # M-2：告知值走 effective_max_chars——與 validator 同一個夾限出口，
        # 否則寬幅頁（容量 97）照原值告知，CLI 守了 prompt 仍被驗紅。
        listed = "\n".join(
            f"   - {key}：{limits['max_points']} 條以內、每條 ≤{effective_max_chars(limits)} 字"
            for key, limits in sorted(capacity.items())
        )
        capacity_note = (
            "   ⚠ 下列項目的要點區版面容量**小於**上述全域上限，以這裡的為準：\n"
            f"{listed}\n"
            "   ⚠ **帶冒號的 `報表:變體` 是「該變體專屬」的容量**，"
            "對應 `reports[報表].variants[變體]`。\n"
            "   同一報表若同時出現 `ipc_main_distribution` 與 "
            "`ipc_main_distribution:L5` 兩行，\n"
            "   **寫 L5 那個變體時一律以 `:L5` 那行為準**；"
            "不帶冒號的只是沒有專屬值時的預設。\n"
            "   ⚠ 為什麼要分開：同一報表的不同變體排在**不同版面**——L4 扁圖走底部雙欄橫幅"
            "（每行字多），\n"
            "   L5 走右側窄欄（每行字少約一半）。照寬的那組寫，窄的那頁會整條被丟掉"
            "（2026-08-03 實機丟了 10 條）。\n"
            "   （未列出的項目用全域上限。超出容量不會被排版成好看的樣子，整條會被丟掉。）\n"
            "   ⚠ 容量是**上限不是目標**：沒話講就少寫一條，不要為湊條數或湊字數灌水；\n"
            "   但也不得敷衍——該報表看得出的判讀不能因為想寫短而省略。\n"
        )
    scope = ""
    if report_keys:
        listed = "、".join(str(k) for k in report_keys)
        scope = (
            f"\n\n**本次只重產這幾張報表的解讀**：{listed}\n"
            f"   ⚠ {narratives_path} 內**其他報表的既有解讀必須原樣保留**：\n"
            "   先讀入現有檔案，只替換上列 report_key 的內容，其餘鍵值不得刪除或改寫。\n"
            "   （寫成只含本次範圍的檔案＝其他解讀全部遺失，且檔案結構仍合法、驗不出來。）"
        )
    extra = ""
    if instruction and instruction.strip():
        extra = (
            "\n\n6. 使用者額外需求（在遵守上述契約的前提下盡量滿足）：\n"
            f"   「{instruction.strip()}」\n"
            "   注意：此需求**不得**牴觸或覆蓋第 4、5 點的輸出契約：仍只寫 narratives.json\n"
            "   這一個檔案、維持 v2 兩層結構、不得改動其他檔案。若需求與契約衝突，\n"
            "   以契約為準，並在對應解讀文字中說明無法滿足的部分。"
        )
    return (
        "任務：產製專利報表解讀 narratives.json（系統派工、非互動、一次性，v3）。\n\n"
        f"1. 先完整閱讀 {skill} 全文，逐字遵守其中的解讀 Prompt 模板、各報表解讀重點、\n"
        "   口徑守則、痛點待調查固定文案（含 {x_median} 實際值代入）與輸出契約 v3。\n"
        f"2. 目標報表目錄：{run_dir}\n"
        "3. 讀取該目錄 report_data.json：sections 鍵列出全部卡片與各卡片內的 variants（含\n"
        "   variant_key）。對每張卡片的每個變體成對讀取該變體的數據 rows 與 SVG 圖檔，\n"
        "   每一變體產一組三件套（headline＋points＋text）。\n"
        f"4. 輸出唯一檔案：{narratives_path}\n"
        f"   形狀（v3 引擎讀取契約）：based_on_version 必須等於 \"{version}\"；reports 以\n"
        "   report_key→variants→variant_key→\n"
        "   {points,headline,text,ai_model,prompt_version,generated_at} 兩層結構。\n"
        f"   prompt_version 一律寫 \"{PROMPT_VERSION}\"（不要自己編版本字串）。\n"
        "   ⚠ **依此順序逐欄寫，不要跳著寫**——順序就是撰寫程序：\n"
        f"   points＝**{NARRATIVE_POINTS_MIN}–{NARRATIVE_POINTS_MAX} 條自由要點**"
        "（2026-08-07 起**不再加「現況／意涵／後續」標籤**，不要輸出 label 欄）。\n"
        "   🔴 **要具名**（Q14／RPT-012）：判讀要點名具體對象（申請人全名、\n"
        "   主題名、專利號），不得整頁只說「主要申請人」「部分廠商」這類泛稱。\n"
        "   🔴 **格式完全不固定（2026-08-07 使用者定案）**：條數、句式、順序、每條講\n"
        "   什麼，都由**這一頁的內容**決定，不要每頁套同一個模子（一條數字、一條解釋、\n"
        "   一條建議的公式化寫法＝失格）。固定的只有兩個**內容**要求：\n"
        "     · 頁內要有**數據依據**——至少一處帶圖上讀得到的數字；\n"
        "     · 要**講得出原因和結論**——為什麼會這樣（背後的驅動、結構或機制），\n"
        "       以及由此得出什麼判斷；不是換句話再描述一次現象。\n"
        "   下一步建議**有可執行內容才寫**；寫的話對象限專利文件層級\n"
        "   （權利範圍、細分類分布、公開情形），不寫商業行動，句型不拘。\n"
        "   ⚠ 同一件事的多個面向**合併在同一條**講完——目標是濃縮，不是少講。\n"
        "   ⚠ 每段 text 不得超過下方列出的該頁字數上限，且**至多 3 句**\n"
        "   ——句子再多就變字牆，讀者一眼抓不到重點。\n"
        "   ⚠ 公司名第一次寫全名，之後用短稱（「〈城市〉〈字號〉〈業別〉公司」→「〈字號〉」）；\n"
        "   不要用「遙遙領先」「僅」「多為」這類程度副詞，直接給數字。\n"
        "   🔴 **敘述口吻一律客觀（2026-08-04 使用者定案）**：\n"
        "   - 只說專利資料能證明的：件數、家數、分類分布、年度變化、權利歸屬。\n"
        "     市占、營收、市場需求、產品競爭力**一律不得推論**。\n"
        "   - 禁用詞：龍頭、戰場、玩家、卡位、壟斷、押注、主攻、聚焦布局、\n"
        "     競爭激烈、值得追、遙遙領先。高參與寫「多方投入」。\n"
        "   - 不作技術生命週期斷言（成熟、衰退、萌芽）：用申請件數與申請人數的\n"
        "     年度變化描述，例如「2022年後參與申請人數下降，布局由多方投入\n"
        "     轉向少數申請人持續布局」。\n"
        "   - IPC/CPC 頁只能說分類分布與集中度，不得寫成技術優劣結論。\n"
        "   - 推論動詞用「顯示／反映／集中於」，不用斷言或擬人化。\n"
        "   CPC 分類那一頁要講的是**與 IPC 的差異**（哪些分類 IPC 沒有、代表什麼），\n"
        "   不是把 IPC 那段重講一次。\n"
        "   🔴 **濃縮五規則（2026-08-04 使用者定案；不是字數門檻，是寫法）**：\n"
        "   ① 刪句測試：每句寫完自問「刪掉這句讀者少知道什麼」——答不出來就刪。\n"
        "   ② 同頁數字不重複：數字只在「現況」出現一次，意涵／後續講意義與行動，\n"
        "      不抄數字（違者記 warning）。\n"
        "   ③ 圖面資訊不轉述：圖上一眼看得到的（排名順序、顏色分級、軸標）不寫成字，\n"
        "      文字只寫圖看不出來的（跨年比較、佔比、集中度、缺口）。\n"
        "   ④ 填充詞禁用：進行、相關、方面、值得注意的是、此外、同時、整體而言\n"
        "      ——刪掉後句意不變的詞一律不寫（違者記 warning）。\n"
        "   ⑤ 動詞收斂：「呈現增加趨勢」寫「增加」，「具有較高集中度」寫「集中」；\n"
        "      一個動作一個動詞，不套名詞化外殼。\n"
        f"   headline＝**從上列要點挑最重要一條濃縮**至 ≤{NARRATIVE_HEADLINE_MAX} 字，\n"
        "   不是另想一句；\n"
        "   text＝由上列要點**逐條展開**成連貫長文（段落數不少於要點條數，\n"
        "   要點出現過的數字必須也出現在長文）。\n"
        + capacity_note
        + "5. 只准寫 narratives.json 這一個檔案；不得改動目錄內其他檔案、不得執行 shell 指令；\n"
        "   寫完即結束，不輸出多餘說明。"
        + scope
        + extra
    )


# 敘述線＝取證等級：要讀報表檔、寫 narratives.json，並經 MCP 唯讀工具查資料庫。
build_cli_command = functools.partial(_gw_build_cli_command, tools=RESEARCH_TOOLS)


def stamp_narrative_metadata(narratives: dict) -> None:
    """把每個 variant 的 prompt_version 蓋成現行值（M-1，2026-08-04）。

    🔴 CLI 會照 skill 契約範例抄 metadata——範例一度寫死 v7，narratives 檔
    從 v8 時代起版本欄一直失真。metadata 的事實來源是 runner，不是 CLI 的抄寫；
    在驗證與上傳**之前**回填，檔案與 DB 都拿到正確值。ai_model 由 CLI 寫
    （它才知道實際跑的模型），不在此覆蓋。
    """
    for report in (narratives.get("reports") or {}).values():
        for entry in (report.get("variants") or {}).values():
            if isinstance(entry, dict):
                entry["prompt_version"] = PROMPT_VERSION


def run_narrative(
    based_on_version: str | None,
    *,
    cli_kind: str = "claude",
    model: str | None = None,
    cli_runner: CliRunner | None = None,
    refresh_index: Callable[[Path], dict[str, Any]] | None = None,
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
    root: Path | None = None,
    skill_path: Path | None = None,
    instruction: str | None = None,
    # 只重產這幾張報表的解讀（2026-07-29）；不給＝整份重跑。
    report_keys: list[str] | None = None,
    resolve_run_dir: Callable[..., Path] | None = None,
    upload_run_dir: Callable[[Path], int] | None = None,
) -> dict[str, Any]:
    """把報表解讀整條系統化：組提示 → 呼叫 headless CLI → 驗收產物 → refresh-index。

    cli_runner 未注入時用預設 subprocess 實作（真跑 CLI，並在二進位缺失時 raise 清楚錯誤）；
    測試／handler 可注入 fake runner 避免真跑。progress(stage, percent) 供 CLI 執行期間緩進。
    model 由任務 payload 帶下來選具體模型（如 claude-opus-4-8），未給則用 CLI 預設。
    回傳：based_on_version、narratives 檔路徑、覆蓋變體數（narrated／variants_total）與缺漏。
    """
    runner = cli_runner if cli_runner is not None else _subprocess_cli_runner
    # 預設 refresh_index 延遲 import：避免 worker 匯入即拉進整個報表引擎相依。
    if refresh_index is None:
        from backend.app.reports.chart_runner import refresh_index as _refresh
        refresh_index = _refresh

    # 參數同名遮蔽了模組層函式，故用 globals() 取預設實作（測試可注入 fake）。
    resolver = resolve_run_dir or globals()["resolve_run_dir"]
    run_dir = resolver(based_on_version, root=root)
    version = run_dir.name
    narratives_path = run_dir / "narratives.json"
    # 重產前的 report_key 快照：限定範圍重產時，範圍外的解讀一張都不許少（見下方檢查）。
    keys_before = _narrative_report_keys(narratives_path)
    if progress is not None:
        progress("cli_running", 30)

    prompt = build_prompt(run_dir, version, skill_path=skill_path,
                          instruction=instruction, report_keys=report_keys)
    argv = build_cli_command(cli_kind, prompt, model=model)
    # 取證稽核（2026-08-10）：解讀線有 RESEARCH_TOOLS 可查 DB，但原本沒有任何紀錄
    # ——它可以完全不查就寫出簡報每一頁的要點，而我們無從得知。落檔路徑由環境變數
    # 傳給 MCP server 子行程，任務結束讀回。⚠ 工具與 report_planning_runner 共用
    # 同一份實作（`report_research`），不複製第二份稽核格式。
    from backend.app.mcp_server.report_research import query_audit_file, read_query_audit

    with query_audit_file() as audit_path:
        cli_result = runner(argv, timeout_seconds)
        query_audit = read_query_audit(audit_path)
    parse_cli_result(cli_result)  # 退出碼／JSON 檢查；不硬用其內容，narratives.json 才是產物
    if progress is not None:
        progress("cli_running", 85)

    if not narratives_path.exists():
        raise NarrativeRunnerError(f"CLI 正常結束但未產出 {narratives_path}")
    narratives = json.loads(narratives_path.read_text(encoding="utf-8"))
    got_version = narratives.get("based_on_version")
    if got_version != version:
        raise NarrativeRunnerError(
            f"narratives.json based_on_version={got_version!r} 與目錄版本 {version!r} 不符（解讀過期）"
        )

    # ⚠ 限定範圍重產：範圍外的既有解讀一張都不許消失（2026-07-31）。
    # CLI 若沒讀入既有檔、直接寫出只含本次範圍的檔案，結構完全合法、版本相符、
    # 契約驗證也會過（那一張本身合規），接著整包 upsert 覆蓋 report_artifacts →
    # 其餘十幾張解讀消失而 job 顯示 succeeded。與同檔「上傳失敗不可吞」同一條原則：
    # 靜默資料損失比 failed 更難判斷，故在 refresh_index 與上傳**之前**擋下。
    if report_keys:
        dropped = keys_before - _narrative_report_keys(narratives_path) - set(report_keys)
        if dropped:
            raise NarrativeRunnerError(
                f"限定重產 {sorted(report_keys)} 後，範圍外的既有解讀消失："
                f"{sorted(dropped)}。CLI 未保留原檔內容，已中止上傳以免覆蓋 "
                "report_artifacts（重跑前請確認 narratives.json 仍在 run_dir）。"
            )

    # M-1：metadata 蓋章（prompt_version 以 runner 為準）後回寫檔案，
    # 再進驗證與上傳——refresh_index 與 DB 拿到的都是蓋章後的版本。
    stamp_narrative_metadata(narratives)
    narratives_path.write_text(
        json.dumps(narratives, ensure_ascii=False, indent=2), encoding="utf-8")

    # 三件套契約驗證（v4）：只警告不 raise——舊格式要能過渡、超限交 PPT 端 fallback；
    # 警告進 summary 讓前端任務進度看得到，違規不得靜默。
    contract_warnings = validate_narrative_contract(
        narratives, load_narrative_capacity(run_dir),
        subjects=load_narrative_subjects(run_dir))

    # 確定性程式重渲染 index（嵌入解讀）；CLI 不碰 index.html。
    refresh = refresh_index(run_dir)

    # 🔴 A1（2026-08-06）：漏產變體必須現形為警告。
    #
    # ⚠ 原缺口是**兩層都不守**：`refresh_index` 早就算出 `pending`（哪些變體沒解讀），
    # `run_narrative` 也把它放進 summary，但沒有任何地方把它當違規；而
    # `validate_narrative_contract` 只走訪 narratives 裡**已存在**的變體，
    # 結構上不可能察覺「少了一個」。結果就是 16/19 卻回報 0 警告。
    #
    # ⚠ 只警告不 raise：漏產的變體在報表頁會顯示「⏳ 待解讀」——使用者看得到，
    # 不是靜默資料損失（與同檔「限定重產範圍外解讀消失」那條 raise 的性質不同：
    # 那個是既有內容被覆蓋消失，這個是本來就沒產出）。已產的部分仍可用，
    # 整批 fail 反而讓使用者連能用的都拿不到。
    pending_variants = list(refresh.get("pending") or [])
    if pending_variants:
        contract_warnings.append(
            f"漏產解讀變體 {len(pending_variants)} 個"
            f"（覆蓋 {refresh.get('narrated')}/{refresh.get('variants_total')}）："
            f"{'、'.join(pending_variants)}"
        )

    # ⚠ 把 narratives.json 上傳回 report_artifacts（2026-07-27 待辦 9d 的「寫」那一段）。
    # CLI 寫的是**本機檔案系統**，但 backend 從 DB 讀（report_artifact_store.read_file）——
    # 不傳回去就永遠讀不到，解讀區維持空白。upload_run_dir 會整包 upsert（同版本同名
    # 檔覆蓋），故也順帶把 refresh_index 重渲染的 index.html 一起更新。
    # 上傳失敗不可吞：backend 讀的是 DB report_artifacts。若 narratives.json 沒進 DB，
    # 使用者看到的是「job succeeded 但完全沒有解讀」，比 failed 更難判斷。
    uploader = upload_run_dir
    if uploader is None:
        from backend.app.db.report_artifact_store import upload_run_dir as _upload
        uploader = _upload
    uploaded = 0
    try:
        uploaded = uploader(run_dir)
    except Exception as exc:  # noqa: BLE001 - 對使用者必須 fail loud
        raise NarrativeRunnerError(
            f"narratives.json 已產生但上傳 report_artifacts 失敗："
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if uploaded <= 0:
        raise NarrativeRunnerError(
            "narratives.json 已產生但沒有任何 report artifact 被上傳；"
            "backend 將讀不到解讀。"
        )

    return {
        "artifacts_uploaded": uploaded,
        "based_on_version": version,
        "run_dir": str(run_dir),
        "narratives_path": str(narratives_path),
        "cli_kind": cli_kind,
        "prompt_version": PROMPT_VERSION,
        "narrated": refresh.get("narrated"),
        "variants_total": refresh.get("variants_total"),
        "pending": refresh.get("pending", []),
        "narratives_expired": refresh.get("narratives_expired", False),
        "contract_warnings": contract_warnings,
        # 取證紀錄：這次解讀查了幾次、用哪些工具、有沒有失敗。
        # ⚠ 空清單＝**完全沒查證**，只用了 report_data 的聚合數字寫要點。
        # 目前只回報不阻擋（解讀是逐報表的，部分報表確實可能不需要額外查證）；
        # 是否升級成硬性要求，等實跑數據看清楚分布再定。
        "query_audit": query_audit,
        "query_count": len(query_audit),
    }
