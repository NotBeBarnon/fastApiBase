# @Description : 安全能力演示路由（JWT 签发 / API Key / 限流）
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.my_tools.security_tools import rate_limit, require_api_key, verify_jwt
from src.my_tools.security_tools.auth import APIKeyInfo
from src.my_tools.security_tools.jwt_tools import create_token
from src.settings import SECURITY_CONFIG

__all__ = ("security_router",)

security_router = APIRouter(prefix="/security", tags=["security"])


class TokenRequest(BaseModel):
    subject: str = Field(..., min_length=1, max_length=64, description="令牌主体（如用户名）")
    role: str = Field("user", description="角色标签")
    expires_seconds: int = Field(3600, ge=1, le=86400, description="有效期（秒）")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


@security_router.post("/token", summary="签发 JWT（演示用）", response_model=TokenResponse)
async def issue_token(body: TokenRequest) -> TokenResponse:
    """演示端点：生产环境请改为登录接口（校验用户名密码后）再签发。"""
    token = create_token(
        {"sub": body.subject, "role": body.role},
        SECURITY_CONFIG["jwt_secret"],
        algorithm=SECURITY_CONFIG["jwt_algorithm"],
        expires_seconds=body.expires_seconds,
    )
    return TokenResponse(access_token=token, expires_in=body.expires_seconds)


@security_router.get("/me", summary="JWT 鉴权示例（Authorization: Bearer <token>）")
async def who_am_i(payload: dict = Depends(verify_jwt)) -> dict:
    return {"subject": payload.get("sub"), "role": payload.get("role"), "issued_at": payload.get("iat")}


@security_router.get("/admin", summary="API Key 鉴权示例（X-API-Key，要求 admin 角色）")
async def admin_only(info: APIKeyInfo = Depends(require_api_key("admin"))) -> dict:
    return {"message": f"Welcome, {info.name} (role={info.role})"}


@security_router.get(
    "/limited",
    summary="限流示例（5 次 / 10 秒，超出返回 429 + Retry-After）",
    dependencies=[Depends(rate_limit(times=5, window_seconds=10))],
)
async def limited_endpoint() -> dict:
    return {"message": "request accepted"}
