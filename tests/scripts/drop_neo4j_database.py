"""
清空 Neo4j 数据库脚本
用于删除所有节点和关系，重建知识图谱
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from neo4j import GraphDatabase

# 导入配置
try:
    from app.core.config import Config
except ImportError:
    from config.config import Config


def get_neo4j_config():
    """获取 Neo4j 连接配置。

    Returns:
        dict: Neo4j URI、用户名和密码。
    """
    conf = Config()
    return {
        "uri": conf.NEO4J_URI,
        "user": conf.NEO4J_USER,
        "password": conf.NEO4J_PASSWORD
    }


def get_database_stats(driver) -> dict:
    """获取数据库统计信息。

    Args:
        driver: Neo4j 驱动实例，用于创建查询会话。

    Returns:
        dict: 包含节点数、关系数、标签分布和关系类型分布的统计结果。
    """
    with driver.session() as session:
        # 节点统计
        node_result = session.run("MATCH (n) RETURN count(n) as node_count")
        node_count = node_result.single()["node_count"]

        # 关系统计
        rel_result = session.run("MATCH ()-[r]->() RETURN count(r) as rel_count")
        rel_count = rel_result.single()["rel_count"]

        # 标签统计
        label_result = session.run("""
            MATCH (n)
            UNWIND labels(n) AS label
            RETURN DISTINCT label, count(n) as count
            ORDER BY count DESC
        """)
        labels = [{"label": record["label"], "count": record["count"]}
                  for record in label_result]

        # 关系类型统计
        rel_type_result = session.run("""
            MATCH ()-[r]->()
            RETURN DISTINCT type(r) as rel_type, count(r) as count
            ORDER BY count DESC
        """)
        rel_types = [{"rel_type": record["rel_type"], "count": record["count"]}
                    for record in rel_type_result]

        return {
            "node_count": node_count,
            "rel_count": rel_count,
            "labels": labels,
            "rel_types": rel_types
        }


def clear_database(driver, skip_confirm: bool = False) -> bool:
    """
    清空数据库（删除所有节点和关系）

    Args:
        driver: Neo4j 驱动
        skip_confirm: 是否跳过确认提示

    Returns:
        是否成功
    """
    try:
        # 获取当前统计
        stats = get_database_stats(driver)

        print("=" * 60)
        print("Neo4j 数据库统计")
        print("=" * 60)
        print(f"节点总数: {stats['node_count']}")
        print(f"关系总数: {stats['rel_count']}")
        print(f"标签类型数: {len(stats['labels'])}")
        print(f"关系类型数: {len(stats['rel_types'])}")

        if stats['labels']:
            print("\n标签分布:")
            for label_info in stats['labels'][:10]:
                print(f"  - {label_info['label']}: {label_info['count']}")
            if len(stats['labels']) > 10:
                print(f"  ... 还有 {len(stats['labels']) - 10} 种标签")

        if stats['rel_types']:
            print("\n关系类型分布:")
            for rel_info in stats['rel_types'][:10]:
                print(f"  - {rel_info['rel_type']}: {rel_info['count']}")
            if len(stats['rel_types']) > 10:
                print(f"  ... 还有 {len(stats['rel_types']) - 10} 种关系")

        print("=" * 60)

        # 确认操作
        if not skip_confirm:
            if stats['node_count'] == 0:
                print("ℹ️  数据库为空，无需清空")
                return True

            confirm = input(f"\n⚠️  警告: 这将删除所有 {stats['node_count']} 个节点和 {stats['rel_count']} 条关系！\n是否继续? (yes/no): ")
            if confirm.lower() not in ['yes', 'y']:
                print("❌ 操作已取消")
                return False

        # 执行清空操作
        print("\n正在清空数据库...")

        with driver.session() as session:
            # 删除所有节点和关系
            session.run("MATCH (n) DETACH DELETE n")

            print(f"✅ 数据库已清空")

            # 验证
            verify_result = session.run("MATCH (n) RETURN count(n) as count")
            final_count = verify_result.single()["count"]

            if final_count == 0:
                print("✅ 验证成功: 数据库已完全清空")
                return True
            else:
                print(f"⚠️  警告: 仍有 {final_count} 个节点未删除")
                return False

    except Exception as e:
        print(f"❌ 清空数据库失败: {e}")
        return False


def main():
    """主函数。

    Returns:
        None: 无返回值。
    """
    import argparse

    parser = argparse.ArgumentParser(description="Neo4j 数据库管理工具")
    parser.add_argument(
        "--clear", "-c",
        action="store_true",
        help="清空数据库（删除所有节点和关系）"
    )
    parser.add_argument(
        "--stats", "-s",
        action="store_true",
        help="仅显示数据库统计信息"
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="跳过确认提示"
    )

    args = parser.parse_args()

    config = get_neo4j_config()

    print("=" * 60)
    print("Neo4j 数据库管理工具")
    print("=" * 60)
    print(f"连接信息: {config['uri']}")
    print(f"用户: {config['user']}")
    print("=" * 60)

    try:
        driver = GraphDatabase.driver(config['uri'], auth=(config['user'], config['password']))

        if args.stats:
            # 仅显示统计
            stats = get_database_stats(driver)
            print("=" * 60)
            print("数据库统计信息")
            print("=" * 60)
            print(f"节点总数: {stats['node_count']}")
            print(f"关系总数: {stats['rel_count']}")
            print(f"标签类型数: {len(stats['labels'])}")
            print(f"关系类型数: {len(stats['rel_types'])}")

            if stats['labels']:
                print("\n标签分布:")
                for label_info in stats['labels']:
                    print(f"  - {label_info['label']}: {label_info['count']}")

            if stats['rel_types']:
                print("\n关系类型分布:")
                for rel_info in stats['rel_types']:
                    print(f"  - {rel_info['rel_type']}: {rel_info['count']}")

            print("=" * 60)

        elif args.clear:
            # 清空数据库
            success = clear_database(driver, skip_confirm=args.yes)
            sys.exit(0 if success else 1)
        else:
            # 默认操作：显示统计并询问是否清空
            stats = get_database_stats(driver)
            print("=" * 60)
            print("数据库统计信息")
            print("=" * 60)
            print(f"节点总数: {stats['node_count']}")
            print(f"关系总数: {stats['rel_count']}")
            print(f"标签类型数: {len(stats['labels'])}")
            print(f"关系类型数: {len(stats['rel_types'])}")
            print("=" * 60)

            if stats['node_count'] > 0:
                confirm = input(f"\n是否清空数据库? (yes/no): ")
                if confirm.lower() in ['yes', 'y']:
                    success = clear_database(driver, skip_confirm=True)
                    sys.exit(0 if success else 1)
                else:
                    print("❌ 操作已取消")
                    sys.exit(0)
            else:
                print("ℹ️  数据库为空")
                sys.exit(0)

    except Exception as e:
        print(f"❌ 操作失败: {e}")
        sys.exit(1)
    finally:
        if 'driver' in locals():
            driver.close()


if __name__ == "__main__":
    main()
