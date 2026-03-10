from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.schemas.token_schema import Token
from backend.schemas.user_schema import UserResponse
from backend.services.login_service.login_service import loginService

login_router = APIRouter(prefix='/login', tags=['login'])
login_service = loginService()


class LoginRequest(BaseModel):
    username: str = Field(min_length=6, max_length=20, description='用户名')
    password: str = Field(min_length=6, max_length=20, description='用户密码')


@login_router.post('/login', response_model=Token)
async def login_user(request: LoginRequest):
    result = await login_service.login(request.username, request.password)
    if result.get("code") == 401:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=result["msg"],
            headers={"WWW-Authenticate": "Bearer"},
        )
    return result


@login_router.post('/register', response_model=UserResponse)
async def register_user(request: LoginRequest):
    result = await login_service.register(request.username, request.password)
    if result.get("code") != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["msg"],
        )
    return result["data"]
