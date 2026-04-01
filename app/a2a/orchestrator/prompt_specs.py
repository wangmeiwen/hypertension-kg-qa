"""主控与子代理职责提示规范。"""

from __future__ import annotations

ORCHESTRATOR_PROMPT_ROLE = {
    "responsibilities": [
        "问题分类",
        "子任务拆解",
        "路由选择",
        "检索方向生成",
        "覆盖判断",
        "终答门控",
    ]
}

SUBAGENT_PROMPT_ROLE = {
    "responsibilities": [
        "严格执行单任务",
        "按主控方向生成查询",
        "返回证据与覆盖状态",
        "不越权终答",
    ]
}
