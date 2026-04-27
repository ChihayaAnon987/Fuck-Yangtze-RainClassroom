import os
import re
import time
from typing import Iterable, List, Dict, Any

from dotenv import load_dotenv

# 加载 .env 文件中的环境变量（优先级最高）
load_dotenv()


def _get_config_value(env_names, default=""):
    """从环境变量读取配置值"""
    for env_name in env_names:
        value = os.getenv(env_name)
        if value is not None:
            value = str(value).strip()
            if value:
                return value
    return default


def _get_boolean_config(env_names, default=False):
    """从环境变量读取布尔配置"""
    raw_value = _get_config_value(env_names, default="")
    if not raw_value:
        return default

    value = str(raw_value).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _get_integer_config(env_names, default=0):
    """从环境变量读取整数配置"""
    raw_value = _get_config_value(env_names, default="")
    if not raw_value:
        return default

    try:
        value = int(str(raw_value).strip())
        return value
    except (TypeError, ValueError):
        return default


def _get_float_config(env_names, default=0.0):
    """从环境变量读取浮点数配置"""
    raw_value = _get_config_value(env_names, default="")
    if not raw_value:
        return default

    try:
        value = float(str(raw_value).strip())
        return value
    except (TypeError, ValueError):
        return default


host = "https://changjiang.yuketang.cn/"

api = {
    # 获取收到的消息
    "get_received": "api/v3/activities/received/",
    # 获取我发布的信息
    "get_published": "api/v3/activities/published/",
    # 进入课堂
    "sign_in_class": "api/v3/lesson/checkin",
    # 登录雨课堂账号
    "login_user": "pc/login/verify_pwd_login/",
    # 个人信息
    "user_info": "v2/api/web/userinfo",
    # 如果是课堂 可以通过此URL进入课堂查看PPT 尾接courseID
    "class_info": "m/v2/lesson/student/",
    # 获取正在处于上课的列表
    "get_listening": "api/v3/classroom/on-lesson-upcoming-exam",
    # 获取PPT
    "get_ppt": "api/v3/lesson/presentation/fetch?presentation_id={}",
    # websocket
    "websocket": "wss://changjiang.yuketang.cn/wsapp/",
    # 答题
    "answer": "api/v3/lesson/problem/answer"
}

log_file_name = "log.json"

# Session ID配置
session_id = _get_config_value(["SESSION"], default="")
headers = {
    "Cookie": "sessionid=" + session_id
}

# 签到来源配置
check_in_sources = {
    "二维码": 0,
    "雨课堂小程序": 2,
    "网页版": 3
}

# 题目类型映射（雨课堂API返回的题型数字对应的文字类型）
# 1: 单选题, 2: 多选题, 3: 投票题, 4: 填空题, 5: 主观题
question_type = {
    1: "单选题",
    2: "多选题",
    3: "投票题", 
    4: "填空题",
    5: "主观题"
}

# 需要监听的课程列表（过滤器），留空则监听所有课程
filtered_courses_str = _get_config_value(["FILTERED_COURSES"], default="")
if filtered_courses_str:
    # 支持逗号分隔的课程名列表
    filtered_courses = [course.strip() for course in filtered_courses_str.split(",") if course.strip()]
else:
    filtered_courses = []

# AI模型配置列表
AI_MODELS_CONFIG = []
for i in range(1, 10):  # 最多支持9个模型
    model_name = _get_config_value([f"MODEL_{i}_NAME"], default="")
    if not model_name:
        continue
    
    api_key = _get_config_value([f"MODEL_{i}_API_KEY"], default="")
    base_url = _get_config_value([f"MODEL_{i}_BASE_URL"], default="")
    model = _get_config_value([f"MODEL_{i}_MODEL"], default="")
    priority = _get_float_config([f"MODEL_{i}_PRIORITY"], default=1.0)
    
    if api_key and model:
        AI_MODELS_CONFIG.append({
            "name": model_name,
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "priority": priority
        })

# AI请求超时时间（秒）
ai_request_timeout = _get_integer_config(["AI_REQUEST_TIMEOUT"], default=30)

# 是否启用题库搜索
enable_question_bank = _get_boolean_config(["ENABLE_QUESTION_BANK"], default=False)

# 题库搜索密钥
enncy_key = _get_config_value(["ENNCY_KEY"], default="")

# 是否启用定时启动（用于Github Actions或本地定时任务）
enable_scheduled_start = _get_boolean_config(["ENABLE_SCHEDULED_START"], default=False)

# 定时启动时间列表（格式: ["08:00", "14:30"]）
scheduled_start_time_str = _get_config_value(["SCHEDULED_START_TIME"], default="")
if scheduled_start_time_str:
    # 支持逗号分隔的多个时间点
    scheduled_start_time = [time.strip() for time in scheduled_start_time_str.split(",") if time.strip()]
else:
    scheduled_start_time = []

# SESSION刷新等待超时（秒），用于二维码扫码流程
session_refresh_timeout_seconds = _get_integer_config(
    ["SESSION_REFRESH_TIMEOUT"],
    default=180,
)

# SESSION保活间隔（秒），<=0 表示禁用保活线程
session_keep_alive_interval_seconds = _get_integer_config(
    ["SESSION_KEEP_ALIVE_INTERVAL"],
    default=600,
)