"""Submission outcomes stay independent of their diagnostic logging."""
import io
import json
from types import SimpleNamespace

import pytest
import requests

from api.logger import logger
from api.work_audit import WorkAudit


def test_lost_response_records_identity_without_form_or_exception_contents():
    output = io.StringIO()
    sink = logger.add(output, format='{message}')
    audit = WorkAudit(task_id='task-diagnostic', course_id='course', chapter_id='chapter', job_id='work-1')
    def send(*_args, **_kwargs):
        raise requests.exceptions.ReadTimeout('signed-url-and-private-form')
    try:
        with pytest.raises(requests.exceptions.ReadTimeout):
            audit.request('submit', send, 'https://example.invalid/?enc=private-signature',
                          data={'answer': 'private-answer'})
    finally:
        logger.remove(sink)
    lines = output.getvalue().splitlines()
    records = [json.loads(line.split('测验操作 ', 1)[1]) for line in lines]
    assert [record['event'] for record in records] == ['request_started', 'request_exception']
    assert records[0]['operation_id'] == records[1]['operation_id']
    assert records[1]['task_id'] == 'task-diagnostic'
    assert records[1]['job_id'] == 'work-1'
    assert records[1]['error_type'] == 'ReadTimeout'
    assert 'private' not in output.getvalue()


def test_failing_log_sink_cannot_change_an_accepted_post_into_an_error(monkeypatch):
    from api import work_audit
    def broken(*_args, **_kwargs):
        raise OSError('log disk unavailable')
    monkeypatch.setattr(work_audit, 'logger', SimpleNamespace(bind=broken))
    response = SimpleNamespace(status_code=200)
    sent = []
    def send():
        sent.append(1)
        return response
    audit = WorkAudit(job_id='work-1')
    assert audit.request('submit', send) is response
    audit.event('submission_accepted')
    audit.grade({'status': 'graded', 'score': 100})
    assert sent == [1]
