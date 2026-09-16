"""Background, read-only refresh of chapter quiz grades for a saved task."""
from __future__ import annotations

import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from api.decode import extract_course_card_data, _extract_job_info, _process_work_task
from api.work_grades import parse_work_grade, summarize_work_grades, retain_grade_comparison
from api.work_audit import WorkAudit
from .task_manager import TaskNotFound


def read_chapter_grades(client, course, chapter, cancelled=None, *, task_id=None, operation_id=None):
    results = {}
    audit = WorkAudit(task_id=task_id, course_id=course['courseId'],
                      chapter_id=chapter['id'], operation_id=operation_id)
    for index in range(7):
        if cancelled is not None and cancelled.is_set():
            return []
        response = audit.request('refresh_cards', client.session.get,
            'https://mooc1.chaoxing.com/mooc-ans/knowledge/cards',
            params={'clazzid': course['clazzId'], 'courseid': course['courseId'],
                    'knowledgeid': chapter['id'], 'cpi': course['cpi'], 'ut': 's', 'num': index, 'mooc2': 1}, timeout=15,
        )
        response.raise_for_status()
        if 'passport2.chaoxing.com' in str(response.url):
            raise ValueError('登录已失效，请重新验证账户')
        data = extract_course_card_data(response.text)
        if data.get('notOpen'):
            break
        info = _extract_job_info(data)
        for card in data.get('attachments', []):
            if card.get('type') != 'workid':
                continue
            job = _process_work_task(card)
            if not job['jobid'] or job['jobid'] in results:
                continue
            work_audit = WorkAudit(task_id=task_id, course_id=course['courseId'],
                                   chapter_id=chapter['id'], job_id=job['jobid'],
                                   operation_id=audit.context['operation_id'])
            response = work_audit.request('refresh_grade', client.session.get,
                'https://mooc1.chaoxing.com/mooc-ans/api/work',
                params={'api': '1', 'workId': job['jobid'].removeprefix('work-'),
                        'jobid': job['jobid'], 'originJobId': job['jobid'],
                        'needRedirect': 'true', 'skipHeader': 'true', 'knowledgeid': str(info['knowledgeid']),
                        'ktoken': info['ktoken'], 'cpi': info['cpi'], 'ut': 's',
                        'clazzId': course['clazzId'], 'enc': job['enc'], 'mooc2': '1', 'courseid': course['courseId']}, timeout=15,
            )
            response.raise_for_status()
            if 'passport2.chaoxing.com' in str(response.url):
                raise ValueError('登录已失效，请重新验证账户')
            grade = parse_work_grade(response.text)
            work_audit.grade(grade)
            results[job['jobid']] = {
                'course_id': str(course['courseId']), 'course_title': course['title'],
                'chapter_id': str(chapter['id']), 'chapter_title': chapter['title'],
                'job_id': job['jobid'], **grade,
            }
    return list(results.values())


def refresh_task_grades(manager, service, snapshot, details, started_at):
    client = None
    cancelled = threading.Event()
    audit = WorkAudit(task_id=snapshot.id)
    audit.event('refresh_started')
    try:
        auth, client = service._client_with_auth(snapshot.account_id)
        service._login(snapshot.account_id, client, auth)
        courses = {str(course['courseId']): course for course in client.get_course_list()}
        selected = [str(course.get('id', '')) for course in details.courses]
        if any(course_id not in courses for course_id in selected):
            raise ValueError('部分课程已不可访问，或历史课程信息不完整，请重新读取课程后再试')
        points = []
        for course_id in selected:
            course = courses[course_id]
            chapter_list = client.get_course_point(course['courseId'], course['clazzId'], course['cpi'])['points']
            points.extend((course, chapter) for chapter in chapter_list)
        manager.update_answer_report(snapshot.id, started_at, {'total_chapters': len(points)})
        works, errors = {}, []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(read_chapter_grades, client, course, chapter, cancelled,
                                   task_id=snapshot.id, operation_id=audit.context['operation_id']): (course, chapter) for course, chapter in points}
            completed = 0
            for future in as_completed(futures):
                course, chapter = futures[future]
                try:
                    for work in future.result():
                        works[(work['course_id'], work['job_id'])] = work
                except Exception as exc:
                    audit.event('refresh_chapter_failed', course_id=course['courseId'],
                                chapter_id=chapter['id'], error_type=type(exc).__name__)
                    errors.append({'course_title': course['title'], 'chapter_title': chapter['title'], 'reason': '判分读取失败，请稍后刷新或重新验证账户。'})
                completed += 1
                if completed % 5 == 0 or completed == len(points):
                    try:
                        if not manager.update_answer_report(snapshot.id, started_at, {'completed_chapters': completed, 'total_chapters': len(points)}):
                            cancelled.set()
                            audit.event('refresh_discarded')
                            return
                    except TaskNotFound:
                        cancelled.set()
                        raise
        previous = [work for work in details.answer_report.get('works', []) if work.get('course_id') in selected]
        values = retain_grade_comparison(list(works.values()), previous)
        values.sort(key=lambda work: (selected.index(work['course_id']), work['chapter_id'], work['job_id']))
        counts = summarize_work_grades(values)
        updated = manager.update_answer_report(snapshot.id, started_at, {
            'status': 'partial' if errors or counts['answer_unavailable_works'] else 'completed',
            'scope': 'selected_courses', 'checked_at': time.time(), 'works': values,
            'errors': errors, 'counts': counts, 'completed_chapters': len(points), 'total_chapters': len(points),
        })
        audit.event('refresh_completed' if updated else 'refresh_discarded',
                    total_works=len(values), changed_works=counts['answer_changed_works'],
                    failed_chapters=len(errors))
    except TaskNotFound:
        audit.event('refresh_discarded')
        pass
    except Exception as exc:
        audit.event('refresh_failed', error_type=type(exc).__name__)
        try:
            # Only application-owned ValueErrors are useful to the user;
            # request exceptions may embed signed URLs from the platform.
            message = str(exc) if isinstance(exc, ValueError) else '判分读取失败，请检查网络或重新验证账户后重试。'
            manager.update_answer_report(snapshot.id, started_at, {'status': 'failed', 'error': message})
        except TaskNotFound:
            pass
    finally:
        if client is not None:
            service._close_client_resources(client)
