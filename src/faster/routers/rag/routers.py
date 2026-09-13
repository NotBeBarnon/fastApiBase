# @Description : RAG 知识库路由：文档 CRUD / 检索 / 问答（同步 + SSE 流式）
from __future__ import annotations

import time
from collections.abc import Coroutine
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from loguru import logger
from tortoise.exceptions import ConfigurationError, DoesNotExist, IntegrityError, OperationalError
from tortoise.queryset import QuerySet
from tortoise.transactions import in_transaction

from src.my_tools.rag_tools import RagService
from src.my_tools.security_tools import verify_jwt
from src.my_tools.security_tools.auth import require_jwt_role
from src.my_tools.sse_tools import SSEEvent, SSEStream
from src.my_tools.tortoise_tools.pagination import OrderByParams, PageResult, PaginationParams, make_order_by_params

from .models import KnowledgeChunk, KnowledgeDoc
from .schemas import (
    ChatRequest,
    ChatResponse,
    DocCreate,
    DocDetailOut,
    DocListOut,
    DocUpdate,
    SearchHitOut,
    SearchRequest,
    SearchResponse,
    StatsOut,
)

rag_router = APIRouter(prefix="/rag", tags=["rag"])

_DB_UNAVAILABLE = (OperationalError, ConfigurationError)
_BUSINESS_ERRORS = (IntegrityError, DoesNotExist)

_ALLOWED_SORT_FIELDS = {"id", "title", "chunk_count", "created_at", "modified_at"}
_DocOrderBy = make_order_by_params(allowed_fields=_ALLOWED_SORT_FIELDS, default_sort="-created_at")


# ------------------------------------------------------------------
#  通用辅助
# ------------------------------------------------------------------
async def _safe(coro: Coroutine) -> Any:
    try:
        return await coro
    except _BUSINESS_ERRORS:
        raise
    except _DB_UNAVAILABLE as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"Database unavailable: {exc.__class__.__name__}"
        ) from exc
    except RuntimeError as exc:
        if "TortoiseContext" not in str(exc):
            raise
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"Database unavailable: {exc.__class__.__name__}"
        ) from exc


def _uid(payload: dict) -> int:
    uid = payload.get("uid")
    if not isinstance(uid, int):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token: uid missing")
    return uid


def _is_admin(payload: dict) -> bool:
    return payload.get("role") == "admin"


def _scoped_qs(payload: dict) -> QuerySet[KnowledgeDoc]:
    qs = KnowledgeDoc.all()
    if not _is_admin(payload):
        qs = qs.filter(owner_id=_uid(payload))
    return qs


async def _get_doc_or_404(payload: dict, doc_id: int) -> KnowledgeDoc:
    qs = _scoped_qs(payload)
    try:
        return await _safe(qs.get(id=doc_id))
    except DoesNotExist as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Doc {doc_id} not found") from exc


def _get_rag(request: Request) -> RagService:
    svc: RagService | None = getattr(request.app.state, "rag", None)
    if svc is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "RAG service not initialized")
    return svc


def _hit_out(hit) -> SearchHitOut:
    return SearchHitOut(
        chunk_id=hit.chunk_id,
        doc_id=hit.doc_id,
        score=round(hit.score, 4),
        title=hit.metadata.get("title", "") if hasattr(hit, "metadata") else "",
        content=hit.content,
    )


# ------------------------------------------------------------------
#  文档管理
# ------------------------------------------------------------------
@rag_router.get(
    "/docs",
    summary="分页查询知识库文档（Bearer JWT）",
    response_model=PageResult[DocListOut],
)
async def list_docs(
    page: PaginationParams = Depends(),
    sort: OrderByParams = Depends(_DocOrderBy),
    title_like: str | None = Query(None, min_length=1, max_length=100, description="按标题模糊搜索"),
    is_active: bool | None = Query(None, description="按启用状态过滤"),
    payload: dict = Depends(verify_jwt),
) -> PageResult[DocListOut]:
    qs = _scoped_qs(payload)
    if title_like:
        qs = qs.filter(title__icontains=title_like)
    if is_active is not None:
        qs = qs.filter(is_active=is_active)
    total = await _safe(qs.count())
    qs = sort.apply(qs)
    items = await _safe(qs.offset(page.offset).limit(page.limit))
    return PageResult(
        total=total,
        page=page.page,
        page_size=page.page_size,
        items=[DocListOut.model_validate(d, from_attributes=True) for d in items],
    )


@rag_router.get(
    "/docs/{doc_id}",
    summary="获取文档详情（含全文）",
    response_model=DocDetailOut,
    responses={404: {"description": "Doc not found"}},
)
async def get_doc(doc_id: int, payload: dict = Depends(verify_jwt)) -> DocDetailOut:
    doc = await _get_doc_or_404(payload, doc_id)
    return DocDetailOut.model_validate(doc, from_attributes=True)


@rag_router.post(
    "/docs",
    summary="创建文档并自动分块+向量化入库（Bearer JWT）",
    response_model=DocDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_doc(body: DocCreate, request: Request, payload: dict = Depends(verify_jwt)) -> DocDetailOut:
    svc = _get_rag(request)
    uid = _uid(payload)

    # 事务内：创建 doc + chunks，成功后再同步到内存向量索引
    try:
        async with in_transaction():
            doc = await KnowledgeDoc.create(
                title=body.title,
                content=body.content,
                summary=body.summary,
                source=body.source,
                owner_id=uid,
                is_active=True,
                chunk_count=0,
            )

            async def _save_chunk(*, chunk_index: int, content: str, embedding: list[float]) -> KnowledgeChunk:
                return await KnowledgeChunk.create(
                    doc=doc,
                    chunk_index=chunk_index,
                    content=content,
                    embedding=embedding,
                )

            chunk_cnt = await svc.index_document(
                doc_id=doc.id,
                owner_id=uid,
                title=body.title,
                content=body.content,
                chunk_model_save=_save_chunk,
            )
            doc.chunk_count = chunk_cnt
            await doc.save(update_fields=["chunk_count", "modified_at"])
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Doc creation conflict: {exc}") from exc

    logger.bind(username=payload.get("sub"), doc_id=doc.id).info(f"rag doc created: {doc.title}, chunks={doc.chunk_count}")
    return DocDetailOut.model_validate(doc, from_attributes=True)


@rag_router.put(
    "/docs/{doc_id}",
    summary="更新文档（内容变更会重新分块+向量化）",
    response_model=DocDetailOut,
)
async def update_doc(
    doc_id: int,
    body: DocUpdate,
    request: Request,
    payload: dict = Depends(verify_jwt),
) -> DocDetailOut:
    doc = await _get_doc_or_404(payload, doc_id)
    svc = _get_rag(request)
    update_data = body.model_dump(exclude_unset=True)
    content_changed = "content" in update_data or "title" in update_data

    try:
        async with in_transaction():
            # 基础字段更新
            for key, val in update_data.items():
                setattr(doc, key, val)

            if content_changed:
                # 删旧 chunks（DB + 索引）
                await KnowledgeChunk.filter(doc_id=doc.id).delete()
                await svc.vector_store.delete_by_doc(doc.id)

                async def _save_chunk(*, chunk_index: int, content: str, embedding: list[float]) -> KnowledgeChunk:
                    return await KnowledgeChunk.create(
                        doc=doc,
                        chunk_index=chunk_index,
                        content=content,
                        embedding=embedding,
                    )

                new_title = doc.title
                new_content = doc.content
                chunk_cnt = await svc.index_document(
                    doc_id=doc.id,
                    owner_id=doc.owner_id,
                    title=new_title,
                    content=new_content,
                    chunk_model_save=_save_chunk,
                )
                doc.chunk_count = chunk_cnt
                await doc.save(update_fields=list(update_data.keys()) + ["chunk_count", "modified_at"])
            else:
                await doc.save(update_fields=list(update_data.keys()) + ["modified_at"])
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Doc update conflict: {exc}") from exc

    logger.bind(username=payload.get("sub"), doc_id=doc.id).info(f"rag doc updated: {doc.title}")
    return DocDetailOut.model_validate(doc, from_attributes=True)


@rag_router.delete(
    "/docs/{doc_id}",
    summary="删除文档",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_doc(doc_id: int, request: Request, payload: dict = Depends(verify_jwt)) -> Response:
    doc = await _get_doc_or_404(payload, doc_id)
    svc = _get_rag(request)

    async def _delete_chunks(_id: int):
        await KnowledgeChunk.filter(doc_id=_id).delete()

    await svc.remove_document(doc.id, _delete_chunks)
    await _safe(doc.delete())
    logger.bind(username=payload.get("sub"), doc_id=doc_id).info(f"rag doc deleted: {doc_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------
#  检索 / 问答
# ------------------------------------------------------------------
@rag_router.post(
    "/search",
    summary="向量检索（无 LLM 也能工作，降级为哈希伪向量）",
    response_model=SearchResponse,
)
async def rag_search(body: SearchRequest, request: Request, payload: dict = Depends(verify_jwt)) -> SearchResponse:
    svc = _get_rag(request)
    uid = _uid(payload)
    owner_ids = None if _is_admin(payload) else [uid]
    doc_ids = body.doc_ids or None
    start = time.time()
    result = await svc.retrieve(
        body.query,
        top_k=body.top_k,
        owner_ids=owner_ids,
        doc_ids=doc_ids,
        score_threshold=body.score_threshold,
    )
    return SearchResponse(
        hits=[_hit_out(h) for h in result.hits],
        fallback_embedding=result.fallback_embedding,
        latency_ms=round((time.time() - start) * 1000, 2),
    )


@rag_router.post(
    "/chat",
    summary="知识库问答（stream=false 返回 JSON；stream=true 返回 SSE 流式）",
)
async def rag_chat(body: ChatRequest, request: Request, payload: dict = Depends(verify_jwt)):
    svc = _get_rag(request)
    uid = _uid(payload)
    owner_ids = None if _is_admin(payload) else [uid]
    doc_ids = body.doc_ids or None

    if not svc.has_llm:
        raise HTTPException(status_code=503, detail="No LLM providers configured for RAG chat")

    chat_kwargs: dict[str, Any] = {
        "query": body.query,
        "top_k": body.top_k,
        "owner_ids": owner_ids,
        "doc_ids": doc_ids,
        "system_prompt": body.system_prompt,
        "chat_history": [m.model_dump() for m in body.chat_history],
    }
    if body.temperature is not None:
        chat_kwargs["temperature"] = body.temperature

    if body.stream:
        async def gen():
            async for evt in svc.stream_chat(**chat_kwargs):
                yield SSEEvent(data=evt, event=evt.get("type", "message"))

        return SSEStream.from_generator(gen(), event_name="message")

    start = time.time()
    # 注意：chat() 内部会重新计时 LLM 调用，这里给出端到端总时长
    result = await svc.chat(**chat_kwargs)
    return ChatResponse(
        answer=result.answer,
        hits=[_hit_out(h) for h in result.hits],
        model=result.model,
        provider=result.provider,
        latency_ms=round((time.time() - start) * 1000, 2),
        fallback_embedding=result.fallback_embedding,
        usage=result.usage,
    )


# ------------------------------------------------------------------
#  Stats
# ------------------------------------------------------------------
@rag_router.get("/stats", summary="RAG 模块统计")
async def rag_stats(request: Request, payload: dict = Depends(verify_jwt)) -> StatsOut:
    svc = _get_rag(request)
    uid = _uid(payload)
    qs = KnowledgeDoc.all()
    if not _is_admin(payload):
        qs = qs.filter(owner_id=uid)
    total_docs = await _safe(qs.count())
    if _is_admin(payload):
        total_chunks = await _safe(KnowledgeChunk.all().count())
    else:
        total_chunks = await _safe(KnowledgeChunk.filter(doc__owner_id=uid).count())
    return StatsOut(
        total_docs=total_docs,
        total_chunks=total_chunks,
        index_size=await svc.vector_store.size(),
        has_llm=svc.has_llm,
        fallback_embedding=svc.fallback_embedding,
    )


# ------------------------------------------------------------------
#  管理员端点
# ------------------------------------------------------------------
@rag_router.get(
    "/admin/docs",
    summary="管理员：全量文档列表",
    response_model=PageResult[DocListOut],
    dependencies=[Depends(require_jwt_role("admin"))],
)
async def admin_list_docs(
    page: PaginationParams = Depends(),
    sort: OrderByParams = Depends(_DocOrderBy),
    title_like: str | None = Query(None, min_length=1, max_length=100),
    is_active: bool | None = Query(None),
    owner_id: int | None = Query(None),
) -> PageResult[DocListOut]:
    qs = KnowledgeDoc.all()
    if title_like:
        qs = qs.filter(title__icontains=title_like)
    if is_active is not None:
        qs = qs.filter(is_active=is_active)
    if owner_id is not None:
        qs = qs.filter(owner_id=owner_id)
    total = await _safe(qs.count())
    qs = sort.apply(qs)
    items = await _safe(qs.offset(page.offset).limit(page.limit))
    return PageResult(
        total=total,
        page=page.page,
        page_size=page.page_size,
        items=[DocListOut.model_validate(d, from_attributes=True) for d in items],
    )
