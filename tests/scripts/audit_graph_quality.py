"""输出 Neo4j 图谱规模、重复项、来源覆盖和多跳路径样例。"""

import asyncio
import json
from pathlib import Path
import sys

from dotenv import load_dotenv


async def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root))
    load_dotenv(project_root / ".env", override=True)

    from app.knowledge_graph.neo4j_manager import neo4j_client

    queries = {
        "summary": """
            MATCH (n)
            OPTIONAL MATCH ()-[r]->()
            RETURN count(DISTINCT n) AS node_count,
                   count(DISTINCT r) AS relationship_count
        """,
        "labels": """
            MATCH (n)
            UNWIND labels(n) AS label
            WITH label, count(*) AS count
            WHERE label <> 'KnowledgeEntity'
            RETURN label, count
            ORDER BY count DESC, label
        """,
        "relationship_types": """
            MATCH ()-[r]->()
            RETURN type(r) AS type, count(*) AS count
            ORDER BY count DESC, type
        """,
        "duplicate_entities": """
            MATCH (n:KnowledgeEntity)
            WITH n.entity_type AS entity_type,
                 coalesce(n.canonical_name, n.name) AS canonical_name,
                 collect(n.name) AS names,
                 count(*) AS count
            WHERE count > 1
            RETURN entity_type, canonical_name, names, count
            ORDER BY count DESC, canonical_name
        """,
        "quality": """
            MATCH (n:KnowledgeEntity)
            WITH count(n) AS node_count,
                 count(CASE WHEN NOT (n)--() THEN 1 END) AS isolated_nodes
            OPTIONAL MATCH ()-[r]->()
            RETURN node_count,
                   isolated_nodes,
                   count(r) AS relationship_count,
                   count(CASE WHEN r.source_id IS NULL THEN 1 END)
                       AS relationships_without_source
        """,
        "noncanonical_aliases": """
            MATCH (n:KnowledgeEntity)
            WHERE n.name IN [
                '孤立性舒张期高血压',
                '孤立性收缩期高血压',
                '年龄',
                '不良生活习惯',
                '高盐饮食',
                '低钾饮食',
                'ABI',
                '踝/臂血压指数',
                '踝肱指数',
                '老年人群',
                '中国高血压防治指南 (2024年修订版)',
                '中国高血压防治指南(2024年修订版)',
                '《中国高血压防治指南(2024年修订版)》'
            ]
            RETURN n.entity_type AS entity_type,
                   n.name AS name,
                   n.aliases AS aliases
            ORDER BY entity_type, name
        """,
        "two_hop_paths": """
            MATCH path=(a:KnowledgeEntity)-[r1]-(b:KnowledgeEntity)-[r2]-(c:KnowledgeEntity)
            WHERE a <> c AND id(r1) <> id(r2)
            RETURN a.name AS start,
                   type(r1) AS first_relation,
                   b.name AS middle,
                   type(r2) AS second_relation,
                   c.name AS target
            LIMIT 20
        """,
        "rule_focused_two_hop_paths": """
            MATCH path=(a:KnowledgeEntity)-[r1]-(b:KnowledgeEntity)-[r2]-(c:KnowledgeEntity)
            WHERE a <> c
              AND id(r1) <> id(r2)
              AND (
                  b:ClinicalRule
                  OR b:ThresholdRule
                  OR b:MeasurementMethod
                  OR b:MeasurementContext
              )
            RETURN a.entity_type AS start_type,
                   a.name AS start,
                   type(r1) AS first_relation,
                   b.entity_type AS middle_type,
                   b.name AS middle,
                   type(r2) AS second_relation,
                   c.entity_type AS target_type,
                   c.name AS target
            LIMIT 40
        """,
    }

    report = {}
    try:
        for name, cypher in queries.items():
            report[name] = await neo4j_client.run_cypher(cypher)
    finally:
        await neo4j_client.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
