"""市場資料 · 範圍與證據的邏輯層契約（純邏輯，無 DB／網路）。

對應 0022 `derived_layer.market_evidence` 的 8 欄語意，但這裡只做邏輯驗證與可比較性計算，
不碰 DB。缺欄一律拒收並指明哪一欄（MarketEvidenceError）。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

# kind 與 reliability 白名單（對應表 CHECK）
KINDS = ("market_size", "region_trend", "customer", "key_player", "pain_point")
RELIABILITY = ("industry_gov_corp", "news", "forum")

# 依 kind 的目標欄必填（自取設計，護欄內）：市場類必填 market，主體類必填 subject
_REQUIRE_MARKET = {"market_size", "region_trend", "key_player"}
_REQUIRE_SUBJECT = {"customer", "pain_point"}

# 範圍（scope）必填欄：產品定義／包含／排除／地區／基準年／預測期間／貨幣
SCOPE_FIELDS = ("product_definition", "includes", "excludes", "regions",
                "base_year", "forecast_period", "currency")

# payload_json 內來源相關必填欄
_PAYLOAD_REQUIRED = ("source_name", "source_url", "published_on", "reliability", "summary")

_URL = re.compile(r"^https?://", re.IGNORECASE)


class MarketEvidenceError(ValueError):
    """範圍或證據契約違規；訊息指明哪一欄。"""


def _is_blank(value: Any) -> bool:
    """None／空字串／空集合視為缺值。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def validate_scope(scope: dict[str, Any]) -> None:
    """研究範圍必填欄檢查；缺欄拒收並指明哪欄。"""
    if not isinstance(scope, dict):
        raise MarketEvidenceError("scope 必須為 dict")
    for field in SCOPE_FIELDS:
        if _is_blank(scope.get(field)):
            raise MarketEvidenceError(f"scope 缺必填欄：{field}")


def validate_evidence(evidence: dict[str, Any]) -> None:
    """單筆證據的邏輯層契約檢查。"""
    if not isinstance(evidence, dict):
        raise MarketEvidenceError("evidence 必須為 dict")
    kind = evidence.get("kind")
    if kind not in KINDS:
        raise MarketEvidenceError(f"非法 kind：{kind!r}（限 {KINDS}）")
    if _is_blank(evidence.get("scope")):
        raise MarketEvidenceError("evidence 缺必填欄：scope")

    if kind in _REQUIRE_MARKET and _is_blank(evidence.get("market")):
        raise MarketEvidenceError(f"{kind} 必填 market（市場碼）")
    if kind in _REQUIRE_SUBJECT and _is_blank(evidence.get("subject")):
        label = "topic_code" if kind == "pain_point" else "客群名"
        raise MarketEvidenceError(f"{kind} 必填 subject（{label}）")

    payload = evidence.get("payload_json")
    if not isinstance(payload, dict):
        raise MarketEvidenceError("evidence 缺必填欄：payload_json")
    for field in _PAYLOAD_REQUIRED:
        if _is_blank(payload.get(field)):
            raise MarketEvidenceError(f"payload_json 缺必填欄：{field}")

    reliability = payload["reliability"]
    if reliability not in RELIABILITY:
        raise MarketEvidenceError(f"非法 reliability：{reliability!r}（限 {RELIABILITY}）")
    if not _URL.match(str(payload["source_url"])):
        raise MarketEvidenceError("source_url 格式非法（須 http(s)://）")
    # 論壇／新聞須可識別發布者與日期（published_on 已在必填內）
    if reliability in ("news", "forum") and _is_blank(payload.get("publisher")):
        raise MarketEvidenceError(f"{reliability} 來源須有發布者 publisher")


def _parse_date(value: Any) -> date:
    """接受 date 或 'YYYY-MM-DD' 字串。"""
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    raise MarketEvidenceError(f"日期格式非法：{value!r}")


def comparability_key(evidence: dict[str, Any]) -> tuple:
    """可比較性 key＝scope＋market＋年份＋市場定義（口徑）；不同 key 不得混算。"""
    payload = evidence.get("payload_json") or {}
    value = payload.get("value") or {}
    year = value.get("year", value.get("base_year"))
    market_definition = value.get("market_definition", "")
    return (evidence.get("scope"), evidence.get("market"), year, market_definition)


def staleness(evidence: dict[str, Any], report_date: Any) -> dict[str, Any]:
    """兩年內視為 fresh，否則標年份差（優先近兩年、逐年放寬）。"""
    payload = evidence.get("payload_json") or {}
    published = _parse_date(payload.get("published_on"))
    years_diff = (_parse_date(report_date) - published).days / 365.25
    return {
        "fresh": years_diff <= 2,
        "years_diff": round(years_diff, 1),
        "published_on": payload.get("published_on"),
    }
