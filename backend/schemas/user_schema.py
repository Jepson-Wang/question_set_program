from pydantic import BaseModel

class UserResponse(BaseModel):
    id: int
    username: str
    user_privilege: int

class UserRequest(BaseModel):
    username: str
    password: str
