# @Description : 文本分块工具（按段落 → 句子滑窗，零依赖）
from __future__ import annotations

import re

# 句子结束符（中英混合）
_SENT_SPLIT = re.compile(r"(?<=[。！？!?\.])\s+|(?<=[。！？!?])(?=[^”’\"'])|\n+")
_PARA_SPLIT = re.compile(r"\n{2,}")


def chunk_text(
    text: str,
    *,
    chunk_size: int = 500,
    chunk_overlap: int = 80,
    length_function: callable = len,
) -> list[str]:
    """
    将长文本切分为重叠块。

    策略：先按段落切分 → 段落过长再按句子滑窗拼接；块之间带 overlap 字符重叠。

    Args:
        text: 原始文本
        chunk_size: 单块最大长度（默认按字符数）
        chunk_overlap: 相邻块重叠长度
        length_function: 长度度量函数，默认 len
    """
    if not text or not text.strip():
        return []

    chunk_size = max(50, int(chunk_size))
    chunk_overlap = max(0, min(int(chunk_overlap), chunk_size // 2))

    # 1. 段落级切分
    paragraphs = [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()]

    # 2. 对过长段落按句子切分后滑窗聚合
    pieces: list[str] = []
    for para in paragraphs:
        if length_function(para) <= chunk_size:
            pieces.append(para)
            continue
        sentences = [s.strip() for s in _SENT_SPLIT.split(para) if s.strip()]
        buf = ""
        for sent in sentences:
            candidate = (buf + " " + sent).strip() if buf else sent
            if length_function(candidate) <= chunk_size:
                buf = candidate
            else:
                if buf:
                    pieces.append(buf)
                # 单句仍超长 → 硬切
                if length_function(sent) > chunk_size:
                    for i in range(0, length_function(sent), chunk_size - chunk_overlap):
                        pieces.append(sent[i : i + chunk_size])
                    buf = ""
                else:
                    buf = sent
        if buf:
            pieces.append(buf)

    # 3. 全局滑窗合并（处理段落之间太短的情况，并保证 overlap）
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = (current + "\n" + piece).strip() if current else piece
        if length_function(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                chunks.append(current)
            # 用 overlap 回带尾巴
            if chunk_overlap and length_function(current) > chunk_overlap:
                tail = current[-chunk_overlap:]
                current = (tail + "\n" + piece).strip()
                # 仍超长则放弃 overlap
                if length_function(current) > chunk_size:
                    current = piece
            else:
                current = piece
    if current:
        chunks.append(current)

    return chunks
