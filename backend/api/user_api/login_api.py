#在这里完成login登录的api接口
from fastapi import APIRouter
from pydantic import BaseModel,Field
from pydantic import BaseModel

from backend.services.login_service.login_service import loginService

login_api=APIRouter(prefix='/login',tags=['login'])

login_service = loginService()

class LoginRequest(BaseModel):
    username :str = Field(min_length=6,max_length=20,description='用户名')
    password :str = Field(min_length=6,max_length=20,description='用户密码')

@login_api.post('/login')
async def login_user(request:LoginRequest):
    username=request.username
    password=request.password
    result=await login_service.login(username,password)
    return result

@login_api.post('/register')
async def register_user(request:LoginRequest):
    username=request.username
    password=request.password
    result=await login_service.register(username,password)
    return result