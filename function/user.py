import requests
from requests.exceptions import RequestException, SSLError
from config import host, api, headers
from util.session_manager import request_with_auto_session_refresh


def _execute_request(method, url, **kwargs):
    try:
        return requests.request(method=method, url=url, timeout=10, **kwargs)
    except SSLError as e:
        print(f"[WARN] 获取用户信息SSL失败: {e}")
    except RequestException as e:
        print(f"[WARN] 获取用户信息失败: {e}")
    return None


# 获取用户名字 用于写日志
def get_user_name():
    response = request_with_auto_session_refresh(
        request_executor=_execute_request,
        method="GET",
        url=host + api["user_info"],
        headers=headers,
        reason="获取用户信息检测到 SESSION 失效",
    )
    if response is None:
        return "未知用户"

    if response.status_code == 200:
        try:
            response_data = response.json()
        except ValueError:
            return "未知用户"

        # 提取 `data` 列表中的第一个元素信息
        if "data" in response_data and response_data["data"]:
            return response_data["data"][0].get("name") or "未知用户"

    return "未知用户"


