#!/usr/bin/env python3
"""
初始化 PostgreSQL 数据库：
- 创建 command_stats 表（主键 command，字段 frequency，embedding vector(384)）
- 创建 sync_metadata 表存储检查点（last_timestamp）
- 创建向量索引（IVFFlat）
"""
import psycopg2
from config import DB_CONFIG

def init_db():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    cur = conn.cursor()

    # 启用 pgvector 扩展（如果尚未启用）
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 命令统计表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS command_stats (
            command TEXT PRIMARY KEY,
            frequency INTEGER NOT NULL DEFAULT 1,
            embedding vector(768)
        );
    """)

    # 元数据表（检查点）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sync_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    cur.execute("""
        INSERT INTO sync_metadata (key, value) VALUES ('last_timestamp', '0')
        ON CONFLICT (key) DO NOTHING;
    """)

    # 创建向量索引（使用余弦距离）
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_command_embedding
        ON command_stats USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100);
    """)

    cur.close()
    conn.close()
    print("Database initialized successfully.")

if __name__ == "__main__":
    init_db()