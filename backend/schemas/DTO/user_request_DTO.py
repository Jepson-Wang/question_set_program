from pydantic import BaseModel

class UserRequestDTO(BaseModel):
    username: str
    password: str

