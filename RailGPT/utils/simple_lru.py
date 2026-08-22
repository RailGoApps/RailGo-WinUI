from collections import OrderedDict
from typing import Any


class SimpleLRUCache:
    """
    通用 LRU 缓存
    - 不关心 value 是什么
    - 不关心网络 / 业务
    """

    def __init__(self, capacity: int = 128):
        self.capacity = capacity
        self.data = OrderedDict()

    def get(self, key: str) -> Any | None:
        if key not in self.data:
            return None
        self.data.move_to_end(key)
        return self.data[key]

    def put(self, key: str, value: Any):
        if key in self.data:
            self.data.move_to_end(key)
        self.data[key] = value

        if len(self.data) > self.capacity:
            self.data.popitem(last=False)

    def __contains__(self, key: str) -> bool:
        return key in self.data