from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import api_manager
from backend.api.user_api.agent_api import agent_router
from backend.api.user_api.login_api import login_router
from backend.core.hooks import startup_event, shutdown_event
from backend.utils.redis_client import close_redis

app = FastAPI(
    debug=True,
    title="学生学情分析系统",
    openapi_url="/api"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册钩子函数
app.on_event("startup")(startup_event)
app.on_event("shutdown")(shutdown_event)

app.include_router(agent_router)
app.include_router(login_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)