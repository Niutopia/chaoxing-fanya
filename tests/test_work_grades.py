import pytest
from api.work_grades import parse_work_grade, summarize_work_grades


def page(marks, *, total=None, score=75, full=100):
    count = len(marks) if total is None else total
    icons = ''.join(f'<div class="answerScore"><div class="CorrectOrNot fl"><span class="{mark}"></span></div></div>' for mark in marks)
    return f'<title>查看已批阅作业</title><div class="newTestCon">题量: {count} 满分: {full}<div>本次成绩 {score} 分</div>{icons}</div>'


def test_observed_graded_page_counts_three_correct_of_four():
    result = parse_work_grade(page(['marking_dui', 'marking_dui', 'marking_cuo', 'marking_dui']))
    assert result['correct_questions'] == 3
    assert result['graded_questions'] == result['total_questions'] == 4
    assert result['score'] == 75
    assert result['status'] == 'graded'


def test_coverage_or_score_alone_is_not_correctness():
    result = parse_work_grade('<div class="newTestCon">题量: 10 满分: 100 本次成绩 100 分 题库覆盖率100%</div>')
    assert result['graded_questions'] == 0
    assert result['pending_questions'] == 0
    assert result['unavailable_questions'] == 10
    assert result['status'] == 'score_only'


def test_pending_questions_stay_out_of_graded_denominator():
    result = parse_work_grade(page(['marking_dui', 'marking_cuo', ''], total=5))
    assert result['graded_questions'] == 2
    assert result['pending_questions'] == 3


def test_partial_credit_is_graded_but_not_fully_correct():
    result = parse_work_grade(page(['marking_dui', 'marking_bandui']))
    assert result['correct_questions'] == 1
    assert result['partial_questions'] == 1
    assert result['graded_questions'] == 2


def test_unknown_page_does_not_fabricate_zero_percent():
    result = parse_work_grade('<h1>登录</h1>')
    assert result['status'] == 'unavailable'
    assert result['graded_questions'] == 0


def test_marks_outside_question_result_are_ignored():
    result = parse_work_grade(page(['marking_cuo']) + '<span class="marking_dui"></span>')
    assert result['correct_questions'] == 0
    assert result['graded_questions'] == 1


def test_inconsistent_counts_are_not_reported_as_accuracy():
    result = parse_work_grade(page(['marking_dui','marking_dui'],total=1))
    assert result['status'] == 'unavailable'
    assert 'graded_questions' not in result


def test_aggregate_is_weighted_by_questions():
    result = summarize_work_grades([parse_work_grade(page(['marking_dui'])), parse_work_grade(page(['marking_cuo']*9))])
    assert result['answer_correct_questions'] == 1
    assert result['answer_graded_questions'] == 10
    assert result['answer_total_works'] == 2


@pytest.mark.parametrize('mode', ['graded', 'saved', 'network_failure'])
def test_submission_records_grading_without_repeating_submission(monkeypatch, mode):
    import api.base as base
    from types import SimpleNamespace
    from test_completion_submission import _CompletionTiku, _completion_question
    calls, posts = [], []
    def get(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1 and mode == 'network_failure':
            raise RuntimeError('grade read failed')
        if len(calls) == 1:
            return SimpleNamespace(status_code=200, text='<div class="newTestCon">待完成 题量: 1 满分: 100</div>')
        return SimpleNamespace(status_code=200, text=page(['marking_dui'], score=100))
    def post(url, **kwargs):
        posts.append(kwargs['data'])
        return SimpleNamespace(status_code=200, json=lambda: {'status': True})
    question = _completion_question(fields=('answerq11',), expected=1)
    monkeypatch.setattr(base, 'decode_questions_info', lambda *_a, **_k: {'questions': [question], 'answerwqbid': 'q1,'})
    engine = base.Chaoxing(base.Account('u', 'p'), tiku=_CompletionTiku('answer', submit='1' if mode == 'saved' else ''), session=SimpleNamespace(get=get, post=post))
    job = {'jobid': 'work-1', 'enc': 'enc'}
    result = engine.study_work({'courseId': 'c', 'clazzId': 'cl'}, job, {'knowledgeid': 'ch', 'ktoken': 't', 'cpi': 'p'})
    assert len(posts) == 1
    if mode == 'graded':
        assert result is base.StudyResult.SUCCESS
        assert job['answer_result']['correct_questions'] == 1
        assert job['answer_result']['graded_questions'] == 1
    elif mode == 'saved':
        assert result is base.StudyResult.SKIPPED
        assert len(calls) == 1
        assert job['answer_result']['status'] == 'saved'
        assert job['answer_result']['graded_questions'] == 0
    else:
        assert result is base.StudyResult.SUCCESS
        assert job['answer_result']['status'] == 'pending'
        assert job['answer_result']['graded_questions'] == 0


def test_unsubmitted_work_is_not_waiting_for_teacher_grading():
    result = parse_work_grade('<div class="newTestCon">题量: 3 满分: 100</div>', submitted=False)
    assert result['status'] == 'unsubmitted'
    assert result['pending_questions'] == 0
    assert result['unsubmitted_questions'] == 3


def test_real_pending_header_overrides_editing_url_with_zero_marked_questions():
    result = parse_work_grade('<div class="newTestCon">章节测验 待批阅 题量: 7 满分: 100</div>', submitted=False)
    assert result['status'] == 'pending'
    assert result['pending_questions'] == 7
    assert result['unsubmitted_questions'] == 0


def test_explicit_unsubmitted_header_is_not_awaiting_grading():
    result = parse_work_grade('<div class="newTestCon">章节测验 未完成 题量: 7 满分: 100 <a>提交</a></div>')
    assert result['status'] == 'unsubmitted'
    assert result['unsubmitted_questions'] == 7
    assert result['pending_questions'] == 0


@pytest.mark.parametrize('state', ['已完成', '已提交', '待批阅', '待批改'])
def test_existing_submission_never_calls_provider_or_posts_again(state):
    from types import SimpleNamespace
    from api.base import Account, Chaoxing, StudyResult
    from test_completion_submission import _CompletionTiku

    html = f'''<div class="newTestCon">章节测验 {state} 题量: 1 满分: 100
      <form><div class="singleQuesId" data="q1"><div class="TiMu" data="0">
      <div class="Zy_TItle">原标题</div><ul><li>A. 正确</li><li>B. 错误</li></ul>
      </div></div></form></div>'''
    reads = []
    def get(*args, **kwargs):
        reads.append(1)
        return SimpleNamespace(status_code=200, text=html)
    def unexpected(*args, **kwargs):
        pytest.fail('An already submitted paper must not be answered or posted')
    tiku = _CompletionTiku('A')
    tiku.query = unexpected
    job = {'jobid': 'work-1', 'enc': 'enc'}
    engine = Chaoxing(Account('u', 'p'), tiku=tiku, session=SimpleNamespace(get=get, post=unexpected))
    result = engine.study_work({'courseId': 'c', 'clazzId': 'cl'}, job, {'knowledgeid': 'ch', 'ktoken': 't', 'cpi': 'p'})
    assert result is StudyResult.SUCCESS
    assert len(reads) == 1
    assert job['answer_result']['pending_questions'] == 1
    assert job['answer_result']['correct_questions'] == 0


def test_explicit_pending_completion_takes_priority_over_submission_argument():
    result = parse_work_grade('<div class="newTestCon">待完成 题量: 2 满分: 100</div>', submitted=True)
    assert result['status'] == 'unsubmitted'
    assert result['unsubmitted_questions'] == 2


def test_retry_after_lost_submission_response_reads_result_without_posting_again():
    from types import SimpleNamespace
    import requests
    from api.base import Account, Chaoxing, StudyResult
    from test_completion_submission import _CompletionTiku

    form = '''<div class="newTestCon">待完成 题量: 1 满分: 100<form>
      <div class="singleQuesId" data="q1"><div class="TiMu" data="0">
      <div class="Zy_TItle">选择正确内容</div><ul>
      <li><span class="num_option" data="A">A</span><a class="after">其他内容</a></li>
      <li><span class="num_option" data="D">B</span><a class="after">正确内容</a></li>
      </ul></div></div></form></div>'''
    posts = []
    def get(*args, **kwargs):
        return SimpleNamespace(status_code=200, text=page(['marking_dui'], score=100) if posts else form)
    def post(url, **kwargs):
        posts.append(dict(kwargs['data']))
        raise requests.ReadTimeout('The platform accepted the work before the response was lost')
    tiku = _CompletionTiku('正确内容')
    engine = Chaoxing(Account('u', 'p'), tiku=tiku, session=SimpleNamespace(get=get, post=post))
    course, info = {'courseId': 'c', 'clazzId': 'cl'}, {'knowledgeid': 'ch', 'ktoken': 't', 'cpi': 'p'}
    job = {'jobid': 'work-1', 'enc': 'enc'}
    with pytest.raises(requests.ReadTimeout):
        engine.study_work(course, job, info)
    assert posts[0]['answerq1'] == 'D'
    tiku.query = lambda *_: pytest.fail('Retry must not answer an already submitted quiz')
    assert engine.study_work(course, job, info) is StudyResult.SUCCESS
    assert len(posts) == 1
    assert job['answer_result']['correct_questions'] == 1
