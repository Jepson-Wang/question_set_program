from datetime import datetime

from pydantic import BaseModel,Field

class UserProfileUpdateDTO(BaseModel):
    user_id: int = Field(description='用户ID', example=1, gt=0)
    grade: str = Field(description='年级', example='七年级')
    subject: str = Field(description='主修学科', example='数学')
    weak_points: dict = Field(description='薄弱知识点', example={'数学': '导数'})
    preferences: dict = Field(description='长期偏好', example={'学习方式': '视频'})
    update_time: datetime = Field(description='更新时间', example="2023-01-01 00:00:00")
    create_time: datetime = Field(description='创建时间', example="2023-01-01 00:00:00")