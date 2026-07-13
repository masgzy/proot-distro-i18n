#
# Proot-Distro - manage proot containers.
#
# Created by Sylirre <sylirre@termux.dev> for Termux project.
# Development assisted by Claude Code (https://claude.ai/code).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

# 架构：Blob 级操作 —— 下载单个层到本地缓存，并将缓存的层应用到
# rootfs 目录。应用器处理 OCI whiteouts（§6.1.2），延迟硬链接复制
# 直到所有常规文件写入完成，并最后戳目录 mtime 以免被中间文件写入覆盖。

import hashlib
import os
import socket
import urllib.error
import urllib.request

from proot_distro.message import log_info
from proot_distro.progress import clear_bar, draw_bytes_bar, fmt_size
from proot_distro.helpers.download import retry_http
from proot_distro.helpers.docker.cache import layer_cache_path
from proot_distro.helpers.docker.transport import (
    opener, _ua,
)
from proot_distro.helpers.tar_extract import extract_tar_to_rootfs


# 下载块大小：1 MiB。在快速镜像源上足以摊薄系统调用开销，
# 又不至于让进度条更新迟钝或占用过多内存。
_CHUNK_SIZE = 1 << 20

# 单次读取超时：30 秒。30 秒内无数据到达则判定连接停滞，触发重试。
# 旧版 120 秒超时意味着一个卡住的镜像源每次读取最多浪费 2 分钟
# 才触发重试。
_READ_TIMEOUT = 30.0

# blob 下载最大重试次数。低于通用的 5 次，因为断点续传意味着
# 每次重试从断点继续，单次重试大概率就能成功。
_BLOB_MAX_RETRIES = 3


def _part_path(dest: str) -> str:
    """返回下载中的持久化 .part 文件路径。"""
    return dest + ".part"


def _cleanup_part(dest: str) -> None:
    """删除残留的 .part 文件（如果存在）。"""
    try:
        os.remove(_part_path(dest))
    except FileNotFoundError:
        pass
    except OSError:
        pass


def download_blob(
    repo: str, digest: str, token: str, base: str,
    insecure: bool = False, *,
    timeout: float = _READ_TIMEOUT,
    quiet: bool = False,
) -> str:
    """下载 blob 到层缓存；返回本地文件路径。

    通过 sha256 流式校验数据，在与期望的 *digest* 匹配后才提升文件。
    因此缓存中只存在完整的层。

    **断点续传**：使用持久化的 ``<dest>.part`` 文件进行下载。如果下载
    中途失败，保留已下载的部分。重试时通过 HTTP ``Range: bytes=N-``
    请求从已下载的字节偏移处继续。这避免了瞬态网络错误中断大层时
    重新下载整个 blob。

    *timeout*（默认 30s）是单次读取的 socket 超时 —— 如果在此时间内
    无数据到达，判定连接停滞。

    *quiet* 抑制逐块进度条输出。并发下载多个层时设为 True，避免
    多个进度条在终端上互相覆盖。
    """
    dest = layer_cache_path(digest)
    if os.path.isfile(dest):
        return dest

    if ":" not in digest:
        raise RuntimeError(f"Malformed layer digest '{digest}'.")
    algo, expected_hex = digest.split(":", 1)
    if algo.lower() != "sha256":
        raise RuntimeError(
            f"Unsupported layer digest algorithm '{algo}' (only sha256 "
            f"is supported)."
        )

    url = f"{base}/v2/{repo}/blobs/{digest}"
    expected_hex = expected_hex.lower()
    short_id = digest.split(":")[-1][:12]
    part = _part_path(dest)

    def _attempt():
        # 检查上次失败留下的部分下载文件。
        existing = 0
        if os.path.isfile(part):
            existing = os.path.getsize(part)

        headers = {**_ua()}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"

        req = urllib.request.Request(url, headers=headers)

        # 每次尝试新建哈希器。如果是续传，需要预读已有字节
        # 喂给哈希器，使最终哈希覆盖完整 blob。
        hasher = hashlib.sha256()
        mode = "ab" if existing > 0 else "wb"

        try:
            resp = opener(insecure).open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            # 416 Range Not Satisfiable：.part 文件比实际 blob 大
            # （不应发生，但通过从头开始处理）。
            if exc.code == 416 and existing > 0:
                _cleanup_part(dest)
                raise urllib.error.URLError(
                    "Range request failed (416); restarting from scratch"
                )
            raise

        with resp:
            status = resp.status
            # 如果服务器忽略 Range 并返回 200（完整内容），
            # 必须从头开始。
            if status == 200 and existing > 0:
                existing = 0
                mode = "wb"
                hasher = hashlib.sha256()

            total = int(resp.headers.get("Content-Length", 0))
            if status == 206:
                # Partial Content：Content-Length 是剩余字节数。
                total = existing + total
            elif total and existing == 0:
                pass  # 完整下载，total 已正确。
            elif total and existing > 0:
                total = existing + total

            downloaded = existing

            # 如果是续传，将已有文件内容喂给哈希器。
            if existing > 0 and not quiet:
                log_info(f"{short_id}: Resuming from {fmt_size(existing)}")
            if existing > 0:
                with open(part, "rb") as fh:
                    while True:
                        chunk = fh.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        hasher.update(chunk)

            with open(part, mode) as fh:
                while True:
                    try:
                        chunk = resp.read(_CHUNK_SIZE)
                    except socket.timeout:
                        raise urllib.error.URLError(
                            f"Read timeout after {timeout}s "
                            f"(downloaded {downloaded}/{total} bytes)"
                        )
                    if not chunk:
                        break
                    fh.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    if not quiet:
                        draw_bytes_bar(downloaded, total, noun="downloaded")

        actual_hex = hasher.hexdigest()
        if actual_hex != expected_hex:
            # 哈希不匹配 —— 部分文件已损坏。删除它使下次重试从头开始。
            _cleanup_part(dest)
            raise RuntimeError(
                f"Layer integrity check failed for digest '{digest}': "
                f"expected {expected_hex}, got {actual_hex}."
            )

        # 成功 —— 原子地将 .part 提升为最终路径。
        os.replace(part, dest)
        return dest

    try:
        result = retry_http(
            _attempt,
            what=f"Downloading layer {short_id}",
            max_retries=_BLOB_MAX_RETRIES,
        )
    finally:
        if not quiet:
            clear_bar()

    return result


def apply_layer(layer_path: str, rootfs_dir: str) -> None:
    """将一个 OCI/Docker 层（gzip 压缩的 tar）应用到 rootfs_dir。

    是 extract_tar_to_rootfs 的薄封装，开启 OCI whiteout 处理
    （.wh.<name> 删除同级条目，.wh..wh..opq 清空父目录）。
    详见该函数的完整提取不变式说明。
    """
    extract_tar_to_rootfs(layer_path, rootfs_dir, handle_whiteouts=True)
