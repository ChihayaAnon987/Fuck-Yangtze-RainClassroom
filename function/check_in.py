import requests
import json
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException, SSLError
from urllib3.util.retry import Retry

from function.listening_socket import start_all_sockets
from function.user import get_user_name
from config import host, api, headers, log_file_name,check_in_sources
from util.file import write_log, read_log
from util.notice import email_notice
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


def _safe_request(method, url, **kwargs):
    try:
        return _http_session.request(method=method, url=url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
    except SSLError as e:
        print(f"[ERROR] SSL连接失败: {e}")
    except RequestException as e:
        print(f"[ERROR] 网络请求失败: {e}")
    return None


# 获取正在进行的
def get_listening():
    response = _safe_request("GET", host + api["get_listening"], headers=headers)
    if response is None:
        return None

    if response.status_code == 200:
        try:
            response_data = response.json()
        except ValueError as e:
            print(f"[ERROR] API响应不是合法JSON: {e}")
            return None

        data = response_data.get("data")
        
        # 处理data可能是JSON字符串的情况（API双重编码）
        if isinstance(data, str):
            try:
                data = json.loads(data)
                print(f"[DEBUG] API data 字段已从JSON字符串解析为对象")
            except json.JSONDecodeError as e:
                print(f"[ERROR] 无法解析API data 字段，请重新获取SESSION: {e}")
                return None
        
        # 验证data是字典类型
        if not isinstance(data, dict):
            print(f"[ERROR] API data 字段类型错误，期望dict但收到 {type(data).__name__}")
            return None
        
        return data
    else:
        print(f"[ERROR] API 请求失败: HTTP {response.status_code}")
        return None


# 获取正在进行的课堂并且签到、写日志 新的签到方法
def get_listening_classes_and_sign(filtered_courses: list):
    response = get_listening()
    name = get_user_name()

    # 短期存储查看PPT用的JWT、lessonId等信息
    on_lesson_list = []

    if response is None:
        return None
    
    # 第二层防守：验证response是字典
    if not isinstance(response, dict):
        print(f"[ERROR] get_listening()返回类型错误，期望dict但收到 {type(response).__name__}: {response}")
        return None
    
    # 检查关键字段存在
    if "onLessonClassrooms" not in response:
        print(f"[ERROR] API响应缺少 onLessonClassrooms 字段，收到字段: {list(response.keys())}")
        return None
    
    on_lesson_classrooms = response["onLessonClassrooms"]
    
    # 处理onLessonClassrooms可能是JSON字符串的情况
    if isinstance(on_lesson_classrooms, str):
        try:
            on_lesson_classrooms = json.loads(on_lesson_classrooms)
            print(f"[DEBUG] onLessonClassrooms 已从JSON字符串解析")
        except json.JSONDecodeError as e:
            print(f"[ERROR] 无法解析 onLessonClassrooms: {e}")
            return None
    
    if not isinstance(on_lesson_classrooms, list):
        print(f"[ERROR] onLessonClassrooms 类型错误，期望list但收到 {type(on_lesson_classrooms).__name__}")
        return None
    
    classes = on_lesson_classrooms
    if len(classes) == 0:
        print("\n无课")
        return
    else:
        print("\n发现上课")
        for item in classes:
            try:
                course_name = item["courseName"]
                lesson_id = item["lessonId"]
                response_sign = check_in_on_listening(lesson_id)

                if response_sign is None:
                    print(f"失败 请求异常，课程: {course_name}")
                    continue

                if response_sign.status_code == 200:
                    status = "签到成功"

                    print(course_name, status)
                    data = response_sign.json()["data"]
                    socket_jwt = data["lessonToken"]

                    jwt = response_sign.headers["Set-Auth"]
                    # print(jwt)
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
                        "student": name,
                        "status": status,
                        "url": "https://changjiang.yuketang.cn/m/v2/lesson/student/" + str(lesson_id)
                    }
                    write_log(log_file_name, new_log)
                else:
                    print("失败", response_sign.status_code, response_sign.text)
            except (KeyError, TypeError) as e:
                print(f"[ERROR] 处理课堂项目时出错: {e}, 项目数据: {item}")
                continue
        # 所有签到完成后，进行死循环巡查，检查是否出现答题
        start_all_sockets(on_lesson_list)

        # for item in on_lesson_list:
        #     start_socket_ppt(ppt_jwt=item["ppt_jwt"],socket_jwt=item["socket_jwt"], lesson_id=item["lesson_id"], identity_id=item["identity_id"])


# 获取正在进行的考试
def check_exam():
    response = get_listening()
    if response is None:
        return None
    
    # 验证response是字典
    if not isinstance(response, dict):
        print(f"[ERROR] get_listening()返回类型错误，期望dict但收到 {type(response).__name__}")
        return None
    
    # 检查关键字段
    if "upcomingExam" not in response:
        print(f"[ERROR] API响应缺少 upcomingExam 字段")
        return None
    
    upcoming_exam = response["upcomingExam"]
    
    # 处理upcomingExam可能是JSON字符串的情况
    if isinstance(upcoming_exam, str):
        try:
            upcoming_exam = json.loads(upcoming_exam)
            print(f"[DEBUG] upcomingExam 已从JSON字符串解析")
        except json.JSONDecodeError as e:
            print(f"[ERROR] 无法解析 upcomingExam: {e}")
            return None
    
    if not isinstance(upcoming_exam, list):
        print(f"[ERROR] upcomingExam 类型错误，期望list但收到 {type(upcoming_exam).__name__}")
        return None
    
    exams = upcoming_exam
    if len(exams) == 0:
        print("无考试")
        return
    else:
        print("发现考试")
        print(exams)
        # 发邮件提醒
        email_notice(subject="雨课堂考试提醒", content="请打开雨课堂")
        return


# 传入lessonId 签到
def check_in_on_listening(lesson_id):
    sign_data = {
        "source": check_in_sources["二维码"],
        "lessonId": str(lesson_id),
        "joinIfNotIn": True
    }

    return _safe_request("POST", host + api["sign_in_class"], headers=headers, json=sign_data)


# 是否已经签过（写入日志）
def has_in_checked(lesson_id):
    logs = read_log(log_file_name)
    if logs and logs[-1]["id"] == lesson_id:
        print("已签过")
        return True
    else:
        return False


# 收到的课程列表前check_num个全部进行签到、写日志 旧的签到方法 弃用
def check_in_on_latest(check_num=1):
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
