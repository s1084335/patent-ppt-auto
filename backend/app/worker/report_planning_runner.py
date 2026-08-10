"""goal-driven 報告規劃 runner（P2 第 4 節）。

把「最大目標＋使用者選定的圖表＋唯讀查證工具」交給 headless CLI，取回
`ReportStrategy`／`SlidePlan`／`EvidenceManifest`，驗證後回傳候選。

🔴 分工（design.md 第 4 點）：
- CLI **只產結構化候選**——沒有任何 DB／artifact 寫入工具；要補證據一律經
  report-research 唯讀 MCP。
- runner 是**唯一保存者**：驗證未過就不落任何 artifact（失敗的規劃不得留痕
  被誤當成可交付）。
- 形狀規則一律走 `planning_contracts`（唯一定義處），本模組不另寫一套。
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from backend.app.mcp_server.report_research import (
    TOOL_NAMES,
    query_audit_file,
    read_query_audit,
)
from backend.app.reports.planning_contracts import (
    APPROVED_LAYOUT_PRESETS,
    validate_evidence,
    evidence_value_warnings,
    slide_plan_capacity_warnings,
    validate_report_brief,
    validate_research_effort,
    validate_slide_plan,
)

# v2（2026-08-10）：頁序改版——取消 exec_summary（發現併進 direction 的「依據」欄）、
# 主題頁正文改用圖（技術＝主題演進圖／功效＝機會四象限）、表格降到附錄、
# 正文不放專利號、KP 依 B-6 軌跡判準分三頁、有圖頁與無圖頁改為兩種寫法。
# ⚠ 版本要跟著 prompt 內容走：`analysis_outputs` 靠它分辨產出是哪一版規則下的成果，
# 不 bump 就無法回答「這份簡報是照舊規則還是新規則產的」。
PROMPT_VERSION = "report_planning_v2"
DEFAULT_CLI_TIMEOUT_SECONDS = 900.0


class ReportPlanningError(RuntimeError):
    """規劃失敗（brief 不合格、CLI 產出不合契約、驗證未過）。"""


def _narrative_digest(narratives: dict[str, Any]) -> str:
    """把既有逐報表解讀壓成 prompt 區塊；沒有解讀時回空字串。

    ⚠ 只取 headline 與要點文字，不帶 evidence 結構——規劃端要的是**素材內容**，
    不是解讀的內部形狀。帶太多會擠掉選圖數據的額度。
    """
    blocks: list[str] = []
    for report_key, entry in (narratives.get("reports") or {}).items():
        for variant_key, variant in (entry.get("variants") or {}).items():
            headline = str(variant.get("headline") or "").strip()
            points = [str(p.get("text") or "").strip()
                      for p in (variant.get("points") or []) if p.get("text")]
            if not headline and not points:
                continue
            body = "\n".join(f"  - {p}" for p in points)
            blocks.append(f"### {report_key}:{variant_key}｜{headline}\n{body}")
    return "\n".join(blocks)


def build_prompt(brief: dict[str, Any]) -> str:
    """組規劃提示：目標／受眾／頁數預算＋每張選圖的圖與數據＋可用查證工具。"""
    charts = brief.get("selected_charts") or []
    chart_blocks = []
    for bundle in charts:
        rows = json.dumps(bundle.get("data_rows") or [], ensure_ascii=False)[:1500]
        chart_blocks.append(
            f"### {bundle['chart_identity']}｜{bundle.get('title') or ''}\n"
            f"- 圖檔：{bundle.get('image_path')}\n"
            f"- 母體：{bundle.get('population_note') or '（未標）'}\n"
            f"- 數據列：{rows}"
        )
    tools = "、".join(TOOL_NAMES)
    presets = "、".join(sorted(APPROVED_LAYOUT_PRESETS))
    existing = _narrative_digest(brief.get("narratives") or {})
    return (
        "任務：依最大目標規劃一份專利分析簡報（系統派工、非互動、一次性）。\n\n"
        f"## 最大目標\n{brief['north_star_goal']}\n"
        + ("（使用者未指定目標，以下為系統預設策略；品質標準不因此降低）\n\n"
           if brief.get("used_default_goal") else "\n")
        + f"## 受眾\n{brief.get('audience') or '未指定'}\n\n"
        + "## 編排方向（方向不是模板；版型是備選庫，出哪幾頁由內容決定）\n"
        + "".join(f"- {d}\n" for d in (brief.get("directions") or [])) + "\n"
        + "## 品質標準（兩份參考範例的共同 DNA）\n"
          "- 結論先行：開頭就要有可行動的判斷，不是把結論留到最後\n"
          "- 每頁要有**具名發現**與依據（誰、哪個主題、幾件），不是泛稱\n"
          "- Key Player 要有定位（全領域／單一技術深布局／利基／前案），不只排名\n"
          "- 收尾要有判讀說明：母體口徑、可觀測性偏差、資料限制\n"
          "\n"
          "## 🔴 有圖頁與無圖頁是**兩種寫法**（不是同一種寫法配不同字數）\n"
          "\n"
          "差別不在寫多寫少，而在**文字要回答的問題不同**。混用必然出事：\n"
          "有圖頁複述圖上的數字＝整頁只講了一件事；無圖頁按有圖頁的量寫＝一張白框。\n"
          "\n"
          "**有圖頁**（`chart_*`／`kp_quadrant`／`table_*`）——文字寫圖上**看不出來的**：\n"
          "- 要點 **3 條、每條約 40 字**（側欄實測容量 82 字，兩條就滿）\n"
          "- ❌ 不得寫排名、件數、佔比、誰最多、哪年高峰——圖上一眼看得到\n"
          "- ✅ 要寫機構層手段、路線異同、為何如此、意味什麼\n"
          "  例：「前段集中在單一機構路線；第三名走另一條路徑，與前兩家不衝突」\n"
          "\n"
          "**無圖頁**（`walls_gaps`／`reading_guide`／`direction`／\n"
          "`kp_deepdive`／`kp_cards`／`kp_compare`）：\n"
          "🔴 **整頁只有你寫的敘述，內容量要拉到有圖頁的兩倍以上**——實測系統產出\n"
          "只有範例的三分之一（118 字 vs 範例 357 字），成品就是一張半空的框。\n"
          "\n"
          "- 每頁**至少 6 個內容單元**（範例是 15–25 個），不是 3–4 條要點\n"
          "- 每個單元＝「短標籤＋一句內容」，格式示例（**內容依你這批資料寫，不要照抄**）：\n"
          "  `<某某期> 2011–2021｜<該期的代表機構手段>，單點改良為主`\n"
          "  `2022 首爆 15 件 13 族｜真成長，非同族灌水`\n"
          "- 分段組織：發現用**編號卡**、建議用**三欄**、判讀用**偏差四條＋結語三條**\n"
          "- ⚠ 寫不滿代表分析還沒做完，不是版面問題——回去查證再寫\n\n"
        f"## 頁數上限\n{brief['page_budget']} 頁（超過即不合格）\n\n"
        "## 🔴 禁用與去重（2026-08-10 使用者定案）\n"
        "- **不要用 `comparison`**：兩張圖擠一頁各講一半，讀者兩張都看不清楚。\n"
        "  一頁一張圖，講透它。\n"
        "- **結論頁與建議頁只留一個**：`exec_summary` 與 `direction` 都是總結，\n"
        "  不要兩頁講同一件事。建議選 `direction`（它同時給判斷與行動），\n"
        "  把三個具名發現寫進它的「依據」欄。\n\n"
        "## 🔴 必出頁（不是「依內容決定」，沒有就是不合格）\n"
        "1. **研發切入建議**：使用者的最大目標就是要這個。只診斷不建議＝沒回答問題。\n"
        "   用 `direction` 版型，每條建議要有「方向｜依據（引用自己報告的數字）｜行動」。\n"
        "2. **Key Player 深入 3 頁**：一張象限圖不夠。🔴 **依有無技術軌跡分組**——\n"
        "   軌跡判準＝該申請人的專利落在 **≥3 個不同申請年**（不是最晚年減最早年 ≥3）。\n"
        "   - **KP①** 領導陣營（件數最前、有軌跡者）：`kp_deepdive`，逐年演進主線＋\n"
        "     三數字卡＋FTO 威脅框。🔴 有共同申請關係時**必須拆開「共同 N 件」與\n"
        "     「各自獨立 N 件」**——合計數字會把兩家講成一家，也看不出誰主導。\n"
        "   - **KP②** 對照組（件數相當但路線不同的兩家）：`kp_compare` 左右兩欄比較。\n"
        "   - **KP③** 利基新興（其餘各家，含無軌跡者）：`kp_cards`，**每家至少一句**。\n"
        "   ⚠ 人數不足可少開，但**不得把三組混成一頁**——混在一起就看不出\n"
        "   「誰在推進、誰在對峙、誰在試水」這三種角色。\n"
        "3. **判讀說明＋三條結語**：收尾要給判斷，不是只講限制。\n\n"
        "## 🔴 主題頁：正文用**圖**，表格只進附錄（2026-08-10 使用者定案）\n"
        "「同樣的東西做一次就好」——同一份主題資料不得在正文既畫圖又列表。\n"
        "- **技術主題**＝用**主題演進圖**（早期 vs 近期雙條）。文字補**機構層手段**，\n"
        "  不要複述件數與佔比。\n"
        "- **功效主題**＝用**機會四象限板**（密度 × 廣度，chip 標「件/家」）。\n"
        "  🔴 逐象限給**行動**（該追／必守迴避／需做痛點調查／單一玩家壟斷），\n"
        "  **空象限要明說「本案無此類」**，不是留白。\n"
        "- **附錄**才放完整表格（含代表專利號），供正文回查。\n"
        "- 🔴 **正文任何一頁都不放專利號**——尤其 KP 深入。一個號佔掉一行卻不給讀者\n"
        "  任何判斷；那一行拿來寫機構層內容價值高得多。\n"
        "- **申請趨勢**的敘述要**點名主題標籤**（「2024：<某主題> 5 件、<另一主題> 1 件」）\n"
        "  ——只寫件數等於沒把技術與時間扣起來。\n\n"
        "## 🔴 建議的頁序（結論先行；實際出哪幾頁仍依資料內容決定）\n"
        "1. 封面 2. **研發切入建議**（`direction`，具名發現寫進「依據」欄）\n"
        "3. 申請趨勢 4. 技術主題（演進圖）5. 功效主題（機會四象限）6. 國家布局\n"
        "7. 申請人排名 8. 申請人年度矩陣 9. KP 象限 10–12. KP 深入 ×3\n"
        "13. 判讀說明＋三條結語 14. 附錄主題全表\n\n"
        "⚠ 這幾頁**看起來像但不重複**，不要合併也不要省：\n"
        "- **申請人排名 vs KP 象限**：象限看定位（廣度×深度），排名看**數量與組成**\n"
        "  （單獨／共同／已轉讓三段）。少了排名就看不出量體。\n"
        "- **年度矩陣**：唯一能看出各家**進場時序與連續性**的圖，其他頁都是靜態切面。\n"
        "  ⚠ 這頁解讀可精簡——圖本身就是主角。\n\n"
        "## 🔴 技術內容必須下到機構層——這是給你查證權限的理由\n"
        "⚠ 下表示範的是**深度層次**，不是內容模板。主題名與公司名一律用佔位符，\n"
        "  你要填的是**這批資料查證後的實際內容**。\n"
        "🔴 特別注意：不得把示範句裡的機構名詞搬進產出——那不是你查到的，是格式說明。\n"
        "| ❌ 聚合數字就寫得出來（不合格） | ✅ 讀過專利內容才寫得出來 |\n"
        "|---|---|\n"
        "| 〈主題A〉6 件，〈甲公司〉獨占 83% | 〈主題A〉：**〈具體機構＋作動方式〉**，〈甲公司〉5 件同架構 |\n"
        "| 〈主題B〉10 件 top3 占 80% | 〈主題B〉：**〈零件如何配置達成該功能〉**，省掉〈原本需要的部件〉 |\n\n"
        "判準：把句子裡的主題名換掉後，剩下的描述**還能不能讓工程師畫出草圖**。\n"
        "能＝寫到機構層了；只剩「電機化」「傳動技術」這種類名＝還沒到。\n\n"
        "怎麼拿到：`applicant_strength_profile` 每列帶 `patent_ids`（該家全部專利 id）；\n"
        "`文獻備註` 是平台為每件寫的 2–3 句技術摘要，用 `query_database` 掃讀整批：\n"
        "`SELECT id, \"公開公告號\", \"發明名稱\", \"文獻備註\" FROM core_layer.patents "
        "WHERE id IN (...)`\n"
        "⚠ **多家申請人都要查**，不是只查第一名。\n\n"
        "## 使用者選定的圖表（**全部都要用到，且不得加入未選的圖**）\n"
        + "\n\n".join(chart_blocks) + "\n\n"
        + (("## 🔴 既有逐報表解讀（你的**素材**——要濃縮它，不是重寫）\n"
            "下列解讀已經過查證產出（讀過專利內容、具名到申請人與機構層）。\n"
            "你的要點應該**從這裡濃縮**，把散在各報表的發現收斂成整份簡報的敘事線。\n\n"
            "⚠ 濃縮**不等於刪細節**：具名對象（誰）、數字（幾件）、機構層描述\n"
            "（具體零件與作動方式，而非「電機化」這種類名）都必須留著——那正是深度所在，\n"
            "丟掉它們就只剩下從聚合數字也能寫出來的空話。\n"
            "要砍的是重複、鋪陳與轉折語，不是資訊。\n\n"
            "🔴 **用到解讀就要標來源**：該要點的 evidence 填\n"
            '`{"source": "narrative", "report_key": "<上列的 report_key>", '
            '"snapshot_id": "…"}`。\n'
            "⚠ 完全沒有任何一筆 narrative 來源＝判定為重寫而非濃縮，整份退回。\n\n"
            f"{existing}\n\n") if existing else "")
        + "## 🔴 必須實際查證（不查就不合格，會被退回）\n"
        "選圖數據只是**起點**，不是全部。你的職責是看著這些圖表與數據，判斷\n"
        "「要回答最大目標，還缺哪些事實」，然後**實際去查**，再依查到的內容撰寫。\n"
        "⚠ 完全沒有查詢紀錄的規劃會被判定不合格並退回——聚合數字寫不出具名發現，\n"
        "  也寫不出「這家在做什麼技術」這種必要的深度。\n"
        "查什麼、查幾次由你依內容判斷，規則不代你決定。\n\n"
        "## 可用的唯讀查證工具（要補證據就呼叫，不得自行編數字）\n"
        f"{tools}\n"
        f"（快照型查詢一律帶 snapshot_id=\"{brief['snapshot_id']}\"，收 typed 參數不吃 SQL；\n"
        " `query_database` 是唯一連資料庫的工具，收單句 SELECT／WITH——選圖數據\n"
        " 答不出來的問題才用它，並在 evidence 標 source=\"tool_query\"）\n\n"
        "## 可選版型（備選庫，不是必出清單——**依內容決定出哪幾種**）\n"
        f"{presets}\n\n"
        "## 輸出（只輸出這個 JSON）\n"
        '{"strategy": {"north_star_goal": "...", "storyline": ["..."]},\n'
        ' "slides": [{"slide_id": "s1", "layout_preset": "<上列其一>",\n'
        '   "purpose": "這頁要回答什麼", "chart_identities": ["..."],\n'
        '   "narrative": [{"text": "...", "evidence_ref": "e1"}]}],\n'
        ' "evidence": {"e1": {"source": "selected_chart|tool_query|narrative",\n'
        '   "chart_identity": "...", "report_key": "...", "snapshot_id": "..."}}}\n'
        '（`chart_identity` 給 selected_chart 用；`report_key` 給 narrative 用）\n\n'
        "規則：\n"
        "- **帶數字的敘述一律要有 evidence_ref**，數字只能來自選圖數據或查證工具。\n"
        "- **沒有依據的敘述就不要建 evidence 項目**，也不要用 `e_na` 之類的佔位鍵\n"
        "  ——那一條要點直接不填 evidence_ref 即可（不帶數字的敘述本來就不需要）。\n"
        "- `snapshot_id` 不用你填，系統會補；但 `source` 與對應的 `report_key`／\n"
        "  `chart_identity` **必須**填，那是追溯的唯一依據。\n"
        "- **直接引用的數字要在 evidence 填 `value`**（例：「〈主題〉11 件」→ `value: 11`）\n"
        "  ——程式會拿去對照引擎數據，對不上就整份退回。\n"
        "- **衍生數字**（比例、成長率、佔比等由多個數算出來的）填 `derived: true`，\n"
        "  這類不對照。⚠ 但不得用 derived 規避：能直接引用的就要填 value。\n"
        "- 只給版型意圖，**不要輸出座標、字級、顏色**——排版由程式決定。\n"
        "- 每一張選圖至少要出現在一頁；沒有內容支撐的版型就不要用。\n"
    )


def _parse_reply(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ReportPlanningError(f"CLI 回覆非 JSON：{text[:200]!r}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ReportPlanningError(f"CLI JSON 解析失敗：{exc}") from exc


def run_report_planning(
    brief: dict[str, Any],
    cli_runner: Callable[..., str],
    persister: Callable[[dict[str, Any]], Any] | None = None,
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    """brief → CLI → 驗證 → 候選 plan。驗證未過一律 raise，不落 artifact。"""
    def _tick(stage: str, pct: int) -> None:
        if progress:
            progress(stage, pct)

    brief_errors = validate_report_brief(brief)
    if brief_errors:
        raise ReportPlanningError(f"ReportBrief 不合格：{brief_errors}")

    _tick("CLI 規劃中", 30)
    # 取證稽核（A7）：指定落檔路徑，MCP server 子行程繼承後逐筆寫入，
    # 任務結束讀回。⚠ 這是唯一能證明「CLI 到底查了沒有」的系統內紀錄——
    # 在此之前只能事後翻開發機上的 CLI transcript。
    with query_audit_file() as audit_path:
        reply = _parse_reply(
            cli_runner(build_prompt(brief), timeout_seconds=timeout_seconds))
        query_audit = read_query_audit(audit_path)

    plan = {
        "plan_id": reply.get("plan_id") or f"plan-{brief['snapshot_id']}",
        "slides": reply.get("slides") or [],
    }
    evidence = reply.get("evidence") or {}
    # snapshot_id 是 runner 本來就知道的值——要求 CLI 每筆 evidence 重複填只是增加
    # 出錯機會（2026-08-10 實跑：CLI 漏填導致整份規劃被擋）。未填就補上；
    # ⚠ 填了但不符仍然擋——那才是「過期證據不得進 manifest」要防的情形。
    for entry in evidence.values():
        if isinstance(entry, dict) and not str(entry.get("snapshot_id") or "").strip():
            entry["snapshot_id"] = brief["snapshot_id"]
    identities = {b["chart_identity"] for b in brief["selected_charts"]}

    _tick("驗證規劃", 70)
    errors = validate_slide_plan(plan, identities, page_budget=brief.get("page_budget"))
    # 數字對照表：選圖 bundle 自帶的 data_rows 就是該報表的 rows，直接組成
    # report_data 形狀餵給驗證——不必多傳參數，也不必再讀一次檔。
    evidence_data = {"chart_rows": {b["report_key"]: b.get("data_rows") or []
                                    for b in brief["selected_charts"]}}
    errors += validate_evidence(plan, evidence, snapshot_id=brief["snapshot_id"],
                                has_narratives=bool(brief.get("narratives")))
    # 數字對照**只警告不阻擋**（2026-08-10 第三輪重新定位）：能對上 rows 的只有
    # 「單列欄位值」，加總／比例／查證來的都對不上——做成阻擋時四次實跑三次誤擋。
    value_warnings = evidence_value_warnings(evidence, evidence_data)
    # 容量超標只記錄不阻擋（組版端會自動截斷；擋下來使用者就完全拿不到 PPT）。
    capacity_warnings = slide_plan_capacity_warnings(plan)
    # 🔴 不查資料庫就不准寫（2026-08-10 使用者定案）：CLI 的職責是依選圖內容判斷
    # 要找什麼證據並實際查。原本 query_audit 只被記錄、不被檢查，等於允許只讀
    # 聚合數字就編出整份敘述。
    errors += validate_research_effort(query_audit)
    if errors:
        # ⚠ 失敗不落檔：留下未通過的候選會讓人誤以為可交付。
        raise ReportPlanningError(f"規劃驗證未過：{errors}")

    result = {
        "plan_id": plan["plan_id"],
        "plan": plan,
        "slides": plan["slides"],
        "strategy": reply.get("strategy") or {},
        "evidence": evidence,
        "prompt_version": PROMPT_VERSION,
        "snapshot_id": brief["snapshot_id"],
        "validation_errors": [],
        # 取證紀錄：查了幾次、用哪些工具、有沒有截斷或失敗。
        # ⚠ 空清單有意義——代表這次規劃**完全沒有查證**，只用了選圖數據。
        "query_audit": query_audit,
        # 對不上引擎數據的直接引用；人工抽驗時看得到「這幾個數字我對不上」。
        "evidence_value_warnings": value_warnings,
        "capacity_warnings": capacity_warnings,
    }
    _tick("保存候選", 90)
    if persister is not None:
        persister(result)
    return result
