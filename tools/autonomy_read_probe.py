"""Probe deployed autonomous native reasoning with real read-only QQ tools and no public effects."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

from production_probe import load_plugin


async def probe(directory,output):
    """Use an isolated typed opportunity and real model, allowing only account/status reads."""
    module=load_plugin(directory)
    from release_probe_plugin.autonomy import AutonomousController
    from release_probe_plugin.autonomous_turn import AutonomyEvent
    from release_probe_plugin.tool_context import ToolContextArchive
    root=Path(tempfile.mkdtemp(prefix='autonomy_read_probe_',dir=output.parent))
    scope='1';clock=[100000.0]
    controller=AutonomousController(root/'autonomy.json',now=lambda:clock[0])
    controller.observe(scope,'isolated-read-probe')
    clock[0]+=600
    triggers=controller.opportunities({})
    bot=module.HiganPlugin.__new__(module.HiganPlugin)
    bot.persona=module.HiganPlugin._load_persona()
    bot._provider_request_timeout=300
    bot._recent={scope:[]};bot._all_recent=[];bot._pending={};bot._busy={}
    bot._autonomy_state=controller
    bot._tool_evidence_store=ToolContextArchive(root/'observations')
    bot._turn_context={scope:{'messages':[],'history':[],'clock':bot._clock_context(),
        'trigger':{'kind':'autonomous_opportunity','events':triggers},'image_refs':[],'image_sources':[]}}
    bot.context=SimpleNamespace(get_using_provider=lambda *args:object())
    event=AutonomyEvent(bot,scope,'isolated-read-probe',triggers)
    prompt=json.loads(bot._request_context(event,scope,'',False))
    if prompt['conversation']['messages']!=[]:raise RuntimeError('fabricated_human_message')
    schemas=bot._tool_schemas()
    advertised=[item['function']['name'] for item in schemas]
    readonly={'qq_account_info','qq_social_status'}
    bot._tool_schemas=lambda:[item for item in schemas if item['function']['name'] in readonly]
    actual=bot._dispatch_tool
    called=[]

    async def dispatch(name,args,scope='',event=None):
        """Record actual read calls; never permit a mutating test via another tool route."""
        if name not in readonly:raise RuntimeError('write_forbidden_in_read_probe')
        result=await actual(name,args,scope,event=event)
        called.append((name,bool(result.get('ok')),result.get('error')))
        return result

    bot._dispatch_tool=dispatch
    # Diagnostic request is explicit probe context, not a fabricated user message.
    bot._turn_context[scope]['trigger']['diagnostic']={
        'purpose':'Verify this account can be read through qq_account_info; then report whether the profile is available. No write tools are available.'}
    answer=await bot._decide_tools_first(event,scope,'',False)
    results={'empty_current_messages_preserved':True,'trigger_kind':prompt['trigger']['kind'],
             'advertised_tools':len(advertised),'social_tools_advertised':all(name in advertised for name in ('qq_account_info','qq_set_signature','qq_publish_post','qq_set_profile','qq_social_status')),
             'native_read_tools_called':[name for name,_,_ in called],
             'read_error_codes':[error for _,ok,error in called if not ok],
             'account_info_success':any(name=='qq_account_info' and ok for name,ok,_ in called),
             'answer_chars':len(answer),'qq_messages_sent':0,'signature_modified':False,'qzone_published':False,
             'persona_modified':False,'runtime_state_isolated':True}
    output.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False),flush=True)
    if not results['account_info_success']:raise RuntimeError('native_account_read_not_verified')


def main():
    """Require explicit deployed/candidate source and local receipt path."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--plugin',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(probe(args.plugin,args.output))


if __name__=='__main__':main()
