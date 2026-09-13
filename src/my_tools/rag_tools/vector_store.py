# @Description : 向量存储抽象 + 内存实现（纯 Python 余弦相似度，零依赖）
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SearchHit:
    """单次检索命中"""

    chunk_id: int
    doc_id: int
    content: str
    score: float
    metadata: dict = field(default_factory=dict)


class VectorStore(ABC):
    """向量存储抽象接口，方便后续替换为 Milvus / PGVector / FAISS。"""

    @abstractmethod
    async def add(self, chunk_id: int, doc_id: int, embedding: list[float], content: str, metadata: dict | None = None) -> None:
        """添加一个 chunk 的向量"""

    @abstractmethod
    async def delete_by_doc(self, doc_id: int) -> int:
        """删除某文档下的所有 chunk，返回删除条数"""

    @abstractmethod
    async def delete(self, chunk_id: int) -> bool:
        """删除单个 chunk"""

    @abstractmethod
    async def search(
        self,
        query_vec: list[float],
        *,
        top_k: int = 5,
        owner_ids: list[int] | None = None,
        doc_ids: list[int] | None = None,
        score_threshold: float = 0.0,
    ) -> list[SearchHit]:
        """按向量检索 top-k 命中"""

    @abstractmethod
    async def size(self) -> int:
        """当前索引中的 chunk 数"""


# ---------------------------------------------------------------------------
#  内存实现
# ---------------------------------------------------------------------------
@dataclass
class _Chunk:
    chunk_id: int
    doc_id: int
    owner_id: int
    embedding: list[float]
    content: str
    metadata: dict


class InMemoryVectorStore(VectorStore):
    """
    基于内存 dict + 余弦相似度的简单向量索引。
    适合万级 chunk 以内的 demo / 中小规模场景。
    """

    def __init__(self) -> None:
        self._chunks: dict[int, _Chunk] = {}

    async def add(
        self,
        chunk_id: int,
        doc_id: int,
        embedding: list[float],
        content: str,
        metadata: dict | None = None,
    ) -> None:
        owner_id = int((metadata or {}).get("owner_id", 0))
        self._chunks[chunk_id] = _Chunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            owner_id=owner_id,
            embedding=embedding,
            content=content,
            metadata=metadata or {},
        )

    async def delete_by_doc(self, doc_id: int) -> int:
        to_remove = [cid for cid, c in self._chunks.items() if c.doc_id == doc_id]
        for cid in to_remove:
            del self._chunks[cid]
        return len(to_remove)

    async def delete(self, chunk_id: int) -> bool:
        return self._chunks.pop(chunk_id, None) is not None

    async def search(
        self,
        query_vec: list[float],
        *,
        top_k: int = 5,
        owner_ids: list[int] | None = None,
        doc_ids: list[int] | None = None,
        score_threshold: float = 0.0,
    ) -> list[SearchHit]:
        top_k = max(1, min(int(top_k), 50))
        owner_set = set(owner_ids) if owner_ids else None
        doc_set = set(doc_ids) if doc_ids else None

        candidates: list[SearchHit] = []
        for c in self._chunks.values():
            if owner_set is not None and c.owner_id not in owner_set:
                continue
            if doc_set is not None and c.doc_id not in doc_set:
                continue
            score = self._cosine(query_vec, c.embedding)
            if score < score_threshold:
                continue
            candidates.append(
                SearchHit(
                    chunk_id=c.chunk_id,
                    doc_id=c.doc_id,
                    content=c.content,
                    score=score,
                    metadata=c.metadata,
                )
            )

        candidates.sort(key=lambda h: h.score, reverse=True)
        return candidates[:top_k]

    async def size(self) -> int:
        return len(self._chunks)

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        # 维度不一致时，按小维度截断
        n = min(len(a), len(b))
        if n == 0:
            return 0.0
        dot = 0.0
        na = 0.0
        nb = 0.0
        for i in range(n):
            ai = a[i]
            bi = b[i]
            dot += ai * bi
            na += ai * ai
            nb += bi * bi
        denom = math.sqrt(na) * math.sqrt(nb)
        if denom == 0:
            return 0.0
        return dot / denom
