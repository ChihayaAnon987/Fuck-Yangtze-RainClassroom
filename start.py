import os
import threading

os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'

from config import (
    filtered_courses,
    ai_request_timeout,
    enable_scheduled_start,
    scheduled_start_time,
    session_keep_alive_interval_seconds,
)
from function.check_in import get_listening_classes_and_sign
from util.ai import init_ai_strategy
from util.session_manager import ensure_session_alive, start_session_keep_alive_daemon
from apscheduler.schedulers.blocking import BlockingScheduler


_system_run_lock = threading.Lock()
_system_running = False
_session_keep_alive_stop_event = None


def start_session_services():
    global _session_keep_alive_stop_event
    ensure_session_alive(reason="start.py 启动时进行会话校验")

    if _session_keep_alive_stop_event is None:
        _session_keep_alive_stop_event = start_session_keep_alive_daemon(
            session_keep_alive_interval_seconds
        )


def initialize_and_start_answer_system():
    try:
        init_ai_strategy(timeout=ai_request_timeout)
        print(f"[INIT] AI策略引擎初始化成功")
    except Exception as e:
        print(f"[ERROR] AI策略引擎初始化失败: {e}")
        print(f"[ERROR] 请检查配置 (特别是 MODEL_1_API_KEY 等模型配置)")
        return
    
    get_listening_classes_and_sign(filtered_courses)


def start_answer_system_if_not_running():
    global _system_running
    with _system_run_lock:
        if _system_running:
            print("[SCHEDULE] 系统已在运行，跳过本次定时启动")
            return
        _system_running = True

    try:
        initialize_and_start_answer_system()
    finally:
        with _system_run_lock:
            _system_running = False


def setup_and_start_scheduler():
    if not scheduled_start_time:
        print("[SCHEDULE] 未配置定时启动时间，已跳过定时调度")
        return

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    for index, time_text in enumerate(scheduled_start_time):
        hour, minute = map(int, time_text.split(":"))
        scheduler.add_job(
            start_answer_system_if_not_running,
            trigger="cron",
            hour=hour,
            minute=minute,
            id=f"daily-start-answer-system-{index}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

    print(f"[SCHEDULE] 定时启动已开启，{', '.join(scheduled_start_time)} 自动启动")
    print("[SCHEDULE] 按 Ctrl+C 可退出定时调度")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("[SCHEDULE] 定时调度已停止")


if __name__ == "__main__":
    start_session_services()

    if enable_scheduled_start:
        setup_and_start_scheduler()
    else:
        initialize_and_start_answer_system()