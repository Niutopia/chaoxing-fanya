import pytest
from api.answer import CacheDAO, question_cache_key
from api.grade_feedback import parse_choice_feedback, feedback_answer, reconcile_answer_cache


def page(answer='A', mark='marking_cuo', mine='B'):
    return f'''<div class="singleQuesId" data="q1"><div class="Zy_TItle"><i>3</i><span class="newZy_TItle">【单选题】</span>广域网的缩写？</div>
      <ul class="qtDetail"><li>A、WAN</li><li>B、LAN</li></ul>
      <div class="myAnswer"><div class="answerCon">{mine}</div></div>
      <div class="answerScore"><div class="CorrectOrNot"><span class="{mark}"></span></div></div>
      <div class="correctAnswer"><div class="answerCon">{answer}</div></div></div>'''


def question(**kwargs):
    return {'id': 'q1', 'type': 'single', 'title': '3【单选题】广域网的缩写？',
            'options': 'A. WAN\nB. LAN', 'answerField': {'answerq1': 'B'}, **kwargs}


def test_reference_corrects_previous_wrong_answer(tmp_path):
    cache = CacheDAO(str(tmp_path / 'cache.json'))
    q = question()
    cache.replace_cache(question_cache_key(q), 'B')
    assert reconcile_answer_cache(cache, [q], page()) == 1
    assert cache.get_cache(question_cache_key(q)) == 'A'


def test_reference_tracks_option_content_when_order_changes():
    feedback = parse_choice_feedback(page())[0]
    assert feedback_answer(question(options='A. LAN\nB. WAN'), feedback) == 'B'


@pytest.mark.parametrize('changes', [
    {'id': 'different'}, {'title': '局域网的缩写？'}, {'type': 'multiple'},
    {'options': 'A. WAN\nB. MAN'}, {'options': 'A. WAN\nB. WAN'},
])
def test_different_question_or_ambiguous_options_are_not_reused(changes):
    assert feedback_answer(question(**changes), parse_choice_feedback(page())[0]) is None


@pytest.mark.parametrize('mark', ['marking_cuo', 'marking_bandui'])
def test_rejected_cache_removed_without_reference(tmp_path, mark):
    cache = CacheDAO(str(tmp_path / 'cache.json'))
    q = question()
    cache.replace_cache(question_cache_key(q), 'B')
    assert reconcile_answer_cache(cache, [q], page('', mark)) == 1
    assert cache.get_cache(question_cache_key(q)) is None


def test_pending_mark_does_not_claim_answer_is_correct(tmp_path):
    cache = CacheDAO(str(tmp_path / 'cache.json'))
    q = question()
    cache.replace_cache(question_cache_key(q), 'B')
    assert reconcile_answer_cache(cache, [q], page('', '')) == 0
    assert cache.get_cache(question_cache_key(q)) == 'B'


def test_delayed_feedback_preserves_newer_cache(tmp_path):
    cache = CacheDAO(str(tmp_path / 'cache.json'))
    q = question()
    cache.replace_cache(question_cache_key(q), 'A')
    assert reconcile_answer_cache(cache, [q], page('', 'marking_cuo')) == 0
    assert cache.get_cache(question_cache_key(q)) == 'A'


def test_correct_mark_can_verify_own_answer_without_reference():
    feedback = parse_choice_feedback(page('', 'marking_dui', 'A'))[0]
    assert feedback_answer(question(), feedback) == 'A'


def test_same_order_duplicate_text_can_keep_explicit_label():
    feedback = {'id': 'q1', 'type': 'single', 'title': question()['title'],
                'options': ['A. n', 'B. n/2', 'C. ', 'D. '], 'answer': 'D'}
    assert feedback_answer(question(options=feedback['options']), feedback) == 'D'


def test_judgement_reference_maps_to_platform_boolean():
    html = page('错').replace('【单选题】', '【判断题】')
    feedback = parse_choice_feedback(html)[0]
    assert feedback_answer(question(type='judgement', title='3【判断题】广域网的缩写？'), feedback) == 'false'


def test_observed_household_radical_alias_matches_same_question():
    feedback = parse_choice_feedback(page().replace('广域网', '客户网络'))[0]
    assert feedback_answer(question(title='3【单选题】客戶网络的缩写？'), feedback) == 'A'


def test_multiple_reference_requires_all_valid_options():
    feedback = parse_choice_feedback(page('AB').replace('【单选题】', '【多选题】'))[0]
    assert feedback_answer(question(type='multiple'), feedback) == 'AB'
    feedback['answer'] = 'AC'
    assert feedback_answer(question(type='multiple'), feedback) is None


def test_java_character_literal_survives_broken_aria_label():
    from bs4 import BeautifulSoup
    from api.decode import _extract_choices
    node = BeautifulSoup('''<li aria-label='D int c='a';选择'><label><span class="num_option">D</span></label><a class="after">int c='a';</a></li>''', 'lxml').li
    assert _extract_choices(node) == "D. int c='a';"


def test_legacy_accessible_choice_without_visible_pair_is_preserved():
    from bs4 import BeautifulSoup
    from api.decode import _extract_choices
    node = BeautifulSoup('<li aria-label="A. 选项全文选择">A.</li>', 'lxml').li
    assert _extract_choices(node) == 'A. 选项全文'


@pytest.mark.parametrize('css_class', ['num_option', 'num_option_dx'])
def test_randomized_choice_uses_submitted_value_and_complete_code(css_class):
    from bs4 import BeautifulSoup
    from api.decode import _extract_choices
    node = BeautifulSoup(f'''<li aria-label='D int c='a';选择'><label><span class="{css_class}" data="D">B</span></label><a class="after">int c='a';</a></li>''', 'lxml').li
    assert _extract_choices(node) == "D. int c='a';"


@pytest.mark.parametrize('css_class', ['num_option', 'num_option_dx'])
def test_visible_wording_retains_word_xuanze(css_class):
    from bs4 import BeautifulSoup
    from api.decode import _extract_choices
    node = BeautifulSoup(f'<li aria-label="C 历史选择选择"><span class="{css_class}" data="C">A</span><a class="after">历史选择</a></li>', 'lxml').li
    assert _extract_choices(node) == 'C. 历史选择'


def test_judgement_boolean_value_keeps_human_readable_options():
    from bs4 import BeautifulSoup
    from api.decode import _extract_choices
    node = BeautifulSoup('<li><span class="num_option" data="false">B</span><a class="after">错</a></li>', 'lxml').li
    assert _extract_choices(node) == 'B. 错'


def test_feedback_maps_displayed_grade_to_randomized_form_value():
    from api.decode import decode_questions_info
    html = '''<form><div class="singleQuesId" data="q1"><div class="TiMu" data="0"><div class="Zy_TItle">3【单选题】广域网的缩写？</div>
      <ul><li><span class="num_option" data="B">A</span><a class="after">WAN</a></li>
      <li><span class="num_option" data="A">B</span><a class="after">LAN</a></li></ul></div></div></form>'''
    q = decode_questions_info(html)['questions'][0]
    # The platform reference is displayed as A (WAN); the live form posts B.
    assert feedback_answer(q, parse_choice_feedback(page())[0]) == 'B'


def test_formula_options_match_by_image_when_labels_are_shuffled():
    html = page().replace('<li>A、WAN</li><li>B、LAN</li>', '<li>A、<img src="https://example.test/one.png"></li><li>B、<img src="https://example.test/two.png"></li>')
    feedback = parse_choice_feedback(html)[0]
    q = question(options=['A. <img src="https://example.test/two.png">', 'B. <img src="https://example.test/one.png">'])
    assert feedback_answer(q, feedback) == 'B'


def test_ai_receives_formula_image_and_its_option_label():
    from api.answer import AI
    messages = AI()._build_messages(question(options=['A. 1', 'B. <img src="https://example.test/formula.png">']))
    content = messages[-1]['content']
    assert content[0]['type'] == 'text'
    assert 'B. <img' in content[0]['text']
    assert content[1] == {'type': 'image_url', 'image_url': {'url': 'https://example.test/formula.png'}}


def test_text_only_questions_keep_compatible_text_request():
    from api.answer import AI
    assert isinstance(AI()._build_messages(question())[-1]['content'], str)


def test_whitespace_inside_code_literal_is_not_discarded():
    feedback = {'id': 'q1', 'type': 'single', 'title': question()['title'],
                'options': ['A. "a b"', 'B. "ab"'], 'answer': 'A'}
    q = question(options=['A. "ab"', 'B. "a b"'])
    assert feedback_answer(q, feedback) == 'B'


def test_consecutive_spaces_in_code_are_not_collapsed_when_reusing_feedback():
    feedback = {'id': 'q1', 'type': 'single', 'title': question()['title'],
                'options': ['A. "a  b"', 'B. "a b"'], 'answer': 'A'}
    q = question(options=['A. "a b"', 'B. "a  b"'])
    assert feedback_answer(q, feedback) == 'B'
