import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests
import websocket
from requests import Response
from requests.exceptions import RequestException
from websocket import WebSocketException, WebSocketTimeoutException

import config


REQUEST_TIMEOUT_SECONDS = 10
DEFAULT_QR_REFRESH_TIMEOUT_SECONDS = 180
SESSION_COOKIE_NAMES = ("sessionid", "session_id")
SESSION_INVALID_STATUS_CODES = {401, 403}
LOGIN_PAGE_MARKERS = (
    "qrcode-box",
    "scan with wechat",
    "rainclassroom",
    "扫码登录",
    "请使用微信扫码登录",
)
REQUEST_LOGIN_PAYLOAD = {
    "op": "requestlogin",
    "role": "web",
    "version": 1.4,
    "type": "qrcode",
    "from": "web",
}

_refresh_lock = threading.Lock()

RequestExecutor = Callable[..., Optional[Response]]


def _log(level: str, message: str) -> None:
    level_prefix = {
        "info": "[SESSION]",
        "ok": "[SESSION][OK]",
        "warn": "[SESSION][WARN]",
        "error": "[SESSION][ERROR]",
    }
    prefix = level_prefix.get(level, "[SESSION]")
    print(f"{prefix} {message}")


def _build_session_cookie_header(session_id: str) -> str:
    return f"sessionid={session_id}"


def _extract_session_id_from_cookie_text(cookie_text: str) -> str:
    if not cookie_text:
        return ""

    cookie_parts = cookie_text.split(";")
    for cookie_part in cookie_parts:
        clean_part = cookie_part.strip()
        for cookie_name in SESSION_COOKIE_NAMES:
            prefix = f"{cookie_name}="
            if clean_part.startswith(prefix):
                return clean_part[len(prefix) :].strip()
    return ""


def _extract_session_id_from_cookie_jar(cookie_jar: requests.cookies.RequestsCookieJar) -> str:
    if cookie_jar is None:
        return ""

    for cookie_name in SESSION_COOKIE_NAMES:
        value = cookie_jar.get(cookie_name)
        if value:
            return str(value).strip()
    return ""


def merge_headers_with_latest_session(headers: Optional[dict] = None) -> dict:
    merged_headers = dict(headers) if headers else {}
    latest_cookie = config.headers.get("Cookie", "")
    if latest_cookie:
        merged_headers["Cookie"] = latest_cookie
    return merged_headers


def _session_env_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".env"


def _persist_session_id_to_env(session_id: str) -> None:
    env_path = _session_env_path()
    if not env_path.exists():
        return

    original_lines = env_path.read_text(encoding="utf-8").splitlines()
    updated_lines = []
    replaced = False

    for line in original_lines:
        stripped = line.strip()
        if stripped.startswith("SESSION=") and not replaced:
            updated_lines.append(f"SESSION={session_id}")
            replaced = True
        else:
            updated_lines.append(line)

    if not replaced:
        updated_lines.append(f"SESSION={session_id}")

    env_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")


def apply_session_id(session_id: str, persist_to_env: bool = True) -> bool:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return False

    config.session_id = clean_session_id
    config.headers["Cookie"] = _build_session_cookie_header(clean_session_id)

    if persist_to_env:
        try:
            _persist_session_id_to_env(clean_session_id)
        except OSError as error:
            _log("warn", f"写入 .env 失败: {error}")

    return True


def is_session_invalid_response(response: Optional[Response]) -> bool:
    if response is None:
        return False

    if response.status_code in SESSION_INVALID_STATUS_CODES:
        return True

    content_type = response.headers.get("Content-Type", "").lower()
    if "text/html" not in content_type:
        return False

    snippet = response.text[:2000].lower()
    return any(marker in snippet for marker in LOGIN_PAGE_MARKERS)


def _is_current_session_still_valid() -> bool:
    try:
        response = requests.get(
            url=config.host + config.api["user_info"],
            headers=merge_headers_with_latest_session(config.headers),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except RequestException:
        return False

    if is_session_invalid_response(response):
        return False
    return response.status_code == 200


def _print_ascii_qrcode(qrcode_text: str) -> None:
    """用 Unicode 半块字符渲染二维码（无损压缩到约 1/4 面积）。"""
    try:
        import qrcode
    except ImportError:
        _log("warn", "未安装 qrcode 库，无法在终端显示二维码。")
        print(f"[SESSION] 请手动打开该链接扫码: {qrcode_text}")
        return

    qr = qrcode.QRCode(border=1)
    qr.add_data(qrcode_text)
    qr.make(fit=True)

    print("\n[SESSION] 请使用微信扫描下方二维码以刷新 SESSION:\n")

    matrix = qr.get_matrix()
    size = len(matrix)
    for i in range(0, size, 2):
        line = ""
        for j in range(size):
            top = matrix[i][j]
            bottom = matrix[i + 1][j] if i + 1 < size else False
            if top and bottom:
                line += "█"
            elif top and not bottom:
                line += "▀"
            elif not top and bottom:
                line += "▄"
            else:
                line += " "
        print(line)

    print(f"[SESSION] 二维码链接(备用): {qrcode_text}\n")


def _complete_web_login_and_get_session_id(user_id: int, auth_token: str) -> str:
    if not user_id or not auth_token:
        return ""

    login_payload = json.dumps({"UserID": user_id, "Auth": auth_token}, ensure_ascii=False)
    web_login_url = config.host.rstrip("/") + "/pc/web_login"

    with requests.Session() as login_session:
        login_response = login_session.post(
            url=web_login_url,
            data=login_payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        session_id = _extract_session_id_from_cookie_jar(login_session.cookies)
        if session_id:
            return session_id

        set_cookie = login_response.headers.get("Set-Cookie", "")
        session_id = _extract_session_id_from_cookie_text(set_cookie)
        if session_id:
            return session_id

        verify_response = login_session.get(
            url=config.host + config.api["user_info"],
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        session_id = _extract_session_id_from_cookie_jar(login_session.cookies)
        if session_id:
            return session_id

        set_cookie = verify_response.headers.get("Set-Cookie", "")
        return _extract_session_id_from_cookie_text(set_cookie)


def _refresh_session_via_qrcode_locked(reason: str) -> bool:
    timeout_seconds = int(
        getattr(
            config,
            "session_refresh_timeout_seconds",
            DEFAULT_QR_REFRESH_TIMEOUT_SECONDS,
        )
    )
    timeout_seconds = max(timeout_seconds, 60)

    if reason:
        _log("warn", f"触发 SESSION 刷新: {reason}")

    ws = None
    deadline = time.time() + timeout_seconds
    qr_expire_at = time.time() + 50
    request_payload = json.dumps(REQUEST_LOGIN_PAYLOAD, ensure_ascii=False)

    try:
        ws = websocket.create_connection(config.api["websocket"], timeout=REQUEST_TIMEOUT_SECONDS)
        ws.send(request_payload)

        while time.time() < deadline:
            remaining_seconds = max(1, min(3, int(deadline - time.time())))
            ws.settimeout(remaining_seconds)

            try:
                message_raw = ws.recv()
            except WebSocketTimeoutException:
                if time.time() >= qr_expire_at:
                    ws.send(request_payload)
                    qr_expire_at = time.time() + 50
                continue

            try:
                message = json.loads(message_raw)
            except json.JSONDecodeError:
                continue

            operation = message.get("op")
            if operation == "requestlogin":
                qrcode_url = message.get("qrcode") or message.get("ticket")
                if qrcode_url:
                    _print_ascii_qrcode(qrcode_url)
                expire_seconds = int(message.get("expire_seconds") or 60)
                qr_expire_at = time.time() + max(expire_seconds - 2, 5)
                continue

            if operation == "loginsuccess":
                user_id = message.get("UserID")
                auth_token = message.get("Auth")
                new_session_id = _complete_web_login_and_get_session_id(
                    user_id=user_id,
                    auth_token=auth_token,
                )

                if not new_session_id:
                    _log("error", "扫码成功，但未能获取新的 SESSION。")
                    return False

                apply_session_id(new_session_id)
                _log("ok", "SESSION 已刷新并写入 .env。")
                return True

        _log("error", "等待扫码超时，请重新尝试。")
        return False

    except (RequestException, WebSocketException, OSError) as error:
        _log("error", f"SESSION 刷新失败: {error}")
        return False
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def ensure_session_alive(reason: str = "") -> bool:
    with _refresh_lock:
        if _is_current_session_still_valid():
            return True
        return _refresh_session_via_qrcode_locked(reason=reason)


def keep_session_alive_once() -> bool:
    try:
        response = requests.get(
            url=config.host + config.api["user_info"],
            headers=merge_headers_with_latest_session(config.headers),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except RequestException as error:
        _log("warn", f"会话保活请求失败: {error}")
        return False

    if is_session_invalid_response(response):
        return ensure_session_alive(reason="会话保活检测到 SESSION 失效")

    return response.status_code == 200


def _session_keep_alive_worker(interval_seconds: int, stop_event: threading.Event) -> None:
    keep_session_alive_once()
    while not stop_event.wait(interval_seconds):
        keep_session_alive_once()


def start_session_keep_alive_daemon(interval_seconds: int) -> Optional[threading.Event]:
    normalized_interval = max(int(interval_seconds), 0)
    if normalized_interval <= 0:
        return None

    stop_event = threading.Event()
    worker = threading.Thread(
        target=_session_keep_alive_worker,
        kwargs={"interval_seconds": normalized_interval, "stop_event": stop_event},
        daemon=True,
        name="session-keep-alive",
    )
    worker.start()
    return stop_event


def request_with_auto_session_refresh(
    request_executor: RequestExecutor,
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    reason: str = "",
    **kwargs,
) -> Optional[Response]:
    response = request_executor(
        method=method,
        url=url,
        headers=merge_headers_with_latest_session(headers),
        **kwargs,
    )

    if not is_session_invalid_response(response):
        return response

    refresh_reason = reason or f"{method} {url} 返回会话失效"
    if not ensure_session_alive(reason=refresh_reason):
        return response

    return request_executor(
        method=method,
        url=url,
        headers=merge_headers_with_latest_session(headers),
        **kwargs,
    )
