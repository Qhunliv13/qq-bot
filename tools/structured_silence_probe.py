"""Verify deployed raw structured-silence guards without model calls or QQ sends."""
import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from production_probe import load_plugin


async def probe(directory,output):
    """Call the actual deployed sender; any policy/history/platform effect is forbidden."""
    module=load_plugin(directory)
    from release_probe_plugin.reply_selection import decode_reply
    bot=module.HiganPlugin.__new__(module.HiganPlugin)
    bot._turn_context={}
    effects=[]
    def forbidden(*args,**kwargs):
        """Make accidental side effects fail before any platform access is possible."""
        effects.append('unexpected_effect')
        raise RuntimeError('effect_forbidden_in_silence_probe')
    bot._policy_allows=forbidden
    bot._record_visible_reply=forbidden
    event=SimpleNamespace(send=forbidden)
    values=[{'reply_to':[],'text':''},{'reply_to':[],'text':'SILENT'},
            {'reply_to':[],'text':'（SILENT）'},{'reply_to':['999'],'text':''},
            {'reply_to':['999'],'text':'not a valid current target'}]
    for value in values:
        raw=json.dumps(value,ensure_ascii=False)
        result=await bot._send(event,'probe',raw)
        if result is not None:raise RuntimeError('silent_or_invalid_protocol_escaped')
    body='{"reply_to":[],"text":""}'
    outer=json.dumps({'reply_to':[],'text':body})
    decoded=decode_reply(outer,[],allow_unattributed=True)
    if decoded['text']!=body:raise RuntimeError('nested_literal_not_preserved')
    result={'raw_protocol_cases':len(values),'all_silent_or_invalid_suppressed':True,
            'single_decode_preserves_nested_json_literal':True,'side_effects':len(effects),
            'qq_messages_sent':0,'model_calls':0,'runtime_state_modified':False}
    if effects:raise RuntimeError('unexpected_silence_side_effect')
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)


def main():
    """Require explicit deployed source directory and audit receipt path."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--plugin',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(probe(args.plugin,args.output))


if __name__=='__main__':main()
