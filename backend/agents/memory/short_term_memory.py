"""
在这里面进行短期记忆的开发
主要包括如下功能：
1. 短期记忆的增加和删除
2. 直接获取前n个短期记忆，或者通过元数据进行索引

主要技术分析：
1. 使用队列进行短期记忆的存储
2. 队列的最大长度应该跟我设置的max_length进行比对，如果超过，就应该对队列进行切分
3. 队列的每个元素应该包含元数据，比如时间戳，智能体ID等
4. 应该直接存记忆，不应该有元数据
5. 那么元数据的形式是什么呢？ 直接存字符串就行
6. 需要标注好这是用户传输还是大模型传输，用户传输的话，我需要先对用户传输进行一个优化
7. 存储结构的话，可以用一个字典类型，分为user和model
8. 我还需要为三种获取记忆的方法做一个统一的返回格式

后期可添加功能：
1. 将短期记忆存进redis中
2. 将用户和大模型的对话放入本地缓存中 ， 根据  用户 ID + 会话 ID / 任务 ID + 类型  来存储
"""
from collections import deque
from typing import Dict

class MemoryUnit(dict):
    def __init__(self, user_memory : Dict[str,str] ="", model_memory : Dict[str,str] =""):
        super().__init__(
            user = user_memory,
            model = model_memory
        )

class ShortTermMemory:
    def __init__(self,max_len = 20):
        self.short_memory = deque(maxlen=max_len)

    def add(self,memory:MemoryUnit) -> None:
        self.short_memory.append(memory)

    def get(self,mount : int = 10) -> list[MemoryUnit]:
        return list(self.short_memory)[-mount:]

    def clear(self) -> None:
        self.short_memory.clear()

    def getSize(self)->int:
        return len(self.short_memory)