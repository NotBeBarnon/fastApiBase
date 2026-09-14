# @Description : FastAPI 应用实例
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from ..settings import HTTP_BASE_URL
from ..version import VERSION
from .events import lifespan
from .middlewares import RequestContextMiddleware
from .routers import all_router

__all__ = ("fast_app",)


fast_app = FastAPI(
    title="FastSample",
    description="FastAPI 示例项目",
    version=VERSION,
    openapi_url=f"{HTTP_BASE_URL}/openapi.json",
    docs_url=f"{HTTP_BASE_URL}/docs",
    redoc_url=f"{HTTP_BASE_URL}/redoc",
    lifespan=lifespan,
)

fast_app.add_middleware(RequestContextMiddleware)
fast_app.include_router(all_router)


# ------------------------------------------------------------------
#  管理后台静态资源（SPA）
#  部署时由 npm run build 产出 admin/dist；开发阶段目录不存在则跳过挂载，
#  直接通过 vite dev server (http://localhost:5173/admin/) 开发即可。
# ------------------------------------------------------------------
_ADMIN_DIST = Path(__file__).resolve().parents[2] / "admin" / "dist"
if _ADMIN_DIST.is_dir():

    @fast_app.get("/admin", include_in_schema=False)
    @fast_app.get("/admin/", include_in_schema=False)
    async def serve_admin_index() -> FileResponse:
        return FileResponse(str(_ADMIN_DIST / "index.html"))

    @fast_app.get("/admin/{full_path:path}", include_in_schema=False)
    async def serve_admin_spa(full_path: str) -> FileResponse:
        file_candidate = _ADMIN_DIST / full_path
        # 静态文件（js/css/图片等）直接返回
        if full_path and file_candidate.is_file():
            return FileResponse(str(file_candidate))
        # 其它路径交给前端路由（SPA history fallback）
        return FileResponse(str(_ADMIN_DIST / "index.html"))
else:
    @fast_app.get("/admin", include_in_schema=False)
    @fast_app.get("/admin/{full_path:path}", include_in_schema=False)
    async def admin_not_built(full_path: str = "") -> dict:
        return {
            "message": "Admin UI not built. Run `cd admin && npm install && npm run build` first.",
            "hint": "Or dev via `npm run dev` in admin/ (Vite proxy to :8080).",
        }

