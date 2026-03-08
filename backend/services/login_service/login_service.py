#在这里面完成login相关逻辑
from sqlalchemy import select
from backend.model import get_db
from backend.model.user import User

class loginService:
    async def login(self, username, password):
        """
        这里可以添加登录逻辑，比如验证用户名密码等
        如果登录成功，返回一个token或session_id
        如果登录失败，抛出异常
        登录逻辑主要包括:检验是否存在用户，并检查密码是否正确
        """
        async for db in get_db():
            result = await db.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if user and user.password == password:
                return {
                    'code': 200,
                    'msg': 'success',
                    'data': {
                        'id': user.id,
                        'username': user.username,
                        'user_privilege': user.user_privilege
                    }
                }
            else:
                return {
                    'code': 401,
                    'msg': '用户未注册或密码错误',
                    'data': None
                }

    async def register(self, username, password):
        """
        注册用户
        """
        async for db in get_db():
            result = await db.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if user:
                return {
                    'code': 400,
                    'msg': '用户名已存在',
                    'data': None
                }
            elif password and password != '' and username and username != '':
                new_user = User(username=username, password=password)
                db.add(new_user)
                await db.commit()
                await db.refresh(new_user)  # 刷新用户对象以获取新的ID
                return {
                    'code': 200,
                    'msg': 'success',
                    'data': {
                        'id': new_user.id,
                        'username': new_user.username,
                        'user_privilege': new_user.user_privilege
                    }
                }
            else:
                return {
                    'code': 400,
                    'msg': '用户名或密码为空',
                    'data': None
                }