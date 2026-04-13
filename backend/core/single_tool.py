"""
在这个文件里需要定义一个singleton全局单例或者每次创建的装饰器
用于确保在程序运行过程中只有一个实例被创建
"""
import threading
import time
from collections import OrderedDict
from functools import wraps


class singleMeta(type):
    """
    类级别单例元类。
    每个使用此元类的类拥有独立的锁，避免不同类的实例化操作互相阻塞。
    适用于基础设施类（MemoryManager、VectorStoreManager 等），实例永久驻留。
    """
    _instances: dict = {}
    _locks: dict = {}
    _meta_lock = threading.Lock()

    def __call__(cls, *args, **kwargs):
        # 确保每个类有自己的锁（仅在首次时创建，由 meta_lock 保护）
        if cls not in cls._locks:
            with cls._meta_lock:
                if cls not in cls._locks:
                    cls._locks[cls] = threading.Lock()

        if cls not in cls._instances:
            with cls._locks[cls]:
                if cls not in cls._instances:
                    cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]


class _LRUTTLCache:
    """
    LRU + TTL 双策略缓存。
    - maxsize：最多缓存 maxsize 个不同参数的结果，超出时淘汰最久未用的
    - ttl：单个缓存项超过 ttl 秒未被访问则视为过期，下次访问时惰性删除
    """

    def __init__(self, maxsize: int = 32, ttl: float = 3600.0):
        self.maxsize = maxsize
        self.ttl = ttl
        # OrderedDict 维护 LRU 顺序：key -> (value, last_access_timestamp)
        self._cache: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        """返回 (value, hit)。过期 key 会被惰性删除。"""
        with self._lock:
            if key not in self._cache:
                return None, False
            value, last_access = self._cache[key]
            if time.monotonic() - last_access > self.ttl:
                # TTL 过期，惰性删除
                del self._cache[key]
                return None, False
            # 命中：移到末尾（最近使用）并刷新时间戳
            self._cache.move_to_end(key)
            self._cache[key] = (value, time.monotonic())
            return value, True

    def set(self, key, value):
        """写入缓存；超出 maxsize 时淘汰最久未用的条目（头部）。"""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (value, time.monotonic())
            if len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)  # 淘汰 LRU 条目


def singleton_method(func=None, *, maxsize: int = 32, ttl: float = 3600.0):
    """
    函数级缓存装饰器，按调用参数缓存返回值。
    支持 LRU（最多 maxsize 个不同参数组合）+ TTL（超过 ttl 秒未访问自动过期）。

    用法：
        @singleton_method                          # 默认 maxsize=32, ttl=1小时
        def get_llm(model, streaming): ...

        @singleton_method(maxsize=8, ttl=1800)     # 自定义参数
        def get_llm(model, streaming): ...
    """
    def decorator(f):
        cache = _LRUTTLCache(maxsize=maxsize, ttl=ttl)
        _lock = threading.Lock()

        @wraps(f)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            value, hit = cache.get(key)
            if hit:
                return value
            with _lock:
                # 二次检查，防止并发时重复创建
                value, hit = cache.get(key)
                if hit:
                    return value
                result = f(*args, **kwargs)
                cache.set(key, result)
            return result

        return wrapper

    # 支持 @singleton_method 和 @singleton_method(...) 两种写法
    if func is not None:
        return decorator(func)
    return decorator
