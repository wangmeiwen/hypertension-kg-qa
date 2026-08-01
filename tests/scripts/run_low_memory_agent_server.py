"""运行跳过检索预热的真实 Agent 后端，避免同时加载文本 RAG 模型。"""

# ruff: noqa: E402

import os
from pathlib import Path
import sys

import uvicorn

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from app.backend.app_factory import BackendAppTestOverrides, create_app
from app.backend.services.startup_runtime_service import StartupRuntimeBehavior

os.environ["KG_ENTITY_RELATION_PRELOAD_EMBEDDER"] = "0"

app = create_app(
    test_overrides=BackendAppTestOverrides(
        startup_behavior=StartupRuntimeBehavior(
            prewarm_retrieval_resources=False,
        )
    )
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
