"""
删除并重新创建知识图谱 Milvus 集合脚本
用于解决 schema 不匹配问题或更新集合结构
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from pymilvus import MilvusClient, DataType

# 导入配置
try:
    from app.common.config import Config
except ImportError:
    from config.config import Config


def get_kg_milvus_config():
    """获取知识图谱 Milvus 配置"""
    conf = Config()
    collection_name = os.getenv("KG_MILVUS_COLLECTION_NAME", "hypertension_kg_graph_collection")
    return {
        "host": conf.MILVUS_HOST,
        "port": conf.MILVUS_PORT,
        "database": conf.MILVUS_DATABASE_NAME,
        "collection_name": collection_name
    }


def drop_collection(client: MilvusClient, collection_name: str) -> bool:
    """删除集合"""
    if client.has_collection(collection_name):
        client.drop_collection(collection_name)
        print(f"✅ 已删除集合: {collection_name}")
        return True
    else:
        print(f"ℹ️ 集合不存在: {collection_name}")
        return False


def create_kg_collection_with_schema(
    client: MilvusClient,
    collection_name: str,
    embedding_dim: int = 1024
) -> bool:
    """
    创建知识图谱 Milvus 集合并设置 Schema
    
    Args:
        client: Milvus 客户端
        collection_name: 集合名称
        embedding_dim: 向量维度（BGE-M3 默认为 1024）
    """
    try:
        # 创建集合 Schema，启用自动 ID，禁用动态字段
        schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
        
        # 添加字段
        schema.add_field(field_name="pk_id", datatype=DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field(field_name="doc_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="item_type", datatype=DataType.VARCHAR, max_length=32)
        schema.add_field(field_name="name", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="label", datatype=DataType.VARCHAR, max_length=128)
        schema.add_field(field_name="source_name", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="source_label", datatype=DataType.VARCHAR, max_length=128)
        schema.add_field(field_name="relation_type", datatype=DataType.VARCHAR, max_length=128)
        schema.add_field(field_name="target_name", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="target_label", datatype=DataType.VARCHAR, max_length=128)
        schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=65535)
        schema.add_field(field_name="timestamp", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=embedding_dim)
        
        # 创建索引参数
        index_params = client.prepare_index_params()
        
        # 向量索引
        index_params.add_index(
            field_name="embedding",
            index_name="embedding_index",
            index_type="IVF_FLAT",
            metric_type="COSINE",
            params={"nlist": 128}
        )
        
        # 创建集合
        client.create_collection(
            collection_name=collection_name,
            schema=schema,
            index_params=index_params
        )
        
        print(f"✅ 已创建知识图谱集合: {collection_name}")
        print(f"   - 向量维度: {embedding_dim}")
        print(f"   - 字段: pk_id, doc_id, item_type, name, label, source_name, source_label,")
        print(f"           relation_type, target_name, target_label, text, timestamp, embedding")
        return True
        
    except Exception as e:
        print(f"❌ 创建集合失败: {e}")
        return False


def recreate_kg_collection(embedding_dim: int = 1024, skip_confirm: bool = False):
    """
    删除并重新创建知识图谱集合
    
    Args:
        embedding_dim: 向量维度
        skip_confirm: 是否跳过确认提示
    """
    config = get_kg_milvus_config()
    collection_name = config["collection_name"]
    
    print("=" * 60)
    print("知识图谱 Milvus 集合重建工具")
    print("=" * 60)
    print(f"连接信息: http://{config['host']}:{config['port']}")
    print(f"数据库: {config['database']}")
    print(f"集合名: {collection_name}")
    print("=" * 60)
    
    # 确认操作
    if not skip_confirm:
        confirm = input(f"\n⚠️  警告: 这将删除知识图谱集合 '{collection_name}' 中的所有数据！\n是否继续? (yes/no): ")
        if confirm.lower() not in ['yes', 'y']:
            print("❌ 操作已取消")
            return False
    
    try:
        # 创建客户端
        client = MilvusClient(
            uri=f"http://{config['host']}:{config['port']}",
            db_name=config["database"]
        )
        
        # 删除现有集合
        drop_collection(client, collection_name)
        
        # 创建新集合
        if create_kg_collection_with_schema(client, collection_name, embedding_dim):
            # 加载集合
            client.load_collection(collection_name)
            print(f"✅ 集合已加载到内存")
            print("\n📝 提示: 现在可以重新从 Neo4j 重建知识图谱嵌入")
            return True
        else:
            return False
            
    except Exception as e:
        print(f"❌ 操作失败: {e}")
        return False


def get_kg_collection_stats():
    """获取知识图谱集合统计信息"""
    config = get_kg_milvus_config()
    collection_name = config["collection_name"]
    
    try:
        client = MilvusClient(
            uri=f"http://{config['host']}:{config['port']}",
            db_name=config["database"]
        )
        
        if not client.has_collection(collection_name):
            print(f"ℹ️ 集合不存在: {collection_name}")
            return None
        
        # 获取集合统计
        stats = client.get_collection_stats(collection_name)
        row_count = stats.get("row_count", 0)
        
        print("=" * 60)
        print("知识图谱集合统计")
        print("=" * 60)
        print(f"集合名: {collection_name}")
        print(f"文档总数: {row_count}")
        
        if row_count > 0:
            # 查询实体和关系数量
            max_query_window = 16384
            result = client.query(
                collection_name=collection_name,
                filter="doc_id != ''",
                output_fields=["item_type"],
                limit=max_query_window,
            )
            
            entity_count = sum(1 for row in result if row.get("item_type") == "entity")
            rel_count = sum(1 for row in result if row.get("item_type") == "relationship")
            
            print(f"实体数量: {entity_count}")
            print(f"关系数量: {rel_count}")
            
            if len(result) >= max_query_window:
                print(f"⚠️  注意: 统计可能被截断（查询窗口限制: {max_query_window}）")
        
        return stats
        
    except Exception as e:
        print(f"❌ 获取统计失败: {e}")
        return None


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="知识图谱 Milvus 集合管理工具")
    parser.add_argument(
        "--recreate", "-r",
        action="store_true",
        help="删除并重新创建集合"
    )
    parser.add_argument(
        "--drop-only", "-d",
        action="store_true",
        help="仅删除集合，不重新创建"
    )
    parser.add_argument(
        "--stats", "-s",
        action="store_true",
        help="显示集合统计信息"
    )
    parser.add_argument(
        "--dim", "-D",
        type=int,
        default=1024,
        help="向量维度 (默认: 1024)"
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="跳过确认提示"
    )
    
    args = parser.parse_args()
    
    if args.stats:
        get_kg_collection_stats()
        sys.exit(0)
    elif args.recreate:
        success = recreate_kg_collection(embedding_dim=args.dim, skip_confirm=args.yes)
        sys.exit(0 if success else 1)
    elif args.drop_only:
        config = get_kg_milvus_config()
        client = MilvusClient(
            uri=f"http://{config['host']}:{config['port']}",
            db_name=config["database"]
        )
        drop_collection(client, config["collection_name"])
    else:
        # 默认操作：显示帮助
        parser.print_help()
        print("\n示例:")
        print("  python drop_kg_milvus_collection.py --stats          # 查看统计")
        print("  python drop_kg_milvus_collection.py --recreate       # 重建集合")
        print("  python drop_kg_milvus_collection.py --recreate -y    # 强制重建（跳过确认）")
        print("  python drop_kg_milvus_collection.py --drop-only      # 仅删除")


if __name__ == "__main__":
    main()
