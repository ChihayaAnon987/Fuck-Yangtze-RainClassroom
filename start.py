import os
import threading
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'  # 跳过连接检查

from config import (
    filtered_courses,
    ai_request_timeout,
    enable_scheduled_start,
    scheduled_start_time,
)
from function.check_in import get_listening_classes_and_sign, check_exam
from util.ai import init_ai_strategy
from apscheduler.schedulers.blocking import BlockingScheduler


_system_run_lock = threading.Lock()
_system_running = False


def start_answer_system():
    # 【新改进】初始化AI策略引擎
    # 所有配置现在统一通过 .env 文件管理
    # 支持多模型并行调用，自动选择最佳答案
    print(f"[INIT] 初始化AI策略引擎...")
    print(f"[INIT] Request Timeout: {ai_request_timeout}s")
    
    try:
        init_ai_strategy(
            timeout=ai_request_timeout
        )
        print(f"[INIT] AI策略引擎初始化成功")
    except Exception as e:
        print(f"[ERROR] AI策略引擎初始化失败: {e}")
        print(f"[ERROR] 请检查配置 (特别是 MODEL_1_API_KEY 等模型配置)")
        return
    
    # 启动答题系统
    get_listening_classes_and_sign(filtered_courses)


def start_answer_system_if_not_running():
    global _system_running
    with _system_run_lock:
        if _system_running:
            print("[SCHEDULE] 系统已在运行，跳过本次定时启动")
            return
        _system_running = True

    try:
        start_answer_system()
    finally:
        with _system_run_lock:
            _system_running = False


def start_with_schedule():
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
    if enable_scheduled_start:
        start_with_schedule()
    else:
        start_answer_system()