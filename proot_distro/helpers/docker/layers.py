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

# Architecture: Blob-level operations — download a single layer to the
# local cache, and apply a cached layer to a rootfs directory. The
# applier handles OCI whiteouts (§6.1.2), defers hard-link copies until
# all regular files are written, and stamps directory mtimes last so
# they don't get clobbered by intermediate file writes.

import hashlib
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from proot_distro.atomic import atomic_replace
from proot_distro.i18n import _
from proot_distro.message import log_info
from proot_distro.progress import clear_bar, draw_bytes_bar, fmt_size
from proot_distro.helpers.download import (
    declared_length, new_request, require_complete_body, retry_http,
)
from proot_distro.helpers.docker.cache import (
    layer_cache_path, open_verified_layer, split_digest,
)
from proot_distro.helpers.docker.transport import (
    opener, _ua,
)
from proot_distro.helpers.tar_extract import extract_tar_fd_to_rootfs


# 下载块大小：1 MiB。
_CHUNK_SIZE = 1 << 20

# 分块并行下载阈值：大于此值的 blob 使用并行分块下载（fork 优化，
# 类似 aria2 的分块下载）。单连接慢的网络（如国内访问 Docker Hub）
# 下，4 路并行可显著提升吞吐。
_PARALLEL_THRESHOLD = 5 << 20  # 5 MiB

# 分块数量：把单个 blob 切成这么多段并行下载。
_PARALLEL_CHUNKS = 4


def _download_chunk_range(
    url: str, headers: dict, start: int, end: int,
    tmp_fd: int, insecure: bool, timeout: float,
    short_id: str, chunk_idx: int, n_chunks: int,
    progress: dict, progress_lock: threading.Lock,
) -> None:
    """下载 blob 的一个字节范围 [start, end]，用 pwrite 写入 tmp_fd。"""
    range_headers = {**headers, "Range": f"bytes={start}-{end}"}
    req = new_request(url, headers=range_headers)

    def _attempt():
        resp = opener(insecure).open(req)
        with resp:
            if resp.status not in (200, 206):
                raise RuntimeError(
                    f"Unexpected status {resp.status} for range {start}-{end}"
                )
            offset = start if resp.status == 206 else 0
            written = 0
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
                os.pwrite(tmp_fd, chunk, offset + written)
                written += len(chunk)
                # 更新共享进度（加锁保护 += ）
                with progress_lock:
                    progress["done"] += len(chunk)
                    done, total = progress["done"], progress["total"]
                elapsed = time.monotonic() - progress["start"]
                speed = done / elapsed if elapsed > 0 else 0
                draw_bytes_bar(
                    done, total,
                    noun=_("downloaded {speed}/s", speed=fmt_size(int(speed))),
                )
            return written

    return retry_http(
        _attempt,
        what=_("Downloading layer {short_id} chunk {idx}/{n}",
               short_id=short_id, idx=chunk_idx, n=n_chunks),
        max_retries=3,
        retry_delay=3,
    )


def _probe_blob_size(
    url: str, headers: dict, insecure: bool, timeout: float,
) -> int:
    """HEAD 探测 blob 大小；服务器不支持 Range 时返回 0。

    探测只是并行下载的优化入口：任何失败（网络、响应形状、
    测试替身缺少属性等）都返回 0，让调用方走官方串行路径。
    """
    head_req = urllib.request.Request(
        url, headers={**headers, "Range": "bytes=0-0"})
    try:
        with opener(insecure).open(head_req) as head_resp:
            status = getattr(head_resp, "status", None) or getattr(
                head_resp, "code", None)
            if status == 206:
                cr = head_resp.headers.get("Content-Range", "")
                # Content-Range: bytes 0-0/47400000
                if "/" in cr:
                    total = cr.rsplit("/", 1)[-1]
                    if total.isdigit():
                        return int(total)
    except Exception:
        pass
    return 0


def _download_blob_parallel(
    url: str, headers: dict, total_size: int,
    tmp_fd: int, insecure: bool, timeout: float,
    short_id: str,
) -> None:
    """将单个 blob 分成多块并行下载到已打开的 tmp_fd。

    每个线程下载一个字节范围并通过 os.pwrite 写入对应偏移
    （各线程写不同区域，互不干扰）。全部完成后由调用方回读校验哈希。
    """
    n_chunks = _PARALLEL_CHUNKS
    chunk_size = total_size // n_chunks
    ranges = []
    for i in range(n_chunks):
        start = i * chunk_size
        end = (i + 1) * chunk_size - 1 if i < n_chunks - 1 else total_size - 1
        ranges.append((start, end))

    log_info(_("{short_id}: downloading in {n} parallel parts "
               "({total} total)...",
               short_id=short_id, n=n_chunks, total=fmt_size(total_size)))

    progress = {"done": 0, "total": total_size, "start": time.monotonic()}
    progress_lock = threading.Lock()

    errors = []
    with ThreadPoolExecutor(max_workers=n_chunks) as pool:
        futures = {}
        for i, (start, end) in enumerate(ranges):
            fut = pool.submit(
                _download_chunk_range,
                url, headers, start, end,
                tmp_fd, insecure, timeout,
                short_id, i + 1, n_chunks,
                progress, progress_lock,
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
        details = "; ".join(
            f"chunk {i+1} ({start}-{end}): {exc}"
            for i, start, end, exc in errors
        )
        raise RuntimeError(
            f"Parallel download failed for {short_id}: {details}"
        )

    elapsed = time.monotonic() - progress["start"]
    speed = total_size / elapsed if elapsed > 0 else 0
    log_info(_("{short_id}: download complete "
               "({total}, {speed}/s)",
               short_id=short_id, total=fmt_size(total_size),
               speed=fmt_size(int(speed))))


def download_blob(
    repo: str, digest: str, token: str, base: str,
    insecure: bool = False,
) -> int:
    """Download a blob to the layer cache; return an open descriptor on it.

    Streams the bytes through sha256 and verifies the result against the
    expected *digest* before promoting the .tmp file. The descriptor is a
    duplicate of the one the bytes were *written* through, so it names
    the very inode that was just hashed — os.replace() carries the inode
    across, and re-opening the destination by name afterwards would have
    cost a second full hash to prove the same thing. The caller closes
    it.

    A blob already in the cache is re-hashed rather than taken at its
    name (open_verified_layer): the file may have been written by
    something other than this function, so its name is not evidence of
    its content. One that fails is dropped and downloaded again.
    """
    dest = layer_cache_path(digest)
    cached = open_verified_layer(digest)
    if cached is not None:
        return cached

    _algo, expected_hex = split_digest(digest)

    url = f"{base}/v2/{repo}/blobs/{digest}"
    headers = {**_ua()}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = new_request(url, headers=headers)

    def _attempt():
        # Fresh hasher per attempt: a retry re-downloads the whole blob, so
        # state from a failed partial download must not carry over.
        hasher = hashlib.sha256()
        try:
            with atomic_replace(dest) as tmp_fd:
                # fork 优化：先探测 blob 大小与 Range 支持，超过阈值则
                # 分块并行下载（类似 aria2），否则走官方串行流式路径。
                blob_size = _probe_blob_size(url, headers, insecure, 30.0)
                if blob_size >= _PARALLEL_THRESHOLD:
                    _download_blob_parallel(
                        url, headers, blob_size, tmp_fd, insecure, 30.0,
                        short_id,
                    )
                    # 回读整个文件计算哈希（并行写时无法边写边算）。
                    got = 0
                    while True:
                        chunk = os.pread(tmp_fd, 1 << 20, got)
                        if not chunk:
                            break
                        hasher.update(chunk)
                        got += len(chunk)
                    if got != blob_size:
                        raise RuntimeError(
                            f"Parallel download incomplete for "
                            f"'{digest}': got {got} of {blob_size} bytes."
                        )
                else:
                    with opener(insecure).open(req) as resp, \
                            open(tmp_fd, "wb", closefd=False) as fh:
                        total = declared_length(resp)
                        downloaded = 0
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            fh.write(chunk)
                            hasher.update(chunk)
                            downloaded += len(chunk)
                            draw_bytes_bar(downloaded, total,
                                           noun=_("downloaded"))
                        # Before the digest, so a connection that ended early
                        # is reported as what it is and retried, instead of
                        # surfacing as the registry serving the wrong bytes --
                        # which is not retried, being no fault of the wire.
                        require_complete_body(
                            downloaded, total,
                            _("Downloading blob {blob}", blob=digest[:19]),
                        )
                actual_hex = hasher.hexdigest()
                if actual_hex != expected_hex.lower():
                    raise RuntimeError(
                        f"Layer integrity check failed for digest '{digest}': "
                        f"expected {expected_hex}, got {actual_hex}."
                    )
                # A duplicate of the descriptor the bytes were written
                # through, so what the caller reads is the inode they
                # went into. atomic_replace closes its own once the
                # rename has promoted it; nothing that later happens to
                # the name changes what this names.
                fd = os.dup(tmp_fd)
                os.lseek(fd, 0, os.SEEK_SET)
        finally:
            clear_bar()
        return fd

    short_id = digest.split(":")[-1][:12]
    return retry_http(_attempt, what=_("Downloading layer {short_id}",
                                       short_id=short_id))


def apply_layer(layer_fd: int, rootfs_fd: int, *, digest: str = "") -> None:
    """Apply one OCI/Docker layer (a tar, compressed or not) into *rootfs_fd*.

    Takes the **descriptor** the verification handed back, not a path, so
    the inode read is the one that was hashed (see
    cache.open_verified_layer). With *digest* the extraction re-hashes as
    it consumes and refuses a total that does not match, which is what
    covers the remaining case a descriptor cannot: the same inode
    truncated and rewritten in place. The rootfs is a descriptor for the
    same reason the blob is: every member goes in as (dir_fd, name)
    beneath it. Thin wrapper around
    extract_tar_fd_to_rootfs that turns on OCI whiteout handling
    (.wh.<name> deletes sibling, .wh..wh..opq clears the parent dir). See
    that function for the full set of invariants enforced during
    extraction.
    """
    short = digest.split(":")[-1][:12] if digest else ""
    expected = split_digest(digest)[1] if digest else ""
    extract_tar_fd_to_rootfs(
        layer_fd, rootfs_fd, handle_whiteouts=True,
        subject=f"layer {short}" if short else "layer",
        expected_sha256=expected,
    )
