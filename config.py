# config.py
import os

DB_CONFIG = {
    'dbname': os.getenv('PG_DB', 'linux_command_db'),
    'user': os.getenv('PG_USER', 'postgres'),
    'password': os.getenv('PG_PASSWORD', 'root'),
    'host': os.getenv('PG_HOST', 'localhost'),
    'port': int(os.getenv('PG_PORT', 5433))
}

# 模型名称
EMBEDDING_MODEL = './models/jina-embeddings-v2-base-code'

# 历史文件路径
ZSH_HISTORY_PATH = os.path.expanduser('~/.zsh_history')