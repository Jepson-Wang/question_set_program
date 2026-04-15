from typing import Optional

from pydantic import BaseModel, Field


class LTMRequest(BaseModel):
    user_id: int = Field(..., description='用户唯一ID（业务主键）')
    grade: Optional[str] = Field(None, description='年级')
    subject: Optional[str] = Field(None, description='主修学科')
    preferences: Optional[dict] = Field(None, description='长期偏好（JSON）')
    weak_points: Optional[dict] = Field(None, description='薄弱知识点（JSON）')
