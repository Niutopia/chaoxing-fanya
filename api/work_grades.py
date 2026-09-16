"""Read platform grading without answering questions or submitting work."""
from __future__ import annotations

import math
import re
from bs4 import BeautifulSoup


def _number(text, pattern):
    match = re.search(pattern, text)
    if not match:
        return None
    value = float(match.group(1))
    return value if math.isfinite(value) and value >= 0 else None


def parse_work_grade(html: str, *, submitted: bool | None = None) -> dict:
    """Count the platform's per-question marks, never infer them from coverage."""
    soup = BeautifulSoup(html, 'lxml')
    for tag in soup(['script', 'style']):
        tag.decompose()
    root = soup.select_one('.newTestCon') or soup
    text = root.get_text(' ', strip=True)
    header = text.split('题量', 1)[0]
    # Chaoxing also serves submitted, teacher-pending papers from
    # doHomeWorkNew. The visible status outranks the URL/form template.
    if any(label in header for label in ('待批阅', '待批改', '已完成', '已提交')):
        submitted = True
    elif any(label in header for label in ('未提交', '未完成', '待完成', '待做', '待提交')):
        submitted = False
    elif submitted is None and any(tag.get_text(strip=True) in {'保存', '提交', '提交作业', '暂存'} for tag in root.select('a,button')):
        submitted = False
    total = _number(text, r'题量\s*[:：]\s*(\d+)')
    score = _number(text, r'本次成绩\s*([\d.]+)\s*分')
    full_score = _number(text, r'满分\s*[:：]\s*([\d.]+)')
    correct = wrong = partial = 0
    marks = root.select('.answerScore .CorrectOrNot')
    for mark in marks:
        classes = {value for node in mark.find_all(True) for value in node.get('class', [])}
        # Count once per question, even if the DOM repeats an icon.
        if 'marking_dui' in classes and 'marking_cuo' not in classes:
            correct += 1
        elif 'marking_cuo' in classes and 'marking_dui' not in classes:
            wrong += 1
        elif 'marking_bandui' in classes:
            partial += 1
    graded = correct + wrong + partial
    total = int(total) if total is not None else len(marks)
    if total < graded:
        return {'status': 'unavailable', 'reason': '平台题量与判分标记不一致，暂不计入正确率'}
    if score is not None and full_score is not None and score > full_score:
        score = full_score = None
    state = 'graded' if total > 0 and graded == total else 'pending' if total > 0 else 'unavailable'
    if total > 0 and graded == 0:
        if submitted is False:
            state = 'unsubmitted'
        elif score is not None:
            state = 'score_only'
    result = {
        'status': state,
        'total_questions': total, 'graded_questions': graded,
        'correct_questions': correct, 'wrong_questions': wrong, 'partial_questions': partial,
        'pending_questions': max(0, total - graded) if state == 'pending' else 0,
        'unsubmitted_questions': total if state == 'unsubmitted' else 0,
        'unavailable_questions': total if state == 'score_only' else 0,
    }
    if submitted is not None:
        result['submitted'] = submitted
    if score is not None and full_score is not None and full_score > 0:
        result.update(score=score, full_score=full_score)
    if not total:
        result['reason'] = '平台尚未提供可读取的逐题判分'
    return result


def summarize_work_grades(works: list[dict]) -> dict:
    """Weight accuracy by questions; unequal-size quizzes must not be averaged."""
    counts = {f'answer_{key}': 0 for key in (
        'total_questions', 'graded_questions', 'correct_questions',
        'wrong_questions', 'partial_questions', 'pending_questions', 'unsubmitted_questions', 'unavailable_questions',
    )}
    for work in works:
        for key in counts:
            value = work.get(key.removeprefix('answer_'), 0)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                counts[key] += value
    counts['answer_total_works'] = len(works)
    counts['answer_graded_works'] = sum(work.get('status') == 'graded' for work in works)
    counts['answer_unavailable_works'] = sum(work.get('status') in {'unavailable', 'score_only'} for work in works)
    counts['answer_changed_works'] = sum(bool(work.get('previous_grade')) for work in works)
    return counts


def retain_grade_comparison(works: list[dict], previous: list[dict]) -> list[dict]:
    """Keep prior evidence visible without counting it as a current grade."""
    fields = ('status', 'total_questions', 'graded_questions', 'correct_questions',
              'wrong_questions', 'partial_questions', 'score', 'full_score')
    identity = lambda work: (work.get('course_id'), work.get('job_id'))
    old_works = {identity(work): work for work in previous if work.get('course_id') and work.get('job_id')}
    current = {identity(work): dict(work) for work in works}
    for key, old in old_works.items():
        if key not in current:
            total = old.get('total_questions', 0)
            current[key] = {
                **{name: old[name] for name in ('course_id', 'course_title', 'chapter_id', 'chapter_title', 'job_id') if name in old},
                'status': 'unavailable', 'total_questions': total,
                'graded_questions': 0, 'correct_questions': 0, 'wrong_questions': 0,
                'partial_questions': 0, 'pending_questions': 0, 'unsubmitted_questions': 0,
                'unavailable_questions': total,
                'reason': '上次读取到的测验本次未能读取，旧成绩仅作对照，未计入当前正确率。',
            }
        work = current[key]
        baseline = old.get('previous_grade') or {name: old[name] for name in fields if name in old}
        fewer_marks = work.get('graded_questions', 0) < baseline.get('graded_questions', 0)
        old_score, new_score = baseline.get('score'), work.get('score')
        lost_score = old_score is not None and new_score is None
        lower_score = (old_score is not None and new_score is not None
                       and baseline.get('full_score') == work.get('full_score') and new_score < old_score)
        if fewer_marks or lost_score or lower_score:
            work['previous_grade'] = baseline
    return list(current.values())
