# -*- coding: utf-8 -*-
import argparse
import configparser
import contextvars
import enum
from functools import partial
import sys
import threading
import time
import traceback
from concurrent.futures.thread import ThreadPoolExecutor
from dataclasses import dataclass
from collections.abc import Mapping
from queue import PriorityQueue
from queue import Empty
from queue import Queue
try:
    from queue import ShutDown
except ImportError:
    # ShutDown is only available in Python 3.13+
    class ShutDown(Exception):
        pass
from threading import RLock
from typing import Any

from tqdm import tqdm

from api.answer import AI, Tiku
from api.base import Chaoxing, Account, StudyResult, build_session
from api.exceptions import LoginError, InputFormatError
from api.logger import logger
from api.notification import Notification
from api.live import Live
from api.live_process import LiveProcessor, StudyCancelled
from api.cookies import load_cookie_file, save_cookie_file
from api.vision_ocr import vision_ocr_context


class EngineResponseError(ValueError):
    """Raised when an engine response is not one of the known wire shapes."""

    code = "engine_response_invalid"

    def __init__(self, response_name: str):
        super().__init__(f"Invalid {response_name} response")


class ChapterResult(enum.Enum):
    SUCCESS=0,
    ERROR=1,
    NOT_OPEN=2,
    PENDING=3


def raise_if_cancelled(config: Mapping[str, Any] | None) -> None:
    """Raise ``StudyCancelled`` at a safe learning-engine boundary.

    The command-line path does not provide a cancellation event, so all
    existing callers retain their historical behaviour.  Web callers inject
    one event into ``common_config`` and every course/chapter/job boundary
    checks it before moving on.
    """

    if not config:
        return
    cancel_event = config.get("cancel_event")
    is_set = getattr(cancel_event, "is_set", None)
    if callable(is_set) and is_set():
        raise StudyCancelled()


def _notify_callback(
    config: Mapping[str, Any] | None,
    name: str,
    *args: Any,
) -> None:
    """Invoke an optional monitoring callback without changing CLI behavior."""

    if not config:
        return
    callback = config.get(name)
    if not callable(callback):
        return
    try:
        callback(*args)
    except StudyCancelled:
        raise
    except Exception as exc:
        # A malformed engine payload is a task failure, not optional
        # monitoring metadata.  Let the owning task boundary observe it.
        if getattr(exc, "code", None) == "engine_response_invalid":
            raise
        # Monitoring is best effort.  The task runner itself owns the public
        # error boundary, so callback diagnostics stay in debug logs.
        logger.debug("任务监控回调失败（异常内容已省略）")


def _normalise_points(value: Any) -> list[dict[str, Any]]:
    """Return chapter points from the decoder or a wrapped integration value."""

    if isinstance(value, Mapping):
        for key in ("points", "data", "items"):
            if key in value:
                return _normalise_points(value[key])
        raise EngineResponseError("course points")
    if isinstance(value, list):
        if not value:
            return []
        if not all(isinstance(item, Mapping) for item in value):
            raise EngineResponseError("course points")
        return [dict(item) for item in value]
    if isinstance(value, tuple):
        if not value:
            return []
        # The decoder's compatibility form is ``(points, metadata)``.  A
        # tuple containing point mappings is also accepted as a plain list.
        first = value[0]
        if len(value) == 2 and isinstance(first, (list, tuple)):
            return _normalise_points(first)
        if len(value) == 2 and isinstance(first, Mapping) and any(
            key in first for key in ("points", "data", "items")
        ):
            return _normalise_points(first)
        if all(isinstance(item, Mapping) for item in value):
            return [dict(item) for item in value]
        raise EngineResponseError("course points")
    raise EngineResponseError("course points")


def _normalise_jobs(value: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return jobs and metadata from tuple, list, or wrapped API responses."""

    if isinstance(value, list):
        if not value:
            return [], {}
        if not all(isinstance(item, Mapping) for item in value):
            raise EngineResponseError("job list")
        return [dict(item) for item in value], {}

    if isinstance(value, tuple):
        if not value:
            return [], {}
        if len(value) == 1:
            return _normalise_jobs(value[0])
        # The engine's native shape is ``(jobs, info)``.  The second item is
        # metadata, while a tuple of job mappings remains a plain job list.
        if len(value) == 2 and isinstance(value[1], Mapping) and (
            isinstance(value[0], (list, tuple))
            or (
                isinstance(value[0], Mapping)
                and any(
                    key in value[0]
                    for key in (
                        "jobs",
                        "job_list",
                        "items",
                        "data",
                        "job_info",
                        "jobInfo",
                        "notOpen",
                        "not_open",
                    )
                )
            )
        ):
            jobs, nested_info = _normalise_jobs(value[0])
            return jobs, {**nested_info, **dict(value[1])}
        if all(isinstance(item, Mapping) for item in value):
            return [dict(item) for item in value], {}
        raise EngineResponseError("job list")

    if isinstance(value, Mapping):
        info: dict[str, Any] = {}
        for info_key in ("job_info", "jobInfo"):
            if info_key in value:
                if not isinstance(value[info_key], Mapping):
                    raise EngineResponseError("job list")
                info.update(dict(value[info_key]))
        if "notOpen" in value:
            info["notOpen"] = value["notOpen"]
        if "not_open" in value:
            info["notOpen"] = value["not_open"]

        for jobs_key in ("jobs", "job_list", "items"):
            if jobs_key in value:
                jobs, nested_info = _normalise_jobs(value[jobs_key])
                return jobs, {**nested_info, **info}
        if "data" in value:
            jobs, nested_info = _normalise_jobs(value["data"])
            return jobs, {**nested_info, **info}

        # A metadata-only response is a valid empty chapter response (for
        # example ``{"notOpen": true}`); any other mapping is malformed.
        if value and set(value).issubset({"job_info", "jobInfo", "notOpen", "not_open"}):
            return [], info
        raise EngineResponseError("job list")

    raise EngineResponseError("job list")


def _normalise_courses(value: Any) -> list[dict[str, Any]]:
    """Return course dictionaries from list and common response wrappers."""

    if isinstance(value, Mapping):
        for key in ("courses", "course_list", "items"):
            if key in value:
                return _normalise_courses(value[key])
        if "data" in value:
            return _normalise_courses(value["data"])
        return []
    if isinstance(value, (list, tuple)):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def run_with_task_context(
    task_id: str,
    target,
    *args,
    **kwargs,
):
    """Run a target under the task-log context used by Web workers."""

    from webapp.task_logging import run_with_task_context as _run_with_task_context

    return _run_with_task_context(task_id, target, *args, **kwargs)


def _run_worker_with_context(
    worker_context: Mapping[str, Any] | None,
    target,
    *args,
    **kwargs,
):
    """Run a worker under both task logging and task-local OCR context.

    The wrapper's first argument deliberately is not named ``config``.  Job
    workers pass their own ``config=...`` keyword through to ``target``; using
    the same name here makes Python reject that valid call before the target
    can run ("multiple values for argument 'config'").
    """

    task_id = worker_context.get("task_id") if worker_context else None
    ocr_config = worker_context.get("ocr_config") if worker_context else None
    log_secrets = worker_context.get("_log_secrets") if worker_context else None

    def invoke():
        with vision_ocr_context(ocr_config):
            return target(*args, **kwargs)

    if task_id is not None:
        if log_secrets is None:
            return run_with_task_context(task_id, invoke)
        return run_with_task_context(task_id, invoke, log_secrets=log_secrets)
    return invoke()


def log_error(func):
    def wrapper(*args, **kwargs):
        try:
            func(*args, **kwargs)
        except StudyCancelled:
            # Cooperative cancellation is an expected terminal path, not a
            # worker error.  Let the owning runner observe it at the next
            # safe boundary without emitting an error traceback per thread.
            raise
        except BaseException as e:
            logger.error("线程工作失败（异常内容已省略）")
            raise

    return wrapper


def str_to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="Samueli924/chaoxing",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--use-cookies", action="store_true", help="使用cookies登录")

    parser.add_argument(
        "-c", "--config", type=str, default=None, help="使用配置文件运行程序"
    )
    parser.add_argument("-u", "--username", type=str, default=None, help="手机号账号")
    parser.add_argument("-p", "--password", type=str, default=None, help="登录密码")
    parser.add_argument(
        "-l", "--list", type=str, default=None, help="要学习的课程ID列表, 以 , 分隔"
    )
    parser.add_argument(
        "-s", "--speed", type=float, default=1.0, help="视频播放倍速 (默认1, 最大2)"
    )
    parser.add_argument(
        "-j", "--jobs", type=int, default=4, help="同时进行的章节数 (默认4, 如果一个章节有多个任务点，不会限制同时处理任务点的数量)"
    )

    parser.add_argument(
        "-v",
        "--verbose",
        "--debug",
        action="store_true",
        help="启用调试模式, 输出DEBUG级别日志",
    )
    parser.add_argument(
        "-a", "--notopen-action", type=str, default="retry", 
        choices=["retry", "ask", "continue"],
        help="遇到关闭任务点时的行为: retry-重试, ask-询问, continue-继续"
    )

    # 在解析之前捕获 -h 的行为
    if len(sys.argv) == 2 and sys.argv[1] in {"-h", "--help"}:
        parser.print_help()
        sys.exit(0)

    return parser.parse_args()


def load_config_from_file(config_path):
    """从配置文件加载设置"""
    config = configparser.ConfigParser()
    config.read(config_path, encoding="utf8")
    
    common_config: dict[str, Any] = {}
    tiku_config: dict[str, Any] = {}
    notification_config: dict[str, Any] = {}
    
    # 检查并读取common节
    if config.has_section("common"):
        common_config = dict(config.items("common"))
        # 处理course_list，将字符串转换为列表
        if "course_list" in common_config and common_config["course_list"]:
            common_config["course_list"] = [item.strip() for item in common_config["course_list"].split(",") if item.strip()]
        # 处理speed，将字符串转换为浮点数
        if "speed" in common_config:
            common_config["speed"] = float(common_config["speed"])
        if "jobs" in common_config:
            common_config["jobs"] = int(common_config["jobs"])
        # 处理notopen_action，设置默认值为retry
        if "notopen_action" not in common_config:
            common_config["notopen_action"] = "retry"
        if "use_cookies" in common_config:
            common_config["use_cookies"] = str_to_bool(common_config["use_cookies"])
        if "username" in common_config and common_config["username"] is not None:
            common_config["username"] = common_config["username"].strip()
        if "password" in common_config and common_config["password"] is not None:
            common_config["password"] = common_config["password"].strip()

    # 检查并读取tiku节
    if config.has_section("tiku"):
        tiku_config = dict(config.items("tiku"))
        # 处理数值类型转换
        for key in ["delay", "cover_rate"]:
            if key in tiku_config:
                tiku_config[key] = float(tiku_config[key])

    # 检查并读取notification节
    if config.has_section("notification"):
        notification_config = dict(config.items("notification"))
    
    return common_config, tiku_config, notification_config


def build_config_from_args(args):
    """从命令行参数构建配置"""
    common_config = {
        "use_cookies": args.use_cookies,
        "username": args.username,
        "password": args.password,
        "course_list": [item.strip() for item in args.list.split(",") if item.strip()] if args.list else None,
        "speed": args.speed if args.speed else 1.0,
        "jobs": args.jobs,
        "notopen_action": args.notopen_action if args.notopen_action else "retry"
    }
    return common_config, {}, {}


def init_config():
    """初始化配置"""
    args = parse_args()
    
    if args.config:
        return load_config_from_file(args.config)
    else:
        return build_config_from_args(args)


def init_chaoxing(
    common_config,
    tiku_config,
    *,
    answer_semaphore: threading.Semaphore | None = None,
    session=None,
    cookie_update_callback=None,
):
    """初始化超星实例"""
    username = common_config.get("username", "")
    password = common_config.get("password", "")
    use_cookies = common_config.get("use_cookies", False)
    
    # 如果没有提供用户名密码，从命令行获取
    if (not username or not password) and not use_cookies:
        username = input("请输入你的手机号, 按回车确认\n手机号:")
        password = input("请输入你的密码, 按回车确认\n密码:")
    
    account = Account(username, password)
    
    # 设置题库
    tiku = Tiku()
    tiku.config_set(tiku_config)  # 载入配置
    tiku = tiku.get_tiku_from_config()  # 载入题库
    if isinstance(tiku, AI) and answer_semaphore is not None:
        tiku.set_request_semaphore(answer_semaphore)
    set_cancel_event = getattr(tiku, "set_cancel_event", None)
    if callable(set_cancel_event):
        set_cancel_event(common_config.get("cancel_event"))
    tiku.init_tiku()  # 初始化题库
    
    # 获取查询延迟设置
    query_delay = tiku_config.get("delay", 0)
    # 获取AI题库并发配置（仅在使用AI题库时生效）
    ai_concurrency = tiku_config.get("ai_concurrency")
    
    # 为当前账号创建独立的 HTTP 会话，并保留 CLI 的 cookie 文件行为。
    # CLI 未提供 cookie_path 时继续读写默认 cookies.txt；Web 调用方可
    # 传入账号专属路径，避免不同账号共享该文件。
    # A Web task supplies an account-owned session and callback directly.
    # Keeping the explicit arguments optional preserves the CLI's historical
    # cookies.txt loading/saving behaviour.
    if session is None:
        cookie_path = common_config.get("cookie_path")
        if cookie_path is None:
            initial_cookies = load_cookie_file()
            cookie_update_callback = save_cookie_file
        else:
            initial_cookies = load_cookie_file(cookie_path)
            cookie_update_callback = partial(save_cookie_file, path=cookie_path)
        session = build_session(initial_cookies)
    task_values = {
        key: common_config[key]
        for key in ("task_id", "ocr_config", "cancel_event")
        if key in common_config
    }
    chaoxing = Chaoxing(
        account=account,
        tiku=tiku,
        query_delay=query_delay,
        ai_concurrency=ai_concurrency,
        session=session,
        cookie_update_callback=cookie_update_callback,
        **task_values,
    )
    
    return chaoxing


def process_job(
    chaoxing: Chaoxing,
    course: dict,
    job: dict,
    job_info: dict,
    speed: float,
    progress_callback=None,
    config: Mapping[str, Any] | None = None,
) -> StudyResult:
    """处理单个任务点"""
    if config is None and isinstance(progress_callback, Mapping) and (
        "cancel_event" in progress_callback or "task_id" in progress_callback
    ):
        config = progress_callback
        progress_callback = config.get("video_progress_callback")
    raise_if_cancelled(config)
    # 视频任务
    if job["type"] == "video":
        logger.trace("识别到视频任务")
        # 超星的接口没有返回当前任务是否为Audio音频任务
        video_result = chaoxing.study_video(
            course, job, job_info, _speed=speed, _type="Video",
            progress_callback=progress_callback,
            cancel_event=config.get("cancel_event") if config else None,
        )
        raise_if_cancelled(config)
        if video_result.is_failure():
            logger.warning("当前任务非视频任务, 正在尝试音频任务解码")
            video_result = chaoxing.study_video(
                course, job, job_info, _speed=speed, _type="Audio",
                progress_callback=progress_callback,
                cancel_event=config.get("cancel_event") if config else None,
            )
            raise_if_cancelled(config)
        if video_result.is_failure():
            logger.warning(
                "视频任务处理失败，已跳过（任务元数据已省略）"
            )
        return video_result
    # 文档任务
    elif job["type"] == "document":
        logger.trace("识别到文档任务")
        result = chaoxing.study_document(course, job)
        raise_if_cancelled(config)
        return result
    # 测验任务
    elif job["type"] == "workid":
        logger.trace("识别到章节检测任务")
        result = chaoxing.study_work(course, job, job_info)
        raise_if_cancelled(config)
        return result
    # 阅读任务
    elif job["type"] == "read":
        logger.trace("识别到阅读任务")
        result = chaoxing.study_read(course, job, job_info)
        raise_if_cancelled(config)
        return result
    # 直播任务
    elif job["type"] == "live":
        logger.trace("识别到直播任务")
        try:
            raise_if_cancelled(config)
            # 准备直播所需参数
            defaults = {
                "userid": chaoxing.get_uid(),
                "clazzId": course.get("clazzId"),
                "knowledgeid": job_info.get("knowledgeid")
            }
            
            # 创建直播对象
            live = Live(
                attachment=job,
                defaults=defaults,
                course_id=course.get("courseId"),
                session=chaoxing.session,
            )
            
            live_error: list[BaseException] = []
            live_result: list[bool] = []

            # Exceptions raised in a worker thread do not propagate to the
            # joining caller by themselves.  Capture them so cancellation is
            # represented by StudyCancelled rather than being converted into
            # an ordinary failed live job.
            def run_live_and_capture():
                try:
                    live_result.append(
                        bool(
                            _run_worker_with_context(
                                config,
                                LiveProcessor.run_live,
                                live,
                                speed,
                                cancel_event=config.get("cancel_event") if config else None,
                            )
                        )
                    )
                except BaseException as exc:
                    live_error.append(exc)

            thread = threading.Thread(target=run_live_and_capture, daemon=True)
            thread.start()
            # The live worker normally observes the same cancel event itself,
            # but a socket/client implementation may block until its timeout.
            # Poll the join so the owning Web task can become stopped promptly
            # instead of waiting indefinitely for a non-cooperative worker.
            while thread.is_alive():
                thread.join(timeout=0.1)
                raise_if_cancelled(config)
            if live_error:
                raise live_error[0]
            raise_if_cancelled(config)
            return StudyResult.SUCCESS if live_result == [True] else StudyResult.ERROR
        except StudyCancelled:
            raise
        except Exception:
            logger.error("处理直播任务失败（异常内容已省略）")
            return StudyResult.ERROR

    logger.error("未知任务类型（任务元数据已省略）")
    return StudyResult.ERROR


@dataclass(order=True)
class ChapterTask:
    index: int
    point: dict[str, Any]
    result: ChapterResult = ChapterResult.PENDING
    tries: int = 0

class JobProcessor:
    def __init__(self, chaoxing: Chaoxing, course: dict[str, Any], tasks: list[ChapterTask], config: dict[str, Any]):
        self.chaoxing = chaoxing
        self.course = course
        self.speed = config["speed"]
        self.max_tries = 5
        self.tasks = tasks
        self.failed_tasks: list[ChapterTask] = []
        self.task_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.retry_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.wait_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.threads: list[threading.Thread] = []
        self.retry_thread_handle: threading.Thread | None = None
        self.worker_num = config["jobs"]
        self.config = config
        self.worker_errors: Queue[BaseException] = Queue()
        self._worker_error_event = threading.Event()
        self._worker_stop_event = threading.Event()

    def _record_worker_error(self, error: BaseException) -> None:
        """Publish a worker failure without logging its raw exception text."""

        if isinstance(error, StudyCancelled):
            return
        self.worker_errors.put(error)
        self._worker_error_event.set()
        self._worker_stop_event.set()

    def _join_workers(self) -> None:
        for thread in self.threads:
            thread.join()
        if self.retry_thread_handle is not None:
            self.retry_thread_handle.join()

    def _first_worker_error(self) -> BaseException | None:
        try:
            return self.worker_errors.get_nowait()
        except Empty:
            return None

    def run(self):
        raise_if_cancelled(self.config)
        for task in self.tasks:
            self.task_queue.put(task)

        for i in range(self.worker_num):
            worker_context = contextvars.copy_context()
            thread = threading.Thread(
                target=worker_context.run,
                args=(_run_worker_with_context, self.config, self.worker_thread),
                daemon=True,
            )
            self.threads.append(thread)
            thread.start()

        retry_context = contextvars.copy_context()
        self.retry_thread_handle = threading.Thread(
            target=retry_context.run,
            args=(_run_worker_with_context, self.config, self.retry_thread),
            daemon=True,
        )
        self.retry_thread_handle.start()

        # ``Queue.join`` cannot be interrupted by a cancellation event.  Poll
        # the unfinished count instead, draining queued work when stopping so
        # the parent runner can leave only already-running blocking calls to
        # finish at their next safe checkpoint.
        while self.task_queue.unfinished_tasks:
            if self._worker_error_event.is_set():
                break
            cancel_event = self.config.get("cancel_event")
            if cancel_event is not None and cancel_event.is_set():
                break
            time.sleep(0.05)

        # Stop and join every worker before draining queued work.  This keeps
        # Queue accounting deterministic and prevents a daemon worker from
        # dying silently while the course is reported as complete.
        self._worker_stop_event.set()
        self._join_workers()
        self._drain_pending_tasks()

        worker_error = self._first_worker_error()
        if worker_error is not None:
            raise worker_error
        raise_if_cancelled(self.config)

    def _drain_pending_tasks(self) -> None:
        """Mark queued tasks complete after cooperative cancellation."""

        while True:
            try:
                self.task_queue.get_nowait()
            except Empty:
                break
            else:
                self.task_queue.task_done()

        while True:
            try:
                self.retry_queue.get_nowait()
            except Empty:
                break
            else:
                # A retry item still owns the original task_queue unfinished
                # count until it is moved back by retry_thread.
                self.retry_queue.task_done()
                if self.task_queue.unfinished_tasks:
                    self.task_queue.task_done()


    def worker_thread(self):
        tqdm.set_lock(tqdm.get_lock())
        while not self._worker_stop_event.is_set():
            try:
                raise_if_cancelled(self.config)
            except StudyCancelled:
                return
            try:
                task = self.task_queue.get(timeout=0.1)
            except Empty:
                if self._worker_stop_event.wait(0.1):
                    return
                if not self.task_queue.unfinished_tasks:
                    return
                continue
            except ShutDown:
                logger.info("Queue shut down")
                return

            task_finished = False
            try:
                # 处理单个章节，并在需要时通过 config 中的回调上报章节完成进度
                raise_if_cancelled(self.config)
                task.result = process_chapter(
                    self.chaoxing,
                    self.course,
                    task.point,
                    self.speed,
                    self.config,
                )
                # A blocking chapter may have returned after cancellation was
                # requested.  Check before scheduling retries or reporting it
                # as completed.
                raise_if_cancelled(self.config)

                match task.result:
                    case ChapterResult.SUCCESS:
                        logger.debug("章节任务完成")
                        self.task_queue.task_done()
                        task_finished = True
                        logger.debug("章节任务队列状态已更新")

                    case ChapterResult.NOT_OPEN:
                        # task.tries += 1
                        if self.config["notopen_action"] == "continue":
                            logger.warning("章节未开启，正在跳过")
                            self.task_queue.task_done()
                            task_finished = True
                            continue

                        if task.tries >= self.max_tries:
                            logger.error(
                                "章节未开启: {} 可能由于上一章节的章节检测未完成, 也可能由于该章节因为时效已关闭，"
                                "请手动检查完成并提交再重试。或者在配置中配置(自动跳过关闭章节/开启题库并启用提交)"
                            )
                            self.task_queue.task_done()
                            task_finished = True
                            continue

                        # self.wait_queue.put(task)
                        self.retry_queue.put(task)

                    case ChapterResult.ERROR:
                        task.tries += 1
                        logger.warning(
                            "章节任务失败，正在重试（第{}次，共{}次）",
                            task.tries,
                            self.max_tries,
                        )
                        if task.tries >= self.max_tries:
                            logger.error("章节任务达到最大重试次数")
                            self.failed_tasks.append(task)
                            self.task_queue.task_done()
                            task_finished = True
                            continue
                        self.retry_queue.put(task)

                    case _:
                        logger.error("章节任务状态无效（任务元数据已省略）")
                        self.failed_tasks.append(task)
                        self.task_queue.task_done()
                        task_finished = True
            except StudyCancelled:
                if not task_finished:
                    self.task_queue.task_done()
                return
            except BaseException as exc:
                # Do not strand queue accounting, and publish the error to
                # the owning course after all workers have shut down.  Raw
                # exception text is intentionally not logged here: the task
                # boundary sanitizes it before exposing failure details.
                if not task_finished:
                    self.task_queue.task_done()
                self._record_worker_error(exc)
                return

    def retry_thread(self):
        try:
            while not self._worker_stop_event.is_set():
                try:
                    raise_if_cancelled(self.config)
                except StudyCancelled:
                    return
                try:
                    task = self.retry_queue.get(timeout=0.1)
                except Empty:
                    if self._worker_stop_event.wait(0.1):
                        return
                    continue
                moved = False
                try:
                    raise_if_cancelled(self.config)
                    self.task_queue.put(task)
                    self.task_queue.task_done() # task_done is not called when a task failed and needs to be retried, so if is reput into the queue, the task num will increase by one and become more than the real task number
                    moved = True
                finally:
                    self.retry_queue.task_done()
                    if not moved:
                        # The retry item owns the original task_queue count;
                        # balance it when cancellation prevents requeueing.
                        self.task_queue.task_done()
                cancel_event = self.config.get("cancel_event")
                if cancel_event is not None:
                    if self._worker_stop_event.wait(1):
                        return
                    if cancel_event.is_set():
                        raise StudyCancelled()
                else:
                    if self._worker_stop_event.wait(1):
                        return
        except StudyCancelled:
            return
        except ShutDown:
            pass
        except BaseException as exc:
            self._record_worker_error(exc)


def process_chapter(chaoxing: Chaoxing, course:dict[str, Any], point:dict[str, Any], speed:float, config: dict[str, Any] | None = None) -> ChapterResult:
    """处理单个章节

    当所有任务点成功完成时，如果 config 中提供了 chapter_done_callback，
    则回调通知外部（如 Web 端）更新进度统计。
    """
    raise_if_cancelled(config)
    logger.info("开始处理章节任务")

    # 通知外部当前章节开始（用于前端显示当前正在学习的章节）
    if config is not None:
        start_cb = config.get("chapter_start_callback")
        if callable(start_cb):
            try:
                start_cb(course, point)
            except StudyCancelled:
                raise
            except Exception:
                logger.debug("章节监控回调失败（异常内容已省略）")
    raise_if_cancelled(config)
    if point.get("has_finished", False):
        logger.info("章节已完成所有任务点")
        # 已经在超星端标记为完成的章节，这里直接视为成功并同步监控器。
        _notify_callback(config, "chapter_done_callback", course, point)
        return ChapterResult.SUCCESS
    
    # 随机等待，避免请求过快
    chaoxing.rate_limiter.limit_rate(random_time=True,random_min=0, random_max=0.2)
    raise_if_cancelled(config)
    
    # 获取当前章节的所有任务点
    jobs, job_info = _normalise_jobs(chaoxing.get_job_list(course, point))
    raise_if_cancelled(config)
    job_info = job_info or {}
    _notify_callback(config, "job_list_callback", course, point, jobs, job_info)

    # 发现未开放章节, 根据配置处理
    if job_info.get("notOpen", False):
        _notify_callback(config, "chapter_status_callback", course, point, "not_open")
        return ChapterResult.NOT_OPEN

    # 已经默认处理空任务，此处不需要判断
    if not jobs:
        pass

    # TODO: 个别章节很恶心，多到5个点，可以并行处理，将来会让不同课程不同章节的所有任务点共享一个队列，从而实现全局并行
    job_results:list[StudyResult]=[]
    video_progress_callback = config.get("video_progress_callback") if config else None
    with ThreadPoolExecutor(max_workers=5) as executor:
        def process_one_job(job):
            # Each executor worker starts with a fresh context, so both task
            # logging and task-local OCR settings must be re-entered here.
            _notify_callback(config, "job_start_callback", course, point, job)
            try:
                result = _run_worker_with_context(
                    config,
                    process_job,
                    chaoxing,
                    course,
                    job,
                    job_info,
                    speed,
                    progress_callback=video_progress_callback,
                    config=config,
                )
            except StudyCancelled:
                raise
            except BaseException:
                _notify_callback(config, "job_done_callback", course, point, job, "failed")
                raise
            _notify_callback(config, "job_done_callback", course, point, job, result)
            return result

        # ThreadPoolExecutor workers do not inherit contextvars.  Capture a
        # fresh context for every submission so task_id/scoped secrets stay
        # isolated when two Web tasks run at the same time.
        futures = [
            executor.submit(contextvars.copy_context().run, process_one_job, job)
            for job in jobs
        ]
        for future in futures:
            raise_if_cancelled(config)
            job_results.append(future.result())
    
    for result in job_results:
        if result.is_failure():
            _notify_callback(config, "chapter_status_callback", course, point, "failed")
            return ChapterResult.ERROR

    # 所有任务点均成功，通知外部本章节已完成（用于前端进度统计）
    if config is not None:
        callback = config.get("chapter_done_callback")
        if callable(callback):
            try:
                callback(course, point)
            except StudyCancelled:
                raise
            except Exception:
                logger.debug("章节监控回调失败（异常内容已省略）")

    return ChapterResult.SUCCESS



def process_course(chaoxing: Chaoxing, course:dict[str, Any], config: dict):
    """处理单个课程"""
    raise_if_cancelled(config)
    logger.info("开始处理课程任务")
    
    # 获取当前课程的所有章节
    point_list = chaoxing.get_course_point(
        course["courseId"], course["clazzId"], course["cpi"]
    )
    raise_if_cancelled(config)
    points = _normalise_points(point_list)
    _notify_callback(
        config,
        "course_points_callback",
        course,
        points,
    )

    # 为了支持课程任务回滚, 采用下标方式遍历任务点

    _old_format_sizeof = tqdm.format_sizeof
    tqdm.format_sizeof = format_time
    tqdm.set_lock(RLock())

    try:
        tasks=[]

        for i, point in enumerate(points):
            raise_if_cancelled(config)
            task = ChapterTask(point=point, index=i)
            tasks.append(task)
        p = JobProcessor(chaoxing, course, tasks, config)
        p.run()
        raise_if_cancelled(config)
        if p.failed_tasks:
            _notify_callback(config, "course_failed_callback", course)
        else:
            _notify_callback(config, "course_done_callback", course)
    finally:
        tqdm.format_sizeof = _old_format_sizeof

    """
    while __point_index < len(point_list["points"]):
        point = point_list["points"][__point_index]
        logger.debug("当前章节索引已更新")
        
        result, auto_skip_notopen = process_chapter(
            chaoxing, course, point, RB, notopen_action, speed, auto_skip_notopen
        )
        
        if result == -1:  # 退出当前课程
            break
        elif result == 0:  # 重试前一章节
            __point_index -= 1  # 默认第一个任务总是开放的
        else:  # 继续下一章节
            __point_index += 1
    """



def filter_courses(all_course, course_list):
    """过滤要学习的课程"""
    if not course_list:
        # 手动输入要学习的课程ID列表
        print("*" * 10 + "课程列表" + "*" * 10)
        for course in all_course:
            print(f"ID: {course['courseId']} 课程名: {course['title']}")
        print("*" * 28)
        try:
            course_list = input(
                "请输入想要学习的课程列表,以逗号分隔,例: 2151141,189191,198198\n"
            ).split(",")
        except Exception as e:
            raise InputFormatError("输入格式错误") from e

    # 筛选需要学习的课程
    course_task = []
    course_ids = []
    for course in all_course:
        if course["courseId"] in course_list and course["courseId"] not in course_ids:
            course_task.append(course)
            course_ids.append(course["courseId"])
    
    # 如果没有指定课程，则学习所有课程
    if not course_task:
        course_task = all_course
    
    return course_task


def format_time(num, suffix='', divisor=''):
    total_time = round(num)
    sec = total_time % 60
    mins = (total_time % 3600) // 60
    hrs = total_time // 3600

    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{sec:02d}"

    return f"{mins:02d}:{sec:02d}"


def main():
    """主程序入口"""
    try:
        # 初始化配置
        common_config, tiku_config, notification_config = init_config()
        
        # 强制播放按照配置文件调节
        common_config["speed"] = min(2.0, max(1.0, common_config.get("speed", 1.0)))
        common_config["notopen_action"] = common_config.get("notopen_action", "retry")
        
        # 初始化超星实例
        chaoxing = init_chaoxing(common_config, tiku_config)
        
        # 设置外部通知
        notification = Notification()
        notification.config_set(notification_config)
        notification = notification.get_notification_from_config()
        notification.init_notification()
        
        # 检查当前登录状态
        _login_state = chaoxing.login(login_with_cookies=common_config.get("use_cookies", False))
        if not _login_state["status"]:
            raise LoginError(_login_state["msg"])
        
        # 获取所有的课程列表
        all_course = _normalise_courses(chaoxing.get_course_list())
        
        # 过滤要学习的课程
        course_task = filter_courses(all_course, common_config.get("course_list"))
        
        # 开始学习
        logger.info("课程列表过滤完毕")
        for course in course_task:
            process_course(chaoxing, course, common_config)
        
        logger.info("所有课程学习任务已完成")
        notification.send("chaoxing : 所有课程学习任务已完成")
        
    except SystemExit as e:
        if e.code != 0:
            logger.error("程序异常退出（返回码已省略）")
        sys.exit(e.code)
    except KeyboardInterrupt:
        logger.error("程序被用户手动中断")
    except BaseException:
        logger.error("程序异常退出（异常内容已省略）")
        try:
            notification.send("chaoxing : 程序异常退出（异常内容已省略）")
        except Exception:
            pass  # 如果通知发送失败，忽略异常
        raise


if __name__ == "__main__":
    main()
