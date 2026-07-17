from __future__ import annotations

import hashlib
import html
import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any


# 本檔只做「模型輸入前」的文本處理：
# 1. 把 DB / Excel 讀到的值轉成文字。
# 2. 做不改變語意結構的保守清理。
# 3. 對 WIPS 獨立項做 claim-aware chunking，避免 PatentSBERTa 截斷。
# 4. 產生後續 embedding / clustering 需要追溯的 metadata。
TEXT_CLEANING_VERSION = "patent_text_clean_v1"
DEFAULT_AGGREGATION_METHOD = "weighted_mean"

# 正規表示式只處理匯出格式噪音，不刪 claim 編號、標點、數字或技術符號。
_INLINE_SPACE_RE = re.compile(r"[ \t\f\v\u00a0]+")
_MULTI_BLANK_LINE_RE = re.compile(r"\n{3,}")

# WIPS 獨立項常見格式是「1. ... 8. ... 15. ...」。
# 這裡只找可能的 claim 起點，不做法律語法解析。
_CLAIM_BOUNDARY_RE = re.compile(r"(?:^|\s)(\d{1,3})\.\s+(?=[A-Za-zΑ-Ωα-ω가-힣一-龥])")

# 這些字元通常是匯出、貼上或編碼過程帶進來的不可見控制字元。
_CONTROL_CHAR_RE = re.compile(
    "["
    "\u0000-\u0008"
    "\u000b-\u000c"
    "\u000e-\u001f"
    "\u007f-\u009f"
    "\u200b-\u200f"
    "\u202a-\u202e"
    "\ufeff"
    "]"
)


@dataclass(frozen=True)
class TextPreprocessConfig:
    """Versioned switches for the shared DB/file preprocessing behavior.

    Keep these flags explicit so a future profile can prove which text
    operations were allowed. The false flags document things we deliberately do
    not do before embedding, such as translation or summarization.
    """
    # 版本號會寫進 model profile；日後清理規則一變就要升版。
    text_cleaning_version: str = TEXT_CLEANING_VERSION

    # 允許的保守清理：只整理格式與不可見字元。
    unicode_normalization: str = "NFKC"
    normalize_newlines: bool = True
    trim_outer_whitespace: bool = True
    collapse_inline_spaces: bool = True
    preserve_paragraphs: bool = True
    remove_invisible_control_chars: bool = True
    decode_html_xml_entities: bool = True
    min_text_chars: int = 50
    deduplicate_exact_text: bool = True
    track_truncation: bool = True

    # 明確禁止的處理：避免前處理改寫專利文本語意或 claim 結構。
    translation: bool = False
    summarization: bool = False
    chunking: bool = False
    sentence_rewrite: bool = False
    claim_element_reorder: bool = False
    keyword_only_replacement: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize config for model profiles and run metadata."""
        return asdict(self)


@dataclass
class ClaimChunk:
    """One tokenizer-safe chunk produced from one or more complete claims."""
    # 實際送進 embedding model 的 chunk 文字。
    text: str

    # 這個 chunk 包含哪些 claim 編號；無法辨識時用 None。
    claim_numbers: list[int | None]

    # 不含 tokenizer special tokens 的內容 token 數。
    content_token_count: int

    # True 代表單一 claim 太長，不得不在 claim 內切段。
    split_within_claim: bool = False


@dataclass
class ProcessedText:
    """Per-document preprocessing audit record.

    The clustering pipeline should persist the hash, token, truncation, and
    chunk fields so every embedding can be traced back to its source text.
    """
    # row_number 用來回查原始資料列；正式 DB pipeline 可對應 patent_id。
    row_number: int

    # raw_text 是原始輸入；cleaned_text 是模型實際使用的文字。
    raw_text: str
    cleaned_text: str

    # hash 用於追溯、去重與確認同一份文本是否被重新處理。
    raw_text_hash: str
    model_text_hash: str

    # 字元數用於快速檢查清理前後是否異常縮短。
    raw_char_count: int
    cleaned_char_count: int

    # status = usable 才會進入 embedding；skipped 會保留原因。
    status: str
    skip_reason: str | None = None

    # token 與 truncation 欄位用來證明是否會被 PatentSBERTa 截斷。
    token_count: int | None = None
    max_seq_length: int | None = None
    was_truncated: bool | None = None
    would_truncate_without_chunking: bool | None = None
    would_truncate_after_chunking: bool | None = None
    truncation_policy: str | None = None

    # chunk 欄位是 embedding 的直接輸入與稽核依據。
    was_chunked: bool = False
    chunk_count: int = 0
    max_content_tokens: int | None = None
    max_chunk_token_count: int | None = None
    chunk_token_counts: list[int] | None = None
    chunk_claim_numbers: list[list[int | None]] | None = None
    chunk_texts: list[str] | None = None
    chunk_overlap_tokens: int = 0
    split_within_claim_count: int = 0
    chunking_strategy: str | None = None
    aggregation_method: str | None = None
    duplicate_of_row_number: int | None = None

    def to_dict(self, include_text: bool = False) -> dict[str, Any]:
        """Return audit-safe metadata; raw text is opt-in to avoid large output."""
        payload = asdict(self)
        if not include_text:
            payload.pop("raw_text", None)
            payload.pop("cleaned_text", None)
            payload.pop("chunk_texts", None)
        return payload


def value_to_text(value: Any) -> str:
    """Normalize empty spreadsheet/DB values before text cleaning."""
    # pandas / DB driver 可能把空值帶成 None、NaN 或字串 "nan"。
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value)
    if text.lower() == "nan":
        return ""
    return text


def sha256_text(text: str) -> str:
    """Hash text for reproducible deduplication and audit records."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_patent_text(value: Any, config: TextPreprocessConfig | None = None) -> str:
    """Conservatively clean patent text without rewriting its semantic structure."""
    config = config or TextPreprocessConfig()
    text = value_to_text(value)
    if not text:
        return ""

    # HTML/XML entity 只做還原，例如 &amp; -> &。
    if config.decode_html_xml_entities:
        text = html.unescape(text)

    # NFKC 統一全半形與相容字元，避免 tokenizer 因格式差異切出不同 token。
    if config.unicode_normalization:
        text = unicodedata.normalize(config.unicode_normalization, text)

    # Windows / Unix 換行先統一成 \n。
    if config.normalize_newlines:
        text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 移除不可見控制字元，但保留可見標點與技術符號。
    if config.remove_invisible_control_chars:
        text = _CONTROL_CHAR_RE.sub("", text)

    if config.collapse_inline_spaces:
        # 只壓縮單行內多餘空白，保留段落換行。
        lines = [_INLINE_SPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
        text = "\n".join(lines)

    # preserve_paragraphs=True 時，只把過多空白行縮成最多一個空白段落。
    if config.preserve_paragraphs:
        text = _MULTI_BLANK_LINE_RE.sub("\n\n", text)
    else:
        # 少數場景如果不保留段落，才會把換行壓成空白。
        text = _INLINE_SPACE_RE.sub(" ", text.replace("\n", " "))

    if config.trim_outer_whitespace:
        text = text.strip()
    return text


def process_patent_text(
    value: Any,
    *,
    row_number: int,
    config: TextPreprocessConfig | None = None,
) -> ProcessedText:
    """Clean one source value and build the base audit record."""
    config = config or TextPreprocessConfig()
    raw_text = value_to_text(value)
    cleaned_text = clean_patent_text(raw_text, config=config)

    # 這裡只判斷文本是否可用，不在前處理階段丟棄整列資料。
    status = "usable"
    skip_reason = None
    if not cleaned_text:
        status = "skipped"
        skip_reason = "empty_text"
    elif len(cleaned_text) < config.min_text_chars:
        status = "skipped"
        skip_reason = "short_text"
    return ProcessedText(
        row_number=row_number,
        raw_text=raw_text,
        cleaned_text=cleaned_text,
        raw_text_hash=sha256_text(raw_text),
        model_text_hash=sha256_text(cleaned_text),
        raw_char_count=len(raw_text),
        cleaned_char_count=len(cleaned_text),
        status=status,
        skip_reason=skip_reason,
    )


def mark_exact_duplicates(processed: list[ProcessedText]) -> None:
    """Mark exact duplicate cleaned texts without dropping any row."""
    # 去重只標記 duplicate_of_row_number；不刪資料，避免 patent 對應關係消失。
    first_seen: dict[str, int] = {}
    for item in processed:
        if item.status != "usable":
            continue
        first_row = first_seen.get(item.model_text_hash)
        if first_row is None:
            first_seen[item.model_text_hash] = item.row_number
            continue
        item.duplicate_of_row_number = first_row


def add_token_stats(
    processed: list[ProcessedText],
    *,
    tokenizer: Any,
    max_seq_length: int,
    truncation_policy: str = "tokenizer_default_front_truncation",
) -> None:
    """Record tokenizer length and direct-truncation risk for each document."""
    for item in processed:
        # 這個函式用於不啟用 chunking 時的截斷風險檢查。
        item.max_seq_length = max_seq_length
        item.truncation_policy = truncation_policy
        if item.status != "usable":
            item.was_truncated = False
            item.would_truncate_without_chunking = False
            item.would_truncate_after_chunking = False
            continue

        token_ids = tokenizer.encode(
            item.cleaned_text,
            add_special_tokens=True,
            truncation=False,
        )

        # truncation=False 是刻意的；我們要量真實長度，不讓 tokenizer 偷截斷。
        item.token_count = len(token_ids)
        item.was_truncated = item.token_count > max_seq_length
        item.would_truncate_without_chunking = item.was_truncated
        item.would_truncate_after_chunking = item.was_truncated


def add_claim_aware_chunks(
    processed: list[ProcessedText],
    *,
    tokenizer: Any,
    max_seq_length: int,
    chunk_overlap_tokens: int = 0,
    aggregation_method: str = DEFAULT_AGGREGATION_METHOD,
) -> None:
    """Create tokenizer-safe chunks and record per-document chunk metadata.

    The embedding stage should embed `ProcessedText.chunk_texts` one by one and
    combine chunk vectors with `aggregation_method` into one patent-level vector.
    """
    # SentenceTransformer tokenizer 會額外加 [CLS] / [SEP] 之類 special tokens；
    # claim 內容本身最多只能使用 max_seq_length 扣掉 special tokens 後的長度。
    special_tokens = int(tokenizer.num_special_tokens_to_add(pair=False))
    max_content_tokens = max_seq_length - special_tokens
    if max_content_tokens <= 0:
        raise ValueError(f"max_seq_length {max_seq_length} is too small for tokenizer special tokens")
    if chunk_overlap_tokens < 0:
        raise ValueError("chunk_overlap_tokens must be >= 0")
    if chunk_overlap_tokens >= max_content_tokens:
        raise ValueError("chunk_overlap_tokens must be smaller than max_content_tokens")

    for item in processed:
        # 以下欄位後續會跟 embedding 一起追溯，確認每篇專利如何被切分與聚合。
        item.max_seq_length = max_seq_length
        item.max_content_tokens = max_content_tokens
        item.chunk_overlap_tokens = chunk_overlap_tokens
        item.aggregation_method = aggregation_method
        item.truncation_policy = "chunked_no_truncation"
        item.chunking_strategy = "claim_aware_greedy"

        if item.status != "usable":
            # skipped 資料不進 embedding，但 metadata 固定清空，方便下游判斷。
            item.token_count = 0
            item.was_truncated = False
            item.would_truncate_without_chunking = False
            item.would_truncate_after_chunking = False
            item.was_chunked = False
            item.chunk_count = 0
            item.max_chunk_token_count = 0
            item.chunk_token_counts = []
            item.chunk_claim_numbers = []
            item.chunk_texts = []
            continue

        item.token_count = len(tokenizer.encode(item.cleaned_text, add_special_tokens=True, truncation=False))
        item.would_truncate_without_chunking = item.token_count > max_seq_length

        # 在前處理階段產生實際 chunk_texts，embedding 階段只負責向量化與聚合。
        chunks = build_claim_chunks(
            item.cleaned_text,
            tokenizer=tokenizer,
            max_content_tokens=max_content_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
        )
        chunk_texts = [chunk.text for chunk in chunks]

        # 重新用 add_special_tokens=True 驗證每段實際送進模型時仍 <= max_seq_length。
        chunk_token_counts = [
            len(tokenizer.encode(chunk_text, add_special_tokens=True, truncation=False))
            for chunk_text in chunk_texts
        ]
        item.chunk_count = len(chunk_token_counts)
        item.was_chunked = item.chunk_count > 1
        item.chunk_texts = chunk_texts
        item.chunk_token_counts = chunk_token_counts
        item.chunk_claim_numbers = [chunk.claim_numbers for chunk in chunks]
        item.max_chunk_token_count = max(chunk_token_counts) if chunk_token_counts else 0
        item.split_within_claim_count = sum(1 for chunk in chunks if chunk.split_within_claim)
        item.would_truncate_after_chunking = any(count > max_seq_length for count in chunk_token_counts)
        item.was_truncated = item.would_truncate_after_chunking


def add_claim_aware_chunk_stats(
    processed: list[ProcessedText],
    *,
    tokenizer: Any,
    max_seq_length: int,
    chunk_overlap_tokens: int = 0,
    aggregation_method: str = DEFAULT_AGGREGATION_METHOD,
) -> None:
    """Backward-compatible wrapper for callers that only need the stats name."""
    # 舊呼叫名稱保留，實際邏輯統一走 add_claim_aware_chunks。
    add_claim_aware_chunks(
        processed,
        tokenizer=tokenizer,
        max_seq_length=max_seq_length,
        chunk_overlap_tokens=chunk_overlap_tokens,
        aggregation_method=aggregation_method,
    )


def split_claim_segments(text: str) -> list[tuple[int | None, str]]:
    """Split text on claim-number boundaries while preserving claim bodies."""
    # 找不到 claim 編號時，整篇視為一段；後續仍可依 token 長度切 chunk。
    matches = list(_CLAIM_BOUNDARY_RE.finditer(text))
    if not matches:
        return [(None, text)]

    segments: list[tuple[int | None, str]] = []
    for index, match in enumerate(matches):
        # 從 claim 編號本身開始切，讓 chunk 文字保留「1.」「8.」這種標記。
        start = match.start(1)
        end = matches[index + 1].start(1) if index + 1 < len(matches) else len(text)
        segment = text[start:end].strip()
        if segment:
            segments.append((int(match.group(1)), segment))
    return segments or [(None, text)]


def build_claim_chunks(
    text: str,
    *,
    tokenizer: Any,
    max_content_tokens: int,
    chunk_overlap_tokens: int = 0,
) -> list[ClaimChunk]:
    """Greedily pack complete claims into tokenizer-safe text chunks.

    If claim 1 + claim 2 exceeds the token limit, claim 1 becomes one chunk and
    claim 2 is tested with claim 3. Only a single overlong claim is split inside.
    """
    if max_content_tokens <= 0:
        raise ValueError("max_content_tokens must be > 0")
    if chunk_overlap_tokens < 0:
        raise ValueError("chunk_overlap_tokens must be >= 0")
    if chunk_overlap_tokens >= max_content_tokens:
        raise ValueError("chunk_overlap_tokens must be smaller than max_content_tokens")

    # step 是 claim 內切段時每次前進的 token 數；預設 overlap=0。
    step = max_content_tokens - chunk_overlap_tokens

    # current_* 暫存目前正在貪婪合併的一組完整 claims。
    chunks: list[ClaimChunk] = []
    current_claims: list[int | None] = []
    current_texts: list[str] = []

    def token_count(chunk_text: str) -> int:
        """Count content tokens without model special tokens."""
        # 這裡不加 special tokens，因為 max_content_tokens 已經先扣掉 special tokens。
        return len(tokenizer.encode(chunk_text, add_special_tokens=False, truncation=False))

    def join_claims(claim_texts: list[str]) -> str:
        """Join complete claims with paragraph spacing preserved."""
        # 用空白段落接 claims，避免 claim 文字黏在一起影響 tokenizer 與可讀性。
        return "\n\n".join(part.strip() for part in claim_texts if part.strip())

    def flush_current() -> None:
        """Save the currently accumulated complete-claim chunk."""
        nonlocal current_claims, current_texts
        if not current_texts:
            return
        chunk_text = join_claims(current_texts)
        chunks.append(
            ClaimChunk(
                text=chunk_text,
                claim_numbers=current_claims,
                content_token_count=token_count(chunk_text),
                split_within_claim=False,
            )
        )
        current_claims = []
        current_texts = []

    for claim_number, claim_text in split_claim_segments(text):
        # 每個完整 claim 先量 token；能完整保留就不在 claim 內切。
        claim_token_ids = tokenizer.encode(claim_text, add_special_tokens=False, truncation=False)
        claim_token_count = len(claim_token_ids)

        if claim_token_count <= max_content_tokens:
            # claim 1 + claim 2 不超過上限就放同一 chunk；
            # 超過就先 flush claim 1，再從 claim 2 重新累積。
            candidate_text = join_claims([*current_texts, claim_text])
            if current_texts and token_count(candidate_text) > max_content_tokens:
                flush_current()
            current_claims.append(claim_number)
            current_texts.append(claim_text)
            continue

        flush_current()
        start = 0
        while start < claim_token_count:
            # 只有單一 claim 自己超過上限時，才在 claim 內按 token 切段。
            end = min(start + max_content_tokens, claim_token_count)
            chunk_token_ids = claim_token_ids[start:end]
            chunk_text = tokenizer.decode(
                chunk_token_ids,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=True,
            ).strip()
            chunks.append(
                ClaimChunk(
                    text=chunk_text,
                    claim_numbers=[claim_number],
                    content_token_count=len(chunk_token_ids),
                    split_within_claim=True,
                )
            )
            if end >= claim_token_count:
                break

            # 若日後設定 overlap，下一段會保留前一段尾端部分 token。
            start += step

    flush_current()
    return chunks


def pack_claim_chunks(
    claim_token_lengths: list[tuple[int | None, int]],
    *,
    max_content_tokens: int,
    step: int,
) -> list[dict[str, Any]]:
    """Legacy token-length packer kept for older tests; new code uses text chunks."""
    # 新流程使用 build_claim_chunks 產生實際 chunk text；
    # 這個函式只保留給早期 token-length 測試或回歸比對。
    chunks: list[dict[str, Any]] = []
    current_claims: list[int | None] = []
    current_len = 0

    def flush_current() -> None:
        """Save the current token-length chunk for legacy tests."""
        nonlocal current_claims, current_len
        if current_claims:
            chunks.append(
                {
                    "claim_numbers": current_claims,
                    "content_token_count": current_len,
                    "split_within_claim": False,
                }
            )
            current_claims = []
            current_len = 0

    for claim_number, token_length in claim_token_lengths:
        if token_length <= max_content_tokens:
            if current_claims and current_len + token_length > max_content_tokens:
                flush_current()
            current_claims.append(claim_number)
            current_len += token_length
            continue

        flush_current()
        start = 0
        while start < token_length:
            end = min(start + max_content_tokens, token_length)
            chunks.append(
                {
                    "claim_numbers": [claim_number],
                    "content_token_count": end - start,
                    "split_within_claim": True,
                }
            )
            if end >= token_length:
                break
            start += step

    flush_current()
    return chunks
