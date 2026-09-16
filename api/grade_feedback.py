"""Reuse explicit platform feedback without treating an AI answer as verified."""
from __future__ import annotations

import re
from bs4 import BeautifulSoup

from api.option_parser import option_entries


def _wording(value):
    value = re.sub(r'^\s*\d*\s*【[^】]+】', '', str(value))
    # The font table renders the 戶 radical for the 户 glyph in some forms.
    # Interior whitespace can be part of code or a string literal. A cache
    # miss is preferable to treating "a  b" and "a b" as the same option.
    return value.strip().replace('戶', '户')


def parse_choice_feedback(html: str, *, session=None) -> list[dict]:
    """Read objective-question reference answers and per-question verdicts."""
    from api.decode import FontDecoder, _extract_choices, _extract_title

    soup = BeautifulSoup(html, 'lxml')
    decoder = FontDecoder(html) if soup.find('style', id='cxSecretStyle') else None
    result = []
    types = {'单选题': 'single', '多选题': 'multiple', '判断题': 'judgement'}
    for node in soup.select('.singleQuesId'):
        title = node.select_one('.Zy_TItle')
        marker = title.select_one('.newZy_TItle') if title else None
        kind = types.get(marker.get_text(strip=True).strip('【】') if marker else '')
        if not kind or not node.get('data'):
            continue
        marks = node.select('.answerScore .CorrectOrNot span')
        classes = {c for mark in marks for c in mark.get('class', [])}
        verdict = ('wrong' if 'marking_cuo' in classes else
                   'partial' if 'marking_bandui' in classes else
                   'correct' if 'marking_dui' in classes else 'pending')
        answer_node = node.select_one('.correctAnswer .answerCon')
        answer = answer_node.get_text('', strip=True) if answer_node else ''
        if not answer and verdict == 'correct':
            answer_node = node.select_one('.myAnswer .answerCon')
            answer = answer_node.get_text('', strip=True) if answer_node else ''
        options = [_extract_choices(li, decoder) for li in node.select('ul.qtDetail > li')]
        if not options:
            options = [_extract_choices(li, decoder) for li in node.select('ul.Zy_ulTop > li')]
        result.append({'id': str(node['data']), 'type': kind,
                       'title': _extract_title(title, decoder, session=session),
                       'options': options, 'answer': answer, 'verdict': verdict})
    return result


def feedback_answer(question: dict, feedback: dict) -> str | None:
    """Map a reference to matching wording and current option order."""
    if (str(question.get('id')) != feedback['id'] or
            question.get('type') != feedback['type'] or
            not _wording(question.get('title', '')) or
            _wording(question.get('title', '')) != _wording(feedback['title'])):
        return None
    answer = feedback.get('answer', '').strip()
    if question['type'] == 'judgement':
        return {'对': 'true', '正确': 'true', '√': 'true',
                '错': 'false', '错误': 'false', '×': 'false'}.get(answer)
    old = option_entries(feedback['options'])
    new = option_entries(question.get('options', ''))
    if not old or len(old) != len(new):
        return None
    old_text = {entry.label: _wording(entry.text) for entry in old}
    new_labels = {_wording(entry.text): entry.label for entry in new}
    identical_order = [(e.label, _wording(e.text)) for e in old] == [(e.label, _wording(e.text)) for e in new]
    if not identical_order:
        if (len(new_labels) != len(new) or len(set(old_text.values())) != len(old) or
                set(old_text.values()) != set(new_labels)):
            return None
    # Chaoxing objective references use single-letter labels. Unsupported
    # labels stay unresolved, rather than guessing how a string is split.
    if any(len(label) != 1 for label in old_text):
        return None
    labels = re.sub(r'[\s,，、;；]', '', answer).upper()
    if (not labels or any(label not in old_text for label in labels) or
            len(set(labels)) != len(labels) or
            question['type'] == 'single' and len(labels) != 1):
        return None
    selected = set(labels) if identical_order else {new_labels[old_text[label]] for label in labels}
    return ''.join(entry.label for entry in new if entry.label in selected)


def reconcile_answer_cache(cache, questions: list[dict], html: str, *, session=None) -> int:
    """Correct known cached answers after grading; discard rejected answers."""
    from api.answer import question_cache_key

    feedbacks = parse_choice_feedback(html, session=session)
    by_id = {item['id']: item for item in feedbacks}
    if len(by_id) != len(feedbacks):
        return 0
    changed = 0
    for question in questions:
        feedback = by_id.get(str(question.get('id')))
        if not feedback:
            continue
        key = question_cache_key(question)
        answer = feedback_answer(question, feedback)
        if answer is not None:
            cache.replace_cache(key, answer)
            changed += 1
        elif (feedback['verdict'] in {'wrong', 'partial'} and
              _wording(question.get('title', '')) == _wording(feedback['title'])):
            submitted = question.get('answerField', {}).get('answer' + str(question.get('id')))
            if submitted is not None and cache.remove_cache(key, expected=submitted):
                changed += 1
    return changed
