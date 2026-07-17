from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.routes import evidence, extract, health, ipinfo, screenshot, search, summary
from app.utils.errors import GatewayError
from app.utils.logging import configure_logging, logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("AI Search Gateway 启动")
    yield
    logger.info("AI Search Gateway 停止")


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="1.2.0",
    description=(
        "Authenticated, provider-neutral search and evidence gateway. "
        "Answer snapshots are dated API observations and do not represent consumer interfaces."
    ),
    docs_url="/docs",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(search.router)
app.include_router(extract.router)
app.include_router(screenshot.router)
app.include_router(summary.router)
app.include_router(ipinfo.router)
app.include_router(evidence.router)


@app.exception_handler(GatewayError)
async def gateway_error_handler(_: Request, exc: GatewayError) -> JSONResponse:
    logger.warning("业务异常: {} {}", exc.status_code, exc.message)
    content = {"success": False, "error": exc.message, "detail": exc.detail}
    if isinstance(exc.detail, dict):
        if isinstance(exc.detail.get("code"), str):
            content["code"] = exc.detail["code"]
        if isinstance(exc.detail.get("retryable"), bool):
            content["retryable"] = exc.detail["retryable"]
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {
            key: value
            for key, value in item.items()
            if key in {"type", "loc", "msg"}
        }
        for item in exc.errors()
    ]
    logger.warning("请求参数校验失败: {}", errors)
    return JSONResponse(
        status_code=422,
        content={"success": False, "error": "请求参数无效", "detail": errors},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常: {}", exc)
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": "服务内部错误", "detail": None},
    )
