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
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from proot_distro.message import log_info, warn
from proot_distro.progress import clear_bar, draw_bytes_bar, fmt_size, progress_active
from proot_distro.helpers.download import retry_http
from proot_distro.helpers.docker.cache import layer_cache_path
from proot_distro.helpers.docker.transport import (
    opener, _ua,
)
from proot_distro.helpers.tar_extract import extract_tar_to_rootfs


# 下载块大小：1 MiB。
_CHUNK_SIZE = 1 << 20

# 单次读取超时：30 秒。
_READ_TIMEOUT = 30.0

# blob 下载最大重试次数。
_BLOB_MAX_RETRIES = 3

# 分块并行下载阈值：大于此值的 blob 使用并行分块下载。
# 小于此值的 blob 使用串行下载（含断点续传）。
_PARALLEL_THRESHOLD = 5 << 20  # 5 MiB

# 分块数量：把单个 blob 切成这么多段并行下载。
_PARALLEL_CHUNKS = 4


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


def _download_chunk(
    url: str, headers: dict, start: int, end: int,
    part_file: str, insecure: bool, timeout: float,
    short_id: str, chunk_idx: int, n_chunks: int,
    progress_state: dict,
) -> int:
    """下载 blob 的一个字节范围 [start, end]，写入 .part 文件的对应位置。

    返回已下载的字节数。使用 ``Range: bytes=start-end`` 请求。
    写入使用 ``seek + write`` 定位到正确偏移，多线程安全（各线程写不同区域）。
    """
    range_headers = {**headers, "Range": f"bytes={start}-{end}"}
    req = urllib.request.Request(url, headers=range_headers)

    def _attempt():
        resp = opener(insecure).open(req, timeout=timeout)
        with resp:
            if resp.status not in (200, 206):
                raise RuntimeError(
                    f"Unexpected status {resp.status} for range {start}-{end}"
                )
            # 206 Partial Content 或 200（服务器忽略 Range）
            offset = start if resp.status == 206 else 0
            written = 0
            with open(part_file, "r+b") as fh:
                fh.seek(offset)
                while True:
                    try:
                        chunk = resp.read(_CHUNK_SIZE)
                    except socket.timeout:
                        raise urllib.error.URLError(
                            f"Read timeout after {timeout}s "
                            f"(chunk {chunk_idx}/{n_chunks}, "
                            f"{written}/{end - start + 1} bytes)"
                        )
                    if not chunk:
                        break
                    fh.write(chunk)
                    written += len(chunk)
                    # 更新全局进度
                    progress_state["done"] += len(chunk)
                    total = progress_state["total"]
                    elapsed = time.monotonic() - progress_state["start"]
                    speed = progress_state["done"] / elapsed if elapsed > 0 else 0
                    draw_bytes_bar(
                        progress_state["done"], total,
                        noun=f"downloaded {fmt_size(int(speed))}/s",
                    )
            return written

    return retry_http(
        _attempt,
        what=f"Downloading layer {short_id} chunk {chunk_idx}/{n_chunks}",
        max_retries=_BLOB_MAX_RETRIES,
        retry_delay=3,
    )


def _download_blob_parallel(
    url: str, headers: dict, total_size: int,
    part_file: str, insecure: bool, timeout: float,
    short_id: str,
) -> None:
    """将单个 blob 分成多块并行下载。

    预分配 .part 文件到目标大小，每个线程下载一个字节范围并
    通过 seek+write 写入对应位置。全部完成后由调用方校验哈希。
    """
    n_chunks = _PARALLEL_CHUNKS
    chunk_size = total_size // n_chunks
    # 最后一块负责剩余字节
    ranges = []
    for i in range(n_chunks):
        start = i * chunk_size
        end = (i + 1) * chunk_size - 1 if i < n_chunks - 1 else total_size - 1
        ranges.append((start, end))

    # 预分配文件
    with open(part_file, "wb") as fh:
        fh.truncate(total_size)

    log_info(f"{short_id}: 并行下载 {n_chunks} 块 "
             f"(总计 {fmt_size(total_size)})...")

    progress_state = {
        "done": 0,
        "total": total_size,
        "start": time.monotonic(),
    }

    errors = []
    with ThreadPoolExecutor(max_workers=n_chunks) as pool:
        futures = {}
        for i, (start, end) in enumerate(ranges):
            fut = pool.submit(
                _download_chunk,
                url, headers, start, end,
                part_file, insecure, timeout,
                short_id, i + 1, n_chunks,
                progress_state,
            )
            futures[fut] = (i, start, end)

        for fut in as_completed(futures):
            i, start, end = futures[fut]
            try:
                fut.result()
            except Exception as exc:
                errors.append((i, start, end, exc))

    clear_bar()

    if errors:
        # 至少有一块失败，清理并报错
        details = "; ".join(
            f"chunk {i+1} ({start}-{end}): {exc}"
            for i, start, end, exc in errors
        )
        raise RuntimeError(
            f"Parallel download failed for {short_id}: {details}"
        )

    elapsed = time.monotonic() - progress_state["start"]
    speed = total_size / elapsed if elapsed > 0 else 0
    log_info(f"{short_id}: 下载完成 "
             f"({fmt_size(total_size)}，{fmt_size(int(speed))}/s)")


def download_blob(
    repo: str, digest: str, token: str, base: str,
    insecure: bool = False, *,
    timeout: float = _READ_TIMEOUT,
    quiet: bool = False,
) -> str:
    """下载 blob 到层缓存；返回本地文件路径。

    通过 sha256 流式校验数据，在与期望的 *digest* 匹配后才提升文件。
    因此缓存中只存在完整的层。

    **并行分块下载**：大于 5 MiB 的 blob 被切成 4 块并行下载
    （类似 aria2 的分块下载）。每个线程使用 HTTP Range 请求
    下载不同的字节范围，写入预分配文件的对应位置。这样即使
    单连接速度只有 500 KB/s，4 路并行也能达到 2 MB/s。

    **断点续传**（仅小 blob）：小于 5 MiB 的 blob 使用串行下载
    带 .part 断点续传支持。

    *timeout*（默认 30s）是单次读取的 socket 超时。

    *quiet* 抑制进度条输出。并发下载多个层时设为 True。
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

    base_headers = {**_ua()}
    if token:
        base_headers["Authorization"] = f"Bearer {token}"

    # ---- 先发 HEAD 请求获取 blob 大小 ----
    blob_size = 0
    head_req = urllib.request.Request(url, headers={**base_headers, "Range": "bytes=0-0"})
    try:
        with opener(insecure).open(head_req, timeout=timeout) as head_resp:
            # 206 Partial Content → 服务器支持 Range
            if head_resp.status == 206:
                cr = head_resp.headers.get("Content-Range", "")
                # Content-Range: bytes 0-0/47400000
                if "/" in cr:
                    blob_size = int(cr.rsplit("/", 1)[-1])
            elif head_resp.status == 200:
                blob_size = int(head_resp.headers.get("Content-Length", 0))
    except Exception:
        pass  # HEAD 失败则回退到串行下载

    # ---- 路径选择：大 blob 并行分块，小 blob 串行续传 ----
    if blob_size >= _PARALLEL_THRESHOLD:
        # 并行分块下载
        def _parallel_attempt():
            _download_blob_parallel(
                url, base_headers, blob_size,
                part, insecure, timeout, short_id,
            )
            # 校验哈希
            hasher = hashlib.sha256()
            with open(part, "rb") as fh:
                while True:
                    chunk = fh.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    hasher.update(chunk)
            actual_hex = hasher.hexdigest()
            if actual_hex != expected_hex:
                _cleanup_part(dest)
                raise RuntimeError(
                    f"Layer integrity check failed for digest '{digest}': "
                    f"expected {expected_hex}, got {actual_hex}."
                )
            os.replace(part, dest)
            return dest

        try:
            return retry_http(
                _parallel_attempt,
                what=f"Downloading layer {short_id}",
                max_retries=2,
                retry_delay=3,
            )
        finally:
            clear_bar()

    # ---- 串行下载（含断点续传） ----
    def _serial_attempt():
        # 检查上次失败留下的部分下载文件。
        existing = 0
        if os.path.isfile(part):
            existing = os.path.getsize(part)

        headers = {**base_headers}
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
            if exc.code == 416 and existing > 0:
                _cleanup_part(dest)
                raise urllib.error.URLError(
                    "Range request failed (416); restarting from scratch"
                )
            raise

        with resp:
            status = resp.status
            if status == 200 and existing > 0:
                existing = 0
                mode = "wb"
                hasher = hashlib.sha256()

            total = int(resp.headers.get("Content-Length", 0))
            if status == 206:
                total = existing + total
            elif total and existing == 0:
                pass
            elif total and existing > 0:
                total = existing + total

            downloaded = existing

            if existing > 0 and not quiet:
                log_info(f"{short_id}: 从 {fmt_size(existing)} 续传")
            if existing > 0:
                with open(part, "rb") as fh:
                    while True:
                        chunk = fh.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        hasher.update(chunk)

            with open(part, mode, buffering=1 << 20) as fh:
                dl_start = time.monotonic()
                last_log = dl_start
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
                        elapsed = time.monotonic() - dl_start
                        speed = (downloaded - existing) / elapsed if elapsed > 0 else 0
                        draw_bytes_bar(
                            downloaded, total,
                            noun=f"downloaded {fmt_size(int(speed))}/s",
                        )
                        # 非 TTY 环境每 5 秒输出一次进度
                        now = time.monotonic()
                        if now - last_log > 5 and not progress_active():
                            last_log = now
                            pct = downloaded * 100 // total if total else 0
                            log_info(f"{short_id}: {pct}% "
                                     f"({fmt_size(downloaded)}/{fmt_size(total)}, "
                                     f"{fmt_size(int(speed))}/s)")

        actual_hex = hasher.hexdigest()
        if actual_hex != expected_hex:
            _cleanup_part(dest)
            raise RuntimeError(
                f"Layer integrity check failed for digest '{digest}': "
                f"expected {expected_hex}, got {actual_hex}."
            )

        os.replace(part, dest)

        dl_elapsed = time.monotonic() - dl_start
        dl_bytes = downloaded - existing
        if dl_elapsed > 0 and dl_bytes > 0:
            speed = dl_bytes / dl_elapsed
            log_info(f"{short_id}: 下载完成 "
                     f"({fmt_size(dl_bytes)}，{fmt_size(int(speed))}/s)")

        return dest

    try:
        result = retry_http(
            _serial_attempt,
            what=f"Downloading layer {short_id}",
            max_retries=_BLOB_MAX_RETRIES,
            retry_delay=3,
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
