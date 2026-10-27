"""Disable or restore one reviewed built-in command using the native hot command API."""
import argparse
import json
from pathlib import Path
import urllib.parse
import urllib.request


def request(method,path,credential,payload=None):
    """Use a scoped legitimate key on localhost; never log authentication headers."""
    secret=json.loads(credential.read_text(encoding='utf-8'))
    if secret.get('scopes')!=['tool']:raise RuntimeError('tool_scoped_key_required')
    req=urllib.request.Request('http://127.0.0.1:6185/api/v1'+path,
          data=json.dumps(payload).encode() if payload is not None else None,
          headers={'Content-Type':'application/json','X-API-Key':secret['api_key']},method=method)
    with urllib.request.urlopen(req,timeout=30) as response:result=json.load(response)
    if result.get('status') not in ('ok','success'):raise RuntimeError('command_api_failed')
    return result.get('data')


def select(items,name):
    """Require a unique actual built-in descriptor; never guess a handler identifier."""
    matches=[item for item in items if item.get('original_command')==name
             and 'builtin_commands' in str(item.get('module_path') or '')]
    if len(matches)!=1:raise RuntimeError('builtin_command_not_unique')
    return matches[0]


def apply(credential,backup,report,enabled):
    """Back up metadata before one idempotent toggle and verify all other handlers unchanged."""
    before=request('GET','/commands',credential)['items']
    command=select(before,'sid')
    # Exclusive backup keeps the prior command state available for native rollback.
    with backup.open('x',encoding='utf-8') as handle:json.dump(before,handle,ensure_ascii=False,indent=2)
    if command['enabled'] is not enabled:
        request('PATCH','/commands/'+urllib.parse.quote(command['handler_full_name'],safe=''),credential,{'enabled':enabled})
    after=request('GET','/commands',credential)['items']
    current=select(after,'sid')
    if current['enabled'] is not enabled:raise RuntimeError('command_toggle_did_not_stick')
    others=lambda rows:{item['handler_full_name']:item.get('enabled') for item in rows
                        if item['handler_full_name']!=command['handler_full_name']}
    if others(before)!=others(after):raise RuntimeError('unrelated_command_state_changed')
    result={'command':'sid','handler_full_name':current['handler_full_name'],
            'previous_enabled':command['enabled'],'enabled':current['enabled'],
            'other_commands_unchanged':True,'total_commands':len(after),
            'native_persistent_hot_toggle':True,'service_restart':False,'qq_test_messages_sent':0}
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def main():
    """Allow only the reviewed sid handler; restoration must be explicitly selected."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--credential',type=Path,required=True)
    parser.add_argument('--backup',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--enable',action='store_true')
    args=parser.parse_args()
    print(json.dumps(apply(args.credential,args.backup,args.report,args.enable),ensure_ascii=False))


if __name__=='__main__':main()
