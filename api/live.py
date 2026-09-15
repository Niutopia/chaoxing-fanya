# -*- coding: utf-8 -*-
import json
import time

import requests

from api.logger import logger

from api.config import GlobalConst as gc


class Live:
    def __init__(
        self,
        attachment: dict,
        defaults: dict,
        course_id: str,
        session: requests.Session,
    ):
        self.attachment = attachment
        self.defaults = defaults  # 包含用户ID、课程ID等信息
        self.course_id = course_id  # 课程ID
        self.session = session
        self.name = self._field("title", "name", default="未知直播")  # 直播名称
        self.headers = gc.HEADERS.copy()
        self.headers.update({
            "Referer": "https://mooc1.chaoxing.com/ananas/modules/live/index.html?v=2022-1214-1139"
        })
        # Chaoxing treats the first report as a session start (0) and every
        # later heartbeat as an in-session report (1).  Re-sending 0 forever
        # eventually makes saveTimePc return ``@fail``.
        self._report_started = False

    def _field(self, *names: str, default=""):
        """Read a live field from the flattened or nested attachment shape."""

        properties = self.attachment.get("property")
        sources = [self.attachment]
        if isinstance(properties, dict):
            sources.append(properties)
        for source in sources:
            for name in names:
                value = source.get(name)
                if value is not None and value != "":
                    return value
        return default

    def _job_id(self) -> str:
        """Return the attachment job ID across known payload variants."""

        return str(self._field("jobid", "jobId", "_jobid", default=""))

    def prepare(self) -> bool:
        """Open the live page once so zhibo.chaoxing.com establishes context."""

        live_id = self._field("liveId", "liveid")
        user_id = self.defaults.get("userid")
        clazz_id = self.defaults.get("clazzId")
        knowledge_id = self.defaults.get("knowledgeid")
        job_id = self._job_id()
        if not all([live_id, user_id, clazz_id, knowledge_id, job_id, self.course_id]):
            logger.error("缺少直播会话必要参数，无法开始观看")
            return False

        params = {
            "courseId": self.course_id,
            "classId": clazz_id,
            "knowledgeId": knowledge_id,
            "jobId": job_id,
            "userId": user_id,
            "rt": self._field("rt", default="0.9"),
            "livesetenc": self._field("liveSetEnc", "livesetenc"),
            "isjob": "true",
            "watchingInCourse": "1",
            "customPara1": f"{clazz_id}_{self.course_id}",
            "customPara2": self._field("authEnc", "authenc"),
            "isNotDrag": self._field("isNotDrag", "isnotdrag", default="1"),
            "jobfs": "0",
            # These fields are emitted by newer live pages.  Empty values are
            # harmless for older attachments, while retaining them is needed
            # for schools whose live page validates the complete context.
            "livedragenc": self._field("liveDragEnc", "livedragenc"),
            "sw": self._field("sw", default="1"),
            "ds": self._field("ds", default="1"),
            "liveswdsenc": self._field("liveSwDsEnc", "liveswdsenc"),
        }
        try:
            response = self.session.get(
                f"https://zhibo.chaoxing.com/{live_id}",
                params=params,
                headers=self.headers,
                timeout=10,
            )
            response.raise_for_status()
            return True
        except Exception as exc:
            logger.error(f"建立直播观看会话失败: {exc}")
            return False

    def do_finish(self):
        """提交直播观看时长（核心方法）"""
        # 从直播信息中提取关键参数
        stream_name = self._field("streamName", "streamname")
        vdoid = self._field("vdoid", "vdoId")
        user_id = self.defaults.get("userid")
        
        if not all([stream_name, vdoid, user_id, self.course_id]):
            logger.error("缺少直播必要参数，无法提交时长")
            return False
        
        # 发送请求记录时长
        is_start = "1" if self._report_started else "0"
        # ``isStart`` describes the position in this client-side session, not
        # whether the previous HTTP response was successful.  Mark the first
        # attempt before sending so a retry after transport/HTTP failure is
        # still a continuation (1), matching the browser implementation.
        self._report_started = True
        try:
            response = self.session.get(
                "https://zhibo.chaoxing.com/saveTimePc",
                params={
                    "streamName": stream_name,
                    "vdoid": vdoid,
                    "userId": user_id,
                    "isStart": is_start,
                    "t": int(time.time() * 1000),
                    "courseId": self.course_id,
                },
                headers=self.headers,
                timeout=10,
            )
            response.raise_for_status()
            success = response.text.strip() == "@success"
            logger.debug("直播时长提交成功" if success else "直播时长提交被服务器拒绝")
            return success
        except Exception as exc:
            logger.error(f"提交直播时长失败: {exc}")
            return False

    def get_status(self) -> dict|None:
        """获取直播状态（总时长等信息）"""
        live_id = self._field("liveId", "liveid")
        user_id = self.defaults.get("userid")
        clazz_id = self.defaults.get("clazzId")
        knowledge_id = self.defaults.get("knowledgeid")
        job_id = self._job_id()
        
        if not all([live_id, user_id, clazz_id, knowledge_id, job_id, self.course_id]):
            logger.error("缺少直播状态查询必要参数")
            return None
        
        # 发送请求并解析状态（包含总时长）
        try:
            response = self.session.get(
                "https://mooc1.chaoxing.com/ananas/live/liveinfo",
                params={
                    "liveid": live_id,
                    "userid": user_id,
                    "clazzid": clazz_id,
                    "knowledgeid": knowledge_id,
                    "courseid": self.course_id,
                    "jobid": job_id,
                    "ut": "s",
                },
                headers=self.headers,
                timeout=10,
            )
            response.raise_for_status()
            return json.loads(response.text)  # 返回包含总时长的状态字典
        except Exception as exc:
            logger.error(f"获取直播状态失败: {exc}")
            return None
