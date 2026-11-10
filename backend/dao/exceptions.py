"""
DAO 层对外抛出的业务异常。

只翻译上层需要按业务含义分别处理的错误：画像已存在要转去更新，画像不存在要判失败。
连接断开、锁超时、死锁这类错误上层的处理都一样（判失败），原样抛出，不在这里包装。

翻译的另一个目的是把数据库方言挡在 DAO 层：唯一键冲突在 MySQL 是错误码 1062，
在测试用的 SQLite 是一段报错文本，上层不该知道这些。
"""


class ProfileNotFoundError(Exception):
    def __init__(self, user_id: int):
        self.user_id = user_id
        super().__init__(f"用户画像不存在：user_id={user_id}")


class ProfileAlreadyExistsError(Exception):
    def __init__(self, user_id: int):
        self.user_id = user_id
        super().__init__(f"用户画像已存在：user_id={user_id}")
