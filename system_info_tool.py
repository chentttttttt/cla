import os
import platform
import shutil
from typing import Dict, List, Any

def get_system_info() -> Dict[str, Any]:
    """收集当前系统环境信息，返回字典"""
    info = {
        "current_directory": os.getcwd(),
        "home_directory": os.path.expanduser("~"),
        "operating_system": platform.system(),
        "os_release": platform.release(),
        "user": os.getenv("USER") or os.getenv("USERNAME"),
        "shell": os.environ.get("SHELL", ""),
        # 常用工具是否存在（可选）
        "has_git": shutil.which("git") is not None,
        "has_docker": shutil.which("docker") is not None,
        "has_python": shutil.which("python") is not None or shutil.which("python3") is not None,
        "has_gcc": shutil.which("gcc") is not None,
        "has_gpp": shutil.which("g++") is not None,
        "has_cmake": shutil.which("cmake") is not None,
        # 可扩展：磁盘空间、内存等（需要 psutil）
        # "disk_usage": shutil.disk_usage("/").free // (1024**3) if hasattr(shutil, 'disk_usage') else None,
    }
    return info

def format_system_info_for_prompt(info: Dict[str, Any]) -> str:
    """将系统信息格式化为适合放入 prompt 的文本"""
    lines = [f"- 当前目录: {info['current_directory']}",
             f"- 操作系统: {info['operating_system']} {info['os_release']}",
             f"- 用户: {info['user']}",
             f"- Shell: {info['shell']}"]
    # 可用工具
    tools_available = [k for k, v in info.items() if k.startswith("has_") and v]
    if tools_available:
        tools = [t.replace("has_", "") for t in tools_available]
        lines.append(f"- 已安装的常用命令: {', '.join(tools)}")
    return "\n".join(lines)