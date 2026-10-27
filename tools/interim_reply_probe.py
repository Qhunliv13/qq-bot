"""Verify real model multi-reply selection with isolated recorded delivery, never QQ sends."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

from production_probe import load_plugin,ProbeEvent


async def probe(directory,output):
    """Run the actual tool loop/provider; replace only platform delivery with a local recorder."""
    module=load_plugin(directory)
    from release_probe_plugin.tool_context import ToolContextArchive
    root=Path(tempfile.mkdtemp(prefix='interim_probe_',dir=output.parent))
    module.HIGAN_ROOT=str(root)
    bot=module.HiganPlugin.__new__(module.HiganPlugin)
    bot.persona=module.HiganPlugin._load_persona()
    bot.context=SimpleNamespace(get_using_provider=lambda *args:object())
    bot._pending={};bot._busy={};bot._all_recent=[];bot._recent={'release-probe':[]}
    bot._recent_images={};bot._member_snapshot={};bot._history_origins={};bot.manifest=[]
    bot._provider_request_timeout=300
    bot._tool_evidence_store=ToolContextArchive(root/'observations')
    bot._turn_context={'release-probe':{'messages':[], 'history':(), 'clock':bot._clock_context()}}
    published=[]
    async def record_send(event,scope,text,quote_id=None,*,decoded=False):
        """Stand in only for external transport; retain actual helper delivery receipt."""
        if not decoded:raise RuntimeError('unvalidated_interim_payload')
        published.append(text)
        return text
    bot._send=record_send
    text='隔离验收：请先用 reply_now 自行写一句简短的开始说明，再调用 tool_describe 查看 workspace_read，得到结果后再用 reply_now 自行写一句不同的结果说明，最后用一句不同的话结束。不要发送图片、创建文件或调用其他工具。这里只验证能力，不规定任何固定话术。'
    event=ProbeEvent(text)
    original=bot._dispatch_tool
    calls=[]
    async def dispatch(name,args,scope='',event=None):
        """Keep every actual allowed handler; prohibit irrelevant production effects."""
        if name not in {'reply_now','tool_describe'}:return {'ok':False,'error':'probe_readonly_boundary'}
        calls.append(name)
        return await original(name,args,scope,event)
    bot._dispatch_tool=dispatch
    final=await bot._decide_tools_first(event,'release-probe',text,False)
    result={'native_tools':len(bot._tool_schemas()),'tool_names':calls,
            'intermediate_messages_recorded':len(published),
            'intermediate_text_chars':[len(value) for value in published],
            'distinct_intermediate_bodies':len(set(published)),
            'final_text_chars':len(final),'actual_model_provider':True,
            'actual_native_loop_dispatch_helper':True,'platform_send_replaced_with_local_recorder':True,
            'qq_messages_sent':0,'state_isolated':True,'no_forced_runtime_reply_templates':True}
    if calls.count('reply_now')<2 or 'tool_describe' not in calls or len(published)<2:
        raise RuntimeError('model_probe_did_not_exercise_intermediate_replies')
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)


def main():
    """Require explicit installed/candidate source and separate nonsecret audit receipt."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--plugin',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();asyncio.run(probe(args.plugin,args.output))


if __name__=='__main__':main()
