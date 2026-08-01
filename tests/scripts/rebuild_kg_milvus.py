"""以最小运行时从 Neo4j 重建知识图谱 Milvus 集合。"""

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
    from app.rag_core.kg_milvus_store import KgMilvusStore

    store = KgMilvusStore()
    try:
        stats = await store.rebuild_from_neo4j(neo4j_client)
    finally:
        await neo4j_client.close()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
