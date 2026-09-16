# -*- coding: utf-8 -*-
import functools
from contextlib import nullcontext
import inspect
import json
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from hashlib import md5
from typing import Callable, Mapping, Optional, Literal

import requests
from loguru import logger
from requests import RequestException
from requests.adapters import HTTPAdapter
from tqdm import tqdm

from api.answer import *
from api.answer import question_cache_key
from api.answer_check import cut
from api.cipher import AESCipher
from api.config import GlobalConst as gc
from api.decode import (
    decode_course_list,
    decode_course_point,
    decode_course_card,
    decode_course_folder,
    decode_questions_info,
)
from api.exceptions import MaxRetryExceeded
from api.live_process import StudyCancelled
from api.work_grades import parse_work_grade
from api.work_audit import WorkAudit
from api.grade_feedback import reconcile_answer_cache
from api.option_parser import (
    answer_parts,
    label_from_token,
    normalize_choice_text,
    option_entries,
    option_lines as _option_lines,
)
from api.vision_ocr import (
    _capture_vision_ocr_context,
    vision_ocr_context,
)


class _CompletionMappingError(ValueError):
    """Raised when a completion answer cannot fit the actual form fields."""


def _check_platform_response(response, operation: str) -> None:
    if response.status_code != 200:
        raise RuntimeError(f"{operation}失败（HTTP {response.status_code}），请重试")
    final_url = str(getattr(response, "url", "") or "")
    if "passport2.chaoxing.com" in final_url:
        raise RuntimeError("登录已失效，请重新验证账户后重试")


def _job_response_succeeded(response, *, required_field=None) -> bool:
    """HTTP success alone does not mean Chaoxing accepted a task report."""
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return False
    if not isinstance(payload, Mapping):
        return False
    flags = (
        [payload.get(required_field)] if required_field else
        [payload[key] for key in ("status", "success", "isPassed") if key in payload]
    )
    return bool(flags) and all(
        value is True or (not isinstance(value, bool) and value == 1)
        or (isinstance(value, str) and value.strip().lower() in {"true", "1", "success"})
        for value in flags
    )


_CACHE_EXPECTED_UNSET = object()


def _completion_field_modes(answer_field: Mapping, question_id: str):
    """Return aggregate, one-based, and underscore-indexed actual fields."""

    answer_key = f"answer{question_id}"
    aggregate = [answer_key] if answer_key in answer_field else []
    one_based = []
    zero_based = []
    for key in answer_field:
        if not isinstance(key, str):
            continue
        match = re.fullmatch(rf"{re.escape(answer_key)}(\d+)", key)
        if match:
            one_based.append((int(match.group(1)), key))
            continue
        match = re.fullmatch(rf"{re.escape(answer_key)}_(\d+)", key)
        if match:
            zero_based.append((int(match.group(1)), key))
    one_based.sort(key=lambda item: item[0])
    zero_based.sort(key=lambda item: item[0])
    return aggregate, [key for _, key in one_based], [key for _, key in zero_based]


def _completion_parts(result) -> list[str]:
    """Normalize completion results while retaining all newline slots."""

    if isinstance(result, (list, tuple)):
        return ["" if item is None else str(item).strip() for item in result]
    if isinstance(result, str):
        normalized = result.replace("\r\n", "\n").replace("\r", "\n")
        return normalized.split("\n") if "\n" in normalized else [result.strip()]
    return [str(result).strip()]


_CHOICE_WORD_RE = re.compile(r"(?<![A-Za-z])([A-Za-z]+)(?![A-Za-z])")
_NATURAL_CHOICE_FRAGMENT_RES = (
    re.compile(
        r"\boptions?\s+(?P<fragment>[A-Za-z]+(?:\s*(?:[,，、]|and|or)\s*"
        r"[A-Za-z]+)*)\s+(?:is|are)\s+(?:correct|right)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:correct\s+)?answers?\s*(?:is|are|:|：|=)\s*"
        r"(?P<fragment>[^.!?。！？;；\n]*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"correct\s+options?\s*(?:is|are|:|：|=)\s*"
        r"(?P<fragment>[^.!?。！？;；\n]*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:答案|答复|正确答案|正确选项)\s*(?:是|为|:|：|=)\s*"
        r"(?P<fragment>[^.!?。！？;；\n]*)",
        re.IGNORECASE,
    ),
)


def _natural_label_gap_is_allowed(gap: str) -> bool:
    """Whether text between two natural-language labels is just a joiner."""

    compact = re.sub(r"[\s,，、|/&+;；:：()（）\[\]【】.．-]", "", gap)
    return compact.casefold() in {"", "and", "or", "和", "或", "及", "与", "以及", "或者"}


def _unwrap_choice_result(result):
    """Extract an Answer/answer value from provider-shaped JSON values."""

    candidate = result
    if isinstance(candidate, str):
        text = candidate.strip()
        if text.startswith("```") and text.endswith("```"):
            text = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                text,
                flags=re.IGNORECASE,
            ).strip()
        if (
            (text.startswith("{") and text.endswith("}"))
            or (text.startswith("[") and text.endswith("]"))
        ):
            try:
                candidate = json.loads(text)
            except (TypeError, ValueError):
                candidate = result
    if isinstance(candidate, Mapping):
        for key, value in candidate.items():
            if str(key).strip().casefold() in {"answer", "answers"}:
                return value
    return candidate


def _natural_choice_labels(value, valid_labels: set[str], *, multiple: bool):
    """Extract explicit labels from a natural-language answer wrapper.

    A label is considered only when the surrounding text contains a stable
    answer/option marker.  This keeps words such as ``Babbage`` from turning
    their first letter into an option label.
    """

    text = str(value or "").strip()
    if not text:
        return []

    labels = []
    for pattern in _NATURAL_CHOICE_FRAGMENT_RES:
        for fragment_match in pattern.finditer(text):
            fragment = fragment_match.group("fragment")
            previous_end = 0
            fragment_labels = []
            for token_match in _CHOICE_WORD_RE.finditer(fragment):
                if not _natural_label_gap_is_allowed(
                    fragment[previous_end : token_match.start()]
                ):
                    break
                token = token_match.group(1)
                label = label_from_token(token, valid_labels)
                if label:
                    fragment_labels.append(label)
                    previous_end = token_match.end()
                    continue
                # Words such as ``and``/``or`` may join two labels, but any
                # other non-label token ends the explicit answer fragment.
                if not _natural_label_gap_is_allowed(
                    fragment[previous_end : token_match.end()]
                ):
                    break
                previous_end = token_match.end()
            labels.extend(fragment_labels)

    if not labels:
        return []

    # For a single-choice response, one explicit label is required.  For a
    # multiple-choice response, repeated labels are harmless but the answer
    # order remains the page's option order through ordered_unique().
    if not multiple and len(set(labels)) != 1:
        return []
    return labels


def _resolve_choice_answer(
    result,
    options,
    *,
    multiple: bool,
    strict_labels_only: bool = False,
) -> str:
    """Map strict labels or complete option text to the form's labels.

    In particular, do not scan every alphabetic character in a normal word:
    providers such as TikuLike return option text rather than option letters.
    """

    entries = option_entries(options)
    valid_labels = {entry.label for entry in entries if entry.label}
    if not entries or not valid_labels:
        return ""

    result = _unwrap_choice_result(result)
    raw_text = str(result or "").strip()
    parts = answer_parts(result)
    nonempty_parts = [part for part in parts if str(part).strip()]
    is_collection = isinstance(result, (list, tuple, set, frozenset))

    def ordered_unique(labels) -> str:
        wanted = set(labels)
        return "".join(entry.label for entry in entries if entry.label in wanted)

    def text_candidates(value) -> list[str]:
        normalized = normalize_choice_text(value)
        if not normalized:
            return []
        return [
            entry.label
            for entry in entries
            if entry.normalized_text == normalized
        ]

    # A complete option body is more authoritative than a natural-language
    # wrapper that happens to contain a legal-looking label.  Reject duplicate
    # display bodies here instead of allowing the wrapper fallback to choose
    # one of them arbitrarily.
    if not strict_labels_only and not is_collection:
        whole_matches = text_candidates(raw_text)
        if len(whole_matches) == 1:
            return whole_matches[0]
        if len(whole_matches) > 1:
            return ""

    # A single value can be either a strict label or complete option text.
    # Multiple requested values are never silently truncated for single-choice
    # questions.
    if len(nonempty_parts) == 1:
        label = label_from_token(nonempty_parts[0], valid_labels)
        if label:
            return label
        part_matches = (
            [] if strict_labels_only else text_candidates(nonempty_parts[0])
        )
        if len(part_matches) == 1:
            return part_matches[0]
        if len(part_matches) > 1:
            return ""
        if not strict_labels_only:
            natural_labels = _natural_choice_labels(
                nonempty_parts[0], valid_labels, multiple=multiple
            )
            if natural_labels:
                return ordered_unique(natural_labels)
    elif multiple and nonempty_parts:
        label_parts = [
            label_from_token(part, valid_labels) for part in nonempty_parts
        ]
        if all(label_parts):
            return ordered_unique(label_parts)
    elif not multiple and nonempty_parts:
        # A multi-token sequence made entirely of labels cannot represent a
        # single answer unless every token repeats the same legal label.
        label_parts = [
            label_from_token(part, valid_labels) for part in nonempty_parts
        ]
        if all(label_parts):
            return label_parts[0] if len(set(label_parts)) == 1 else ""

    # Prefer complete normalized text before splitting ordinary phrases into
    # segments (for example, an option whose text is "New York").
    if not strict_labels_only and not is_collection:
        natural_labels = _natural_choice_labels(
            raw_text, valid_labels, multiple=multiple
        )
        if natural_labels:
            return ordered_unique(natural_labels)

    # Compact multi-choice labels are accepted only when the unseparated
    # answer has exactly one segmentation under the current option labels.
    # Exact multi-letter labels such as AA were handled above by
    # label_from_token and therefore take precedence.
    if (
        multiple
        and not is_collection
        and len(raw_text) >= 2
        and all(
            ("A" <= char <= "Z") or ("a" <= char <= "z")
            for char in raw_text
        )
    ):
        compact = raw_text.upper()
        compact_labels = sorted(
            (label for label in valid_labels if label),
            key=lambda label: (-len(label), label),
        )
        # Dynamic programming keeps one path per suffix and caps the count at
        # two.  We only need to distinguish zero, one, and ambiguous; keeping
        # all exponential paths would add risk without adding information.
        path_counts = [0] * (len(compact) + 1)
        unique_paths: list[tuple[str, ...] | None] = [None] * (len(compact) + 1)
        path_counts[-1] = 1
        unique_paths[-1] = ()
        for index in range(len(compact) - 1, -1, -1):
            for label in compact_labels:
                if not compact.startswith(label, index):
                    continue
                suffix_index = index + len(label)
                suffix_count = path_counts[suffix_index]
                if not suffix_count:
                    continue
                if path_counts[index] == 0:
                    unique_paths[index] = (label,) + (unique_paths[suffix_index] or ())
                path_counts[index] = min(2, path_counts[index] + suffix_count)
                if path_counts[index] == 2:
                    break
        if path_counts[0] == 1:
            return ordered_unique(unique_paths[0] or ())
        return ""

    if not nonempty_parts:
        return ""

    matched = []
    matched_texts = set()
    for part in nonempty_parts:
        label = label_from_token(part, valid_labels)
        if label:
            candidates = [label]
        else:
            part_matches = (
                [] if strict_labels_only else text_candidates(part)
            )
            if len(part_matches) > 1:
                return ""
            natural_labels = (
                _natural_choice_labels(part, valid_labels, multiple=False)
                if not strict_labels_only and not part_matches
                else []
            )
            candidates = (
                part_matches
                if part_matches
                else natural_labels
                if natural_labels
                else []
            )
        # Every non-empty segment must identify exactly one option.  This
        # prevents a useful-looking partial result from hiding bad data.
        if len(candidates) != 1:
            return ""
        if not label:
            normalized = normalize_choice_text(part)
            if normalized in matched_texts:
                return ""
            matched_texts.add(normalized)
        matched.append(candidates[0])

    if not multiple and len(matched) != 1:
        return ""
    return ordered_unique(matched)


def get_timestamp():
    return str(int(time.time() * 1000))


def _raise_if_cancelled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise StudyCancelled()


def _wait_or_cancel(cancel_event, seconds: float) -> None:
    _raise_if_cancelled(cancel_event)
    if cancel_event is not None:
        if cancel_event.wait(seconds):
            raise StudyCancelled()
    else:
        time.sleep(seconds)


def build_session(initial_cookies: Mapping[str, str] | None = None) -> requests.Session:
    """Build a fully configured HTTP session for one account."""

    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=10))
    session.mount("http://", HTTPAdapter(max_retries=10))
    session.request = functools.partial(session.request, timeout=5)
    # For debug purposes
    # session.verify=False
    session.headers.clear()
    session.headers.update(gc.HEADERS)
    if initial_cookies:
        session.cookies.update(dict(initial_cookies))
    return session


class Account:
    username = None
    password = None
    last_login = None
    isSuccess = None

    def __init__(self, _username, _password):
        self.username = _username
        self.password = _password


class RateLimiter:
    def __init__(self, call_interval):
        self.last_call = time.time()
        self.lock = threading.Lock()
        self.call_interval = call_interval

    def limit_rate(self, random_time=False, random_min=0.0, random_max=1.0):
        with self.lock:
            if random_time:
                wait_time = random.uniform(random_min, random_max)
                time.sleep(wait_time)
            now = time.time()
            time_elapsed = now - self.last_call
            if time_elapsed <= self.call_interval:
                time.sleep(self.call_interval - time_elapsed)
                self.last_call = time.time()
                return

            self.last_call = now
            return


class StudyResult(Enum):
    SUCCESS = 0
    FORBIDDEN = 1  # 403
    ERROR = 2
    TIMEOUT = 3
    SKIPPED = 4  # Saved/disabled/expired work still needs manual completion.
    BLOCKED = 5  # A platform condition must change before retrying.

    def is_success(self):
        return self == StudyResult.SUCCESS
    def is_failure(self):
        return self not in {StudyResult.SUCCESS, StudyResult.SKIPPED}

class Chaoxing:
    def __init__(
        self,
        account: Account = None,
        tiku: Tiku = None,
        *,
        session: requests.Session | None = None,
        cookie_update_callback: Callable[[dict[str, str]], None] | None = None,
        **kwargs,
    ):
        self.account = account
        self.cipher = AESCipher()
        self.tiku = tiku
        self.session = session if session is not None else build_session()
        if self.tiku is not None:
            # Answer-time OCR runs after the question parser and therefore
            # needs the same account-owned session as the Chaoxing client.
            self.tiku.session = self.session
        self.cookie_update_callback = cookie_update_callback
        self.kwargs = kwargs
        self.rollback_times = 0
        self.rate_limiter = RateLimiter(0.5) # 其他接口速率限制比较松
        self.video_log_limiter = RateLimiter(2) # 上报进度极其容易卡验证码，限制2s一次

    def login(self, login_with_cookies=False):
        if login_with_cookies:
            logger.info("Logging in with cookies")
            if not self._validate_cookie_session():
                logger.warning("Cookie 登录校验失败，尝试使用账号密码重新登录")
                if self.account and self.account.username and self.account.password:
                    return self.login(login_with_cookies=False)
                return {"status": False, "msg": "cookies 已失效，请更新 cookies 或提供账号密码"}
            self._notify_cookie_update()
            logger.info("登录成功...")
            return {"status": True, "msg": "登录成功"}

        _url = "https://passport2.chaoxing.com/fanyalogin"
        _data = {
            "fid": "-1",
            "uname": self.cipher.encrypt(self.account.username),
            "password": self.cipher.encrypt(self.account.password),
            "refer": "https%3A%2F%2Fi.chaoxing.com",
            "t": True,
            "forbidotherlogin": 0,
            "validate": "",
            "doubleFactorLogin": 0,
            "independentId": 0,
        }
        logger.trace("正在尝试登录...")
        resp = self.session.post(_url, headers=gc.HEADERS, data=_data)
        if resp and resp.json()["status"] == True:
            self._notify_cookie_update()
            logger.info("登录成功...")
            return {"status": True, "msg": "登录成功"}
        else:
            return {"status": False, "msg": str(resp.json()["msg2"])}

    def _notify_cookie_update(self) -> None:
        if callable(self.cookie_update_callback):
            self.cookie_update_callback(self.session.cookies.get_dict())

    def _validate_cookie_session(self) -> bool:
        session = self.session
        if not session.cookies.get("_uid"):
            return False

        try:
            resp = session.post(
                "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata",
                data={"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0},
                timeout=8,
            )
        except RequestException as exc:
            logger.debug("Cookie validation request failed (exception omitted)")
            return False

        if resp.status_code != 200:
            return False

        if "passport2.chaoxing.com" in resp.text or "login" in resp.text.lower():
            return False

        return True

    def get_fid(self):
        return self.session.cookies.get("fid")

    def get_uid(self):
        s = self.session
        if "_uid" in s.cookies:
            return s.cookies["_uid"]
        if "UID" in s.cookies:
            return s.cookies["UID"]
        raise ValueError("Cannot get uid !")

    def get_course_list(self):
        _session = self.session
        _url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata"
        _data = {"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0}
        logger.trace("正在读取所有的课程列表...")

        # 接口突然抽风, 增加headers
        # 有可能只是referer的问题
        _headers = {
            "Referer": "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction?moocDomain=https://mooc1-1.chaoxing.com/mooc-ans",
        }
        _resp = _session.post(_url, headers=_headers, data=_data)
        _check_platform_response(_resp, "读取课程列表")
        # logger.trace(f"原始课程列表内容:\n{_resp.text}")
        logger.info("课程列表读取完毕...")
        course_list = decode_course_list(_resp.text)

        _interaction_url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction"
        _interaction_resp = _session.get(_interaction_url)
        _check_platform_response(_interaction_resp, "读取课程文件夹")
        course_folder = decode_course_folder(_interaction_resp.text)
        for folder in course_folder:
            _data = {
                "courseType": 1,
                "courseFolderId": folder["id"],
                "query": "",
                "superstarClass": 0,
            }
            _resp = _session.post(_url, data=_data)
            _check_platform_response(_resp, "读取文件夹课程")
            course_list += decode_course_list(_resp.text)
        return course_list

    def get_course_point(self, _courseid, _clazzid, _cpi):
        _session = self.session
        _url = f"https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse?courseid={_courseid}&clazzid={_clazzid}&cpi={_cpi}&ut=s"
        logger.trace("开始读取课程所有章节...")
        _resp = _session.get(_url)
        _check_platform_response(_resp, "读取课程章节")
        # logger.trace(f"原始章节列表内容:\n{_resp.text}")
        logger.info("课程章节读取成功...")
        return decode_course_point(_resp.text)

    def get_job_list(self, course: dict, point: dict) -> tuple[list[dict], dict]:
        _session = self.session
        self.rate_limiter.limit_rate()
        job_list = []
        job_info = {}
        cards_params = {
            "clazzid": course["clazzId"],
            "courseid": course["courseId"],
            "knowledgeid": point["id"],
            "ut": "s",
            "cpi": course["cpi"],
            "v": "2025-0424-1038-3",
            "mooc2": 1
        }

        # 学习界面任务卡片数, 很少有3个的, 但是对于章节解锁任务点少一个都不行, 可以从API /mooc-ans/mycourse/studentstudyAjax获取值, 或者干脆直接加, 但二者都会造成额外的请求
        for _possible_num in "0123456":

            logger.trace("开始读取章节所有任务点...")

            cards_params.update({"num": _possible_num})
            _resp = _session.get("https://mooc1.chaoxing.com/mooc-ans/knowledge/cards", params=cards_params)
            _check_platform_response(_resp, "读取章节任务卡")

            _job_list, _job_info = decode_course_card(_resp.text)
            if _job_info.get("notOpen", False):
                # 直接返回, 节省一次请求
                logger.info("该章节未开放")
                return [], _job_info

            job_list += _job_list
            job_info.update(_job_info)

        if not job_list:
            if self.study_emptypage(course, point) is not StudyResult.SUCCESS:
                raise RuntimeError("空页面任务处理失败，请重试")
        # logger.trace(f"原始任务点列表内容:\n{_resp.text}")
        logger.info("章节任务点读取成功...")

        return job_list, job_info

    def get_enc(self, clazzId, jobid, objectId, playingTime, duration, userid):
        return md5(
            f"[{clazzId}][{userid}][{jobid}][{objectId}][{playingTime * 1000}][d_yHJ!$pdA~5][{duration * 1000}][0_{duration}]".encode()
        ).hexdigest()

    def video_progress_log(
            self,
            _session,
            _course,
            _job,
            _job_info,
            _dtoken,
            _duration,
            _playingTime,
            _type: str = "Video",
            headers: Optional[dict] = None,
    ) -> tuple[bool, int]:

        if headers is None:
            logger.warning("null headers")
            headers = gc.VIDEO_HEADERS

        self.video_log_limiter.limit_rate(random_time=True, random_max=2)

        if "courseId" in _job["otherinfo"]:
            # ``otherinfo`` carries session/job tokens in normal responses;
            # never dump it while diagnosing a malformed job.
            logger.error("任务元数据格式异常: otherinfo 包含 courseId")
            raise RuntimeError("this is not possible")

        enc = self.get_enc(_course["clazzId"], _job["jobid"], _job["objectid"], _playingTime, _duration, self.get_uid())
        params = {
            "clazzId": _course["clazzId"],
            "playingTime": _playingTime,
            "duration": _duration,
            "clipTime": f"0_{_duration}",
            "objectId": _job["objectid"],
            "otherInfo": _job["otherinfo"],
            "courseId": _course["courseId"],
            "jobid": _job["jobid"],
            "userid": self.get_uid(),
            "isdrag": "3",
            "view": "pc",
            "enc": enc,
            "dtype": _type
        }

        _url = (
            f"https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/"
            f"{_course['cpi']}/"
            f"{_dtoken}"
        )


        face_capture_enc = _job["videoFaceCaptureEnc"]
        att_duration = _job["attDuration"]
        att_duration_enc = _job["attDurationEnc"]

        if face_capture_enc:
            params["videoFaceCaptureEnc"] = face_capture_enc
        if att_duration:
            params["attDuration"] = att_duration
        if att_duration_enc:
            params["attDurationEnc"] = att_duration_enc

        rt = _job['rt']
        if not rt:
            rt_search = re.search(r"-rt_([1d])", _job['otherinfo'])
            if rt_search:
                rt_char = rt_search.group(1)
                rt = "0.9" if rt_char == "d" else "1"
                logger.trace(f"Got rt from otherinfo: {rt}")

        if rt:
            logger.trace(f"Got rt: {rt}")
            params.update({"rt": rt,
                           "_t": get_timestamp()})
            resp = _session.get(_url, params=params, headers=headers)
        else:
            logger.warning("Failed to get rt")
            for rt in [0.9, 1]:
                params.update({"rt": rt,
                               "_t": get_timestamp()})
                resp = _session.get(_url, params=params, headers=headers)
                if resp.status_code == 200:
                    return _job_response_succeeded(resp, required_field="isPassed"), 200
                #elif resp.ok:
                #    # TODO: 处理验证码
                #    pass
                elif resp.status_code == 403:
                    logger.warning("出现403报错, 正常尝试切换rt")

                else:
                    logger.warning("未知错误 jobid={}, status_code={}",
                                   _job.get("jobid"),
                                   resp.status_code,
                    )
                    break

        if resp.status_code == 200:
            return _job_response_succeeded(resp, required_field="isPassed"), 200

        elif resp.status_code == 403:
            logger.debug("视频进度上报返回403, jobid={}", _job.get("jobid"))

            # 若出现两个rt参数都返回403的情况, 则跳过当前任务
            logger.error("出现403报错, 尝试修复无效, 正在跳过当前任务点...")
            return False, 403

        logger.error(f"未知错误: {resp.status_code}")
        return False, resp.status_code


    def _refresh_video_status(self, session: requests.Session, job: dict, _type: Literal["Video", "Audio"]) -> Optional[dict]:
        self.rate_limiter.limit_rate(random_time=True, random_max=0.2)
        headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS
        info_url = (
            f"https://mooc1.chaoxing.com/ananas/status/{job['objectid']}?"
            f"k={self.get_fid()}&flag=normal"
        )
        try:
            resp = session.get(info_url, timeout=8, headers=headers)
        except RequestException:
            logger.debug("刷新视频状态失败（异常内容已省略）")
            return None

        if resp.status_code != 200:
            logger.debug("刷新视频状态返回码异常: {}", resp.status_code)
            return None

        try:
            data = resp.json()
        except ValueError:
            logger.debug("解析视频状态响应失败（响应内容已省略）")
            return None

        if data.get("status") == "success":
            return data

        return None

    def _recover_after_forbidden(self, session: requests.Session, job: dict, _type: Literal["Video", "Audio"]):
        refreshed = self._refresh_video_status(session, job, _type)
        if refreshed:
            return refreshed

        # FIXME: Temporarily disabled for multithreading support
        if False and self.account and self.account.username and self.account.password:
            login_result = self.login(login_with_cookies=False)
            if login_result.get("status"):
                return self._refresh_video_status(session, job, _type)
            logger.warning("账号密码登录失败")

        return None


    def study_video(
        self,
        _course,
        _job,
        _job_info,
        _speed: float = 1.0,
        _type: Literal["Video", "Audio"] = "Video",
        progress_callback=None,
        cancel_event=None,
    ) -> StudyResult:
        _session = self.session
        _raise_if_cancelled(cancel_event)

        headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS
        _info_url = f"https://mooc1.chaoxing.com/ananas/status/{_job['objectid']}?k={self.get_fid()}&flag=normal"
        _video_info = _session.get(_info_url, headers=headers).json()

        if _video_info["status"] != "success":
            logger.error("视频状态异常（服务端状态已省略）")
            return StudyResult.ERROR

        _dtoken = _video_info["dtoken"]

        _crc = _video_info["crc"]
        _key = _video_info["key"]

        # Time in the real world: last_iter, gc.THRESHOLD
        # Time in the video (can be scaled with the speed factor): duration, play_time, last_log_time, wait_time

        duration = int(_video_info["duration"])
        play_time = int(_job["playTime"]) // 1000
        last_log_time = 0
        last_iter = time.time()
        wait_time = int(random.uniform(30, 90))

        logger.info(
            "开始视频任务（时长={}s，已进行={}s；任务标题已省略）",
            duration,
            play_time,
        )

        # 首次上报进度
        if callable(progress_callback):
            try:
                progress_callback(_course, _job, float(play_time), float(duration))
            except StudyCancelled:
                raise
            except Exception:
                logger.debug("视频进度回调执行失败(初始，异常内容已省略)")

        pbar = tqdm(total=duration, initial=play_time, desc="视频任务",
                    unit_scale=True, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}')

        try:
            forbidden_retry = 0
            max_forbidden_retry = 2
            final_confirmation_attempts = 0
            max_final_confirmation_attempts = 5

            passed, state = self.video_progress_log(_session, _course, _job, _job_info, _dtoken, duration, play_time, _type,headers=headers)
            _raise_if_cancelled(cancel_event)
            if not passed:
                passed, state = self.video_progress_log(_session, _course, _job, _job_info, _dtoken, duration, duration, _type, headers=headers)
                _raise_if_cancelled(cancel_event)

            if passed:
                logger.info("视频任务瞬间完成（任务标题已省略）")
                return StudyResult.SUCCESS

            while not passed:
                _raise_if_cancelled(cancel_event)
                # Sometimes the last request needs to be sent several times to complete the task
                if play_time - last_log_time >= wait_time or play_time == duration:

                    passed, state = self.video_progress_log(_session, _course, _job, _job_info, _dtoken, duration,
                                                            int(play_time), _type, headers=headers)

                    if passed:
                        break

                    if state == 403:
                        if forbidden_retry >= max_forbidden_retry:
                            logger.warning("403重试失败, 跳过当前任务")
                            return StudyResult.FORBIDDEN
                        forbidden_retry += 1
                        logger.warning(
                            "出现403报错, 正在尝试刷新会话状态 (第{}次)",
                            forbidden_retry,
                        )
                        _wait_or_cancel(cancel_event, random.uniform(2, 4))
                        refreshed_meta = self._recover_after_forbidden(_session, _job, _type)
                        if refreshed_meta:
                            # FIXME: Maybe it should be considered an error if those keys aren't present in the refreshed meta, so we perhaps shouldn't use get()
                            _dtoken = refreshed_meta.get("dtoken", _dtoken)
                            _duration = refreshed_meta.get("duration", duration)
                            play_time = refreshed_meta.get("playTime", play_time)

                            logger.debug(
                                "视频元数据已刷新（时长={}，进度={}；令牌已省略）",
                                _duration,
                                play_time,
                            )
                            continue

                    elif not passed and state != 200:
                        return StudyResult.ERROR

                    if play_time >= duration and state == 200:
                        final_confirmation_attempts += 1
                        if final_confirmation_attempts >= max_final_confirmation_attempts:
                            logger.warning("视频已到结尾，但平台仍未确认完成，将交由章节重试")
                            return StudyResult.TIMEOUT
                        # Give the platform time to acknowledge the final
                        # report, with a finite budget and cooperative stop.
                        _wait_or_cancel(cancel_event, 10)

                    wait_time = int(random.uniform(30, 90))
                    last_log_time = play_time

                dt = (time.time() - last_iter) * _speed # Since uploading the progress takes time, we assume that the video is still playing in the background, so manually calculate the time elapsed is required
                last_iter = time.time()
                play_time = min(duration, play_time+dt)

                pbar.n = int(play_time)
                pbar.refresh()

                # 实时上报进度给外部（如 Web 前端）
                if callable(progress_callback):
                    try:
                        progress_callback(_course, _job, float(play_time), float(duration))
                    except StudyCancelled:
                        raise
                    except Exception:
                        logger.debug("视频进度回调执行失败（异常内容已省略）")

                _wait_or_cancel(cancel_event, gc.THRESHOLD)

            logger.info("视频任务完成（任务标题已省略）")
            return StudyResult.SUCCESS
        finally:
            pbar.close()

    def study_document(self, _course, _job) -> StudyResult:
        """
        Study a document in Chaoxing platform.

        This method makes a GET request to fetch document information for a given course and job.

        Args:
            _course (dict): Dictionary containing course information with keys:
                - courseId: ID of the course
                - clazzId: ID of the class
            _job (dict): Dictionary containing job information with keys:
                - jobid: ID of the job
                - otherinfo: String containing node information
                - jtoken: Authentication token for the job

        Returns:
            requests.Response: Response object from the GET request

        Note:
            This method requires the following helper functions:
            - init_session(): To initialize a new session
            - get_timestamp(): To get current timestamp
            - re module for regular expression matching
        """
        _session = self.session
        _url = f"https://mooc1.chaoxing.com/ananas/job/document?jobid={_job['jobid']}&knowledgeid={re.findall(r'nodeId_(.*?)-', _job['otherinfo'])[0]}&courseid={_course['courseId']}&clazzid={_course['clazzId']}&jtoken={_job['jtoken']}&_dc={get_timestamp()}"
        _resp = _session.get(_url)
        return StudyResult.SUCCESS if _job_response_succeeded(_resp) else StudyResult.ERROR


    def study_work(self, _course, _job, _job_info) -> StudyResult:
        # FIXME: 这一块可以单独搞一个类出来了，方法里面又套方法，每一次调用都会创建新的方法，十分浪费
        if self.tiku is None or self.tiku.DISABLE:
            return StudyResult.SKIPPED
        audit = WorkAudit(task_id=self.kwargs.get("task_id"),
                          course_id=_course.get("courseId"),
                          chapter_id=_job_info.get("knowledgeid"), job_id=_job.get("jobid"))
        _ORIGIN_HTML_CONTENT = ""  # 用于配合输出网页源码, 帮助修复#391错误

        def multi_cut(answer: str):
            """
            将多选题答案字符串按特定字符进行切割, 并返回切割后的答案列表

            参数:
            answer(str): 多选题答案字符串.

            返回:
            list[str]: 切割后的答案列表,如果无法切割, 则返回默认的选项列表None

            注意:
            如果无法从网页中提取题目信息,将记录警告日志并返回None
            """
            # cut_char = [',','，','|','\n','\r','\t','#','*','-','_','+','@','~','/','\\','.','&',' ']    # 多选答案切割符
            # ',' 在常规被正确划分的, 选项中出现, 导致 multi_cut 无法正确划分选项 #391
            # IndexError: Cannot choose from an empty sequence #391
            # 同时为了避免没有考虑到的 case, 应该先按照 '\n' 匹配, 匹配不到再按照其他字符匹配
            cut_char = [
                "\n",
                ",",
                "，",
                "|",
                "\r",
                "\t",
                "#",
                "*",
                "-",
                "_",
                "+",
                "@",
                "~",
                "/",
                "\\",
                ".",
                "&",
                " ",
                "、",
            ]  # 多选答案切割符
            res = cut(answer)
            if res is None:
                logger.warning(
                    "未能从网页中提取题目选项信息 (响应内容已省略)"
                )  # 尝试输出网页内容和选项信息
                logger.warning("未能正确提取题目选项信息! 请反馈并提供以上信息")
                return None
            else:
                return res

        # FIXME: Use tenacity for retrying
        def with_retry(max_retries=3, delay=1):
            def decorator(func):
                def wrapper(*args, **kwargs):
                    retries = 0
                    while retries < max_retries:
                        try:
                            _resp = func(*args, **kwargs)

                            # 未创建完成该测验则不进行答题，目前遇到的情况是未创建完成等同于没题目
                            if '教师未创建完成该测验' in _resp.text:
                                raise PermissionError("教师未创建完成该测验")

                            _check_platform_response(_resp, "读取测验")
                            grade = parse_work_grade(_resp.text)
                            audit.grade(grade)
                            if grade.get('total_questions', 0) > 0 and (
                                grade.get('submitted') is True
                                or grade['status'] in {'graded', 'score_only'}
                            ):
                                return _resp, {'_submitted_grade': grade}
                            questions = decode_questions_info(_resp.text, session=self.session)

                            if _resp.status_code == 200 and questions.get("questions"):
                                return (_resp, questions)

                            logger.warning(
                                f"无效响应 (Code: {getattr(_resp, 'status_code', 'Unknown')}), 重试中... ({retries + 1}/{max_retries})")

                        except requests.exceptions.RequestException:
                            logger.warning(
                                f"请求失败，重试中... ({retries + 1}/{max_retries})"
                            )
                        retries += 1
                        _wait_or_cancel(self.kwargs.get("cancel_event"), delay * (2 ** retries))
                    raise MaxRetryExceeded(f"超过最大重试次数 ({max_retries})")

                return wrapper

            return decorator

        # 学习通这里根据参数差异能重定向至两个不同接口, 需要定向至https://mooc1.chaoxing.com/mooc-ans/workHandle/handle
        _session = self.session

        _url = "https://mooc1.chaoxing.com/mooc-ans/api/work"

        def load_work_page(action="read_page"):
            return audit.request(action, _session.get,
                _url,
                params={
                    "api": "1",
                    "workId": _job["jobid"].replace("work-", ""),
                    "jobid": _job["jobid"],
                    "originJobId": _job["jobid"],
                    "needRedirect": "true",
                    "skipHeader": "true",
                    "knowledgeid": str(_job_info["knowledgeid"]),
                    "ktoken": _job_info["ktoken"],
                    "cpi": _job_info["cpi"],
                    "ut": "s",
                    "clazzId": _course["clazzId"],
                    "type": "",
                    "enc": _job["enc"],
                    "mooc2": "1",
                    "courseid": _course["courseId"],
                }
            )

        fetch_response = with_retry(max_retries=3, delay=1)(load_work_page)
        final_resp = {}
        questions = {}

        try:
            final_resp, questions = fetch_response()
        except StudyCancelled:
            raise
        except Exception as exc:
            audit.event("work_read_failed", error_type=type(exc).__name__)
            logger.error("请求题目失败")
            return StudyResult.ERROR

        _ORIGIN_HTML_CONTENT = final_resp.text  # 用于配合输出网页源码, 帮助修复#391错误

        if '_submitted_grade' in questions:
            # The first POST may have succeeded before a timeout/restart.
            # A submitted or teacher-pending form is not a new attempt.
            _job['answer_result'] = questions['_submitted_grade']
            audit.event("existing_submission_preserved")
            logger.info("平台已接收该测验，保留现有作答及判分")
            return StudyResult.SUCCESS

        missing_choice_options = sum(
            1
            for question in questions.get("questions", [])
            if question.get("type") in {"single", "multiple"}
            and not _option_lines(question.get("options"))
        )
        if missing_choice_options:
            logger.warning(
                "检测到单选/多选题没有有效选项，已停止答题并跳过提交 "
                f"(count={missing_choice_options})"
            )
            return StudyResult.ERROR

        # 搜题
        total_questions = len(questions["questions"])
        found_answers = 0
        has_partial_completion = False
        has_uncovered_answers = False
        cancel_event = self.kwargs.get("cancel_event")

        def _mark_uncovered(q, reason_code: str) -> None:
            nonlocal has_uncovered_answers
            has_uncovered_answers = True
            logger.warning(
                "题目答案未覆盖（题目ID={}，题型={}，原因码={}）",
                q.get("id", ""),
                q.get("type", "unknown"),
                reason_code,
            )

        def _update_choice_cache(
            q, answer: Optional[str], *, expected=_CACHE_EXPECTED_UNSET
        ) -> None:
            """Keep only canonical AI/SiliconFlow answers in the cache."""

            if not isinstance(self.tiku, (AI, SiliconFlow)):
                return
            cache = getattr(self.tiku, "_cache", None)
            question = question_cache_key(q)
            if cache is None or not question:
                return

            def invoke_expected(method, *args) -> bool:
                """Call an explicitly expected-aware cache API, if present."""

                try:
                    parameter = inspect.signature(method).parameters.get("expected")
                except (TypeError, ValueError):
                    return False
                if parameter is None:
                    return False
                try:
                    if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
                        method(*args, expected)
                    else:
                        method(*args, expected=expected)
                except Exception:
                    raise
                return True

            try:
                if answer is None:
                    remove = getattr(cache, "remove_cache", None)
                    if callable(remove):
                        if expected is _CACHE_EXPECTED_UNSET:
                            remove(question)
                        elif invoke_expected(remove, question):
                            return
                        else:
                            # A legacy remove_cache(question) cannot safely
                            # emulate compare-and-delete.  Do not read and
                            # then call its unconditional remover.
                            return
                        return
                    replace = getattr(cache, "replace_cache", None)
                    if callable(replace):
                        if expected is _CACHE_EXPECTED_UNSET:
                            replace(question, None)
                        elif invoke_expected(replace, question, None):
                            return
                        else:
                            # The same rule applies to a legacy replacement
                            # API: an observed value is never a license for a
                            # later unconditional overwrite.
                            return
                        return
                    # Legacy test doubles/custom DAOs may only expose add_cache;
                    # an empty value is treated as a miss by Tiku.query.
                    if expected is not _CACHE_EXPECTED_UNSET:
                        return
                    add = getattr(cache, "add_cache", None)
                    if callable(add):
                        add(question, "")
                    return

                replace = getattr(cache, "replace_cache", None)
                if callable(replace):
                    replace(question, answer)
                    return
                add = getattr(cache, "add_cache", None)
                if callable(add):
                    add(question, answer)
            except Exception:
                # Cache maintenance must not turn a safe uncovered answer into
                # a failed work submission.
                logger.warning("选择题缓存更新失败（缓存内容已省略）")

        def _cache_value_for_result(q, result):
            """Return the exact cached value only when it is this result.

            A provider result can outlive a concurrent canonical cache write.
            Capturing a matching value before repair lets cleanup use
            compare-and-delete and leaves a newer canonical value untouched.
            """

            if not isinstance(self.tiku, (AI, SiliconFlow)):
                return _CACHE_EXPECTED_UNSET
            cache = getattr(self.tiku, "_cache", None)
            question = question_cache_key(q)
            getter = getattr(cache, "get_cache", None)
            if not cache or not question or not callable(getter):
                return _CACHE_EXPECTED_UNSET
            try:
                cached = getter(question)
            except Exception:
                return _CACHE_EXPECTED_UNSET
            if cached is None or result is None:
                return _CACHE_EXPECTED_UNSET
            try:
                if str(cached).strip() == str(result).strip():
                    return cached
            except Exception:
                pass
            return _CACHE_EXPECTED_UNSET

        def _choice_cache_value(q, result):
            """Backward-compatible name for choice cache observations."""

            return _cache_value_for_result(q, result)

        def _resolve_provider_choice(q, result) -> tuple[str, str]:
            """Resolve a provider answer, allowing one AI format repair."""

            multiple = q.get("type") == "multiple"
            cached_bad = _cache_value_for_result(q, result)
            answer = _resolve_choice_answer(
                result, q.get("options", ""), multiple=multiple
            )
            if answer:
                _update_choice_cache(q, answer)
                return answer, ""

            # Remove only the raw/legacy value actually observed for this
            # result.  A concurrent canonical write must survive this cleanup.
            if cached_bad is not _CACHE_EXPECTED_UNSET:
                _update_choice_cache(q, None, expected=cached_bad)

            # A missing answer means the provider did not return content; do
            # not turn that absence into another network request.  Only an
            # AI/OpenAI-compatible provider that explicitly exposes the
            # repair capability may receive the one strict retry.
            if not result or not isinstance(self.tiku, (AI, SiliconFlow)):
                return "", "provider_answer_empty" if not result else "choice_unmapped"

            repair = getattr(self.tiku, "repair_choice_answer", None)
            if not callable(repair):
                return "", "choice_unmapped"

            _raise_if_cancelled(cancel_event)
            try:
                repaired = repair(q, result)
            except StudyCancelled:
                raise
            except Exception:
                logger.warning(
                    "选择题答案修复失败（题目ID={}，题型={}，原因码=repair_provider_error）",
                    q.get("id", ""),
                    q.get("type", "unknown"),
                )
                return "", "choice_repair_error"
            _raise_if_cancelled(cancel_event)

            answer = _resolve_choice_answer(
                repaired,
                q.get("options", ""),
                multiple=multiple,
                strict_labels_only=True,
            )
            if answer:
                _update_choice_cache(q, answer)
                return answer, ""
            if cached_bad is not _CACHE_EXPECTED_UNSET:
                _update_choice_cache(q, None, expected=cached_bad)
            return "", (
                "choice_repair_empty"
                if not repaired
                else "choice_repair_unmapped"
            )

        def _handle_question(q, inc_found):
            nonlocal found_answers, has_partial_completion
            _raise_if_cancelled(cancel_event)
            q['course_title'] = _course.get('title', '')
            logger.debug(
                "开始处理题目（题型={}，题目内容与选项已省略）",
                q.get("type", "unknown"),
            )
            # 添加搜题延迟 #428 - 默认0s延迟
            query_delay = self.kwargs.get("query_delay", 0)
            if query_delay:
                _wait_or_cancel(cancel_event, query_delay)
            res = self.tiku.query(q)
            _raise_if_cancelled(cancel_event)
            answer = ""
            parts = []
            observed_cache = _CACHE_EXPECTED_UNSET
            if not res:
                answer = ""
                q[f'answerSource{q["id"]}'] = "uncovered"
                _mark_uncovered(q, "provider_answer_empty")
            else:
                # 根据响应结果选择答案
                if q["type"] in {"multiple", "single"}:
                    answer, mapping_reason = _resolve_provider_choice(q, res)
                    if not answer:
                        q[f'answerSource{q["id"]}'] = "uncovered"
                        _mark_uncovered(q, mapping_reason or "choice_unmapped")
                elif q["type"] == "judgement":
                    observed_cache = _cache_value_for_result(q, res)
                    selected = self.tiku.judgement_select(res)
                    answer = (
                        "true"
                        if selected is True
                        else "false"
                        if selected is False
                        else ""
                    )
                    if answer:
                        _update_choice_cache(q, answer)
                    elif observed_cache is not _CACHE_EXPECTED_UNSET:
                        _update_choice_cache(q, None, expected=observed_cache)
                elif q["type"] == "completion":
                    # Keep blank positions: indexed Chaoxing fields are
                    # positional, and ``splitlines`` would discard a tail
                    # blank.  Semicolons and slashes remain ordinary text.
                    observed_cache = _cache_value_for_result(q, res)
                    parts = _completion_parts(res)
                    answer = "\n".join(parts)
                else:
                    # 其他类型直接使用答案 （目前仅知有简答题，待补充处理）
                    answer = res

            if q["type"] == "completion" and parts:
                # Validate capacity even when every returned slot is empty;
                # an oversized provider response must never reach POST.
                _, one_based_fields, zero_based_fields = _completion_field_modes(
                    q["answerField"], str(q["id"])
                )
                try:
                    expected_count = int(q.get("expectedBlankCount"))
                    if expected_count < 0:
                        raise ValueError
                except (TypeError, ValueError):
                    expected_count = None
                indexed_count = max(
                    len(one_based_fields), len(zero_based_fields)
                )
                limits = [
                    value
                    for value in (expected_count, indexed_count or None)
                    if value is not None
                ]
                if limits and len(parts) > min(limits):
                    if observed_cache is not _CACHE_EXPECTED_UNSET:
                        _update_choice_cache(q, None, expected=observed_cache)
                    raise _CompletionMappingError(
                        "completion answer count exceeds form capacity"
                    )

            has_answer = bool(answer)
            if q["type"] == "completion":
                has_answer = any(str(part).strip() for part in parts)

            if not has_answer:  # 检查 answer 是否为空
                q[f'answerSource{q["id"]}'] = "uncovered"
                if q["type"] not in {"single", "multiple"}:
                    _mark_uncovered(q, "provider_answer_empty")
                answer = ""
                if q["type"] == "completion":
                    # Even an all-empty completion is an incomplete answer;
                    # save its cleared positional fields rather than submit.
                    has_partial_completion = True
                    if observed_cache is not _CACHE_EXPECTED_UNSET:
                        _update_choice_cache(q, None, expected=observed_cache)
            else:
                if q["type"] == "completion":
                    answer_key = f'answer{q["id"]}'
                    field_modes = _completion_field_modes(
                        q["answerField"], str(q["id"])
                    )
                    aggregate_fields, one_based_fields, zero_based_fields = field_modes
                    if not (
                        aggregate_fields
                        or one_based_fields
                        or zero_based_fields
                    ):
                        # Legacy fallback for hand-built/older question
                        # objects that expose no answer control at all.
                        q["answerField"][answer_key] = ""
                        aggregate_fields = [answer_key]

                    try:
                        expected_count = int(q.get("expectedBlankCount"))
                        if expected_count < 0:
                            raise ValueError
                    except (TypeError, ValueError):
                        expected_count = None

                    indexed_count = max(
                        len(one_based_fields), len(zero_based_fields)
                    )
                    limits = [
                        value
                        for value in (expected_count, indexed_count or None)
                        if value is not None
                    ]
                    if limits and len(parts) > min(limits):
                        raise _CompletionMappingError(
                            "completion answer count exceeds form capacity"
                        )

                    required_count = max(limits) if limits else None
                    if required_count is None:
                        source = "cover"
                    else:
                        source = (
                            "cover"
                            if len(parts) == required_count
                            and all(
                                str(part).strip()
                                for part in parts[:required_count]
                            )
                            else "partial"
                        )
                    q[f'answerSource{q["id"]}'] = source
                    if source == "cover":
                        inc_found()
                        _update_choice_cache(q, answer)
                    else:
                        has_partial_completion = True
                        if observed_cache is not _CACHE_EXPECTED_UNSET:
                            _update_choice_cache(
                                q, None, expected=observed_cache
                            )
                else:
                    logger.info(
                        "题库答案处理完成（题型={}，答案内容已省略）",
                        q.get("type", "unknown"),
                    )
                    q[f'answerSource{q["id"]}'] = "cover"
                    inc_found()

            # Fill only actual completion modes.  The aggregate is written
            # only when it was present (or the legacy fallback was required).
            if q["type"] == "completion":
                answer_key = f'answer{q["id"]}'
                aggregate_fields, one_based_fields, zero_based_fields = (
                    _completion_field_modes(q["answerField"], str(q["id"]))
                )
                if not (
                    aggregate_fields
                    or one_based_fields
                    or zero_based_fields
                ):
                    q["answerField"][answer_key] = ""
                    aggregate_fields = [answer_key]
                aggregate_value = "\n".join(parts)
                for key in aggregate_fields:
                    q["answerField"][key] = aggregate_value
                for fields in (one_based_fields, zero_based_fields):
                    for position, key in enumerate(fields):
                        q["answerField"][key] = (
                            parts[position] if position < len(parts) else ""
                        )
            else:
                answer_key = f'answer{q["id"]}'
                q["answerField"][answer_key] = answer
            logger.info(
                "答题字段已填写（题型={}，题干与答案内容已省略）",
                q.get("type", "unknown"),
            )

        # 若使用 AI 题库，则在同一张卷内并发搜题，避免单题串行阻塞
        if isinstance(self.tiku, AI):
            lock = threading.Lock()

            ocr_context_bound, ocr_config = _capture_vision_ocr_context()
            if not ocr_context_bound and "ocr_config" in self.kwargs:
                # Direct/legacy integrations may construct Chaoxing with task
                # metadata but without entering the outer worker context.
                ocr_context_bound = True
                configured_ocr = self.kwargs.get("ocr_config")
                ocr_config = (
                    dict(configured_ocr)
                    if configured_ocr is not None
                    else None
                )
            task_id = self.kwargs.get("task_id")

            def inc_found_concurrent():
                nonlocal found_answers
                with lock:
                    found_answers += 1

            ai_concurrency = self.kwargs.get("ai_concurrency", 3)
            try:
                ai_concurrency = int(ai_concurrency)
            except (TypeError, ValueError):
                ai_concurrency = 3
            ai_concurrency = max(1, ai_concurrency)

            def _handle_question_in_context(q):
                def invoke():
                    ocr_scope = (
                        vision_ocr_context(ocr_config)
                        if ocr_context_bound
                        else nullcontext()
                    )
                    with ocr_scope:
                        return _handle_question(q, inc_found_concurrent)

                if task_id:
                    with logger.contextualize(task_id=str(task_id)):
                        return invoke()
                return invoke()

            with ThreadPoolExecutor(max_workers=ai_concurrency) as executor:
                futures = []
                for q in questions["questions"]:
                    _raise_if_cancelled(cancel_event)
                    futures.append(executor.submit(_handle_question_in_context, q))

                # Futures must be observed explicitly.  Otherwise worker
                # exceptions (including cooperative cancellation) are stored
                # in the Future and the caller proceeds to calculate coverage
                # and submit an incomplete answer sheet.
                mapping_error = False
                for future in futures:
                    try:
                        future.result()
                    except _CompletionMappingError:
                        mapping_error = True
                if mapping_error:
                    logger.warning(
                        "填空题答案数量超过表单字段容量，已停止提交"
                    )
                    return StudyResult.ERROR
        else:
            def inc_found_seq():
                nonlocal found_answers
                found_answers += 1

            for q in questions["questions"]:
                try:
                    _handle_question(q, inc_found_seq)
                except _CompletionMappingError:
                    logger.warning(
                        "填空题答案数量超过表单字段容量，已停止提交"
                    )
                    return StudyResult.ERROR
        # A cancellation may arrive after the last question has completed but
        # before coverage/submit bookkeeping.  Do not continue into either
        # path once the task has been asked to stop.
        _raise_if_cancelled(cancel_event)
        cover_rate = (found_answers / total_questions) * 100
        logger.info(f"章节检测题库覆盖率： {cover_rate:.0f}%")

        # 提交模式  现在与题库绑定,留空直接提交, 1保存但不提交
        if has_uncovered_answers or has_partial_completion:
            # A partial completion answer must be saved so known positions are
            # not discarded, and an uncovered answer must never be submitted.
            questions["pyFlag"] = "1"
        elif self.tiku.get_submit_params() == "1":
            questions["pyFlag"] = "1"
        elif cover_rate >= self.tiku.COVER_RATE * 100 or self.rollback_times >= 1:
            questions["pyFlag"] = ""
        else:
            questions["pyFlag"] = "1"
            logger.info(f"章节检测题库覆盖率低于{self.tiku.COVER_RATE * 100:.0f}% ，不予提交")

        def _fill_answers_into_form(is_save: bool):
            """将每道题的 answerField 写回提交表单。

            - is_save=True: 仅在 answerSource 为 cover 时写入答案；未覆盖题留空。
            - is_save=False: 所有 answer* 字段直接写入。
            """
            for q in questions["questions"]:
                src = q.get(f'answerSource{q["id"]}', "")
                # 写入所有 answer* 字段（包括 answer{id}, answer{id}_0 等）
                for key, val in q["answerField"].items():
                    if not isinstance(key, str) or not key.startswith("answer"):
                        continue
                    if is_save:
                        # Save both covered and partial completion answers so
                        # known positions survive a save; random answers stay
                        # cleared as before.
                        keep_completion = (
                            q.get("type") == "completion"
                            and src in {"cover", "partial"}
                        )
                        questions[key] = (
                            val if src == "cover" or keep_completion else ""
                        )
                    else:
                        questions[key] = val

                # 写入 answertype{id}
                answertype_key = f'answertype{q["id"]}'
                if answertype_key in q["answerField"]:
                    questions[answertype_key] = q["answerField"][answertype_key]

        # 组建提交表单
        if questions["pyFlag"] == "1":
            _fill_answers_into_form(is_save=True)
        else:
            _fill_answers_into_form(is_save=False)

        _job["answer_result"] = {"status": "unsubmitted", "total_questions": total_questions,
                                 "covered_questions": found_answers, "graded_questions": 0,
                                 "correct_questions": 0, "pending_questions": 0,
                                 "unsubmitted_questions": total_questions}
        answered_questions = questions.pop("questions")

        # Re-check immediately before the side-effecting request.  The event
        # can be set while filling the local form after the coverage check.
        _raise_if_cancelled(cancel_event)
        res = audit.request("submit" if questions["pyFlag"] == "" else "save", _session.post,
            "https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNew",
            data=questions,
            headers={
                "Host": "mooc1.chaoxing.com",
                "sec-ch-ua-platform": '"Windows"',
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "sec-ch-ua": '"Microsoft Edge";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "sec-ch-ua-mobile": "?0",
                "Origin": "https://mooc1.chaoxing.com",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5",
            },
        )
        if res.status_code == 200:
            res_json = res.json()
            if _job_response_succeeded(res):
                audit.event("submission_accepted", mode="submit" if questions["pyFlag"] == "" else "save",
                            total_questions=total_questions, covered_questions=found_answers)
                logger.info(
                    "{}答题成功（服务端消息已省略）",
                    "提交" if questions["pyFlag"] == "" else "保存",
                )
            else:
                audit.event("submission_rejected", http_status=res.status_code)
                msg = str(res_json.get("msg", ""))
                # 作业已过期：直接视为跳过本作业，不再重试
                if "已过期" in msg:
                    logger.warning(
                        "{}答题失败（作业已过期，将跳过本作业；服务端消息已省略）",
                        "提交" if questions["pyFlag"] == "" else "保存",
                    )
                    return StudyResult.SKIPPED

                logger.error(
                    "{}答题失败（服务端消息已省略）",
                    "提交" if questions["pyFlag"] == "" else "保存",
                )
                return StudyResult.ERROR
        else:
            audit.event("submission_rejected", http_status=res.status_code)
            logger.error(
                "{}答题失败（HTTP status={}）",
                "提交" if questions["pyFlag"] == "" else "保存",
                res.status_code,
            )
            return StudyResult.ERROR
        _job["answer_result"]["status"] = "saved" if questions["pyFlag"] == "1" else "pending"
        if questions["pyFlag"] == "":
            _job["answer_result"]["pending_questions"] = total_questions
            _job["answer_result"]["unsubmitted_questions"] = 0
            try:
                _raise_if_cancelled(cancel_event)
                graded_response = load_work_page("read_grade")
                _check_platform_response(graded_response, "读取测验判分")
                grade = parse_work_grade(graded_response.text, submitted=True)
                audit.grade(grade)
                # A delayed/hidden grade must not convert a successful
                # submission into a failed job and trigger another submit.
                if grade.get("total_questions", 0) > 0:
                    _job["answer_result"].update(grade)
                else:
                    _job["answer_result"]["reason"] = grade.get("reason", "平台暂未提供判分")
                cache = getattr(self.tiku, "_cache", None)
                if isinstance(self.tiku, (AI, SiliconFlow)) and cache is not None:
                    try:
                        reconcile_answer_cache(cache, answered_questions, graded_response.text, session=self.session)
                    except Exception:
                        logger.warning("提交已成功，平台判分缓存校正暂未完成")
            except StudyCancelled:
                raise
            except Exception as exc:
                audit.event("grade_read_failed_after_submission", error_type=type(exc).__name__)
                _job["answer_result"]["reason"] = "本次提交已完成，判分暂未读取，可在任务页刷新判分。"
        return StudyResult.SUCCESS if questions["pyFlag"] == "" else StudyResult.SKIPPED

    def study_read(self, _course, _job, _job_info) -> StudyResult:
        """
        阅读任务学习, 仅完成任务点, 并不增长时长
        """
        _session = self.session
        _resp = _session.get(
            url="https://mooc1.chaoxing.com/ananas/job/readv2",
            params={
                "jobid": _job["jobid"],
                "knowledgeid": _job_info["knowledgeid"],
                "jtoken": _job["jtoken"],
                "courseid": _course["courseId"],
                "clazzid": _course["clazzId"],
            },
        )
        if not _job_response_succeeded(_resp):
            logger.error("阅读任务学习失败（HTTP status={}）", _resp.status_code)
            return StudyResult.ERROR
        else:
            logger.info("阅读任务学习完成（服务端消息已省略）")
            return StudyResult.SUCCESS

    def study_emptypage(self, _course, point):
        _session = self.session
        # &cpi=0&verificationcode=&mooc2=1&microTopicId=0&editorPreview=0
        _resp = _session.get(
            url="https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax",
            params={
                "courseId": _course["courseId"],
                "clazzid": _course["clazzId"],
                "chapterId": point["id"],
                "cpi": _course["cpi"],
                "verificationcode": "",
                "mooc2": 1,
                "microTopicId": 0,
                "editorPreview": 0,
            },
        )
        if _resp.status_code != 200:
            logger.error(
                "空页面任务失败（HTTP status={}；章节标题已省略）",
                _resp.status_code,
            )
            return StudyResult.ERROR
        else:
            logger.info("空页面任务完成（章节标题已省略）")
            return StudyResult.SUCCESS
