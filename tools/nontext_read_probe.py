"""Verify a real non-text source reaches the actual configured model without QQ sends."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import urllib.request

from production_probe import load_plugin


def read_source(base, message_id):
    """Read exactly one existing message; no send endpoint or platform mutation exists here."""
    request=urllib.request.Request(base.rstrip('/')+'/get_msg',
        data=json.dumps({'message_id':message_id}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=30) as response:
        result=json.load(response)
    if result.get('retcode',0)!=0:raise RuntimeError('source_read_failed')
    return result.get('data',result)


async def probe(directory, base, message_id, output):
    """Use source metadata, native context and actual model; never invent image interpretation."""
    module=load_plugin(directory)
    from release_probe_plugin.nontext_context import describe_event
    from release_probe_plugin.message_batch import message_record
    from release_probe_plugin.conversation_context import system_prompt
    from release_probe_plugin.tool_context import ToolContextArchive
    source=await asyncio.to_thread(read_source,base,message_id)

    class Event:
        """Mirror a text-empty adapter event while retaining its real raw components."""
        def __init__(self):
            self.message_obj=SimpleNamespace(message_id=str(message_id),message=[],raw_message=source)

        def get_messages(self):
            """Raw source must recover metadata even if the normalized adapter omits it."""
            return []

        def get_message_str(self):
            """Nontext is not empty source, even though its text renderer returns empty."""
            return ''

        def get_sender_id(self):
            """Do not impersonate the original source actor in an isolated probe."""
            return 'probe'

        def get_sender_name(self):
            """Keep the probe speaker explicitly synthetic."""
            return 'probe'

    event=Event();context=describe_event(event)
    if not context.get('recognized'):raise RuntimeError('nontext_source_not_recognized')
    event._higan_nontext_context=context
    data=json.dumps({'source':'message_components','trust':'untrusted_data','segments':context['segments']},ensure_ascii=False)
    question='请只根据附带的真实消息段元数据，给出其 type 和 id，格式为 JSON。不猜测图片含义，不调用工具。'
    bot=module.HiganPlugin.__new__(module.HiganPlugin)
    bot.persona=module.HiganPlugin._load_persona()
    bot._tool_evidence_store=ToolContextArchive(Path(tempfile.mkdtemp(prefix='nontext_probe_',dir=output.parent)))
    bot._turn_context={'nontext-probe':{'history':(),'messages':[message_record(event,question+'\n'+data)],
                                     'clock':bot._clock_context(),'image_refs':[]}}
    prompt=bot._request_context(event,'nontext-probe',question,False)
    answer,calls=await bot._llm_tools(system_prompt(bot.persona),prompt,[],allow_tools=False)
    expected=context['segments'][0]
    correct=str(expected['type']) in answer and str(expected.get('id','')) in answer
    result={'message_id':str(message_id),'recognized':context['recognized'],'source_type':expected['type'],
            'source_id':str(expected.get('id','')),'segments':len(context['segments']),
            'invented_image_refs':len(context['image_refs']),'request_preserves_metadata':data in json.loads(prompt)['conversation']['messages'][0]['body'],
            'real_model_recognized_type_and_id':correct,'answer_chars':len(answer),'requested_tools':len(calls),
            'qq_messages_sent':0,'pixels_inspected':False}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if not correct:raise RuntimeError('model_source_verification_failed')


def main():
    """Require explicit candidate/source inputs with no implicit messaging destination."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--plugin',type=Path,required=True)
    parser.add_argument('--onebot-base',required=True)
    parser.add_argument('--message-id',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(probe(args.plugin,args.onebot_base,args.message_id,args.output))


if __name__=='__main__':main()
