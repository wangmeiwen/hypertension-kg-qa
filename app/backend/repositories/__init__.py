"""
后端数据仓库模块。

本模块提供数据库模型和数据访问层的管理，包括：
- 数据库连接管理 (database)
- 会话和消息管理 (session_manager)
- 会话记忆摘要管理 (session_memory_manager)
- OCR 文档管理 (ocr_manager)
- Token 使用量管理 (token_usage_manager)

主要导出：
- get_db: 数据库连接上下文管理器
- SessionManager: 会话管理器
- MessageManager: 消息管理器
- SessionMemoryManager: 会话记忆摘要管理器
- OcrDocumentManager: OCR 文档管理器
- OcrTokenUsageManager: OCR Token 使用记录管理器
- TokenUsageManager: Token 使用量管理器
"""

from app.backend.repositories.database import get_db, initialize_database
from app.backend.repositories.session_manager import SessionManager, MessageManager
from app.backend.repositories.session_memory_manager import SessionMemoryManager
from app.backend.repositories.ocr_manager import OcrDocumentManager, OcrTokenUsageManager
from app.backend.repositories.token_usage_manager import TokenUsageManager

__all__ = [
    "get_db",
    "initialize_database",
    "SessionManager",
    "MessageManager",
    "SessionMemoryManager",
    "OcrDocumentManager",
    "OcrTokenUsageManager",
    "TokenUsageManager",
]

