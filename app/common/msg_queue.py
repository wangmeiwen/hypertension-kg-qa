"""
消息队列管理模块。

本模块提供异步消息队列管理功能，支持：
- 多用户并发请求的独立队列管理
- 线程安全的队列操作
- 流式输出消息推送

主要类：
- QueueBinding: 队列绑定数据结构
- MsgQueueManager: 消息队列管理器

依赖：
- asyncio: Python 异步 I/O 库
"""

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import threading
from typing import Optional


def build_queue_key(user_id: str, session_id: str = "", request_id: str = "") -> str:
    """
    构造消息队列复合键，避免同用户并发请求互相串流。

    Args:
        user_id: 用户 ID
        session_id: 会话 ID，可选
        request_id: 请求 ID，可选

    Returns:
        队列复合键字符串
    """
    return f"{user_id}::{session_id}::{request_id}"


@dataclass
class QueueBinding:
    """
    队列绑定数据结构。

    关联队列和事件循环，用于线程安全操作。

    Attributes:
        queue: 异步消息队列
        loop: 事件循环
    """
    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop


class MsgQueueManager:
    """
    消息队列管理器。

    管理多个并发请求的独立队列，提供线程安全的队列操作。

    Attributes:
        msg_queue_dict: 队列键到队列绑定的映射字典
        _lock: 线程锁，保护队列字典的并发访问
    """

    def __init__(self):
        """
        初始化消息队列管理器。
        """
        self.msg_queue_dict: dict[str, QueueBinding] = {}
        self._lock = threading.Lock()

    def get_msg_queue(self, queue_key: str) -> asyncio.Queue:
        """
        获取或创建指定队列键的消息队列。

        Args:
            queue_key: 队列键

        Returns:
            异步消息队列
        """
        with self._lock:
            if queue_key not in self.msg_queue_dict:
                self.msg_queue_dict[queue_key] = QueueBinding(
                    queue=asyncio.Queue(),
                    loop=asyncio.get_running_loop(),
                )
            return self.msg_queue_dict[queue_key].queue

    def _get_binding(self, queue_key: str) -> Optional[QueueBinding]:
        """
        获取队列绑定（队列+事件循环）。

        Args:
            queue_key: 队列键

        Returns:
            队列绑定对象，不存在则返回 None
        """
        with self._lock:
            return self.msg_queue_dict.get(queue_key)

    async def put_async(self, queue_key: str, message: dict) -> None:
        """
        异步向队列中放入消息。

        Args:
            queue_key: 队列键
            message: 消息字典
        """
        await self.get_msg_queue(queue_key).put(message)

    def put_threadsafe(self, queue_key: str, message: dict) -> None:
        """
        线程安全地向队列中放入消息。

        Args:
            queue_key: 队列键
            message: 消息字典
        """
        binding = self._get_binding(queue_key)
        if binding is None:
            return
        future = asyncio.run_coroutine_threadsafe(binding.queue.put(message), binding.loop)
        try:
            future.result(timeout=5)
        except FutureTimeoutError:
            return

    def del_msg_queue(self, queue_key: str) -> None:
        """
        删除指定队列。

        Args:
            queue_key: 队列键
        """
        with self._lock:
            if queue_key in self.msg_queue_dict:
                del self.msg_queue_dict[queue_key]


# 全局消息队列管理器实例
msg_queue_manager = MsgQueueManager()


async def put_msg_sentence_content(queue_key: str, put_content: str) -> None:
    """
    异步向队列中放入句子内容（流式输出用）。

    Args:
        queue_key: 队列键
        put_content: 要放入的内容
    """
    await msg_queue_manager.put_async(queue_key, {"type": "msg", "msg": put_content})
    await asyncio.sleep(0.1)
    await msg_queue_manager.put_async(queue_key, {"type": "msg", "msg": "\n"})
    await asyncio.sleep(0.1)


async def put_reply_content(queue_key: str, put_content: str) -> None:
    """
    异步向队列中放入回复内容。

    Args:
        queue_key: 队列键
        put_content: 回复内容
    """
    await msg_queue_manager.put_async(queue_key, {"type": "reply", "msg": put_content})
    await asyncio.sleep(0.1)


async def put_done_content(queue_key: str, final_output: str = "", traces: Optional[list] = None, status: str = "") -> None:
    """
    结束流时传入最终回复内容。

    前端若未收到 reply 可用 done 的 msg 显示。

    Args:
        queue_key: 队列键
        final_output: 最终输出内容
        traces: 工具调用追踪信息，可选
        status: 状态标识，可选
    """
    message = {"type": "done", "msg": final_output or ""}
    if traces is not None:
        message["traces"] = traces
    if status:
        message["status"] = status
    await msg_queue_manager.put_async(queue_key, message)
    await asyncio.sleep(0.1)


def put_msg_sentence_content_sync(queue_key, put_content):
    """线程安全地向队列中放入句子内容（流式输出用）"""
    msg_queue_manager.put_threadsafe(queue_key, {"type": "msg", "msg": put_content})
    time.sleep(0.1)
    msg_queue_manager.put_threadsafe(queue_key, {"type": "msg", "msg": "\n"})
    time.sleep(0.1)


def put_reply_content_sync(queue_key, put_content):
    """线程安全地向队列中放入回复内容"""
    msg_queue_manager.put_threadsafe(queue_key, {"type": "reply", "msg": put_content})
    time.sleep(0.1)


def put_done_content_sync(queue_key, final_output="", traces=None, status=""):
    """线程安全地向队列中放入完成消息（结束流式输出）

    Args:
        queue_key: 队列键
        final_output: 最终输出内容
        traces: 工具调用追踪信息（可选）
        status: 状态标识（可选）
    """
    message = {"type": "done", "msg": final_output or ""}
    if traces is not None:
        message["traces"] = traces
    if status:
        message["status"] = status
    msg_queue_manager.put_threadsafe(queue_key, message)
    time.sleep(0.1)


def remove_msg_queue(queue_key):
    """移除指定消息队列"""
    msg_queue_manager.del_msg_queue(queue_key)

