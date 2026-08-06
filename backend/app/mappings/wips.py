from __future__ import annotations

import re

MAPPING_VERSION = "wips_v1_2026-07-01"
SOURCE_SYSTEM = "WIPS"

TRADITIONAL_PHRASE_REPLACEMENTS = {
    "資料庫": "数据库",
    "鏈結": "链接",
    "連結": "链接",
}

TRADITIONAL_TO_SIMPLIFIED = str.maketrans(
    {
        "國": "国",
        "碼": "码",
        "資": "资",
        "庫": "库",
        "稱": "称",
        "類": "类",
        "發": "发",
        "專": "专",
        "實": "实",
        "標": "标",
        "題": "题",
        "狀": "状",
        "態": "态",
        "申": "申",
        "請": "请",
        "號": "号",
        "權": "权",
        "審": "审",
        "開": "开",
        "項": "项",
        "圖": "图",
        "獨": "独",
        "數": "数",
        "籤": "签",
        "領": "领",
        "域": "域",
        "課": "课",
        "題": "题",
        "決": "决",
        "徵": "征",
        "術": "术",
        "優": "优",
        "關": "关",
        "聯": "联",
        "號": "号",
        "與": "与",
        "獻": "献",
        "獨": "独",
        "當": "当",
        "前": "前",
        "讓": "让",
        "轉": "转",
        "態": "态",
        "體": "体",
        "適": "适",
        "長": "长",
        "訴": "诉",
        "訟": "讼",
        "轄": "辖",
        "檔": "档",
        "鏈": "链",
        "結": "结",
        "詳": "详",
        "錄": "录",
        "註": "注",
        "糾": "纠",
        "準": "准",
        "構": "构",
        "報": "报",
        "記": "记",
        "屬": "属",
        "變": "变",
        "動": "动",
        "標": "标",
        "準": "准",
        "員": "员",
        "總": "总",
        "數": "数",
        "審": "审",
        "閱": "阅",
        "絕": "绝",
        "決": "决",
        "進": "进",
        "現": "现",
        "實": "实",
        "施": "施",
        "許": "许",
        "可": "可",
    }
)


def canonical_field_name(field_name: str) -> str:
    canonical = field_name.strip()
    for traditional, simplified in TRADITIONAL_PHRASE_REPLACEMENTS.items():
        canonical = canonical.replace(traditional, simplified)
    return canonical.translate(TRADITIONAL_TO_SIMPLIFIED)


DISPLAY_PHRASE_REPLACEMENTS = {
    "文图像文件": "文圖像文件",
    "链接": "連結",
    "详细查看链接": "詳細查看連結",
    "登录": "登入",
    "发明专利": "發明專利",
    "实用新型": "實用新型",
    "授权公告": "授權公告",
    "未审查的公开": "未審查的公開",
    "审查的公告": "審查的公告",
    "申请": "申請",
    "优先权": "優先權",
    "国家": "國家",
    "数据库": "資料庫",
    "文献": "文獻",
    "同族专利": "同族專利",
    "专利权人": "專利權人",
    "受让人": "受讓人",
    "转让人": "轉讓人",
    "当前": "當前",
    "标准": "標準",
    "韩国": "韓國",
    "实施许可": "實施許可",
    "被许可人": "被許可人",
    "权利要求": "權利要求",
    "权利变动": "權利變動",
    "法律状态": "法律狀態",
    "实体状态": "實體狀態",
    "是否请求审查": "是否請求審查",
    "意见提出通知书": "意見提出通知書",
    "拒绝决定": "拒絕決定",
    "再审申请": "再審申請",
    "优先请求审查": "優先請求審查",
    "新颖性丧失例外主张": "新穎性喪失例外主張",
    "审查员": "審查員",
    "审判": "審判",
    "诉讼": "訴訟",
    "管辖": "管轄",
    "个别图数量": "個別圖數量",
    "文献备注": "文獻備註",
    "纠正公告存在": "糾正公告存在",
    "食品药品专利记载": "食品藥品專利記載",
    "所有权利要求": "所有權利要求",
    "独立项": "獨立項",
    "分类标签": "分類標籤",
    "解决课题": "解決課題",
    "解决手段": "解決手段",
    "技术领域": "技術領域",
    "特征": "特徵",
    "翻译文": "翻譯文",
    "指定国家代码": "指定國家代碼",
    "引用文献": "引用文獻",
    "非专利参考文献": "非專利參考文獻",
    "到期日期": "到期日期",
    "年费缴纳日": "年費繳納日",
    "标准号码": "標準號碼",
    "标准化机构": "標準化機構",
}

DISPLAY_TO_TRADITIONAL = str.maketrans(
    {
        "图": "圖",
        "权": "權",
        "项": "項",
        "数": "數",
        "发": "發",
        "审": "審",
        "开": "開",
        "号": "號",
        "请": "請",
        "国": "國",
        "码": "碼",
        "类": "類",
        "标": "標",
        "题": "題",
        "术": "術",
        "领": "領",
        "课": "課",
        "决": "決",
        "征": "徵",
        "译": "譯",
        "关": "關",
        "联": "聯",
        "优": "優",
        "献": "獻",
        "专": "專",
        "为": "為",
        "准": "準",
        "当": "當",
        "让": "讓",
        "转": "轉",
        "许": "許",
        "属": "屬",
        "变": "變",
        "动": "動",
        "缴": "繳",
        "纳": "納",
        "无": "無",
        "统": "統",
        "辖": "轄",
        "诉": "訴",
        "讼": "訟",
        "详": "詳",
        "链": "鏈",
        "编": "編",
        "录": "錄",
        "个": "個",
        "备": "備",
        "纠": "糾",
        "药": "藥",
        "载": "載",
        "颖": "穎",
        "丧": "喪",
        "语": "語",
        "员": "員",
        "总": "總",
        "据": "據",
        "库": "庫",
        "实": "實",
        "态": "態",
        "构": "構",
        "报": "報",
        "记": "記",
        "长": "長",
        "适": "適",
        "预": "預",
        "计": "計",
        "复": "複",
        "韩": "韓",
    }
)


def display_field_name(field_name: str) -> str:
    display = canonical_field_name(field_name)
    for source, target in sorted(DISPLAY_PHRASE_REPLACEMENTS.items(), key=lambda item: len(item[0]), reverse=True):
        display = display.replace(source, target)
    return display.translate(DISPLAY_TO_TRADITIONAL)

PATENT_FIELDS = {
    # 主附圖：WIPS Excel 的浮動圖片欄（儲存格文字恆為空白，真值在 ws._images）。
    # 列在此表使其歸屬主表 core_layer.patents（0026 起為 bytea）而非自動推導的
    # patent_attributes；值由 wips_importer 的內嵌圖擷取填入，不走 clean_long_text。
    "主附图": "主附圖",
    "国家代码": "country_code",
    "数据库名称": "database_name",
    "文献种类": "document_kind",
    "发明专利/实用新型": "patent_type",
    "授权公告号": "授權公告號",
    "未审查的公开号": "未審查的公開號",
    "审查的公告号": "審查的公告號",
    "申请号": "申請號",
    "标题": "title",
    "标题(原文)": "title_original",
    "摘要": "abstract",
    "权利要求的项数": "權利要求的項數",
    "所有权利要求[JP,KR,CN]": "所有權利要求[JP,KR,CN]",
    "主权项": "主權項",
    "主权项(原文)": "主權項(原文)",
    "独立项数量[KR,JP,US,CN,EP,IN]": "獨立項數量[KR,JP,US,CN,EP,IN]",
    "独立项[KR,JP,US,CN,EP,IN]": "獨立項[KR,JP,US,CN,EP,IN]",
    "独立项(原文)[KR,JP,CN,EP]": "獨立項(原文)[KR,JP,CN,EP]",
    "效果 摘要[US,EP,PCT,JP,KR,CN,TW]": "效果 摘要[US,EP,PCT,JP,KR,CN,TW]",
    "Orig. CPC(Main)": "Orig. CPC(Main)",
    "Orig. IPC(Main)": "Orig. IPC(Main)",
    "Curr. CPC(Main)": "Curr. CPC(Main)",
    "Curr. IPC(Main)": "Curr. IPC(Main)",
    "状态[US,JP,KR,CN,EP,CA,AU]": "legal_status",
    "WIPS同族ID": "WIPS同族ID",
    # ── 欄位重分類（0046，2026-08-06）──────────────────────────────
    # 規格＝`docs/patent_core_field_reclassification_spec.md`：
    # **會被程式用到的欄位一律歸 core table**，`patent_attributes` 只留未使用欄位。
    #
    # ⚠ 加進本 dict 就會自動從 `ATTRIBUTE_FIELDS` 掉出來（見檔尾的推導），
    # 這是唯一定義處——不需要也不得在 `FIELD_GROUPS` 另外刪一次。
    #
    # 逐欄的實際讀取點（preflight 實查，證據見 patent-db-claude-plan.md）：
    #   patent_queries `_ATTRIBUTE_FIELDS` 8 欄
    "摘要(原文)": "摘要(原文)",
    "未审查的公开日": "未審查的公開日",
    "授权公告日": "授權公告日",
    "优先权号": "優先權號",
    "优先权国家": "優先權國家",
    "优先权日": "優先權日",
    "详细查看链接(登录)": "詳細查看連結(登入)",
    #   target_source（案件比對取 PDF）＋ patent_queries
    "文图像文件(PDF)链接": "文圖像文件(PDF)連結",
    #   refresh_report_patent_base（報表 derived）
    "WIPS同族各国家文献数量(申请为准)": "WIPS同族各國家文獻數量(申請為準)",
    "EPC有效国家[EP]": "EPC有效國家[EP]",
    "EPC无效国家[EP]": "EPC無效國家[EP]",
    "(F1)引用文献数": "(F1)引用文獻數",
    "(B1)引用文献数": "(B1)引用文獻數",
    #   功效通道 AI 補分的輸入（2026-08-05 定案）——已是分析輸入，不得留在 attributes
    "解决课题 摘要[US,EP,PCT,JP,KR,CN,TW]": "解決課題 摘要[US,EP,PCT,JP,KR,CN,TW]",
}

APPLICATION_DATE_FIELD = "申请日"

PUBLICATION_NUMBER_FIELDS = [
    "授权公告号",
    "未审查的公开号",
    "审查的公告号",
]

PUBLICATION_DATE_FIELDS = [
    "授权公告日",
    "未审查的公开日",
    "审查的公告日",
]

PEOPLE_GROUPS = {
    "applicant": {
        "name": "申请人",
        "name_original": "申请人(第2语言)",
        "country_code": "申请人国籍",
        "standardized_name": "标准化申请人",
        "person_code": "申请人代表码",
    },
    "inventor": {
        "name": "发明人",
        "name_original": "发明人(第2语言)",
        "country_code": "发明人国籍",
    },
    "agent": {
        "name": "代理人(机构)",
    },
    "owner": {
        "name": "最近专利权人[US,JP,KR,CN,CA,AU]",
        "name_original": "最近专利权人(第2语言)[JP,KR,CN]",
        "standardized_name": "标准当前专利权人[US,JP,KR,CN,CA,AU]",
        "person_code": "标准当前专利权人代码[US,JP,KR,CN,CA,AU]",
    },
    "assignee": {
        "name": "最近受让人[US,KR,CN]",
    },
    "assignor": {
        "name": "最近转让人[US,KR,CN]",
    },
    "declarant": {
        "name": "申报（登记）人",
        "country_code": "申报（登记）人国籍",
    },
}

# 人員統計欄（0046，2026-08-06）：不屬於任何一個「人」的分組，但語意上是 people 資料。
# ⚠ 單獨列出而非塞進上面某一組——它們是**整件專利的人數**，不是某個申請人的屬性；
# 塞進 `applicant` 會讓 `PEOPLE_GROUPS` 的語意（一組＝一種角色的欄位集）走樣。
PEOPLE_STAT_FIELDS = {
    "发明人数": "發明人數",
    "申请人数": "申請人數",
}

PEOPLE_FIELD_COLUMNS = {
    "申请人": "申請人",
    "申请人(第2语言)": "申請人(第2語言)",
    "申请人国籍": "申請人國籍",
    "标准化申请人": "標準化申請人",
    "申请人代表码": "申請人代表碼",
    "发明人": "發明人",
    "发明人(第2语言)": "發明人(第2語言)",
    "发明人国籍": "發明人國籍",
    "代理人(机构)": "代理人(機構)",
    "最近专利权人[US,JP,KR,CN,CA,AU]": "最近專利權人[US,JP,KR,CN,CA,AU]",
    "最近专利权人(第2语言)[JP,KR,CN]": "最近專利權人(第2語言)[JP,KR,CN]",
    "标准当前专利权人[US,JP,KR,CN,CA,AU]": "標準當前專利權人[US,JP,KR,CN,CA,AU]",
    "标准当前专利权人代码[US,JP,KR,CN,CA,AU]": "標準當前專利權人代碼[US,JP,KR,CN,CA,AU]",
    "最近受让人[US,KR,CN]": "最近受讓人[US,KR,CN]",
    "最近转让人[US,KR,CN]": "最近轉讓人[US,KR,CN]",
    "申报（登记）人": "申報（登記）人",
    "申报（登记）人国籍": "申報（登記）人國籍",
    # ── 欄位重分類（0046，2026-08-06）──────────────────────────────
    # 人員統計欄歸 people，不留在 attributes。
    # ⚠ `发明人数` 已被報表 derived 使用（`refresh_report_patent_base`）；
    #    `申请人数` 語意上屬 people 統計，與人員資料同表才不會兩處各查一次。
    # ⚠ 它們原本在 `FIELD_GROUPS["people_stats"]`——那一組**不叫 `people`**，
    #    所以不在 `PEOPLE_FIELD_SET` 內，才會落進 ATTRIBUTE_FIELDS。
    #    加進本 dict 並補進 PEOPLE_GROUPS 後，兩邊都會正確排除。
    "发明人数": "發明人數",
    "申请人数": "申請人數",
}

CLASSIFICATION_FIELDS = {
    "Orig. CPC(Main)": {"scheme": "CPC", "is_primary": True, "is_original": True, "is_current": False},
    "Orig. CPC(All)": {"scheme": "CPC", "is_primary": False, "is_original": True, "is_current": False},
    "Orig. IPC(Main)": {"scheme": "IPC", "is_primary": True, "is_original": True, "is_current": False},
    "Orig. IPC(All)": {"scheme": "IPC", "is_primary": False, "is_original": True, "is_current": False},
    "Orig. US Class(Main)[US]": {"scheme": "USPC", "is_primary": True, "is_original": True, "is_current": False},
    "Orig. US Class(All)[US]": {"scheme": "USPC", "is_primary": False, "is_original": True, "is_current": False},
    "Orig. FI[JP]": {"scheme": "FI", "is_primary": False, "is_original": True, "is_current": False},
    "Orig. F-term[JP]": {"scheme": "FTERM", "is_primary": False, "is_original": True, "is_current": False},
    "Orig. Theme Code[JP]": {"scheme": "THEME", "is_primary": False, "is_original": True, "is_current": False},
    "Curr. CPC(Main)": {"scheme": "CPC", "is_primary": True, "is_original": False, "is_current": True},
    "Curr. CPC(All)": {"scheme": "CPC", "is_primary": False, "is_original": False, "is_current": True},
    "Curr. IPC(Main)": {"scheme": "IPC", "is_primary": True, "is_original": False, "is_current": True},
    "Curr. IPC(All)": {"scheme": "IPC", "is_primary": False, "is_original": False, "is_current": True},
    "Curr. US Class(Main)[US]": {"scheme": "USPC", "is_primary": True, "is_original": False, "is_current": True},
    "Curr. US Class(All)[US]": {"scheme": "USPC", "is_primary": False, "is_original": False, "is_current": True},
    "Curr. FI[JP]": {"scheme": "FI", "is_primary": False, "is_original": False, "is_current": True},
    "Curr. F-term[JP]": {"scheme": "FTERM", "is_primary": False, "is_original": False, "is_current": True},
}

FIELD_GROUPS = {
    "base": {
        "主附图", "国家代码", "标题", "标题(原文)", "摘要", "摘要(原文)", "主权项", "主权项(原文)",
        "数据库名称", "发明专利/实用新型", "文献种类", "独立项[KR,JP,US,CN,EP,IN]",
        "独立项(原文)[KR,JP,CN,EP]", "权利要求的项数", "独立项数量[KR,JP,US,CN,EP,IN]",
        "分类标签", "WIPSGLOBAL KEY",
    },
    "wips_ai": {
        "AI摘要[US,EP,PCT,JP,KR,CN,TW]", "技术领域 摘要[US,EP,PCT,JP,KR,CN,TW]",
        "解决课题 摘要[US,EP,PCT,JP,KR,CN,TW]", "解决手段 摘要[US,EP,PCT,JP,KR,CN,TW]",
        "特征 摘要[US,EP,PCT,JP,KR,CN,TW]", "效果 摘要[US,EP,PCT,JP,KR,CN,TW]",
    },
    "numbers_dates": {
        "申请号", "申请日", "翻译文提交日", "未审查的公开号", "未审查的公开日",
        "审查的公告号", "审查的公告日", "授权公告号", "授权公告日", "发行日[JP,EP,PCT]",
    },
    "priority": {
        "优先权号", "优先权国家", "优先权日", "关联申请号 [US,PCT,AU]", "关联申请日 [US,PCT,AU]",
        "母案申请号 [KR,JP,EP,CN,IN,CA]", "母案申请日 [KR,JP,EP,CN,IN,CA]",
        "分案申请 [KR,US,JP,EP,CN,IN,CA,AU]", "优先权申请号", "优先权申请国家", "优先权申请日",
    },
    "pct": {"PCT申请号", "PCT申请日", "PCT公开号", "PCT公开日", "指定国家代码"},
    "classification": set(CLASSIFICATION_FIELDS),
    "citation": {
        "(B1)引用文献数", "(B1)引用文献号码", "(B1)自引文献号码", "(B1)他引文献号码",
        "(B1)审查官引用文献[US,JP,KR,EP]", "(B1)非专利参考文献", "(B1)非专利参考文献数",
        "(F1)引用文献数", "(F1)引用文献号码", "(F1)自引被引文献号码", "(F1)他引被引文献号码",
        "(F1)审查官引用文献[US,JP,KR,EP]",
    },
    "family": {
        "WIPS同族ID", "WIPS同族专利Basic patent编号", "WIPS同族文献编号(申请基准)",
        "WIPS同族文献数量(申请为准)", "WIPS同族各国家文献数量(申请为准)", "WIPS同族国家数量(申请为准)",
        "EPO同族ID", "EPO同族专利文献号码(申请为准)", "EPO同族文献数量(申请为准)",
        "EPO同族专利个别国家文献数量(申请为准)", "EPO同族国家数量(申请为准)",
    },
    "legal_admin": {
        "状态[US,JP,KR,CN,EP,CA,AU]", "(预计)到期日期[US,JP,KR,CN,EP,CA,AU]", "DOCDB法律状态",
        "实体状态[US]", "AIA适用[US]", "PTA延长日期[US]", "最近年费缴纳日[US,EP,KR]",
        "EPC指定国[EP]", "EPC无效国家[EP]", "EPC有效国家[EP]", "统一专利法院[EP]",
    },
    "rights": {
        "实施许可[KR]", "被许可人数量[KR]", "最近专利权人[US,JP,KR,CN,CA,AU]",
        "最近专利权人(第2语言)[JP,KR,CN]", "标准当前专利权人代码[US,JP,KR,CN,CA,AU]",
        "标准当前专利权人[US,JP,KR,CN,CA,AU]", "韩国标准当前专利权人", "最近受让人[US,KR,CN]",
        "最近转让人[US,KR,CN]", "最近转让日[US,KR,CN]", "最近转让类型[US,KR,CN]", "权利变动[US,KR,CN]",
    },
    "review_litigation": {
        "是否请求审查(日期)[JP,KR,EP,CA]", "意见提出通知书次数[KR]", "拒绝决定[JP,KR]", "再审申请[KR]",
        "优先请求审查[KR]", "新颖性丧失例外主张[JP]", "审查员[US,JP,KR,CN]", "审判总数[US,JP,KR,EP]",
        "审判管辖类型[US,JP,KR,EP]", "诉讼总数[US]", "管辖法院类型[US]",
    },
    "documents": {
        # 「文献备注」(文獻備註) 0032 起改為 AI 產出欄、落 core_layer.patents 主表，
        # 不再是 patent_attributes 屬性欄，故從此 group 移除——否則 ATTRIBUTE_FIELDS 會含它，
        # 匯入時 replace_attributes 會 INSERT 到已被 0032 移除的 patent_attributes 欄而爆。
        "文图像文件(PDF)链接", "详细查看链接(登录)", "个别图数量", "纠正公告存在[JP,KR]",
        "食品药品专利记载[US]", "所有权利要求[JP,KR,CN]",
    },
    "standard": {"标准化机构", "标准号码", "申报日", "申报（登记）人", "申报（登记）人国籍"},
    "people_stats": {"申请人数", "标准化申请人[KR]", "申请人名称标准化代码[JP]", "发明人数"},
}
PEOPLE_FIELD_SET = set().union(
    *(set(values.values()) for values in PEOPLE_GROUPS.values()),
    # 0046：人數統計欄一併算 people，才會從 ATTRIBUTE_FIELDS 排除。
    set(PEOPLE_STAT_FIELDS),
)
FIELD_GROUPS["people"] = PEOPLE_FIELD_SET

ATTRIBUTE_FIELDS = tuple(
    dict.fromkeys(
        field
        for group, fields in FIELD_GROUPS.items()
        if group != "people"
        for field in fields
        if field not in PATENT_FIELDS and field != APPLICATION_DATE_FIELD and field not in PEOPLE_FIELD_SET
    )
)
ATTRIBUTE_FIELD_COLUMNS = {field: display_field_name(field) for field in ATTRIBUTE_FIELDS}


def source_group_for(field_name: str) -> str:
    canonical_name = canonical_field_name(field_name)
    for group, fields in FIELD_GROUPS.items():
        if canonical_name in fields:
            return group
    return "other"


def attribute_key_for(field_name: str) -> str:
    key = canonical_field_name(field_name).lower().strip()
    key = re.sub(r"\[[^\]]+\]", "", key)
    key = re.sub(r"[()（）/\s]+", "_", key)
    key = re.sub(r"[^0-9a-zA-Z_\u4e00-\u9fff]+", "_", key)
    return key.strip("_") or "unknown"
