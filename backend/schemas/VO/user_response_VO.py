from pydantic import BaseModel

class UserResponseVO(BaseModel):
    id: int
    username: str
    user_privilege: int