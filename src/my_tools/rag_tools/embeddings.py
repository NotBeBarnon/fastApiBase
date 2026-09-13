# @Description : Embedding 服务：优先走 LLM Gateway，无 provider 时降级为确定性哈希伪向量
from __future__ import annotations

import hashlib
import math
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm_tools import LLMGateway

# 伪向量维度（固定 256 维）
_HASH_DIM = 256
# char n-gram 提取（中文按字、英文按 2-gram）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _pseudo_embedding(text: str, dim: int = _HASH_DIM) -> list[float]:
    """
    基于 char n-gram + MD5 的确定性伪 embedding。
    - 中文按单字、英文按 2-gram 生成 token
    - 每个 token 通过 MD5 映射到 dim 维空间的一个随机方向
    - 叠加后 L2 归一化
    - 相同文本永远产出相同向量；语义相似的文本因 token 重叠会有一定余弦相似度。
    这是为了在没有配置 embedding provider 时 demo 仍可运行，不可用于生产检索质量对比。
    """
    text = text.lower().strip()
    vec = [0.0] * dim

    tokens: list[str] = []
    # 中文单字
    for ch in _CJK_RE.findall(text):
        tokens.append(ch)
    # 英文 / 数字 2-gram（不足 2 个字符按单 token）
    for word in _ASCII_TOKEN_RE.findall(text):
        if len(word) <= 2:
            tokens.append(word)
        else:
            for i in range(len(word) - 1):
                tokens.append(word[i : i + 2])

    if not tokens:
        tokens = [text] if text else ["__empty__"]

    for tok in tokens:
        h = hashlib.md5(tok.encode("utf-8")).digest()
        # 每 4 字节映射为一个有符号 32 位整数，重复使用直到凑够 dim
        for i in range(dim):
            j = (i * 4) % len(h)
            val = int.from_bytes(h[j : j + 4], "little", signed=True) / 2**31
            vec[i] += val

    # L2 normalize
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class EmbeddingService:
    """
    Embedding 门面：
    - 优先通过 LLM Gateway 调用真实 embedding API；
    - 若网关无可用 provider，降级为伪向量（打 warning）。
    """

    def __init__(self, llm_gateway: "LLMGateway | None" = None, *, provider: str | None = None, model: str | None = None):
        self._llm = llm_gateway
        self._provider = provider
        self._model = model
        self._fallback_mode = False

    @property
    def fallback_mode(self) -> bool:
        """是否处于伪向量降级模式（无真实 embedding provider）"""
        return self._fallback_mode

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量获取 embedding，确保返回非空归一化向量列表。"""
        if not texts:
            return []

        # 尝试真实 LLM
        if self._llm is not None and getattr(self._llm, "_providers", None):
            try:
                result = await self._llm.embeddings(texts, provider=self._provider, model=self._model)
                # 校验维度一致且非零
                if result and len(result) == len(texts) and all(len(v) > 0 for v in result):
                    self._fallback_mode = False
                    return [self._normalize(v) for v in result]
            except Exception:
                # 失败则降级到伪向量
                pass

        self._fallback_mode = True
        return [_pseudo_embedding(t) for t in texts]

    async def embed_one(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result[0]

    @staticmethod
    def _normalize(v: list[float]) -> list[float]:
        norm = math.sqrt(sum(x * x for x in v))
        if norm == 0:
            return [0.0] * len(v)
        return [x / norm for x in v]
