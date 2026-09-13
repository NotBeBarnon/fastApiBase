# @Description : RAG 编排服务：文档入库 / 检索 / 问答（普通 + 流式）
from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from ..llm_tools import LLMMessage
from .chunking import chunk_text
from .embeddings import EmbeddingService
from .vector_store import SearchHit, VectorStore

if TYPE_CHECKING:
    from ..llm_tools import LLMGateway


DEFAULT_SYSTEM_PROMPT = (
    "你是一个基于知识库回答问题的助手。请严格使用下面「参考资料」中的内容回答用户问题，"
    "若参考资料不足以回答，请如实说明，不要编造。回答请使用与用户提问相同的语言，"
    "并在回答末尾以「参考」为标题列出用到的文档片段编号。\n\n"
    "参考资料：\n{context}"
)


@dataclass
class RagServiceConfig:
    chunk_size: int = 500
    chunk_overlap: int = 80
    default_top_k: int = 4
    max_top_k: int = 10
    score_threshold: float = 0.0
    default_system_prompt: str = DEFAULT_SYSTEM_PROMPT
    embedding_provider: str | None = None
    embedding_model: str | None = None
    chat_provider: str | None = None
    chat_model: str | None = None
    chat_temperature: float = 0.3
    chat_max_tokens: int | None = None


@dataclass
class RetrievalResult:
    hits: list[SearchHit]
    fallback_embedding: bool


@dataclass
class ChatResult:
    answer: str
    hits: list[SearchHit]
    model: str = ""
    provider: str = ""
    latency_ms: float = 0.0
    fallback_embedding: bool = False
    usage: dict = field(default_factory=dict)


class RagService:
    """
    RAG 编排服务：
    - index_document:       文档 → 分块 → embedding → 入库（DB + 向量索引）
    - remove_document:      删除文档及索引
    - retrieve:             向量检索
    - chat:                 检索 + LLM 问答
    - stream_chat:          检索 + LLM 流式问答（SSE 对接）
    """

    def __init__(
        self,
        llm_gateway: "LLMGateway | None",
        vector_store: VectorStore,
        config: RagServiceConfig | None = None,
    ) -> None:
        self._llm = llm_gateway
        self._store = vector_store
        self._config = config or RagServiceConfig()
        self._embed = EmbeddingService(
            llm_gateway,
            provider=self._config.embedding_provider,
            model=self._config.embedding_model,
        )

    @property
    def vector_store(self) -> VectorStore:
        return self._store

    @property
    def embedding(self) -> EmbeddingService:
        return self._embed

    @property
    def has_llm(self) -> bool:
        return self._llm is not None and bool(getattr(self._llm, "_providers", None))

    @property
    def fallback_embedding(self) -> bool:
        return self._embed.fallback_mode

    # ------------------------------------------------------------------
    #  索引
    # ------------------------------------------------------------------
    async def index_document(
        self,
        *,
        doc_id: int,
        owner_id: int,
        title: str,
        content: str,
        chunk_model_save: callable,
    ) -> int:
        """
        对文档做分块、embedding、并通过 chunk_model_save(chunk_index, content, embedding) 持久化到 DB，
        同时加入向量索引。返回 chunk 数量。
        """
        cfg = self._config
        body = (title + "\n\n" + content).strip() if title else content
        chunks = chunk_text(body, chunk_size=cfg.chunk_size, chunk_overlap=cfg.chunk_overlap)
        if not chunks:
            chunks = [body or ""]

        vectors = await self._embed.embed(chunks)
        # 持久化
        for idx, (chunk_text_val, vec) in enumerate(zip(chunks, vectors)):
            chunk_obj = await chunk_model_save(
                chunk_index=idx,
                content=chunk_text_val,
                embedding=vec,
            )
            await self._store.add(
                chunk_id=chunk_obj.id,
                doc_id=doc_id,
                embedding=vec,
                content=chunk_text_val,
                metadata={
                    "owner_id": owner_id,
                    "title": title,
                    "chunk_index": idx,
                },
            )
        logger.debug(f"RAG indexed doc {doc_id}: {len(chunks)} chunks, fallback={self._embed.fallback_mode}")
        return len(chunks)

    async def remove_document(self, doc_id: int, delete_chunks: callable) -> int:
        await delete_chunks(doc_id)
        return await self._store.delete_by_doc(doc_id)

    async def add_chunk_to_index(
        self,
        chunk_id: int,
        doc_id: int,
        owner_id: int,
        content: str,
        embedding: list[float],
        title: str = "",
        extra_meta: dict | None = None,
    ) -> None:
        meta = {"owner_id": owner_id, "title": title}
        if extra_meta:
            meta.update(extra_meta)
        await self._store.add(chunk_id, doc_id, embedding, content, meta)

    # ------------------------------------------------------------------
    #  检索
    # ------------------------------------------------------------------
    async def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        owner_ids: list[int] | None = None,
        doc_ids: list[int] | None = None,
        score_threshold: float | None = None,
    ) -> RetrievalResult:
        cfg = self._config
        k = top_k or cfg.default_top_k
        k = max(1, min(int(k), cfg.max_top_k))
        thr = score_threshold if score_threshold is not None else cfg.score_threshold

        q_vec = (await self._embed.embed([query]))[0]
        hits = await self._store.search(
            q_vec,
            top_k=k,
            owner_ids=owner_ids,
            doc_ids=doc_ids,
            score_threshold=thr,
        )
        return RetrievalResult(hits=hits, fallback_embedding=self._embed.fallback_mode)

    # ------------------------------------------------------------------
    #  问答
    # ------------------------------------------------------------------
    async def chat(
        self,
        query: str,
        *,
        top_k: int | None = None,
        owner_ids: list[int] | None = None,
        doc_ids: list[int] | None = None,
        system_prompt: str | None = None,
        chat_history: list[dict] | None = None,
        temperature: float | None = None,
    ) -> ChatResult:
        """检索 → 拼装 prompt → LLM 同步回答。"""
        import time

        if not self.has_llm:
            raise RuntimeError("No LLM providers configured for RAG chat")

        retrieval = await self.retrieve(
            query, top_k=top_k, owner_ids=owner_ids, doc_ids=doc_ids
        )
        context = self._format_context(retrieval.hits)
        messages = self._build_messages(query, context, system_prompt, chat_history)

        temp = temperature if temperature is not None else self._config.chat_temperature
        start = time.time()
        resp = await self._llm.chat(
            messages,
            provider=self._config.chat_provider,
            model=self._config.chat_model,
            temperature=temp,
            max_tokens=self._config.chat_max_tokens,
        )
        latency = (time.time() - start) * 1000

        return ChatResult(
            answer=resp.content,
            hits=retrieval.hits,
            model=resp.model,
            provider=resp.provider,
            latency_ms=round(latency, 2),
            fallback_embedding=retrieval.fallback_embedding,
            usage={
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            },
        )

    async def stream_chat(
        self,
        query: str,
        *,
        top_k: int | None = None,
        owner_ids: list[int] | None = None,
        doc_ids: list[int] | None = None,
        system_prompt: str | None = None,
        chat_history: list[dict] | None = None,
        temperature: float | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        流式问答生成器，产出以下形态的 dict：
            {"type": "retrieval", "hits": [...]}            # 一次性：检索结果
            {"type": "delta", "content": "..."}              # 多次：LLM 文本增量
            {"type": "done", "meta": {...}}                  # 一次性：结束元数据
            {"type": "error", "error": "..."}                # 出错
        """
        import time

        if not self.has_llm:
            yield {"type": "error", "error": "No LLM providers configured for RAG chat"}
            return

        try:
            retrieval = await self.retrieve(
                query, top_k=top_k, owner_ids=owner_ids, doc_ids=doc_ids
            )
            yield {
                "type": "retrieval",
                "hits": [
                    {
                        "chunk_id": h.chunk_id,
                        "doc_id": h.doc_id,
                        "score": round(h.score, 4),
                        "title": h.metadata.get("title", ""),
                        "snippet": h.content[:120],
                    }
                    for h in retrieval.hits
                ],
                "fallback_embedding": retrieval.fallback_embedding,
            }

            context = self._format_context(retrieval.hits)
            messages = self._build_messages(query, context, system_prompt, chat_history)

            temp = temperature if temperature is not None else self._config.chat_temperature
            start = time.time()
            async for chunk in self._llm.stream_chat(
                messages,
                provider=self._config.chat_provider,
                model=self._config.chat_model,
                temperature=temp,
                max_tokens=self._config.chat_max_tokens,
            ):
                yield {"type": "delta", "content": chunk}

            yield {
                "type": "done",
                "meta": {
                    "latency_ms": round((time.time() - start) * 1000, 2),
                    "hits_count": len(retrieval.hits),
                    "fallback_embedding": retrieval.fallback_embedding,
                },
            }
        except Exception as exc:
            logger.exception("RAG stream_chat error")
            yield {"type": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    #  内部辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _format_context(hits: list[SearchHit]) -> str:
        if not hits:
            return "（未检索到相关内容）"
        blocks = []
        for i, h in enumerate(hits, 1):
            title = h.metadata.get("title") or f"文档{h.doc_id}"
            blocks.append(f"[{i}] 《{title}》(doc_id={h.doc_id}, chunk={h.chunk_id}, score={h.score:.3f})\n{h.content}")
        return "\n\n".join(blocks)

    def _build_messages(
        self,
        query: str,
        context: str,
        system_prompt: str | None,
        chat_history: list[dict] | None,
    ) -> list[LLMMessage]:
        sys_tpl = system_prompt or self._config.default_system_prompt
        messages: list[LLMMessage] = [LLMMessage(role="system", content=sys_tpl.format(context=context))]
        if chat_history:
            for m in chat_history:
                role = m.get("role", "user")
                content = m.get("content", "")
                if role in {"user", "assistant", "system"} and content:
                    messages.append(LLMMessage(role=role, content=content))
        messages.append(LLMMessage(role="user", content=query))
        return messages
