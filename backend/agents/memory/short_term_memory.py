"""
在这里面进行短期记忆的开发
主要包括如下功能：
1. 短期记忆的增加和删除
2. 直接获取前n个短期记忆，或者通过元数据进行索引

主要技术分析：
1. 使用队列进行短期记忆的存储
2. 队列的最大长度应该跟我设置的max_length进行比对，如果超过，就应该对队列进行切分
3. 队列的每个元素应该包含元数据，比如时间戳，智能体ID等
"""
from collections import deque
from typing import Dict

class MemoryUnit(dict):
    def __init__(self,memory:str,metadata:list):
        super().__init__(memory=memory,metadata=metadata)
        self.memory = memory
        self.metadata = metadata

class ShortTermMemory:
    def __init__(self):
        self.memory_list = []

    def add_memory(self,memory_unit:MemoryUnit):
        self.memory_list.append(memory_unit)