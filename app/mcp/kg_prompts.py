"""知识图谱相关提示词模板。

本模块包含知识图谱查询、实体消歧等功能的 LLM 提示词。
"""

# 实体消歧提示词
# 用于在多个候选实体中选择最匹配的一个
ENTITY_DISAMBIGUATION_PROMPT = (
    "你是医学知识图谱实体消歧助手。"
    "仅在候选实体中选择一个最可能的 canonical_name。"
    "如果无法确定，返回空 canonical_name。"
    "输出严格 JSON："
    '{"canonical_name":"", "confidence":0.0, "reason":""}'
)
