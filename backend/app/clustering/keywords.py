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

#: 詞長下限。⚠ 只適用拉丁字母詞——單字母與單一數字沒有辨識度。
#: 中文不受此限（見 `_tokenize`：中文改取相鄰兩字的 bigram）。
MIN_LATIN_TERM_LENGTH = 2

# ⚠ **沒有寫死的停用詞表**（2026-08-09 使用者要求）。
# 原本有一份 STOP_TERMS（the／and／device／apparatus／method…），那是語言與領域
# 綁定的——換成中文專利、換個技術領域就失效，而且失效時不會報錯，只會讓關鍵詞
# 變成一堆廢詞。改為完全由資料決定：出現在**每一群**的詞沒有辨識度，直接剔除
# （見 `_top_terms_for_class`）。那個規則對任何語言、任何領域都成立。


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
    """單一群的 class-TF-IDF 排序。

    ⚠ **出現在所有群的詞直接剔除**，不只是折減。實測（滑雪機功效通道）：每筆
    效果摘要都以 "The invention thereby improves..." 開頭，improves／invention／
    thereby／of 在五個群都出現。class-TF-IDF 的 IDF 項有折減（df=全部時
    log(1+1)=0.69 vs df=1 時 log(1+5)=1.79），但這些詞的 TF 太高，折減後仍排進
    前四——結果兩個主題的關鍵詞長得幾乎一樣，使用者看不出差別。
    出現在每一群的詞**沒有任何辨識度**，留著只會排擠真正有區別的詞。
    """
    total = sum(counts.values()) or 1
    scored = [
        (term, (count / total) * math.log(1 + total_classes / document_frequency[term]))
        for term, count in counts.items()
        if not (total_classes > 1 and document_frequency[term] >= total_classes)
    ]
    # ⚠ 次要排序鍵用 term 本身：分數相同時的順序必須固定，否則同樣輸入會得到
    # 不同關鍵詞，指標就不可重現了。
    scored.sort(key=lambda item: (-item[1], item[0]))
    return [term for term, _ in scored[:limit]]


def _tokenize(document: str) -> list[str]:
    """切詞。⚠ 空文本回空清單，不得 raise。

    ⚠ 只處理英數詞：2026-08-09 使用者確認「給 BERTopic 的欄位會用到的都是英文
    值」。中文逐字切後單字沒有辨識度，若日後真有中文語料要處理，需要 bigram
    ——但那是還沒發生的需求，現在不加。
    """
    if not document:
        return []
    return [token for token in TOKEN_PATTERN.findall(document.lower())
            if len(token) >= MIN_LATIN_TERM_LENGTH]
