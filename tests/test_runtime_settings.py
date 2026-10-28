"""Fresh installation inputs must not inherit production identities or QQ routing."""
import json
from plugin import runtime_settings


def test_absent_settings_preserves_legacy(monkeypatch):
    """Existing deployment keeps its source and persona behavior when opt-in is absent."""
    monkeypatch.delenv('HIGAN_SETTINGS_FILE', raising=False)
    assert runtime_settings.configured_owners({'legacy'}) == {'legacy'}
    assert runtime_settings.configured_persona() is None
    assert runtime_settings.onebot_connection('http://legacy') == ('http://legacy', '')


def test_explicit_fresh_settings_do_not_inherit_owners_or_qq(tmp_path, monkeypatch):
    """An empty owner list and absent QQ connection are meaningful, not fallback values."""
    source = tmp_path / 'settings.json'
    source.write_text(json.dumps({'schema_version': 1, 'owner_qqs': [], 'persona': '原样\n人设'}), encoding='utf-8')
    monkeypatch.setenv('HIGAN_SETTINGS_FILE', str(source))
    assert runtime_settings.configured_owners({'legacy'}) == set()
    assert runtime_settings.configured_persona() == '原样\n人设'
    assert runtime_settings.onebot_connection('http://legacy') == ('', '')


def test_external_onebot_is_explicit(tmp_path, monkeypatch):
    """Connection details are operator input, never embedded NapCat code or account data."""
    source = tmp_path / 'settings.json'
    source.write_text(json.dumps({'schema_version': 1, 'owner_qqs': ['123'], 'persona': '',
                      'onebot': {'enabled': True, 'http_base': 'http://external:3000/', 'token': 'fixture'}}), encoding='utf-8')
    monkeypatch.setenv('HIGAN_SETTINGS_FILE', str(source))
    assert runtime_settings.configured_owners({'legacy'}) == {'123'}
    assert runtime_settings.onebot_connection('http://legacy') == ('http://external:3000', 'fixture')
