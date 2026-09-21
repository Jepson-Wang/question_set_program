from pydantic import BaseModel, Field


class UserProfileResponse(BaseModel):
    model_config = {"from_attributes": True}

    user_id: int = Field(description='用户ID', examples=[1], gt=0)
    grade: str = Field(description='年级', examples=['七年级'])
    subject: str = Field(description='主修学科', examples=['数学'])
    weak_points: dict = Field(description='薄弱知识点', examples=[{'数学': '导数'}])
    preferences: dict = Field(description='长期偏好', examples=[{'学习方式': '视频'}])
    notes: list = Field(default_factory=list, description='自由观察记录',
                        examples=[['做题时喜欢先看思路']])
