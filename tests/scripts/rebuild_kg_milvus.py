"""以最小运行时从 Neo4j 重建知识图谱 Milvus 集合。"""

import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from dotenv import load_dotenv


def _drop_incompatible_collection(conf: Any) -> bool:
    """删除无法按当前合同打开的旧 KG 集合。"""
    from pymilvus import MilvusClient

    from app.rag_core.milvus_schema import (
        KG_MILVUS_SCHEMA,
        describe_and_validate_milvus_schema,
    )

    client = MilvusClient(
        uri=f"http://{conf.MILVUS_HOST}:{conf.MILVUS_PORT}",
        db_name=conf.MILVUS_DATABASE_NAME,
    )
    collection_name = conf.KG_MILVUS_COLLECTION_NAME
    if not client.has_collection(collection_name=collection_name):
        return False
    try:
        describe_and_validate_milvus_schema(
            client,
            collection_name=collection_name,
            contract=KG_MILVUS_SCHEMA,
        )
        return False
    except RuntimeError as exc:
        if not str(exc).startswith("milvus_schema_mismatch:"):
            raise
    client.drop_collection(collection_name=collection_name)
    return True


async def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root))
    load_dotenv(project_root / ".env", override=True)

    from app.core.config import Config
    from app.knowledge_graph.neo4j_manager import neo4j_client
    from app.rag_core.kg_milvus_store import KgMilvusStore

    conf = Config()
    dropped_incompatible_collection = _drop_incompatible_collection(conf)
    store = KgMilvusStore(conf=conf)
    try:
        stats = await store.rebuild_from_neo4j(neo4j_client)
    finally:
        await neo4j_client.close()
    stats["dropped_incompatible_collection"] = dropped_incompatible_collection
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
