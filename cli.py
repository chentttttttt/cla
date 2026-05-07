#!/usr/bin/env python3
"""
命令行助手 - 支持云端/本地模型切换，debug 模式
"""
import re
import sys
import shutil
import unicodedata
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from help import call_tokenhub_api, continuous_chat, _deep_clean
from recommend import recommend_commands

# ========== 通用清洗函数（与 help.py 一致） ==========
def is_executable_command(cmd: str) -> bool:
    safe = _deep_clean(cmd)
    if not safe:
        return False
    main_cmd = safe.split()[0]   # 第一个词为命令名
    return shutil.which(main_cmd) is not None

# ========== 推荐输出 ==========
def print_recommendations(query: str, top_k: int = 3, debug: bool = False):
    # 取较多候选，alpha=0 表示只按相似度排序
    recs = recommend_commands(query, top_k=20, alpha=0.0, debug=debug)
    if not recs:
        print("未找到相关命令。")
        return

    # 过滤可执行命令
    executable = [(cmd, sim, freq, score) for cmd, sim, freq, score in recs
                  if is_executable_command(cmd)]
    if not executable:
        print("未找到可执行的相关命令。")
        return

    # 按相似度降序（已排序，但确保万无一失）
    executable.sort(key=lambda x: x[1], reverse=True)
    top_exec = executable[:top_k]

    print(f"\n🔍 根据「{query}」推荐以下可执行命令：")
    for i, (cmd, sim, freq, _) in enumerate(top_exec, 1):
        print(f"  {i}. {cmd}  (相似度: {sim:.2f}, 使用次数: {freq})")

# ========== 入口 ==========
def main():
    parser = argparse.ArgumentParser(description="智能命令行助手")
    parser.add_argument("query", nargs="*", help="要询问的问题或命令描述")
    parser.add_argument("--recommend-only", "-r", action="store_true",
                        help="仅推荐命令，不调用模型")
    parser.add_argument("--top", type=int, default=3, help="推荐数量")
    parser.add_argument("--local", action="store_true",
                        help="使用本地 Ollama 模型，否则使用云端 API")
    parser.add_argument("--debug", action="store_true",
                        help="启用调试输出（显示回调信息）")
    args = parser.parse_args()

    provider = 'local' if args.local else 'cloud'
    debug = args.debug

    if not args.query:
        continuous_chat(provider=provider, debug=debug)
        return

    query_str = _deep_clean(" ".join(args.query))

    if args.recommend_only:
        print_recommendations(query_str, top_k=args.top, debug=debug)
    else:
        reply, _ = call_tokenhub_api(query_str, history=None, provider=provider, debug=debug)
        print(reply)

if __name__ == "__main__":
    main()