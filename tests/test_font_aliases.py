import json
from api import cxsecret_font


def test_chinese_radical_wins_over_identical_phonetic_glyph(tmp_path, monkeypatch):
    path = tmp_path / 'font-map.json'
    path.write_text(json.dumps({'uni2F34': 'same-glyph', 'uni312C': 'same-glyph'}))
    dao = cxsecret_font.FontHashDAO(str(path))
    monkeypatch.setattr(cxsecret_font, 'fonthash_dao', dao)
    assert cxsecret_font.decrypt({'uni5FB3': 'same-glyph'}, '徳域网') == '广域网'


def test_real_font_table_resolves_observed_guang_glyph():
    dao = cxsecret_font.FontHashDAO()
    assert dao.find_char(dao.find_hash('uni2F34')) == 'uni2F34'


def test_unique_glyph_and_unknown_character_keep_existing_behavior(tmp_path, monkeypatch):
    path = tmp_path / 'font-map.json'
    path.write_text(json.dumps({'uni7F51': 'network'}))
    monkeypatch.setattr(cxsecret_font, 'fonthash_dao', cxsecret_font.FontHashDAO(str(path)))
    assert cxsecret_font.decrypt({'uni5FB0': 'network'}, '徰络A') == '网络A'


def test_supplemental_radicals_from_current_course_pages():
    assert cxsecret_font.decrypt({}, '⻚面置换、中国⻛格、⻘年') == '页面置换、中国风格、青年'
