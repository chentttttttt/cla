#!/usr/bin/env python3
"""使用 Jina 模型全量重建 embedding（768 维）"""
import os, re, shutil, unicodedata, psycopg2, numpy as np
from sentence_transformers import SentenceTransformer
from config import DB_CONFIG, EMBEDDING_MODEL

# 确保镜像
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'

model = SentenceTransformer(
    EMBEDDING_MODEL,
    trust_remote_code=True,
    model_kwargs={'attn_implementation': 'eager'},
    local_files_only=True
)
print(f"模型维度: {model.encode(['test']).shape[1]}")

# ---------- 2. 清洗工具 ----------
def clean_cmd(s):
    s = unicodedata.normalize('NFKC', s)
    s = re.sub(r'[\ud800-\udfff\ufeff\u200b-\u200f\u2028-\u202f\u00ad\u2060-\u2064]', '', s)
    return s.strip()

def is_valid(cmd):
    if not cmd: return False
    if len(cmd) > 150: return False
    if re.search(r'[\u4e00-\u9fff]', cmd): return False   # 有中文则丢弃（乱码）
    return True

# ---------- 3. 数据库操作 ----------
conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor()

# 清空旧向量
cur.execute("UPDATE command_stats SET embedding = NULL")
conn.commit()

cur.execute("SELECT id, command FROM command_stats")
rows = cur.fetchall()
print(f"总命令数: {len(rows)}")

valid, invalid_ids = [], []
for rid, cmd in rows:
    cleaned = clean_cmd(cmd)
    if is_valid(cleaned) and shutil.which(cleaned.split()[0]) is not None:
        valid.append((rid, cleaned))
    else:
        invalid_ids.append(rid)

if invalid_ids:
    cur.execute("DELETE FROM command_stats WHERE id = ANY(%s)", (invalid_ids,))
    print(f"删除无效命令 {len(invalid_ids)} 条")

# 批量生成归一化向量并更新
batch_size = 128
for i in range(0, len(valid), batch_size):
    batch = valid[i:i+batch_size]
    ids, cmds = zip(*batch)
    vecs = model.encode(cmds, show_progress_bar=False)
    # L2 归一化
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1
    vecs = vecs / norms
    for rid, vec in zip(ids, vecs):
        vec_str = '[' + ', '.join(f'{x:.15f}' for x in vec) + ']'
        cur.execute("UPDATE command_stats SET embedding = %s::vector WHERE id = %s", (vec_str, rid))

conn.commit()
cur.close()
conn.close()
print(f"✅ 重建完成，有效命令 {len(valid)} 条")