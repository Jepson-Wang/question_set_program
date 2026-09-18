from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.middleware.logging import setup_logging, LoggingMiddleware
from backend.api.user_api.agent_api import agent_router
from backend.api.user_api.login_api import login_router
from backend.api.health_api import health_router
from backend.core.hooks import startup_event, shutdown_event
from backend.core.app_settings import cors_allow_origins, debug_enabled

setup_logging()

app = FastAPI(
    debug=debug_enabled(),
    title="学生学情分析系统",
    openapi_url="/api"
)

app.add_middleware(LoggingMiddleware)
cors_origins = cors_allow_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    # CORS 规范不允许「任意来源」和「携带凭据」同时成立；只有明确列出来源时才允许携带凭据
    allow_credentials=cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册钩子函数
app.on_event("startup")(startup_event)
app.on_event("shutdown")(shutdown_event)

app.include_router(agent_router)
app.include_router(login_router)
app.include_router(health_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)