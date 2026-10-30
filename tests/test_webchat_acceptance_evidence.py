"""Safe recorded real-runtime integration evidence must keep its synthetic-model boundary."""
import json
from pathlib import Path


def test_first_webchat_http_integration_is_recorded_without_overclaiming():
    """Retain actual replies/history while refusing to relabel synthetic transport acceptance."""
    path = Path(__file__).resolve().parents[1] / 'docs/evidence/webchat-first-turn-offline-20261030.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['status'] == 'passed'
    assert data['synthetic_model'] is True
    assert data['full_compose_e2e'] is False and data['real_model_accepted'] is False
    assert data['qq_transport_tested'] is False and data['browser_rendering_tested'] is False
    assert data['dashboard_html_http_200'] is True and data['dashboard_login'] is True
    assert data['distinct_webchat_sessions'] == 2
    assert data['provider_requests'] == 3 and data['provider_failures'] == []
    assert all(item['reply_before_end'] and item['terminal_end'] for item in data['streams'])
    assert data['plugin_context']['same_session_history_retained'] is True
    assert data['plugin_context']['cross_session_history_isolated'] is True
    assert data['persisted_history']['isolated'] is True
