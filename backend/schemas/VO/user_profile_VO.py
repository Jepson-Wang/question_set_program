from pydantic import BaseModel,Field

class UserProfileVO(BaseModel):
    user_id: str = Field(description='用户ID', example=1, gt=0)
    grade: str = Field(description='年级', example='七年级')
    subject: str = Field(description='主修学科', example='数学')
    weak_points: dict = Field(description='薄弱知识点', example={'数学': '导数'})
    preferences: dict = Field(description='长期偏好', example={'学习方式': '视频'})