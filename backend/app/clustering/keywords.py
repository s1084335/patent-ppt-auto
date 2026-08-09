"""主題關鍵詞抽取（class-TF-IDF），供不經 BERTopic 的分群使用。

## 為什麼需要它

`topic_cv_coherence_per_topic` 與 `topic_diversity` 只需要 `top_terms`——
它們**不綁 BERTopic**（gensim 算 c_v 用的是文件本身）。所以只要能自己產出
每群的關鍵詞，這兩個既有品質指標對 DP-Means 一樣適用。

⚠ 2026-08-09 修正：先前判斷「DP-Means 沒有 c-TF-IDF，所以指標只能填 None」
是錯的——缺的只是關鍵詞這一步，補上就好。

## 做法

class-TF-IDF：把每群的文件併成一個「類別文件」算詞頻，再依該詞出現在幾個
類別中折減。與 BERTopic 的 c-TF-IDF 同原理，但只用既有 tokenizer，不引入新依賴。

⚠ 關鍵詞**不得**進 CLI payload——`ai_topic_label_runner` 的紅線黑名單不變。
它們的用途是品質指標與前端顯示，不是餵給 LLM 命名（給了關鍵字，LLM 會覆述
關鍵詞而不是讀專利內容）。
"""
from __future__ import annotations

import math
import re

#: 詞彙切分樣式。與 model.py 的 c_v tokenizer 同一套字元類，讓抽出的詞能直接
#: 對上 coherence 的詞典——⚠ 兩邊用不同切法時，term 會查不到而被整批丟棄，
#: 症狀是 coherence 莫名其妙偏低。
TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[一-鿿]")

#: 這些詞出現在幾乎所有專利文本裡，留著會讓每個主題的關鍵詞長得一樣。
STOP_TERMS = frozenset({
    "the", "and", "for", "with", "that", "this", "are", "not", "from", "has",
    "one", "two", "said", "which", "wherein", "comprising", "including",
    "device", "apparatus", "method", "system", "unit", "means",
})

#: 詞長下限。單字母與單一數字沒有辨識度。
MIN_TERM_LENGTH = 2


def extract_top_terms(
    documents: list[str], *, labels: list[int], limit: int,
) -> dict[int, list[str]]:
    """每群抽出最具辨識度的關鍵詞（class-TF-IDF）。

    ⚠ 挑的是「**這群才有**的詞」而不是「最常出現的詞」：兩群都有的詞排在前面
    會讓兩個主題的關鍵詞長得一模一樣，多樣性歸零，使用者看到兩張講一樣事情
    的卡片。
    """
    if not documents or not labels:
        return {}
    class_counts = _class_term_counts(documents, labels)
    document_frequency = _class_document_frequency(class_counts)
    return {
        label: _top_terms_for_class(counts, document_frequency,
                                    total_classes=len(class_counts), limit=limit)
        for label, counts in class_counts.items()
    }


def _class_term_counts(documents: list[str],
                       labels: list[int]) -> dict[int, dict[str, int]]:
    """每群的詞頻（把該群所有文件併成一個「類別文件」）。"""
    class_counts: dict[int, dict[str, int]] = {}
    for document, label in zip(documents, labels):
        counts = class_counts.setdefault(label, {})
        for token in _tokenize(document):
            counts[token] = counts.get(token, 0) + 1
    return class_counts


def _class_document_frequency(
    class_counts: dict[int, dict[str, int]],
) -> dict[str, int]:
    """每個詞出現在幾個群裡——用來折減共通詞。"""
    frequency: dict[str, int] = {}
    for counts in class_counts.values():
        for term in counts:
            frequency[term] = frequency.get(term, 0) + 1
    return frequency


def _top_terms_for_class(counts: dict[str, int], document_frequency: dict[str, int],
                         *, total_classes: int, limit: int) -> list[str]:
    """單一群的 class-TF-IDF 排序。"""
    total = sum(counts.values()) or 1
    scored = [
        (term, (count / total) * math.log(1 + total_classes / document_frequency[term]))
        for term, count in counts.items()
    ]
    # ⚠ 次要排序鍵用 term 本身：分數相同時的順序必須固定，否則同樣輸入會得到
    # 不同關鍵詞，指標就不可重現了。
    scored.sort(key=lambda item: (-item[1], item[0]))
    return [term for term, _ in scored[:limit]]


def _tokenize(document: str) -> list[str]:
    """切詞並濾掉沒有辨識度的詞。⚠ 空文本回空清單，不得 raise。"""
    if not document:
        return []
    return [
        token for token in TOKEN_PATTERN.findall(document.lower())
        if len(token) >= MIN_TERM_LENGTH and token not in STOP_TERMS
    ]
