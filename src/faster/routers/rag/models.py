# @Description : RAG 知识库模型（文档 + 分块，归属到具体用户）
from __future__ import annotations

from tortoise import fields, models

from . import app_name


class KnowledgeDoc(models.Model):
    """知识库文档"""

    id = fields.IntField(pk=True, description="文档 ID")
    title = fields.CharField(max_length=200, description="文档标题")
    content = fields.TextField(description="文档原始内容")
    summary = fields.CharField(max_length=500, default="", description="简短摘要（可选）")
    source = fields.CharField(max_length=500, default="", description="来源（URL/文件名等，可选）")
    chunk_count = fields.IntField(default=0, description="分块数量")
    owner = fields.ForeignKeyField(
        "users.User",
        related_name="knowledge_docs",
        on_delete=fields.OnDelete.CASCADE,
        description="所属用户",
    )
    is_active = fields.BooleanField(default=True, description="是否启用")
    created_at = fields.DatetimeField(auto_now_add=True, description="创建时间")
    modified_at = fields.DatetimeField(auto_now=True, description="修改时间")

    class Meta:
        table = f"{app_name}_document"
        table_description = "知识库文档表"
        indexes = [("owner_id", "is_active"), ("title",)]


class KnowledgeChunk(models.Model):
    """文档分块（一个文档对应多个 chunk，向量以 JSON 存储）"""

    id = fields.IntField(pk=True, description="分块 ID")
    doc = fields.ForeignKeyField(
        "rag.KnowledgeDoc",
        related_name="chunks",
        on_delete=fields.OnDelete.CASCADE,
        description="所属文档",
    )
    chunk_index = fields.IntField(description="块序号")
    content = fields.TextField(description="分块文本")
    embedding = fields.JSONField(default=list, description="embedding 向量（JSON list[float]）")
    created_at = fields.DatetimeField(auto_now_add=True, description="创建时间")

    class Meta:
        table = f"{app_name}_chunk"
        table_description = "知识库文档分块表"
        indexes = [("doc_id", "chunk_index")]
