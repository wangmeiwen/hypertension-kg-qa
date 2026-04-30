"""前端 E2E 使用的 create_app 测试装配入口。"""

from __future__ import annotations

from fastapi import FastAPI

from app.backend.app_factory import create_app as create_backend_app
from app.backend.testing.deterministic_support import build_deterministic_test_overrides


def create_deterministic_app() -> FastAPI:
    """基于真实 create_app 装配一个确定性测试实例。"""
    return create_backend_app(test_overrides=build_deterministic_test_overrides())


def create_app() -> FastAPI:
    """供 uvicorn --factory 调用。"""
    return create_deterministic_app()
