"""
删除并重新创建 Milvus 集合脚本
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


def get_milvus_config():
    """获取 Milvus 配置"""
    conf = Config()
    return {
        "host": conf.MILVUS_HOST,
        "port": conf.MILVUS_PORT,
        "database": conf.MILVUS_DATABASE_NAME,
        "collection_name": conf.MILVUS_COLLECTION_NAME
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


def create_collection_with_schema(
    client: MilvusClient,
    collection_name: str,
    dense_dim: int = 1024
) -> bool:
    """
    创建 Milvus 集合并设置 Schema
    
    Args:
        client: Milvus 客户端
        collection_name: 集合名称
        dense_dim: 稠密向量维度（BGE-M3 默认为 1024）
    """
    try:
        # 创建集合 Schema，禁用自动 ID，启用动态字段
        schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
        
        # 添加字段
        schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
        schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=65535)
        schema.add_field(field_name="dense_vector", datatype=DataType.FLOAT_VECTOR, dim=dense_dim)
        schema.add_field(field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field(field_name="parent_id", datatype=DataType.VARCHAR, max_length=100)
        schema.add_field(field_name="parent_content", datatype=DataType.VARCHAR, max_length=65535)
        schema.add_field(field_name="source", datatype=DataType.VARCHAR, max_length=50)
        schema.add_field(field_name="timestamp", datatype=DataType.VARCHAR, max_length=50)
        schema.add_field(field_name="page_num", datatype=DataType.INT64)
        schema.add_field(field_name="start_page", datatype=DataType.INT64)
        schema.add_field(field_name="end_page", datatype=DataType.INT64)
        
        # 创建索引参数
        index_params = client.prepare_index_params()
        
        # 稠密向量索引
        index_params.add_index(
            field_name="dense_vector",
            index_name="dense_index",
            index_type="IVF_FLAT",
            metric_type="IP",
            params={"nlist": 128}
        )
        
        # 稀疏向量索引
        index_params.add_index(
            field_name="sparse_vector",
            index_name="sparse_index",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="IP",
            params={"drop_ratio_build": 0.2}
        )
        
        # 创建集合
        client.create_collection(
            collection_name=collection_name,
            schema=schema,
            index_params=index_params
        )
        
        print(f"✅ 已创建集合: {collection_name}")
        print(f"   - 稠密向量维度: {dense_dim}")
        print(f"   - 字段: id, text, dense_vector, sparse_vector, parent_id, parent_content, source, timestamp, page_num, start_page, end_page")
        return True
        
    except Exception as e:
        print(f"❌ 创建集合失败: {e}")
        return False


def recreate_collection(dense_dim: int = 1024, skip_confirm: bool = False):
    """
    删除并重新创建集合
    
    Args:
        dense_dim: 稠密向量维度
        skip_confirm: 是否跳过确认提示
    """
    config = get_milvus_config()
    collection_name = config["collection_name"]
    
    print("=" * 60)
    print("Milvus 集合重建工具")
    print("=" * 60)
    print(f"连接信息: http://{config['host']}:{config['port']}")
    print(f"数据库: {config['database']}")
    print(f"集合名: {collection_name}")
    print("=" * 60)
    
    # 确认操作
    if not skip_confirm:
        confirm = input(f"\n⚠️  警告: 这将删除集合 '{collection_name}' 中的所有数据！\n是否继续? (yes/no): ")
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
        if create_collection_with_schema(client, collection_name, dense_dim):
            # 加载集合
            client.load_collection(collection_name)
            print(f"✅ 集合已加载到内存")
            print("\n📝 提示: 现在可以重新上传文档进行嵌入")
            return True
        else:
            return False
            
    except Exception as e:
        print(f"❌ 操作失败: {e}")
        return False


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Milvus 集合管理工具")
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
        "--dim", "-D",
        type=int,
        default=1024,
        help="稠密向量维度 (默认: 1024)"
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="跳过确认提示"
    )
    
    args = parser.parse_args()
    
    if args.recreate:
        success = recreate_collection(dense_dim=args.dim, skip_confirm=args.yes)
        sys.exit(0 if success else 1)
    elif args.drop_only:
        config = get_milvus_config()
        client = MilvusClient(
            uri=f"http://{config['host']}:{config['port']}",
            db_name=config["database"]
        )
        drop_collection(client, config["collection_name"])
    else:
        # 默认操作：删除并重新创建
        success = recreate_collection(dense_dim=args.dim, skip_confirm=args.yes)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

