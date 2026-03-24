#在这里面定义所有的api路由
from fastapi import APIRouter

from backend.api.manage_api import manage_api
from backend.api.user_api import user_api

api_manager=APIRouter(prefix='/api')
api_manager.include_router(user_api)
api_manager.include_router(manage_api)

