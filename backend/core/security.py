# from datetime import datetime, timedelta, timezone
# from typing import Optional
#
# import jwt
# from passlib.context import CryptContext
#
# SECRET_KEY = "3f8a2b1c9d4e7f6a0b5c8d2e1f4a7b3c9d6e2f5a8b1c4d7e0f3a6b9c2d5e8f1"
# ALGORITHM = "HS256"
# ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24
#
# pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
#
#
# def verify_password(plain_password: str, hashed_password: str) -> bool:
#     return pwd_context.verify(plain_password, hashed_password)
#
#
# def get_password_hash(password: str) -> str:
#     return pwd_context.hash(password)
#
#
# def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
#     to_encode = data.copy()
#     expire = datetime.now(timezone.utc) + (
#         expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
#     )
#     to_encode.update({"exp": expire})
#     return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# from datetime import datetime, timedelta, timezone
# from typing import Optional
#
# import jwt
# from passlib.context import CryptContext
#
# SECRET_KEY = "3f8a2b1c9d4e7f6a0b5c8d2e1f4a7b3c9d6e2f5a8b1c4d7e0f3a6b9c2d5e8f1"
# ALGORITHM = "HS256"
# ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24
#
# pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
#
#
# def verify_password(plain_password: str, hashed_password: str) -> bool:
#     return pwd_context.verify(plain_password, hashed_password)
#
#
# def get_password_hash(password: str) -> str:
#     return pwd_context.hash(password)
#
#
# def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
#     to_encode = data.copy()
#     expire = datetime.now(timezone.utc) + (
#         expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
#     )
#     to_encode.update({"exp": expire})
#     return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt
import bcrypt

SECRET_KEY = "3f8a2b1c9d4e7f6a0b5c8d2e1f4a7b3c9d6e2f5a8b1c4d7e0f3a6b9c2d5e8f1"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    # 直接使用bcrypt进行密码验证
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """获取密码哈希值"""
    # 直接使用bcrypt生成密码哈希
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)