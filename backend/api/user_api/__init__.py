#定义user_api的父层api，便于管理所有用户端api，降低耦合度
from fastapi import APIRouter

from backend.api.user_api.agent_api import agent_router
from backend.api.user_api.login_api import login_router

user_api=APIRouter(prefix='/user',tags=['user'])

user_api.include_router(login_router)
user_api.include_router(agent_router)
