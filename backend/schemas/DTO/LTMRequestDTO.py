from pydantic import BaseModel,Field

class LTMRequestDTO(BaseModel):
    user_id: str = Field(...,description='用户唯一ID（业务主键）')
    grade: str = Field(...,description='年级')
    subject: str = Field(...,description='主修学科')
    preferences: str = Field(...,description='长期偏好（JSON）')