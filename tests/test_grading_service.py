import json
import threading
from types import SimpleNamespace

from api.work_grades import parse_work_grade, summarize_work_grades, retain_grade_comparison
from webapp import create_app
from webapp.crypto import SecretBox
from webapp.grading_service import read_chapter_grades, refresh_task_grades
from webapp.store import SQLiteStore
from webapp.task_manager import TaskManager

GRADE = '<div class="newTestCon">题量: 2 满分: 100 本次成绩 50 分<div class="answerScore"><div class="CorrectOrNot"><span class="marking_dui"></span></div></div><div class="answerScore"><div class="CorrectOrNot"><span class="marking_cuo"></span></div></div></div>'
COURSE = {'courseId': 'course', 'clazzId': 'class', 'cpi': 'cpi', 'title': '课程'}
CHAPTER = {'id': 'chapter', 'title': '第一章'}


def seed(tmp_path):
    store = SQLiteStore(tmp_path / 'chaoxing-web.sqlite3', SecretBox(tmp_path))
    account = store.create_account('user', 'u', 'p')
    store.save_web_task({'id': 'task-grade', 'account_id': account.id, 'state': 'failed', 'started_at': 1, 'finished_at': 2, 'progress': 0, 'total': 1, 'error': '原始失败原因', 'stats': {'completed_courses': 0, 'total_courses': 1}}, {'courses': [{'id': 'course', 'title': '课程', 'chapters': []}], 'counts': {'completed_courses': 0, 'total_courses': 1}})
    return store, TaskManager(persistence=store)


class ReadOnlySession:
    def __init__(self):
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith('/cards'):
            data = {'defaults': {'knowledgeid': 'chapter', 'ktoken': 'token', 'cpi': 'cpi'}, 'attachments': [{'type': 'workid', 'jobid': 'work-1', 'enc': 'signed', 'job': True, 'isPassed': True}]}
            # The same work appears twice; the result must be counted once.
            text = 'mArg=' + json.dumps(data) if kwargs['params']['num'] < 2 else 'mArg=$mArg;'
        else:
            text = GRADE
        return SimpleNamespace(status_code=200, text=text, url=url, raise_for_status=lambda: None)
    def post(self, *args, **kwargs):
        raise AssertionError('Grading must not submit work or progress')


def test_read_completed_quizzes_without_study_side_effects():
    session = ReadOnlySession()
    result = read_chapter_grades(SimpleNamespace(session=session), COURSE, CHAPTER)
    assert len(result) == 1
    assert result[0]['correct_questions'] == 1
    assert result[0]['graded_questions'] == 2
    assert len([url for url, _ in session.calls if url.endswith('/api/work')]) == 1


def test_report_survives_restart_without_changing_task_result(tmp_path):
    store, manager = seed(tmp_path)
    before = manager.get_snapshot('task-grade')
    generation = manager.begin_answer_report('task-grade')
    assert manager.begin_answer_report('task-grade') is None
    works = [parse_work_grade(GRADE)]
    manager.update_answer_report('task-grade', generation, {'status': 'completed', 'checked_at': 10, 'works': works, 'counts': summarize_work_grades(works)})
    restored = TaskManager(persistence=store)
    result = restored.get_snapshot('task-grade')
    assert (result.state, result.progress, result.total, result.error, result.finished_at) == (before.state, before.progress, before.total, before.error, before.finished_at)
    assert result.stats['answer_correct_questions'] == 1
    assert result.stats['answer_graded_questions'] == 2
    assert restored.get_details('task-grade').answer_report['works'] == works
    assert not manager.update_answer_report('task-grade', generation, {'status': 'failed'})


def test_interrupted_report_is_retryable_after_restart(tmp_path):
    store, manager = seed(tmp_path)
    manager.begin_answer_report('task-grade')
    restored = TaskManager(persistence=store)
    assert restored.get_details('task-grade').answer_report['status'] == 'failed'
    assert restored.begin_answer_report('task-grade') is not None


def test_refresh_runs_read_only_pipeline_and_persists_summary(tmp_path):
    _, manager = seed(tmp_path)
    client = SimpleNamespace(session=ReadOnlySession(), get_course_list=lambda: [COURSE], get_course_point=lambda *_: {'points': [CHAPTER]})
    closed = []
    service = SimpleNamespace(_client_with_auth=lambda _: (None, client), _login=lambda *_: None, _close_client_resources=closed.append)
    generation = manager.begin_answer_report('task-grade')
    refresh_task_grades(manager, service, manager.get_snapshot('task-grade'), manager.get_details('task-grade'), generation)
    report = manager.get_details('task-grade').answer_report
    assert report['status'] == 'completed'
    assert report['scope'] == 'selected_courses'
    assert report['counts']['answer_graded_questions'] == 2
    assert report['completed_chapters'] == report['total_chapters'] == 1
    assert closed == [client]


def test_refresh_endpoint_starts_once_and_returns_public_report(tmp_path, monkeypatch):
    seed(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def fake_refresh(manager, service, snapshot, details, started_at):
        calls.append(snapshot.id)
        entered.set()
        release.wait(2)
    monkeypatch.setattr('webapp.grading_service.refresh_task_grades', fake_refresh)
    app = create_app({'TESTING': True, 'DATA_DIR': tmp_path})
    try:
        client = app.test_client()
        response = client.post('/api/tasks/task-grade/answer-report')
        assert response.status_code == 202
        assert response.json['data']['answer_report']['status'] == 'running'
        assert entered.wait(1)
        assert client.post('/api/tasks/task-grade/answer-report').status_code == 202
        assert calls == ['task-grade']
        assert client.get('/api/tasks/task-grade/details').json['data']['answer_report']['status'] == 'running'
    finally:
        release.set()
        app.extensions['task_log_sink_finalizer']()


def previous_full_grade():
    return {'course_id': 'course', 'course_title': '课程', 'chapter_id': 'chapter',
            'chapter_title': '章节', 'job_id': 'work-1', 'status': 'graded',
            'total_questions': 5, 'graded_questions': 5, 'correct_questions': 5,
            'wrong_questions': 0, 'score': 100, 'full_score': 100}


def test_regression_keeps_evidence_but_does_not_inflate_current_accuracy():
    original = previous_full_grade()
    reset = {**original, **parse_work_grade('<div class="newTestCon">待完成 题量: 5 满分: 100</div>')}
    reset.pop('score')
    compared = retain_grade_comparison([reset], [original])
    repeated = retain_grade_comparison([reset], compared)
    assert repeated[0]['previous_grade']['score'] == 100
    counts = summarize_work_grades(repeated)
    assert counts['answer_correct_questions'] == counts['answer_graded_questions'] == 0
    assert counts['answer_unsubmitted_questions'] == 5
    assert counts['answer_changed_works'] == 1
    recovered = retain_grade_comparison([original], repeated)
    assert 'previous_grade' not in recovered[0]
    assert summarize_work_grades(recovered)['answer_changed_works'] == 0


def test_lower_score_keeps_actual_current_marks_and_previous_score():
    original = previous_full_grade()
    lower = {**original, 'score': 60, 'correct_questions': 3, 'wrong_questions': 2}
    work = retain_grade_comparison([lower], [original])[0]
    assert (work['score'], work['correct_questions'], work['graded_questions']) == (60, 3, 5)
    assert work['previous_grade']['score'] == 100


def test_missing_quiz_is_visible_as_unavailable_instead_of_disappearing():
    compared = retain_grade_comparison([], [previous_full_grade()])
    assert len(compared) == 1
    assert compared[0]['status'] == 'unavailable'
    assert 'score' not in compared[0]
    counts = summarize_work_grades(compared)
    assert counts['answer_total_questions'] == counts['answer_unavailable_questions'] == 5
    assert counts['answer_graded_questions'] == 0


def test_refresh_persists_a_missing_previous_quiz_without_claiming_complete(tmp_path):
    store, manager = seed(tmp_path)
    first = manager.begin_answer_report('task-grade')
    original = previous_full_grade()
    manager.update_answer_report('task-grade', first, {'status': 'completed', 'works': [original], 'counts': summarize_work_grades([original])})
    details = manager.get_details('task-grade')
    second = manager.begin_answer_report('task-grade')
    client = SimpleNamespace(get_course_list=lambda: [COURSE], get_course_point=lambda *_: {'points': []})
    service = SimpleNamespace(_client_with_auth=lambda _: (None, client), _login=lambda *_: None, _close_client_resources=lambda _: None)
    refresh_task_grades(manager, service, manager.get_snapshot('task-grade'), details, second)
    restored = TaskManager(persistence=store)
    report = restored.get_details('task-grade').answer_report
    assert report['status'] == 'partial'
    assert report['works'][0]['previous_grade']['score'] == 100
    assert report['counts']['answer_graded_questions'] == 0
    assert restored.get_snapshot('task-grade').state == 'failed'
