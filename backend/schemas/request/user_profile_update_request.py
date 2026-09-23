from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class UserProfileUpdateRequest(BaseModel):
    """部分更新用户画像的请求模型，除 user_id 外所有字段均可选。"""
    model_config = {"from_attributes": True}

    user_id: int = Field(..., description='用户ID', examples=[1], gt=0)
    grade: Optional[str] = Field(None, description='年级', examples=['七年级'])
    subject: Optional[str] = Field(None, description='主修学科', examples=['数学'])
    weak_points: Optional[dict] = Field(None, description='薄弱知识点', examples=[{'数学': '导数'}])
    preferences: Optional[dict] = Field(None, description='长期偏好', examples=[{'学习方式': '视频'}])
    update_time: Optional[datetime] = Field(None, description='更新时间')
    create_time: Optional[datetime] = Field(None, description='创建时间')
    notes: Optional[list] = Field(None,description="自由观察记录（数组），放不进结构化键的内容")
