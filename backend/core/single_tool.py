"""
在这个文件里需要定义一个singleton全局单例或者每次创建的装饰器
用于确保在程序运行过程中只有一个实例被创建
"""
import threading
from functools import wraps


class singleMeta(type):

    _instance = {}
    _lock = threading.Lock()

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instance:
            with cls._lock:
                if cls not in cls._instance:
                    cls._instance[cls] = super().__call__(*args, **kwargs)
        return cls._instance[cls]

def singleton_method(func):
    """支持参数的方法级单例装饰器：按参数缓存结果"""
    # 缓存字典：key=参数元组，value=方法结果
    _cache = {}
    _lock = threading.Lock()

    @wraps(func)
    def wrapper(*args, **kwargs):
        # 将参数转为不可变的key（args+kwargs排序后的元组）
        key = (args, tuple(sorted(kwargs.items())))
        if key not in _cache:
            with _lock:
                if key not in _cache:
                    _cache[key] = func(*args, **kwargs)
        return _cache[key]

    return wrapper