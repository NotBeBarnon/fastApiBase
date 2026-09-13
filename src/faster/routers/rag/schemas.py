# @Description : RAG 知识库 Schemas
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="文档标题")
    content: str = Field(..., min_length=2, description="文档正文")
    summary: str = Field("", max_length=500, description="可选摘要")
    source: str = Field("", max_length=500, description="可选来源")


class DocUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    content: str | None = Field(None, min_length=2)
    summary: str | None = Field(None, max_length=500)
    source: str | None = Field(None, max_length=500)
    is_active: bool | None = None


class DocOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    summary: str
    source: str
    chunk_count: int
    owner_id: int
    is_active: bool
    created_at: datetime
    modified_at: datetime


class DocListOut(DocOut):
    """列表项：不带 content 以减小响应体"""


class DocDetailOut(DocOut):
    content: str


# ---------- 检索 / 问答 ----------
class SearchHitOut(BaseModel):
    chunk_id: int
    doc_id: int
    score: float
    title: str = ""
    content: str


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000, description="检索问题")
    top_k: int = Field(4, ge=1, le=10, description="返回命中条数")
    doc_ids: list[int] = Field(default_factory=list, description="限定在指定文档范围内检索（可空）")
    score_threshold: float = Field(0.0, ge=-1.0, le=1.0, description="最低相似度阈值")


class SearchResponse(BaseModel):
    hits: list[SearchHitOut]
    fallback_embedding: bool = False
    latency_ms: float = 0.0


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str = Field(..., min_length=1)


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(4, ge=1, le=10)
    doc_ids: list[int] = Field(default_factory=list)
    system_prompt: str | None = Field(None, max_length=4000)
    chat_history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    stream: bool = Field(False, description="true 走 SSE 流式；false 返回完整 JSON")
    temperature: float | None = Field(None, ge=0.0, le=2.0)


class ChatResponse(BaseModel):
    answer: str
    hits: list[SearchHitOut]
    model: str = ""
    provider: str = ""
    latency_ms: float = 0.0
    fallback_embedding: bool = False
    usage: dict = Field(default_factory=dict)


class StatsOut(BaseModel):
    total_docs: int
    total_chunks: int
    index_size: int
    has_llm: bool
    fallback_embedding: bool


__all__ = (
    "DocCreate",
    "DocUpdate",
    "DocOut",
    "DocListOut",
    "DocDetailOut",
    "SearchHitOut",
    "SearchRequest",
    "SearchResponse",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "StatsOut",
)
