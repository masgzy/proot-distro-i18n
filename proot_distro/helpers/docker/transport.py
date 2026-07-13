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

# Architecture: Registry HTTP plumbing used by both pull and push.
# Three concerns live here:
#
#   - User-Agent header generation (so registries can spot us).
#   - Authorization-stripping redirect handler — Docker Hub blob URLs
#     redirect to CDN hosts that reject Bearer tokens with HTTP 400.
#     Python's default redirect handler keeps headers across hops, so
#     we subclass it to drop the header when the host changes.
#   - Token-exchange flow: PD_DOCKER_AUTH (username:password) is the
#     single auth contract; the registry's WWW-Authenticate header
#     tells us where to redeem it for a Bearer token.

import base64
import http.client
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from proot_distro.constants import PROGRAM_NAME, PROGRAM_VERSION
from proot_distro.helpers.download import (
    certificate_error_msg,
    insecure_ssl_context,
    is_cert_verification_error,
    is_plaintext_http_tls_error,
    retry_http,
    _tune_socket,
)


# 旧版硬编码的单一镜像源；现已通过 mirrors.resolve_registry_url() 动态读取。
# 模块级 REGISTRY_URL 常量已移除（v6）——它会固化导入时的值，导致
# `mirror use` 后本进程内不生效。所有调用方应使用 registry_base_url("")
# 或 _resolve_registry_url() 动态获取。
from proot_distro.mirrors import resolve_registry_url as _resolve_registry_url

AUTH_URL = "https://auth.docker.io/token"


# ---------------------------------------------------------------------------
# TCP socket 调优
# ---------------------------------------------------------------------------
#
# Python 的 urllib.request 默认使用操作系统的 TCP 接收缓冲区大小，
# 通常只有 8-64 KiB。在高延迟连接（如中国到海外镜像源，RTT 150-300ms）
# 上，这严重限制吞吐量：
#
#   最大吞吐量 ≈ SO_RCVBUF / RTT
#   8 KiB / 200ms  =  40 KB/s
#   64 KiB / 200ms = 320 KB/s
#   2 MiB / 200ms  = 10 MB/s  ← 我们的目标
#
# 将 SO_RCVBUF 设为 2 MiB 后，即使在 300ms RTT 的连接上也能达到
# 7 MB/s 的单连接吞吐量，多线程并发后轻松超过 10 MB/s。
#
# 注意：操作系统可能将 SO_RCVBUF 限制在 /proc/sys/net/core/rmem_max
# （Linux）或动态窗口上限以内。setsockopt 会静默截断到允许的最大值，
# 不会报错。
#
# _tune_socket 定义在 helpers/download.py 中，供 transport.py 和
# download.py 共用，避免循环导入。


class _TunedHTTPSConnection(http.client.HTTPSConnection):
    """建立连接后自动调优 socket 参数的 HTTPS 连接。"""

    def connect(self):
        super().connect()
        _tune_socket(self.sock)


class _TunedHTTPConnection(http.client.HTTPConnection):
    """建立连接后自动调优 socket 参数的 HTTP 连接。"""

    def connect(self):
        super().connect()
        _tune_socket(self.sock)


class _TunedHTTPSHandler(urllib.request.HTTPSHandler):
    """使用调优连接的 HTTPS handler。"""

    def https_open(self, req):
        return self.do_open(_TunedHTTPSConnection, req, context=self._context)


class _TunedHTTPHandler(urllib.request.HTTPHandler):
    """使用调优连接的 HTTP handler。"""

    def http_open(self, req):
        return self.do_open(_TunedHTTPConnection, req)


def _ua() -> dict:
    return {"User-Agent": f"{PROGRAM_NAME}/{PROGRAM_VERSION}"}


class AuthStrippingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Strip the Authorization header when following a cross-host redirect.

    Docker Hub blob endpoints redirect to CDN pre-signed URLs. Those CDN
    hosts return HTTP 400 when they receive a Bearer token. Python's
    default redirect handler forwards all headers unchanged, so we
    override it to drop Authorization whenever the redirect target
    host differs from the source host.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        orig_host = urllib.parse.urlparse(req.full_url).netloc
        new_host = urllib.parse.urlparse(newurl).netloc
        if orig_host != new_host:
            new_req.headers.pop("Authorization", None)
        return new_req


def _build_opener(insecure: bool):
    """构建带 Auth 跨域剥离 + socket 调优的 opener。

    *insecure* 变体额外安装跳过证书校验的 HTTPS handler，使
    ``--allow-insecure`` 下能到达不受信任证书的 HTTPS 端点。
    无论何种模式，HTTP/HTTPS 连接都会在建立后调优 socket 参数
    （SO_RCVBUF、TCP_NODELAY），以提升下载吞吐量。
    """
    if insecure:
        https_handler = _TunedHTTPSHandler(context=insecure_ssl_context())
    else:
        https_handler = _TunedHTTPSHandler()
    handlers = [
        AuthStrippingRedirectHandler,
        _TunedHTTPHandler(),
        https_handler,
    ]
    return urllib.request.build_opener(*handlers)


_verified_opener = _build_opener(False)
# Eagerly build the insecure opener too — avoids a lazy-init race when
# multiple threads call opener(insecure=True) concurrently. The cost
# is one extra HTTPSHandler at import, which is negligible.
_insecure_opener = _build_opener(True)


def opener(insecure: bool = False):
    """Return a shared opener; the insecure variant skips TLS cert checks."""
    return _insecure_opener if insecure else _verified_opener


def auth_opener():
    """Return the shared (certificate-verifying) opener that strips Auth."""
    return _verified_opener


def _request_body(open_fn, req, what: str, timeout: float = 30.0,
                  max_retries: int = 2) -> bytes:
    """Open *req* via *open_fn* and return the full response body.

    Transient network failures are retried. HTTP errors — including the
    expected 401 that carries the Bearer challenge — and deterministic
    TLS failures are not retried; they propagate to the caller.

    *timeout* defaults to 30 seconds — without it, urlopen blocks
    indefinitely on a hung connection (e.g. auth.docker.io behind a
    firewall that drops packets silently).

    *max_retries* defaults to 2 (not 5) because auth/probe requests
    are small and fast — a failure usually means the endpoint is
    unreachable, not temporarily flaky. Reducing retries avoids
    making the user wait 90+ seconds on a dead connection.
    """
    def _attempt():
        # Inject timeout into the open call. urllib.request.urlopen
        # accepts a timeout= kwarg; opener.open does too.
        try:
            resp = open_fn(req, timeout=timeout)
        except TypeError:
            # Fallback for open_fn that doesn't accept timeout=
            resp = open_fn(req)
        try:
            return resp.read()
        finally:
            resp.close()
    return retry_http(_attempt, what=what, max_retries=max_retries)


def registry_base_url(registry: str, insecure: bool = False) -> str:
    """Return the base URL for *registry* (empty string ⇒ Docker Hub).

    HTTPS is used by default. When *insecure* is set the custom registry
    is addressed over plain HTTP — the opt-in behaviour behind the
    install command's ``--allow-insecure``. Docker Hub (empty registry)
    is always served over HTTPS and ignores *insecure*.

    Empty *registry* now resolves to the user-configured mirror (managed
    by ``proot-distro mirror``); see mirrors.resolve_registry_url.
    """
    if not registry:
        # 动态查询，使 `mirror use` 后无需重启 Python 进程即生效
        return _resolve_registry_url()
    scheme = "http" if insecure else "https"
    return f"{scheme}://{registry}"


def insecure_registry_msg(registry: str) -> str:
    """Return the error shown when an HTTPS-only pull hits an HTTP registry."""
    return (
        f"Registry '{registry}' is served over plain HTTP, not HTTPS. "
        f"proot-distro enforces TLS by default. If you trust this registry "
        f"and the network path to it, re-run with '--allow-insecure' to "
        f"permit the unencrypted connection."
    )


def _http_registry_reachable(registry: str, timeout: float = 6.0) -> bool:
    """Return True if *registry* answers a /v2/ probe over plaintext HTTP.

    Fallback used on the error path when the TLS error itself is not a
    conclusive plaintext signal (see is_plaintext_http_tls_error), to
    decide whether an HTTPS failure is because the registry is HTTP-only
    (so we can point the user at ``--allow-insecure``) rather than simply
    unreachable. Any HTTP-level response — including 401/404 — confirms the
    host speaks HTTP on that endpoint.
    """
    req = urllib.request.Request(f"http://{registry}/v2/", headers=_ua())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(64)
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError):
        return False


def auth_denied_msg(image_ref: str, code: int) -> str:
    """Return a descriptive error string for 401/403 registry responses."""
    if os.environ.get("PD_DOCKER_AUTH"):
        return (
            f"Access denied to '{image_ref}' (HTTP {code}). "
            f"Check that PD_DOCKER_AUTH=username:password is correct "
            f"and the account has pull access to the image."
        )
    # Public image got 401/403 — most likely the mirror is misbehaving
    # or the image name is wrong. Don't tell the user it's "private"
    # because that's misleading for official images like 'ubuntu'.
    return (
        f"Access denied to '{image_ref}' (HTTP {code}). "
        f"This is a public image — the mirror may not have it cached, "
        f"or the image name may be wrong.\n"
        f"Try: 1) {PROGRAM_NAME} mirror test --use-best  (switch to a working mirror)\n"
        f"      2) Use full reference like 'library/ubuntu:latest' or 'ubuntu:24.04'\n"
        f"      3) For private images, set PD_DOCKER_AUTH=username:password"
    )


def push_denied_msg(image_ref: str, code: int) -> str:
    """Return a context-sensitive error string for 401/403 on push."""
    if os.environ.get("PD_DOCKER_AUTH"):
        return (
            f"Push denied for '{image_ref}' (HTTP {code}). "
            f"Check that PD_DOCKER_AUTH=username:password is correct "
            f"and the account has push access to the repository."
        )
    return (
        f"Push denied for '{image_ref}' (HTTP {code}). "
        f"Set PD_DOCKER_AUTH=username:password to authenticate, or, "
        f"for self-hosted registries that allow anonymous push, check "
        f"the registry configuration."
    )


_CHALLENGE_PARAM_RE = re.compile(
    r'(\w+)\s*=\s*(?:"([^"]*)"|([^",\s]+))'
)


def _parse_bearer_challenge(header_value: str) -> dict:
    """Return the key=value pairs from a Bearer WWW-Authenticate header.

    Per RFC 7235 each auth-param's value may be either a quoted-string
    or a bare token. Practical registries (Docker Hub, GHCR, ECR) quote
    everything, but the spec permits e.g.
        Bearer realm=https://auth.example/token,service=svc
    on a self-hosted registry. We accept both forms so the probe still
    works against spec-compliant minimal implementations.
    """
    return {
        key: (quoted if quoted else bare)
        for key, quoted, bare in _CHALLENGE_PARAM_RE.findall(header_value)
    }


def env_basic_auth() -> str:
    """Return a Basic auth header value from PD_DOCKER_AUTH, or ''.

    Accepts 'username:password' — the colon is the required separator.
    Returns '' when the variable is unset; raises RuntimeError when the
    variable is set but contains no colon (wrong format).
    """
    raw = os.environ.get("PD_DOCKER_AUTH", "")
    if not raw:
        return ""
    if ":" not in raw:
        raise RuntimeError(
            "PD_DOCKER_AUTH must be in 'username:password' format "
            "(e.g. 'myuser:mypassword' or 'myuser:ghp_xxx'). "
            "A bare token without a username cannot be used — registry "
            "auth requires a token exchange with Basic credentials."
        )
    return "Basic " + base64.b64encode(raw.encode()).decode()


def get_auth_token(
    repo: str, registry: str = "", actions: str = "pull",
    insecure: bool = False,
) -> tuple:
    """Resolve a registry's base URL and an OAuth2 token for *repo*.

    Returns ``(token, base_url)`` where *base_url* is the resolved
    ``scheme://registry`` that every subsequent request for this image must
    use. *token* is empty for wide-open registries.

    `actions` is a comma-separated list of registry actions to request,
    such as 'pull' (default), 'push', or 'pull,push'. The push flow
    needs 'pull,push'; the pull flow uses the default 'pull'.

    When PD_DOCKER_AUTH is set, its 'username:password' value is
    forwarded as HTTP Basic auth to the registry's token endpoint,
    enabling access to private images. PD_DOCKER_AUTH must always
    contain a colon separating the username from the password/PAT.

    Without PD_DOCKER_AUTH Docker Hub uses its well-known auth endpoint for
    anonymous requests (always HTTPS). For any other registry the scheme and
    Bearer realm are discovered with a single /v2/ probe:

      * HTTPS is tried first — even under *insecure*, so a registry serving
        an untrusted certificate is reached (cert verification is skipped
        only when *insecure* is set).
      * A certificate failure raises a RuntimeError pointing at
        ``--allow-insecure`` (unless already insecure).
      * A registry that answers the HTTPS probe with plaintext is HTTP-only:
        under *insecure* it is retried over http://; otherwise a RuntimeError
        points the user at ``--allow-insecure``.
    """
    basic_auth = env_basic_auth()

    if not registry:
        # Docker Hub image. Two cases:
        #
        # 1. base is the official Docker Hub (registry-1.docker.io):
        #    use auth.docker.io to get a Bearer token, then request
        #    registry-1.docker.io with it. Standard flow.
        #
        # 2. base is a mirror (daocloud, 1panel, etc.): the mirror is a
        #    transparent proxy. It may (a) accept anonymous requests
        #    with no token, (b) require its own Bearer token via a
        #    WWW-Authenticate challenge, or (c) accept the Docker Hub
        #    token. We probe /v2/ first and let the mirror tell us
        #    what it wants — this avoids the "Unauthorized" failure
        #    that happens when we present a registry.docker.io token
        #    to a mirror that validates the service field.
        base = _resolve_registry_url()
        is_official_hub = base.rstrip("/").endswith("registry-1.docker.io")

        if is_official_hub:
            # Standard Docker Hub auth flow.
            url = (
                f"{AUTH_URL}?service=registry.docker.io"
                f"&scope=repository:{repo}:{actions}"
            )
            req = urllib.request.Request(url, headers=_ua())
            if basic_auth:
                req.add_header("Authorization", basic_auth)
            data = json.loads(
                _request_body(urllib.request.urlopen, req,
                              f"Authenticating {repo}", timeout=30.0)
            )
            token = data.get("token") or data.get("access_token", "")
            return token, base

        # Mirror: probe /v2/ to discover auth requirements.
        op = opener(insecure)
        scheme = "https"
        while True:
            probe_base = f"{scheme}://{urllib.parse.urlparse(base).netloc}"
            probe_req = urllib.request.Request(
                f"{probe_base}/v2/", headers=_ua()
            )
            try:
                _request_body(op.open, probe_req,
                              f"Probing {probe_base}/v2/", timeout=15.0)
                # Mirror accepts anonymous requests — no token needed.
                return "", probe_base
            except urllib.error.HTTPError as exc:
                if exc.code != 401:
                    raise
                www_auth = exc.headers.get("WWW-Authenticate", "")
                if not www_auth.lower().startswith("bearer "):
                    # Non-Bearer challenge (e.g. Basic) — return empty
                    # token; caller will retry and get a clearer error.
                    return "", probe_base
                # Bearer challenge: exchange a token at the mirror's
                # own realm (not auth.docker.io).
                params = _parse_bearer_challenge(
                    www_auth.split(" ", 1)[1]
                )
                realm = params.get("realm", "")
                if not realm:
                    return "", probe_base
                service = params.get("service", "")
                qs_parts = []
                if service:
                    qs_parts.append(
                        f"service={urllib.parse.quote(service, safe='')}"
                    )
                qs_parts.append(f"scope=repository:{repo}:{actions}")
                sep = "&" if "?" in realm else "?"
                token_req = urllib.request.Request(
                    f"{realm}{sep}{'&'.join(qs_parts)}", headers=_ua()
                )
                if basic_auth:
                    token_req.add_header("Authorization", basic_auth)
                data = json.loads(
                    _request_body(op.open, token_req,
                                  "Requesting auth token", timeout=30.0)
                )
                token = data.get("token") or data.get("access_token", "")
                return token, probe_base
            except urllib.error.URLError as exc:
                if not insecure and is_cert_verification_error(exc):
                    raise RuntimeError(
                        certificate_error_msg(probe_base)
                    ) from exc
                if scheme == "https" and (
                    is_plaintext_http_tls_error(exc)
                    or _http_registry_reachable(urllib.parse.urlparse(base).netloc)
                ):
                    if insecure:
                        scheme = "http"
                        continue
                    raise RuntimeError(
                        insecure_registry_msg(probe_base)
                    ) from exc
                raise

    # Custom registry: probe /v2/ to resolve the scheme and discover the
    # Bearer realm. Registries serving public images still require this dance —
    # they answer 401 to unauthenticated requests and embed the token endpoint
    # in the challenge.
    op = opener(insecure)
    scheme = "https"
    while True:
        base = f"{scheme}://{registry}"
        probe_req = urllib.request.Request(f"{base}/v2/", headers=_ua())
        try:
            _request_body(op.open, probe_req, f"Probing {base}/v2/")
            return "", base  # registry is wide open; no token required
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise
            www_auth = exc.headers.get("WWW-Authenticate", "")
            if not www_auth.lower().startswith("bearer "):
                return "", base
            params = _parse_bearer_challenge(www_auth.split(" ", 1)[1])
            realm = params.get("realm", "")
            if not realm:
                return "", base
            service = params.get("service", "")
            qs_parts = []
            if service:
                qs_parts.append(
                    f"service={urllib.parse.quote(service, safe='')}"
                )
            qs_parts.append(f"scope=repository:{repo}:{actions}")
            sep = "&" if "?" in realm else "?"
            token_req = urllib.request.Request(
                f"{realm}{sep}{'&'.join(qs_parts)}", headers=_ua()
            )
            if basic_auth:
                token_req.add_header("Authorization", basic_auth)
            data = json.loads(
                _request_body(op.open, token_req, "Requesting auth token")
            )
            token = data.get("token") or data.get("access_token", "")
            return token, base
        except urllib.error.URLError as exc:
            # The server speaks TLS but its certificate is untrusted. Only
            # reachable when enforcing HTTPS (the insecure opener skips
            # verification, so no cert error occurs there).
            if not insecure and is_cert_verification_error(exc):
                raise RuntimeError(certificate_error_msg(registry)) from exc
            # The registry answered the HTTPS probe with plaintext (or only
            # responds over plain HTTP): it is HTTP-only. Two signals,
            # cheapest first — the handshake error itself (WRONG_VERSION_NUMBER
            # and friends), else an active HTTP re-probe.
            if scheme == "https" and (
                is_plaintext_http_tls_error(exc)
                or _http_registry_reachable(registry)
            ):
                if insecure:
                    scheme = "http"  # retry the whole probe over plain HTTP
                    continue
                raise RuntimeError(insecure_registry_msg(registry)) from exc
            raise


def auth_note(prefix_space: bool = True) -> str:
    """Return ' (user credentials)' or ' (anonymous)' for log lines."""
    head = " " if prefix_space else ""
    if os.environ.get("PD_DOCKER_AUTH"):
        return f"{head}(user credentials)"
    return f"{head}(anonymous)"
