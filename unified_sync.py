#!/usr/bin/env python3
"""
统一同步脚本：支持增量同步或全量重建 embedding。
- 增量模式（默认）：从上次时间戳之后导入新命令，自动清洗并生成归一化 embedding。
- 重建模式（--rebuild）：清空所有 embedding，重新为有效命令生成 768 维 Jina 向量。

用法：
    python unified_sync.py                 # 增量同步
    python unified_sync.py --rebuild       # 全量重建（会删除无效命令）
    python unified_sync.py --help          # 查看帮助
"""

import os
import sys
import re
import argparse
import unicodedata
import warnings
from pathlib import Path
from collections import Counter
from typing import List, Tuple, Optional

import torch
import psycopg2
import numpy as np
from sentence_transformers import SentenceTransformer

from config import DB_CONFIG, EMBEDDING_MODEL, ZSH_HISTORY_PATH

# 屏蔽 optimum 未安装的警告
warnings.filterwarnings("ignore", message=".*optimum.*")

# 正则匹配 zsh 历史行
LINE_PATTERN = re.compile(r'^:\s*(\d+):\d+;(.*)$')

# ---------- 模型加载 ----------
_model = None

def _get_model():
    """按需加载 Jina 模型（768 维），优先使用 GPU 但提供 OOM 保护"""
    global _model
    if _model is not None:
        return _model

    os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')
    os.environ.setdefault('HF_MIRROR', 'https://hf-mirror.com')

    # 检测 GPU
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    torch_dtype = 'float16' if device == 'cuda' else 'float32'

    try:
        _model = SentenceTransformer(
            EMBEDDING_MODEL,
            trust_remote_code=True,
            model_kwargs={'attn_implementation': 'eager', 'torch_dtype': torch_dtype},
            device=device,
            local_files_only=True
        )
    except Exception:
        # 回退到普通加载（CPU）
        _model = SentenceTransformer(EMBEDDING_MODEL, device='cpu')
    return _model

# ---------- 文本清洗 ----------
def clean_command(cmd: str) -> str:
    if not cmd:
        return ""
    cmd = unicodedata.normalize('NFKC', cmd)
    cmd = re.sub(r'[\ud800-\udfff\ufeff\u200b-\u200f\u2028-\u202f\u00ad\u2060-\u2064]', '', cmd)
    return cmd.strip()

# ---------- 解析历史文件 ----------
def parse_new_commands(last_ts: int) -> List[Tuple[int, str]]:
    history_file = Path(ZSH_HISTORY_PATH)
    if not history_file.exists():
        return []

    new_cmds = []
    current_ts: Optional[int] = None
    current_lines: List[str] = []
    skip_current: bool = False

    with open(history_file, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.rstrip('\n')
            m = LINE_PATTERN.match(line)
            if m:
                # 处理上一条完整命令
                if current_ts is not None and current_lines and not skip_current and current_ts > last_ts:
                    full_cmd = '\n'.join(current_lines)
                    clean = clean_command(full_cmd)
                    if clean:
                        new_cmds.append((current_ts, clean))

                current_ts = int(m.group(1))
                cmd = m.group(2).strip()
                current_lines = []
                skip_current = False
                if not cmd or cmd.startswith(' '):
                    skip_current = True
                else:
                    current_lines.append(cmd)
            else:
                if current_ts is not None and not skip_current:
                    current_lines.append(line)

        # 文件末尾命令
        if current_ts is not None and current_lines and not skip_current and current_ts > last_ts:
            full_cmd = '\n'.join(current_lines)
            clean = clean_command(full_cmd)
            if clean:
                new_cmds.append((current_ts, clean))
    return new_cmds

# ---------- 生成归一化向量 ----------
def embed_commands(commands: List[str], max_seq_length: int = 128) -> List[List[float]]:
    if not commands:
        return []
    model = _get_model()
    model.tokenizer.model_max_length = max_seq_length
    batch_size = 32 if torch.cuda.is_available() else 64

    try:
        vectors = model.encode(
            commands,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True   # 直接归一化，省去 L2 步骤
        )
    except torch.cuda.OutOfMemoryError:
        print("GPU 显存不足，自动切换为 CPU 推理", file=sys.stderr)
        model.to('cpu')
        vectors = model.encode(
            commands,
            batch_size=8,
            show_progress_bar=False,
            normalize_embeddings=True
        )
    return vectors.tolist()

# ---------- 数据库操作 ----------
def get_last_timestamp(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT value FROM sync_metadata WHERE key = 'last_timestamp'")
    row = cur.fetchone()
    cur.close()
    return int(row[0]) if row else 0

def set_last_timestamp(conn, ts: int):
    cur = conn.cursor()
    cur.execute("UPDATE sync_metadata SET value = %s WHERE key = 'last_timestamp'", (str(ts),))
    cur.close()
    conn.commit()

def upsert_commands(conn, freq_dict: dict):
    """批量 upsert 命令、频率和 embedding"""
    if not freq_dict:
        return
    commands = list(freq_dict.keys())
    vectors = embed_commands(commands)
    data = [(cmd, freq_dict[cmd], vec) for cmd, vec in zip(commands, vectors)]

    cur = conn.cursor()
    from psycopg2.extras import execute_values
    execute_values(cur,
        """
        INSERT INTO command_stats (command, frequency, embedding)
        VALUES %s
        ON CONFLICT (command) DO UPDATE SET
            frequency = command_stats.frequency + EXCLUDED.frequency,
            embedding = EXCLUDED.embedding
        """,
        data, template="(%s, %s, %s::vector)")
    cur.close()
    conn.commit()

def incremental_sync():
    """增量同步：只处理新命令"""
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False

    last_ts = get_last_timestamp(conn)
    new_entries = parse_new_commands(last_ts)

    if not new_entries:
        print("No new commands found.")
        conn.close()
        return

    freq_counter = Counter()
    max_ts = last_ts
    for ts, cmd in new_entries:
        freq_counter[cmd] += 1
        if ts > max_ts:
            max_ts = ts

    upsert_commands(conn, dict(freq_counter))
    set_last_timestamp(conn, max_ts)

    conn.close()
    print(f"Synced {len(new_entries)} commands, last timestamp now {max_ts}")

def rebuild_embeddings():
    """全量重建：清空旧向量，清洗无效命令，重新生成 embedding"""
    import shutil  # 用于 is_valid 检查可执行性

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # 1. 清空所有 embedding
    cur.execute("UPDATE command_stats SET embedding = NULL")
    conn.commit()

    # 2. 获取所有命令
    cur.execute("SELECT id, command FROM command_stats")
    rows = cur.fetchall()
    print(f"总命令数: {len(rows)}")

    # 3. 判断有效命令（清洗 + 可执行检查）
    def is_valid(cmd):
        if not cmd: return False
        if len(cmd) > 150: return False
        if re.search(r'[\u4e00-\u9fff]', cmd): return False
        return True

    valid, invalid_ids = [], []
    for rid, cmd in rows:
        cleaned = clean_command(cmd)
        if is_valid(cleaned) and shutil.which(cleaned.split()[0]) is not None:
            valid.append((rid, cleaned))
        else:
            invalid_ids.append(rid)

    if invalid_ids:
        cur.execute("DELETE FROM command_stats WHERE id = ANY(%s)", (invalid_ids,))
        print(f"删除无效命令 {len(invalid_ids)} 条")

    # 4. 批量生成归一化向量并更新
    batch_size = 128 if torch.cuda.is_available() else 64
    for i in range(0, len(valid), batch_size):
        batch = valid[i:i+batch_size]
        ids, cmds = zip(*batch)
        vecs = embed_commands(list(cmds))
        for rid, vec in zip(ids, vecs):
            vec_str = '[' + ', '.join(f'{x:.15f}' for x in vec) + ']'
            cur.execute("UPDATE command_stats SET embedding = %s::vector WHERE id = %s", (vec_str, rid))

    conn.commit()
    cur.close()
    conn.close()
    print(f"✅ 重建完成，有效命令 {len(valid)} 条")

# ---------- 命令行入口 ----------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="统一同步工具")
    parser.add_argument("--rebuild", action="store_true",
                        help="全量重建所有命令的 embedding（会删除无效命令）")
    args = parser.parse_args()

    if args.rebuild:
        print("开始全量重建 embedding ...")
        rebuild_embeddings()
    else:
        print("开始增量同步 ...")
        incremental_sync()