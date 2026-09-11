# @Description : LLM 网关示例路由
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.my_tools.llm_tools import LLMMessage
from src.my_tools.sse_tools import SSEStream

llm_router = APIRouter(prefix="/llm", tags=["llm"])


class ChatRequest(BaseModel):
    message: str = Field(..., description="用户消息")
    system_prompt: str = "你是一个有帮助的助手。"
    provider: str | None = None
    model: str | None = None
    temperature: float = 0.7
    stream: bool = True


@llm_router.get("/stats")
async def llm_stats(request: Request):
    """获取 LLM 网关统计信息（调用次数、费用估算等）"""
    llm = request.app.state.llm
    return llm.get_stats()


@llm_router.get("/health")
async def llm_health(request: Request):
    """LLM 网关健康检查（各 provider 配置状态）"""
    llm = request.app.state.llm
    return llm.health_check()


@llm_router.post("/chat")
async def llm_chat(request: Request, body: ChatRequest):
    """
    对话接口（支持流式和非流式）。

    当 body.stream=True 时，返回 SSE 流式响应；
    当 body.stream=False 时，返回完整 JSON 响应。
    """
    llm = request.app.state.llm
    if not llm._providers:
        raise HTTPException(status_code=503, detail="No LLM providers configured")

    messages = [
        LLMMessage(role="system", content=body.system_prompt),
        LLMMessage(role="user", content=body.message),
    ]

    if body.stream:
        # 流式响应
        async def gen():
            try:
                async for chunk in llm.stream_chat(
                    messages,
                    provider=body.provider,
                    model=body.model,
                    temperature=body.temperature,
                ):
                    yield {"content": chunk, "role": "assistant"}
            except Exception as exc:
                yield {"error": str(exc)}

        return SSEStream.from_generator(gen(), event_name="delta")
    else:
        # 非流式响应
        resp = await llm.chat(
            messages,
            provider=body.provider,
            model=body.model,
            temperature=body.temperature,
        )
        return {
            "content": resp.content,
            "model": resp.model,
            "provider": resp.provider,
            "usage": {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            },
            "latency_ms": round(resp.latency_ms, 2),
            "finish_reason": resp.finish_reason,
        }
