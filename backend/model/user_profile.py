"""
CREATE TABLE `edu_user_profile` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `user_id` varchar(64) NOT NULL COMMENT '用户唯一ID（业务主键）',
  `grade` varchar(32) NOT NULL COMMENT '年级',
  `subject` varchar(32) NOT NULL COMMENT '主修学科',
  `weak_points` json NOT NULL COMMENT '薄弱知识点（JSON）',
  `preferences` json NOT NULL COMMENT '长期偏好（JSON）',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='学生用户画像表';
"""

from sqlalchemy import Integer, Column, String, DateTime, JSON
from sqlalchemy.sql import func
from backend.model import Base

class UserProfile(Base):
    __tablename__ = 'edu_user_profile'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键')
    user_id = Column(String(64), unique=True, nullable=False, comment='用户唯一ID（业务主键）')
    grade = Column(String(32), nullable=False, comment='年级')
    subject = Column(String(32), nullable=False, default='数学', comment='主修学科')
    weak_points = Column(JSON, nullable=False, comment='薄弱知识点（JSON）')
    preferences = Column(JSON, nullable=False, comment='长期偏好（JSON）')
    update_time = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp(), comment='更新时间')
    create_time = Column(DateTime, default=func.current_timestamp(), comment='创建时间')