import requests
import json
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException, SSLError
from urllib3.util.retry import Retry

from function.listening_socket import start_all_sockets
from function.user import get_user_name
from config import host, api, headers, log_file_name, check_in_sources
from util.file import write_log, read_log
from util.notice import email_notice
from util.session_manager import request_with_auto_session_refresh
from util.timestamp import get_now


REQUEST_TIMEOUT_SECONDS = 10
REQUEST_RETRY_TOTAL = 3


def _build_retry_session():
    retry = Retry(
        total=REQUEST_RETRY_TOTAL,
        connect=REQUEST_RETRY_TOTAL,
        read=REQUEST_RETRY_TOTAL,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_http_session = _build_retry_session()


def _execute_request(method, url, **kwargs):
    try:
        return _http_session.request(
            method=method,
            url=url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            **kwargs,
        )
    except SSLError:
        pass
    except RequestException:
        pass
    return None


def _safe_request(method, url, **kwargs):
    return request_with_auto_session_refresh(
        request_executor=_execute_request,
        method=method,
        url=url,
        reason="课堂请求检测到 SESSION 失效",
        **kwargs,
    )


def _parse_api_response_data(response_data):
    """解析API响应中的data字段"""
    data = response_data.get("data")
    
    # 处理data可能是JSON字符串的情况（API双重编码）
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            print(f"[ERROR] 无法解析API data 字段，请重新获取SESSION: {e}")
            return None
    
    # 验证data是字典类型
    if not isinstance(data, dict):
        return None
        
    return data


def _parse_list_field(field_value, field_name):
    """解析列表字段，处理可能的JSON字符串情况"""
    if isinstance(field_value, str):
        try:
            field_value = json.loads(field_value)
        except json.JSONDecodeError as e:
            print(f"[ERROR] 无法解析 {field_name}: {e}")
            return None
    
    if not isinstance(field_value, list):
        return None
        
    return field_value


def get_listening():
    """获取正在进行的课堂信息"""
    response = _safe_request("GET", host + api["get_listening"], headers=headers)
    if response is None:
        return None

    if response.status_code == 200:
        try:
            response_data = response.json()
        except ValueError as e:
            print(f"[ERROR] API响应不是合法JSON: {e}")
            return None

        return _parse_api_response_data(response_data)
    else:
        print(f"[ERROR] API 请求失败: HTTP {response.status_code}")
        return None


def _process_classroom_item(item, filtered_courses, on_lesson_list):
    """处理单个课堂项目"""
    try:
        course_name = item["courseName"]
        lesson_id = item["lessonId"]
        response_sign = check_in_on_listening(lesson_id)

        if response_sign is None:
            print(f"失败 请求异常，课程: {course_name}")
            return False

        if response_sign.status_code == 200:
            status = "签到成功"
            print(course_name, status)
            
            data = response_sign.json()["data"]
            socket_jwt = data["lessonToken"]
            jwt = response_sign.headers["Set-Auth"]
            identity_id = data["identityId"]

            def queue_on_listening_task():
                on_lesson_list.append({
                    "ppt_jwt": jwt,
                    "socket_jwt": socket_jwt,
                    "lesson_id": lesson_id,
                    "identity_id": identity_id,
                    "course_name": course_name
                })

            if len(filtered_courses) == 0:  # 无需过滤 全部进入监听队列
                queue_on_listening_task()
            else:  # 只监听符合条件的课程 其余的就签到
                if course_name in filtered_courses:
                    queue_on_listening_task()

            # 将签到信息写入文件顶部
            new_log = {
                "id": lesson_id,
                "title": course_name,
                "name": course_name,
                "time": get_now(),
                "student": get_user_name(),
                "status": status,
                "url": "https://changjiang.yuketang.cn/m/v2/lesson/student/" + str(lesson_id)
            }
            write_log(log_file_name, new_log)
            return True
        else:
            print("失败", response_sign.status_code, response_sign.text)
            return False
    except (KeyError, TypeError) as e:
        print(f"[ERROR] 处理课堂项目时出错: {e}")
        return False


def get_listening_classes_and_sign(filtered_courses: list):
    """获取正在进行的课堂并且签到、写日志"""
    response = get_listening()
    if response is None:
        return None
    
    # 第二层防守：验证response是字典
    if not isinstance(response, dict):
        print("[ERROR] API响应格式异常")
        return None
    
    # 检查关键字段存在
    if "onLessonClassrooms" not in response:
        print("[ERROR] API响应缺少课堂数据")
        return None
    
    on_lesson_classrooms = _parse_list_field(response["onLessonClassrooms"], "onLessonClassrooms")
    if on_lesson_classrooms is None:
        return None
    
    classes = on_lesson_classrooms
    if len(classes) == 0:
        print("\n无课")
        return
    else:
        print("\n发现上课")
        on_lesson_list = []
        
        for item in classes:
            _process_classroom_item(item, filtered_courses, on_lesson_list)
            
        # 所有签到完成后，进行死循环巡查，检查是否出现答题
        start_all_sockets(on_lesson_list)


def check_exam():
    """获取正在进行的考试"""
    response = get_listening()
    if response is None:
        return None
    
    # 验证response是字典
    if not isinstance(response, dict):
        print("[ERROR] API响应格式异常")
        return None
    
    # 检查关键字段
    if "upcomingExam" not in response:
        print("[ERROR] API响应缺少考试数据")
        return None
    
    upcoming_exam = _parse_list_field(response["upcomingExam"], "upcomingExam")
    if upcoming_exam is None:
        return None
    
    exams = upcoming_exam
    if len(exams) == 0:
        print("无考试")
        return
    else:
        print("发现考试")
        # 发邮件提醒
        email_notice(subject="雨课堂考试提醒", content="请打开雨课堂")
        return


def check_in_on_listening(lesson_id):
    """传入lessonId进行签到"""
    sign_data = {
        "source": check_in_sources["二维码"],
        "lessonId": str(lesson_id),
        "joinIfNotIn": True
    }

    return _safe_request("POST", host + api["sign_in_class"], headers=headers, json=sign_data)


def has_in_checked(lesson_id):
    """检查是否已经签过到"""
    logs = read_log(log_file_name)
    if logs and logs[-1]["id"] == lesson_id:
        print("已签过")
        return True
    else:
        return False


def check_in_on_latest(check_num=1):
    """收到的课程列表前check_num个全部进行签到、写日志（旧方法，已弃用）"""
    data = {
        "size": check_num,
        "type": [],
        "beginTime": None,
        "endTime": None
    }

    name = get_user_name()
    # 检查收到消息
    response = _safe_request("POST", host + api["get_received"], headers=headers, json=data)
    if response is None:
        print("[ERROR] 获取课程列表失败：网络异常")
        return

    if response.status_code == 200:
        response_data = response.json()
        # 提取 `data` 列表中的第一个元素信息
        if "data" in response_data and response_data["data"]:
            courseware_info = response_data["data"][0]
            courseware_id = courseware_info.get("coursewareId")
            courseware_title = courseware_info.get("coursewareTitle")
            course_name = courseware_info.get("courseName")

            if has_in_checked(courseware_id):
                return

            print("标题:", courseware_title)
            print("名称:", course_name)

            response_sign = check_in_on_listening(courseware_id)

            if response_sign.status_code == 200:
                status = "签到成功"

                print(name, status)

                # 将签到信息写入文件顶部
                new_log = {
                    "id": courseware_id,
                    "title": courseware_title,
                    "name": course_name,
                    "time": get_now(),
                    "student": name,
                    "status": status,
                    "url": "https://changjiang.yuketang.cn/m/v2/lesson/student/" + str(courseware_id)
                }
                write_log(log_file_name, new_log)
            else:
                print("失败", response_sign.status_code, response_sign.text)
        else:
            print("没有找到数据")
    else:
        print("请求失败:", response.status_code, response.text)
