import threading
import time
import os
import traceback
import re
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import requests
from requests.exceptions import RequestException
import websocket
import json
from config import host, api, headers, question_type, ai_request_timeout
from util.notice import email_notice
from util.ai import request_ai, init_ai_strategy
from util.session_manager import request_with_auto_session_refresh
from util.timestamp import get_date_time
from util.answer_validator import validate_answer_for_problem_type


answer_cache_file = "problem_answer_cache.json"
answer_cache_lock = threading.Lock()
VERBOSE_LOG = False
shutdown_event = threading.Event()
active_ws_lock = threading.Lock()
active_ws = {}


def _execute_direct_request(method, url, **kwargs):
    try:
        return requests.request(method=method, url=url, timeout=10, **kwargs)
    except RequestException:
        return None


def _log_message(level: str, message: str, course_name=None):
    """统一的日志输出函数"""
    level_map = {
        "info": "[INFO]",
        "ok": "[ OK ]",
        "warn": "[WARN]",
        "error": "[ERR ]"
    }
    prefix = level_map.get(level, "[INFO]")
    if course_name is None:
        print(f"{prefix} {message}")
    else:
        print(f"{prefix} [{course_name}] {message}")


def log(level, message, course_name=None):
    _log_message(level, message, course_name)


def debug_log(message, course_name=None):
    if VERBOSE_LOG:
        _log_message("info", message, course_name)


def wait_or_shutdown(seconds):
    return shutdown_event.wait(seconds)


def register_ws(lesson_id, ws):
    with active_ws_lock:
        active_ws[lesson_id] = ws


def unregister_ws(lesson_id, ws):
    with active_ws_lock:
        current = active_ws.get(lesson_id)
        if current is ws:
            active_ws.pop(lesson_id, None)


def close_all_active_ws():
    with active_ws_lock:
        ws_list = list(active_ws.values())
    for ws in ws_list:
        try:
            ws.close()
        except Exception:
            pass


def load_answer_cache():
    if not os.path.exists(answer_cache_file):
        return {}
    try:
        with open(answer_cache_file, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        _log_message("warn", f"读取题目缓存失败: {e}")
        return {}


def save_answer_cache(cache):
    try:
        with open(answer_cache_file, "w", encoding="utf-8") as file:
            json.dump(cache, file, ensure_ascii=False, indent=2)
    except Exception as e:
        _log_message("warn", f"写入题目缓存失败: {e}")


answer_cache = load_answer_cache()


def get_cached_result(lesson_id, problem_id):
    key = f"{lesson_id}:{problem_id}"
    with answer_cache_lock:
        cached = answer_cache.get(key)
        if isinstance(cached, dict):
            answer = cached.get("answer")
            question = cached.get("question")
            if answer is not None and isinstance(question, dict):
                return answer
        return None


def set_cached_result(lesson_id, problem_id, result, problem_type, problem_content, options, img_url):
    key = f"{lesson_id}:{problem_id}"
    with answer_cache_lock:
        answer_cache[key] = {
            "question": {
                "problem_id": problem_id,
                "problem_type": problem_type,
                "problem_content": problem_content,
                "options": options,
                "img_url": img_url
            },
            "answer": result,
            "updated_at": get_date_time()
        }
        save_answer_cache(answer_cache)


def _normalize_match_text(text):
    if text is None:
        return ""
    value = str(text).strip().lower()
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[，。,.、；;:：!！?？#\"'“”‘’（）()\[\]{}<>《》]", "", value)
    return value


def _extract_option_keys_from_text(raw_text, valid_keys):
    """仅在文本明确是选项键表达时提取 A/B/C...，避免从英文单词误提取。"""
    text = str(raw_text or "").strip().upper()
    if not text:
        return []

    if text in valid_keys:
        return [text]

    text = re.sub(r"^\s*(?:答案|选项|应选|选择|正确答案|参考答案|选)\s*[:：]?\s*", "", text)

    # 形如 A,C 或 A 和 C 的显式多选表达
    explicit_pattern = r"^[A-F](?:[\s,，、;/|+&和与及]+[A-F])+$"
    if re.fullmatch(explicit_pattern, text):
        letters = re.findall(r"[A-F]", text)
        return [ch for ch in letters if ch in valid_keys]

    # 形如 AC / ABC 的紧凑表达
    if re.fullmatch(r"^[A-F]{1,6}$", text):
        letters = list(text)
        deduped = []
        for ch in letters:
            if ch in valid_keys and ch not in deduped:
                deduped.append(ch)
        return deduped

    return []


def _to_answer_list(answer):
    if answer is None:
        return []
    if isinstance(answer, list):
        result = []
        for item in answer:
            if item is None:
                continue
            value = str(item).strip()
            if value:
                result.append(value)
        return result
    value = str(answer).strip()
    return [value] if value else []


def _map_answers_to_option_keys(answer, normalized_options, options_for_submit):
    """将题库/AI返回的文本答案映射为可提交的选项key（A/B/C...）。"""
    answer_list = _to_answer_list(answer)
    if not answer_list:
        return answer_list

    if not normalized_options or not isinstance(normalized_options, list):
        return answer_list
    if not normalized_options or not isinstance(normalized_options[0], dict):
        return answer_list

    valid_keys = set(k.upper() for k in options_for_submit if isinstance(k, str) and k)
    if not valid_keys:
        return answer_list

    option_items = []
    for opt in normalized_options:
        if not isinstance(opt, dict):
            continue
        key = str(opt.get("key", "")).strip().upper()
        value = str(opt.get("value", "")).strip()
        if key and value:
            option_items.append((key, value, _normalize_match_text(value)))

    mapped = []
    for item in answer_list:
        raw = str(item).strip()
        upper_raw = raw.upper()

        if upper_raw in valid_keys:
            mapped.append(upper_raw)
            continue

        extracted_keys = _extract_option_keys_from_text(raw, valid_keys)
        if extracted_keys:
            mapped.extend(extracted_keys)
            continue

        norm_raw = _normalize_match_text(raw)
        best_key = None
        best_score = 0.0
        best_norm_len = -1
        for key, value, norm_value in option_items:
            score = 0.0
            if norm_raw and norm_value:
                if norm_raw == norm_value:
                    score = 1.0
                elif norm_raw in norm_value:
                    # 返回文本更短时，按覆盖率加分，避免短词错误抢占。
                    coverage = len(norm_raw) / max(len(norm_value), 1)
                    score = 0.70 + (0.25 * coverage)
                elif norm_value in norm_raw:
                    # 返回文本更长时，优先选择被更完整覆盖的选项。
                    coverage = len(norm_value) / max(len(norm_raw), 1)
                    score = 0.78 + (0.22 * coverage)
                else:
                    score = SequenceMatcher(None, norm_raw, norm_value).ratio()

            if score > best_score or (abs(score - best_score) < 1e-9 and len(norm_value) > best_norm_len):
                best_score = score
                best_key = key
                best_norm_len = len(norm_value)

        if best_key and best_score >= 0.55:
            mapped.append(best_key)
            continue

        mapped.append(raw)

    deduped = []
    for item in mapped:
        value = str(item).strip().upper()
        if value and value in valid_keys and value not in deduped:
            deduped.append(value)

    return deduped if deduped else answer_list


def on_message_connect(ppt_jwt, lesson_id, identity_id, socket_jwt, sleep_second=10,
                       listening_started_ref=None, listening_start_time_ref=None,
                       listening_stop_event=None, status_thread=None,
                       answered_success=None, answering_in_progress=None,
                       answer_state_lock=None, course_name=None):
    # 这些是从外层传入的引用，用来保持状态在重连之间
    if listening_started_ref is None:
        listening_started_ref = [False]
    if listening_start_time_ref is None:
        listening_start_time_ref = [None]
    if listening_stop_event is None:
        listening_stop_event = threading.Event()
    if status_thread is None:
        status_thread = [None]
    if answered_success is None:
        answered_success = set()
    if answering_in_progress is None:
        answering_in_progress = set()
    if answer_state_lock is None:
        answer_state_lock = threading.Lock()
    
    problem_list = dict()
    
    def listening_status_updater():
        """后台线程：每秒更新一次监听状态"""
        while not listening_stop_event.is_set():
            if listening_started_ref[0] and listening_start_time_ref[0]:
                listening_duration = int(time.time() - listening_start_time_ref[0])
                print(f"\r[ >> ] [{course_name}] 监听中... ({listening_duration}s)", end='', flush=True)
            time.sleep(1)

    def send_safe(ws, payload):
        try:
            if ws is None or ws.sock is None or not ws.sock.connected:
                debug_log(f"连接已关闭，跳过发送 {payload.get('op', 'unknown')}", course_name)
                return False
            ws.send(json.dumps(payload))
            return True
        except Exception as e:
            log("warn", f"发送失败: {e}", course_name)
            return False

    def on_message(ws, message):
        try:
            # 下课 结束监听
            if "lessonfinished" in message:
                listening_stop_event.set()
                if listening_started_ref[0] and listening_start_time_ref[0]:
                    listening_duration = int(time.time() - listening_start_time_ref[0])
                    print()  # 换行
                    log("ok", f"课程结束，监听时长 {listening_duration}s", course_name)
                else:
                    log("ok", "课程结束，关闭连接", course_name)
                ws.lesson_ended = True
                ws.close()  # 关闭 WebSocket 连接
                return

            # 定时监听当前进度
            msg_json = json.loads(message)
            action = msg_json.get("op")
            if action == "fetchtimeline":
                # 检查返回timeline的最后一个（最新的时间）是否为problem，是则回答问题
                # time_lines = msg_json.get("timeline", [])
                # 过滤 time_lines["type"]!="problem"移除列表
                time_lines = msg_json.get("unlockedproblem", [])
                # 最新的题目
                if len(time_lines) == 0:
                    # 没题可答，继续获取PPT内容，看看是否老师换了新的PPT文件
                    debug_log("当前无题，等待下一次轮询", course_name)
                    if wait_or_shutdown(sleep_second):
                        return
                    auth_payload = {
                        "op": "hello",
                        "userid": identity_id,
                        "role": "student",
                        "auth": socket_jwt,
                        "lessonid": lesson_id
                    }
                    send_safe(ws, auth_payload)
                else:
                    for q_id in time_lines:
                        # 根据id进行检索已有的列表problem_list成员为dict,key["id"]为id
                        problem = problem_list.get(q_id)
                        if problem is not None:
                            answer(
                                lesson_id=lesson_id,
                                problem_id=q_id,
                                problem_type=problem["type"],
                                problem_content=problem["content"],
                                options=problem["options"],
                                jwt=ppt_jwt,
                                img_url=problem["img_url"],
                                answered_success=answered_success,
                                answering_in_progress=answering_in_progress,
                                answer_state_lock=answer_state_lock,
                                course_name=course_name
                            )
                            # 移除回答完的问题
                            if q_id in problem_list:
                                del problem_list[q_id]
                    # 答题/检查完成后再次发送检查 直到(下课)关闭socket通道
                    send_safe(ws, {
                        "op": "fetchtimeline",
                        "lessonid": str(lesson_id),
                        "msgid": 1
                    })
                # 睡一会，别频率过头了被封
                if wait_or_shutdown(sleep_second):
                    return
            else:
                # 首次获取PPT内容，进而保存所有题目
                # 解析出pres_id
                ppt_ids = set()
                if "timeline" in message:
                    time_lines = list(json.loads(message)["timeline"])
                    # 每一item中type=slide代表每一张PPT，拿到pres后，请求get_ppt接口拿到PPT具体内容，然后进行检测是否有problem
                    for item in time_lines:
                        # 是PPT
                        if item["type"] == "slide":
                            ppt_ids.add(item["pres"])
                else:
                    debug_log("收到非timeline消息", course_name)
                # 开始获取PPT
                new_headers = headers.copy()
                new_headers["Authorization"] = "Bearer " + ppt_jwt
                new_headers["User-Agent"] = (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0")

                for pres_id in ppt_ids:
                    url = host + api["get_ppt"].format(pres_id)

                    response = request_with_auto_session_refresh(
                        request_executor=_execute_direct_request,
                        method="GET",
                        url=url,
                        headers=new_headers,
                        reason="拉取PPT时检测到 SESSION 失效",
                    )
                    if response is None:
                        log("warn", "获取PPT失败: 网络异常", course_name)
                        continue
                    if response.status_code == 200:
                        ppt_pages = response.json()["data"]["slides"]
                        for ppt in ppt_pages:
                            try:
                                # 有答题
                                if "problem" in ppt:
                                    # print(ppt)
                                    # 先保存所有题目，供索引，然后监听socket，对应的问题发送的瞬间进行answer
                                    question = ppt["problem"]
                                    options = None
                                    q_type = question["problemType"]
                                    if q_type == 1 or q_type == 2 or q_type == 3:
                                        raw_options = question.get("options")
                                        # 【防御】处理options可能是JSON字符串的情况
                                        if isinstance(raw_options, str):
                                            try:
                                                options = json.loads(raw_options)
                                                debug_log(f"选项从JSON字符串解析: {type(options)}", course_name)
                                            except:
                                                options = raw_options
                                        else:
                                            options = raw_options

                                    answered = list(ppt["problem"]["answers"])
                                    if len(answered) == 0:  # 回答完的问题不入队
                                        # 保存
                                        save_dict = {
                                            "type": question["problemType"],
                                            "content": question["body"],
                                            "options": options,
                                            "img_url": ppt["coverAlt"]
                                        }
                                        debug_log(f"缓存待答题 problemId={question['problemId']} options_type={type(options)}", course_name)
                                        problem_list[question["problemId"]] = save_dict
                            except Exception as ppt_err:
                                log("warn", f"处理PPT题目异常: {ppt_err}", course_name)
                                if VERBOSE_LOG:
                                    print(f"[WARN] PPT处理异常详情: {traceback.format_exc()}")
                    else:
                        log("warn", f"获取PPT失败 status={response.status_code}", course_name)
                # 开始监听 启动后台线程
                # 标记监听已启动
                if not listening_started_ref[0]:
                    listening_started_ref[0] = True
                    listening_start_time_ref[0] = time.time()
                    log("info", "进入监听状态", course_name)
                    # 启动后台线程来定期更新监听状态
                    if status_thread[0] is None:
                        status_thread[0] = threading.Thread(target=listening_status_updater, daemon=True)
                        status_thread[0].start()
                
                send_safe(ws, {
                    "op": "fetchtimeline",
                    "lessonid": str(lesson_id),
                    "msgid": 1
                })
        except Exception as e:
            # 【改进】捕获详细的错误信息便于调试
            error_detail = traceback.format_exc()
            log("error", f"消息处理异常: {e}", course_name)
            if VERBOSE_LOG or "string indices" in str(e):
                # 输出完整的traceback便于调试
                print(f"[ERR TRACE] {error_detail}")
    return on_message


def on_error(ws, error):
    if "interpreter shutdown" in str(error).lower():
        ws.stop_reconnect = True


def on_close(ws, close_status_code, close_msg):
    pass


def on_open_connet(jwt, lesson_id, identity_id):
    def on_open(ws):
        auth_payload = {
            "op": "hello",
            "userid": identity_id,
            "role": "student",
            "auth": jwt,
            "lessonid": lesson_id
        }
        ws.send(json.dumps(auth_payload))

    return on_open


# 监听上课
def start_socket_ppt(ppt_jwt, socket_jwt, lesson_id, identity_id, course_name=None):
    reconnect_count = 0
    delay = 2

    # 在外层创建这些变量，使其在整个连接生命周期内保持
    listening_started_ref = [False]
    listening_start_time_ref = [None]
    listening_stop_event = threading.Event()
    status_thread = [None]  # 用列表存储线程对象，方便在嵌套函数中修改
    answered_success = set()
    answering_in_progress = set()
    answer_state_lock = threading.Lock()

    while not shutdown_event.is_set():
        ws = websocket.WebSocketApp(
            url=api["websocket"],
            on_open=on_open_connet(lesson_id=lesson_id, identity_id=identity_id, jwt=socket_jwt),
            on_message=on_message_connect(
                ppt_jwt=ppt_jwt,
                lesson_id=lesson_id,
                identity_id=identity_id,
                socket_jwt=socket_jwt,
                listening_started_ref=listening_started_ref,
                listening_start_time_ref=listening_start_time_ref,
                listening_stop_event=listening_stop_event,
                status_thread=status_thread,
                answered_success=answered_success,
                answering_in_progress=answering_in_progress,
                answer_state_lock=answer_state_lock,
                course_name=course_name
            ),
            on_error=on_error,
            on_close=on_close,
        )
        ws.lesson_ended = False
        ws.stop_reconnect = False
        register_ws(lesson_id, ws)
        ws.run_forever(ping_interval=20, ping_timeout=10)
        unregister_ws(lesson_id, ws)

        if shutdown_event.is_set():
            log("info", "收到退出信号，停止监听", course_name)
            break

        if getattr(ws, "lesson_ended", False):
            log("ok", "课程结束，停止重连", course_name)
            break

        if getattr(ws, "stop_reconnect", False):
            log("info", "解释器正在退出，停止重连", course_name)
            break

        reconnect_count += 1
        if wait_or_shutdown(delay):
            break
        delay = 5


# 多线程 多个上课同时监听
def start_all_sockets(on_lesson_list):
    threads = []

    for item in on_lesson_list:
        t = threading.Thread(
            target=start_socket_ppt,
            kwargs={
                "ppt_jwt": item["ppt_jwt"],
                "socket_jwt": item["socket_jwt"],
                "lesson_id": item["lesson_id"],
                "identity_id": item["identity_id"],
                "course_name": item.get("course_name")
            },
            daemon=False
        )
        t.start()
        threads.append(t)

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        log("info", "收到 Ctrl+C，正在优雅退出...")
        shutdown_event.set()
        close_all_active_ws()
        for t in threads:
            t.join(timeout=3)
        log("ok", "监听线程已退出")


# 答题
def answer(lesson_id, problem_id, problem_type, jwt, problem_content, options, img_url,
           answered_success=None, answering_in_progress=None, answer_state_lock=None,
           course_name=None):
    debug_log(f"答题触发 type={question_type[problem_type]} problemId={problem_id}", course_name)

    if answered_success is None:
        answered_success = set()
    if answering_in_progress is None:
        answering_in_progress = set()
    if answer_state_lock is None:
        answer_state_lock = threading.Lock()

    dedupe_key = f"{lesson_id}:{problem_id}"
    with answer_state_lock:
        if dedupe_key in answered_success:
            #log("info", f"题目已提交过，跳过重复答题 problemId={problem_id}", lesson_id)
            return
        if dedupe_key in answering_in_progress:
            debug_log(f"题目正在答题中，跳过重复触发 problemId={problem_id}", course_name)
            return
        answering_in_progress.add(dedupe_key)

    try:
        # 【改进】确保options可用，并区分“给AI的完整选项”和“提交/兜底用的选项key”
        normalized_options = options
        if normalized_options is None:
            normalized_options = []
        elif isinstance(normalized_options, str):
            # 如果是字符串，尝试解析为JSON
            try:
                parsed = json.loads(normalized_options)
                normalized_options = parsed if isinstance(parsed, list) else []
            except Exception:
                debug_log(f"无法解析选项字符串: {normalized_options[:50]}", course_name)
                normalized_options = []

        if not isinstance(normalized_options, list):
            normalized_options = []

        # 传给AI：保留原始结构（dict列表会包含value，信息更完整）
        options_for_ai = normalized_options

        # 提交/兜底用：提取可提交的选项key
        options_for_submit = []
        if normalized_options and isinstance(normalized_options[0], dict):
            options_for_submit = [opt.get("key", "") for opt in normalized_options if isinstance(opt, dict) and opt.get("key")]
        else:
            options_for_submit = [opt for opt in normalized_options if isinstance(opt, str) and opt]

        cached_result = get_cached_result(lesson_id=lesson_id, problem_id=problem_id)
        hit_cache = cached_result is not None
        if cached_result is not None:
            result = cached_result
            debug_log(f"命中题目缓存，跳过题库API problemId={problem_id}", course_name)
        else:
            # 【关键改进】添加WebSocket超时保护
            # WebSocket的ping_timeout=10秒，这里设置<8秒的超时可以防止连接断开
            # 如果request_ai超时，则使用默认答案而不是让WebSocket断开连接
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    # 设置超时为 ai_request_timeout 秒（配置项，默认8秒）
                    future = executor.submit(
                        request_ai,
                        type=question_type[problem_type],
                        problem=problem_content,
                        options=options_for_ai,
                        img_url=img_url
                    )
                    # 等待结果，超时时间为配置的值
                    result = future.result(timeout=ai_request_timeout)
                    debug_log(f"AI解题完成 problemId={problem_id}", course_name)
            except FuturesTimeoutError:
                # 超时：选择一个安全的默认答案
                log("warn", f"AI解题超时（{ai_request_timeout}秒），使用默认答案 problemId={problem_id}", course_name)
                # 如果有选项，返回第一个选项；否则返回"A"
                if options_for_submit and len(options_for_submit) > 0:
                    result = [options_for_submit[0]]
                else:
                    result = ["A"]  # 最后的保险
            except Exception as e:
                log("error", f"AI解题异常: {str(e)[:100]} problemId={problem_id}", course_name)
                # 异常处理：也使用默认答案
                if options_for_submit and len(options_for_submit) > 0:
                    result = [options_for_submit[0]]
                else:
                    result = ["A"]

        mapped_result = _map_answers_to_option_keys(result, normalized_options, options_for_submit)
        was_mapped = mapped_result != result
        if was_mapped:
            debug_log(f"答案映射: 原始={result} -> 提交={mapped_result}", course_name)
        result = mapped_result

        # 【质量保障】校验答案与题型是否匹配
        is_valid, validated_result, validation_reason = validate_answer_for_problem_type(
            answer=result,
            problem_type=problem_type,
            options=normalized_options
        )

        if not is_valid:
            log("warn", f"答案校验失败: {validation_reason}，使用默认答案 problemId={problem_id}", course_name)
            # 校验失败时使用默认答案
            if options_for_submit and len(options_for_submit) > 0:
                result = [options_for_submit[0]]
            else:
                result = ["A"]
        elif len(validated_result) != len(result):
            debug_log(f"答案已规范化: {validation_reason}，从 {result} -> {validated_result}", course_name)
            result = validated_result

        if (not hit_cache) or was_mapped:
            # 缓存结果（保存映射后的可提交答案）
            # 命中旧缓存但被映射成功时，回写缓存，避免后续继续读到value文本
            set_cached_result(
                lesson_id=lesson_id,
                problem_id=problem_id,
                result=result,
                problem_type=problem_type,
                problem_content=problem_content,
                options=normalized_options,
                img_url=img_url
            )

        post_json = {
            "problemId": problem_id,
            "problemType": problem_type,
            "dt": get_date_time(),
            "result": result
        }

        new_headers = headers.copy()
        new_headers["Authorization"] = "Bearer " + jwt
        new_headers["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                     "Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0")

        response = request_with_auto_session_refresh(
            request_executor=_execute_direct_request,
            method="POST",
            url=host + api["answer"],
            headers=new_headers,
            json=post_json,
            reason="提交答案时检测到 SESSION 失效",
        )
        if response is None:
            log("error", f"答题失败 problemId={problem_id}（网络异常）", course_name)
            return

        if response.status_code == 200:
            with answer_state_lock:
                answered_success.add(dedupe_key)
            log("ok", f"答题成功  题目={problem_content} 答案={result}", course_name)
        else:
            email_notice(content="答题失败，请手动前往雨课堂", subject="答题失败")
            log("error", f"答题失败 problemId={problem_id}", course_name)
            msg = response.json()["msg"]
            if msg == "LESSON_END":
                log("info", "题目已结束", course_name)
            else:
                log("warn", f"答题返回: {msg}", course_name)
    finally:
        with answer_state_lock:
            answering_in_progress.discard(dedupe_key)
