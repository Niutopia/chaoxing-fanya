"""Small, best-effort work-operation trail in the existing application log."""
from __future__ import annotations

import json
from uuid import uuid4

from api.logger import logger


class WorkAudit:
    """Correlate reads, POST outcomes and grades without storing form contents.

    Logging must never change a successful submission into a retry. Request
    errors still propagate to the caller; only failures to emit a log are ignored.
    """

    def __init__(self, *, task_id=None, course_id=None, chapter_id=None,
                 job_id=None, operation_id=None):
        self.context = {
            "operation_id": operation_id or uuid4().hex,
            "task_id": task_id, "course_id": course_id,
            "chapter_id": chapter_id, "job_id": job_id,
        }
        self.sequence = 0

    def event(self, event, **values):
        try:
            message = json.dumps({**self.context, "event": event, **values},
                                 ensure_ascii=False, separators=(",", ":"))
            logger.bind(task_id=self.context["task_id"], diagnostic_only=True).info("测验操作 {}", message)
        except Exception:
            pass

    def request(self, action, send, *args, **kwargs):
        self.sequence += 1
        request_id = self.sequence
        self.event("request_started", action=action, request_id=request_id)
        try:
            response = send(*args, **kwargs)
        except Exception as exc:
            # A lost POST response does not prove the platform rejected it.
            self.event("request_exception", action=action, request_id=request_id,
                       error_type=type(exc).__name__)
            raise
        self.event("response_received", action=action, request_id=request_id,
                   http_status=getattr(response, "status_code", None))
        return response

    def grade(self, grade):
        fields = ("status", "submitted", "score", "full_score", "total_questions",
                  "graded_questions", "correct_questions", "wrong_questions",
                  "partial_questions", "pending_questions", "unsubmitted_questions")
        self.event("grade_observed", **{key: grade[key] for key in fields if key in grade})
