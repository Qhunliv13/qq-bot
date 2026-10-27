"""Metadata-only lossless layout experiment; offline by default, never executes tools.

Synthetic fixtures are deliberately not production traffic or chat exports. Optional
live mode makes exactly four provider requests using read-only native settings.
Cache warmth/order is uncontrolled, so measured differences are not causal claims.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.request


def load_context(directory):
    """Load the pure context builder without importing runtime or framework state."""
    spec = importlib.util.spec_from_file_location('probe_context', directory / 'conversation_context.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_pair(builder):
    """Keep complete deterministic history; vary observations, scope, current and time."""
    history = [{'id': 'synthetic-history-' + str(i), 'text': ('Synthetic public discussion paragraph %d. ' % i) * 8} for i in range(40)]
    values = []
    for step in range(2):
        observations = [{'tool_name': 'synthetic_read', 'result': {'sequence': i, 'text': ('Synthetic observation %d step%d. ' % (i, step)) * 8}} for i in range(8)]
        values.append(builder(scope='synthetic-scope-' + str(step), history=history,
                              messages=[{'id': 'synthetic-current-' + str(step), 'text': 'Synthetic layout diagnostic ' + str(step)}],
                              observations=observations, clock={'sampled_at': 'synthetic-time-' + str(step)}))
    return values


def prior_layout(text):
    """Move changing observations before scope-first history, preserving all values."""
    data = json.loads(text)
    conversation = data['conversation']
    data['conversation'] = {key: conversation[key] for key in ('scope', 'history', 'messages')}
    data['conversation'].update({key: value for key, value in conversation.items() if key not in data['conversation']})
    order = ('version', 'source', 'observations', 'conversation', 'other_scopes', 'task', 'attachments', 'timing', 'trigger', 'activity')
    result = {key: data[key] for key in order if key in data}
    result.update({key: value for key, value in data.items() if key not in result})
    return json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def history_first_layout(text):
    """Reconstruct the preceding history-first control, retaining unknown fields.

    The live builder can change order independently; this named control must not
    silently become observation-first when the production default changes.
    """
    data = json.loads(text)
    order = ('version', 'source', 'conversation', 'observations', 'other_scopes',
             'task', 'attachments', 'timing', 'trigger', 'activity')
    result = {key: data[key] for key in order if key in data}
    result.update({key: value for key, value in data.items() if key not in result})
    return json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def common(first, second):
    """Measure UTF-8 bytes, never label this a token count or provider hit."""
    left, right = first.encode('utf-8'), second.encode('utf-8')
    return next((i for i, (a, b) in enumerate(zip(left, right)) if a != b), min(len(left), len(right)))


def native_usage(response):
    """Allowlist provider usage only; absent cache telemetry remains unknown."""
    usage = response.get('usage') or {}
    details = usage.get('prompt_tokens_details') or {}
    cached = details.get('cached_tokens', usage.get('prompt_cache_hit_tokens'))
    prompt = usage.get('prompt_tokens')
    valid = isinstance(prompt, int) and not isinstance(prompt, bool) and isinstance(cached, int) and not isinstance(cached, bool) and 0 <= cached <= prompt
    return {'prompt_tokens': prompt if isinstance(prompt, int) else None,
            'cached_tokens': cached if isinstance(cached, int) else None, 'cache_valid': valid}


def weighted(rows):
    """Use sums of authoritative prompt and cache tokens, not mean percentages."""
    valid = [row['usage'] for row in rows if row['usage']['cache_valid']]
    prompt = sum(row['prompt_tokens'] for row in valid)
    cached = sum(row['cached_tokens'] for row in valid)
    return {'valid_requests': len(valid), 'prompt_tokens': prompt, 'cached_tokens': cached,
            'weighted_hit_percent': 100 * cached / prompt if prompt else None}


def run_probe(directory, live=False):
    """Build metadata offline; explicit live mode never dispatches returned tool calls."""
    context = load_context(directory)
    candidate = fixture_pair(context.build_context)
    prior = [prior_layout(value) for value in candidate]
    history_first = [history_first_layout(value) for value in candidate]
    layouts = (('prior', prior), ('history_first', history_first),
               ('observations_first', candidate))
    if any(json.loads(a) != json.loads(b) or len(a.encode('utf-8')) != len(b.encode('utf-8'))
           for _, values in layouts for a, b in zip(values, candidate)):
        raise ValueError('layout_data_changed')
    report = {'synthetic_fixtures': True, 'parsed_data_equal': True, 'history_entries': 40,
              'observations_per_request': 8, 'provider_request_count': 0,
              'qq_messages_sent': 0, 'tool_actions_executed': 0,
              'production_parameters_modified': False, 'cache_warmth_uncontrolled': True,
              'causal_improvement_asserted': False, 'layouts': {}, 'requests': []}
    for name, values in layouts:
        report['layouts'][name] = {'user_bytes': [len(value.encode('utf-8')) for value in values],
                                   'common_user_prefix_bytes': common(*values)}
    if not live:
        return report
    # Runtime owns provider selection. No guessed provider ID, source or credential path.
    from production_probe import load_plugin
    with tempfile.TemporaryDirectory(prefix='prompt-layout-probe-') as temporary:
        previous = os.environ.get('HIGAN_ROOT')
        os.environ['HIGAN_ROOT'] = temporary
        try:
            module = load_plugin(directory)
            bot = module.HiganPlugin.__new__(module.HiganPlugin)
            # The production resolver reads native framework configuration independently
            # of the isolated mutable state root. Reuse its exact routing semantics.
            base, key, model = bot._her_llm_config()
            if not base or not key or not model:
                raise RuntimeError('provider_not_configured')
            persona = module.HiganPlugin._load_persona()
            tools = bot._tool_schemas()
            system = context.system_prompt(persona)
            # Keep the explicit opt-in budget at four requests: compare the
            # immediately preceding control against the current production order.
            for name, values in layouts[1:]:
                for step, value in enumerate(values):
                    # Only this diagnostic response is bounded; production config untouched.
                    payload = {'model': model, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': value}], 'tools': tools, 'tool_choice': 'auto', 'max_tokens': 8}
                    request = urllib.request.Request(base + '/chat/completions', data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
                    started = time.monotonic()
                    with urllib.request.urlopen(request, timeout=300) as response:
                        data = json.load(response)
                    report['provider_request_count'] += 1
                    calls = ((data.get('choices') or [{}])[0].get('message') or {}).get('tool_calls') or []
                    report['requests'].append({'layout': name, 'step': step + 1, 'usage': native_usage(data), 'seconds': round(time.monotonic() - started, 3), 'tool_requests_not_executed': len(calls)})
        finally:
            if previous is None:
                os.environ.pop('HIGAN_ROOT', None)
            else:
                os.environ['HIGAN_ROOT'] = previous
    report['weighted_usage_by_layout'] = {name: weighted([row for row in report['requests'] if row['layout'] == name]) for name in ('history_first', 'observations_first')}
    return report


def main():
    """Require explicit source/output paths and explicit opt-in for provider traffic."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--plugin', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    result = run_probe(args.plugin, args.live)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
