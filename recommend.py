#!/usr/bin/env python3
"""
根据用户输入（自然语言或命令片段）推荐最相关的历史命令。
使用 Jina 模型 (768 维) + pgvector 高效读取向量。
"""
import os
import re
import warnings
import unicodedata
import psycopg2
import numpy as np
from sentence_transformers import SentenceTransformer
from pgvector.psycopg2 import register_vector
from config import DB_CONFIG, EMBEDDING_MODEL

# 忽略 optimum 未安装的警告
warnings.filterwarnings("ignore", message=".*optimum.*")

_model = None

def _get_model(debug: bool = False):
    """加载模型，支持离线优先，静默或调试模式"""
    global _model
    if _model is not None:
        return _model

    if not debug:
        os.environ['SENTENCE_TRANSFORMERS_SILENCE'] = '1'
        os.environ['DISABLE_TQDM'] = '1'
        from transformers import logging as hf_logging
        hf_logging.set_verbosity_error()

    os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')
    os.environ.setdefault('HF_MIRROR', 'https://hf-mirror.com')

    try:
        # 优先尝试 Jina 模型加载（需要 trust_remote_code）
        _model = SentenceTransformer(
            EMBEDDING_MODEL,
            trust_remote_code=True,
            model_kwargs={'attn_implementation': 'eager'},
            local_files_only=True
        )
        return _model
    except Exception:
        # 回退到普通模型加载
        _model = SentenceTransformer(EMBEDDING_MODEL)
        return _model

def sanitize_text(text: str) -> str:
    """清洗命令文本：全角转半角、移除不可见字符"""
    if not isinstance(text, str):
        return text
    text = unicodedata.normalize('NFKC', text)
    text = re.sub(r'[\ud800-\udfff\ufeff\u200b-\u200f\u2028-\u202f\u00ad\u2060-\u2064]', '', text)
    return text.strip()

def recommend_commands(input_text: str, top_k: int = 5, alpha: float = 0.5, debug: bool = False):
    """
    返回推荐命令列表，按混合得分排序。
    alpha: 频率权重 (0~1)，0 表示纯相似度，1 表示纯频率。
    """
    model = _get_model(debug=debug)

    # 编码查询向量并归一化
    query_vec = model.encode([input_text])[0]
    query_vec = query_vec / np.linalg.norm(query_vec)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except Exception as e:
        if debug:
            print(f"数据库连接失败: {e}")
        return []

    try:
        # 注册 pgvector 适配器，以便直接读取向量
        register_vector(conn)
        cur = conn.cursor()
        cur.execute("""
            SELECT id, command, frequency, embedding
            FROM command_stats
            WHERE embedding IS NOT NULL;
        """)
        rows = cur.fetchall()
        cur.close()
    except Exception as e:
        if debug:
            print(f"数据库查询失败: {e}")
        conn.close()
        return []
    finally:
        conn.close()

    if not rows:
        return []

    # 提取字段
    ids, commands, freqs, embs = [], [], [], []
    for row in rows:
        ids.append(row[0])
        commands.append(row[1])
        freqs.append(row[2])
        # pgvector 的 Vector 可直接转为 numpy 数组
        embs.append(np.array(row[3]))

    embs = np.array(embs)  # shape: (N, 768)

    # 归一化数据库向量
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1
    embs = embs / norms

    # 余弦相似度（点积）
    sims = np.dot(embs, query_vec)

    # 混合得分（相似度 + 频率）
    max_freq = max(freqs) if freqs else 1
    freq_norm = np.array(freqs) / max_freq
    scores = (1 - alpha) * sims + alpha * freq_norm

    # 按得分降序取 top_k
    sorted_indices = np.argsort(scores)[::-1][:top_k]

    results = []
    for idx in sorted_indices:
        cmd_clean = sanitize_text(commands[idx])
        results.append((cmd_clean, sims[idx], freqs[idx], scores[idx]))

    return results

def format_recommendations(recommendations: list) -> str:
    """将推荐结果格式化为可读文本"""
    if not recommendations:
        return "未找到相关命令。"
    lines = ["📌 根据你的使用习惯，以下命令可能对你有帮助："]
    for i, (cmd, sim, freq, score) in enumerate(recommendations, 1):
        lines.append(f"  {i}. `{cmd}`  (相似度: {sim:.2f}, 使用次数: {freq})")
    return "\n".join(lines)

if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "列出当前目录"
    recs = recommend_commands(query)
    print(format_recommendations(recs))