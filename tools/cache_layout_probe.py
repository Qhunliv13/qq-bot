"""Compare two lossless context layouts using actual provider cache-token telemetry.

Four bounded diagnostic requests reuse the bot's own config/persona/tools and actual
private history/observations; no tool executes, QQ sends, or live state is changed.
Output records numeric usage/bytes only, never content, credentials or scope IDs.
"""
import argparse
import asyncio
import json
from pathlib import Path
import time
import urllib.request

from production_probe import load_plugin


def old_layout(text):
    """Reconstruct the prior deterministic top-level key order without changing data."""
    data=json.loads(text)
    order=('version','source','conversation','observations','other_scopes','task','attachments','timing','trigger','activity')
    return json.dumps({key:data[key] for key in order if key in data},ensure_ascii=False,allow_nan=False,separators=(',',':'))


def common(first,second):
    """Count exact common UTF-8 bytes, explicitly not model tokens or backend caches."""
    first=first.encode();second=second.encode()
    return next((i for i,(a,b) in enumerate(zip(first,second)) if a!=b),min(len(first),len(second)))


async def probe(directory,output):
    """Read one evidence-heavy existing scope privately and compare changed-current requests."""
    module=load_plugin(directory)
    from release_probe_plugin.conversation_context import build_context,system_prompt
    from release_probe_plugin.history_window import history_snapshot
    from release_probe_plugin.tool_context import ToolContextArchive
    from release_probe_plugin.model_usage import usage_metrics
    bot=module.HiganPlugin.__new__(module.HiganPlugin)
    base,key,model=bot._her_llm_config()
    if not base or not key:raise RuntimeError('provider_not_configured')
    groups=json.loads(Path('/root/higan/autonomy.json').read_text())['origins']
    archive=ToolContextArchive('/root/higan/tool_observations')
    snapshots=[(scope,archive.recent(scope)) for scope in groups]
    if not snapshots:raise RuntimeError('no_existing_scopes')
    scope,observations=max(snapshots,key=lambda pair:len(json.dumps(pair[1],ensure_ascii=False)))
    history=[]
    path=Path('/root/higan/history')/(scope+'.jsonl')
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            try:record=json.loads(line)
            except ValueError:continue
            if isinstance(record,dict):history.append(record.get('line') or record.get('text') or record.get('content') or '')
    history=list(history_snapshot(history[-2000:],max(0,len(history)-2000)))
    persona=module.HiganPlugin._load_persona()
    text=[]
    for marker in ('first','next'):
        text.append(build_context(scope=scope,history=history,
             messages=[{'id':'cache-probe-'+marker,'sender':{'id':'cache-probe','role':'member'},
                        'text':'隔离诊断：仅说明你收到这条缓存布局测试消息，不调用工具。'+marker}],
             clock={'sampled_at':marker,'timezone':'Asia/Shanghai'},observations=observations))
    old=[old_layout(value) for value in text]
    if any(json.loads(a)!=json.loads(b) for a,b in zip(old,text)):raise RuntimeError('layout_data_changed')
    results=[]
    for layout,values in (('prior',old),('evidence_first',text)):
        for index,value in enumerate(values):
            payload={'model':model,'messages':[{'role':'system','content':system_prompt(persona)},
                    {'role':'user','content':value}], 'tools':bot._tool_schemas(),'tool_choice':'auto'}
            payload.update(bot._llm_extra())
            # Only diagnostic output length is bounded; production params unchanged.
            payload['max_tokens']=128
            def call():
                """Use legitimate provider credentials without printing request/response content."""
                request=urllib.request.Request(base+'/chat/completions',data=json.dumps(payload).encode(),
                        headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
                with urllib.request.urlopen(request,timeout=300) as response:return json.load(response)
            started=time.monotonic();response=await asyncio.to_thread(call)
            results.append({'layout':layout,'step':index+1,'usage':usage_metrics(response),
                            'seconds':round(time.monotonic()-started,3),
                            'tool_requests_not_executed':len(((response.get('choices') or [{}])[0].get('message') or {}).get('tool_calls') or [])})
    result={'scope_identity_omitted':True,'observations':len(observations),
            'observations_utf8_bytes':len(json.dumps(observations,ensure_ascii=False,separators=(',',':')).encode()),
            'user_utf8_bytes':len(text[0].encode()),'parsed_data_equal':True,
            'prior_common_prefix_bytes':common(*old),'evidence_first_common_prefix_bytes':common(*text),
            'actual_provider_requests':results,'qq_messages_sent':0,'tool_actions_executed':0,
            'production_parameters_modified':False,'whole_population_improvement_not_asserted':True}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)


def main():
    """Require explicit candidate source and private numeric result file."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--plugin',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();asyncio.run(probe(args.plugin,args.output))


if __name__=='__main__':main()
