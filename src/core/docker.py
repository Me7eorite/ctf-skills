"""Docker CLI 隔离工具。

项目中只有本模块允许调用 ``docker`` 子进程做镜像检查。
其他模块必须通过 ``image_exists`` 函数来判断镜像是否存在，
禁止直接 import subprocess 来调用 docker 命令。
"""

from __future__ import annotations

import subprocess

# Docker inspect 的默认超时时间（秒）。防止网络不通或 docker daemon 卡死时永久阻塞。
_DEFAULT_TIMEOUT_SECONDS = 5.0


def image_exists(image: str, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> bool:
    """检查本地 Docker 镜像是否存在。

    参数:
        image: 镜像标签（如 "ubuntu:22.04"）
        timeout: 子进程超时秒数

    返回:
        True 表示镜像存在，False 表示不存在或检查失败。

    【不会抛出异常】以下情况统一返回 False：
      - image 为空字符串
      - docker 命令不在系统 PATH 中
      - inspect 命令执行超时
      - docker image inspect 返回非零退出码（镜像不存在）
    """
    # 空镜像名直接返回 False
    if not image:
        return False
    try:
        # 调用 docker image inspect 检查镜像
        # shell=False 确保安全性（不经过 shell 解释）
        # check=False 避免非零退出码时抛出 CalledProcessError
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL,  # 丢弃正常输出
            stderr=subprocess.DEVNULL,  # 丢弃错误输出
            timeout=timeout,
            shell=False,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # docker 命令不存在 / 超时 / 其他系统错误 → 视为镜像不可用
        return False
    # 退出码为 0 表示镜像存在
    return result.returncode == 0
