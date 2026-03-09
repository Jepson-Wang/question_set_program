from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.api.user_api.agent_api import agent_router
from backend.api.user_api.login_api import login_router

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

app.include_router(agent_router)
app.include_router(login_api)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)