#!/usr/bin/env python3
"""
核心对话与工具模块
- 使用 LangChain ChatOpenAI / ChatOllama 对接模型
- 自动注入系统环境信息与可执行历史命令作为上下文
- 支持调试回调
"""

import re
import sys
import os
import shutil
import unicodedata
import warnings

import dotenv
from typing import List, Dict, Any, Optional
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama

from recommend import recommend_commands
from system_info_tool import get_system_info, format_system_info_for_prompt

# 屏蔽 optimum 未安装的警告
warnings.filterwarnings("ignore", message=".*optimum.*")
dotenv.load_dotenv()

# ========== 通用清洗函数 ==========
def _deep_clean(s: str) -> str:
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize('NFKC', s)
    s = re.sub(r'[\ud800-\udfff\ufeff\u200b-\u200f\u2028-\u202f\u00ad\u2060-\u2064]', '', s)
    return s.strip()

def sanitize_text(text: str) -> str:
    return _deep_clean(text)

def clean_command(cmd: str) -> str:
    return _deep_clean(cmd)

# ========== 可执行命令检查 ==========
def is_executable_command(cmd: str) -> bool:
    safe = _deep_clean(cmd)
    if not safe:
        return False
    main_cmd = safe.split()[0]
    return shutil.which(main_cmd) is not None

# ========== 调试回调 ==========
class CustomCallbackHandler(BaseCallbackHandler):
    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs):
        print("======聊天模型开始执行======")
    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs):
        print("======聊天模型结束执行======")
    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs):
        print(f"开始执行当前组件，run_id: {run_id}, 入参：{inputs}")
    def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs):
        print(f"结束执行当前组件，run_id: {run_id}, 执行结果：{outputs}")

# ========== 提示词与推荐上下文 ==========
def read_prompt_from_file(file_path: str = "prompt.md") -> str:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return _deep_clean(f.read().strip())
    except Exception as e:
        print(f"读取 prompt.md 失败: {e}", file=sys.stderr)
        return "You are a helpful assistant."

def format_recommendations_for_prompt(recommendations: list) -> str:
    if not recommendations:
        return ""
    lines = []
    for cmd, sim, freq, _ in recommendations:
        if is_executable_command(cmd):
            lines.append(f"  - `{cmd}` (使用次数: {freq}, 相似度: {sim:.2f})")
    if not lines:
        return ""
    return ("\n\n用户历史中可执行的相关命令（供参考，你可以在回答时提及）：\n" +
            "\n".join(lines))

# ========== LLM 选择 ==========
def get_llm(provider: str = 'cloud', debug: bool = False):
    callbacks = [CustomCallbackHandler()] if debug else []

    if provider == 'local':
        base_url = os.getenv('OLLAMA_BASE_URL', 'http://localhost:8000')
        model = os.getenv('OLLAMA_MODEL', 'qwen3.5:4b')
        llm = ChatOllama(
            model=model,
            base_url=base_url,
            temperature=0.7,
            callbacks=callbacks
        )
    else:  # 默认云端
        api_key = os.getenv('CLOUD_API_KEY')
        base_url = os.getenv('CLOUD_API_BASE', 'https://tokenhub.tencentmaas.com/v1')
        if not api_key:
            raise ValueError("缺少 CLOUD_API_KEY 环境变量，请在 .env 文件中设置")
        llm = ChatOpenAI(
            model="minimax-m2.7",
            openai_api_key=api_key,
            openai_api_base=base_url,
            temperature=0.7,
            streaming=False,
            callbacks=callbacks
        )
    return llm

# ========== 核心对话 ==========
def call_tokenhub_api(user_input: str, history: list = None, provider: str = 'cloud', debug: bool = False) -> tuple:
    base_prompt = read_prompt_from_file("prompt.md")
    user_input = _deep_clean(user_input)

    # 获取系统信息并格式化
    sys_info = get_system_info()
    sys_text = format_system_info_for_prompt(sys_info)

    # 推荐上下文
    rec_text = ""
    try:
        recs = recommend_commands(user_input, top_k=5, alpha=0.5, debug=debug)
        rec_text = format_recommendations_for_prompt(recs)
    except Exception as e:
        print(f"[警告] 推荐命令失败: {e}", file=sys.stderr)

    # 构建增强 prompt：基础提示词 + 系统环境 + 推荐命令
    enhanced_prompt = f"{base_prompt}\n\n当前系统环境:\n{sys_text}"
    if rec_text:
        enhanced_prompt += f"\n\n{rec_text}"

    if history is None:
        history = []

    clean_history = [{"role": h["role"], "content": _deep_clean(h["content"])} for h in history]

    messages = [
        {"role": "system", "content": _deep_clean(enhanced_prompt)},
        *clean_history,
        {"role": "user", "content": user_input}
    ]

    llm = get_llm(provider, debug=debug)
    try:
        resp = llm.invoke(messages)
        reply = _deep_clean(resp.content)
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": reply})
        return reply, history
    except Exception as e:
        error_msg = f"API 调用出错: {_deep_clean(str(e))}"
        print(error_msg, file=sys.stderr)
        return error_msg, history

def continuous_chat(provider: str = 'cloud', debug: bool = False):
    backend = "云端 (TokenHub)" if provider == 'cloud' else "本地 (Ollama)"
    print(f"=== 持续对话 - {backend} （输入'退出'结束）===")
    chat_history = []
    while True:
        try:
            user_input = input("用户：")
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.strip().lower() in ("退出", "exit", "quit"):
            print("对话结束！")
            break
        reply, chat_history = call_tokenhub_api(user_input, chat_history, provider=provider, debug=debug)
        print(f"助手：{reply}")

if __name__ == "__main__":
    continuous_chat()