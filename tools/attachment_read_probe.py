"""Validate actual image publisher attribution without platform sends or persona edits."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import urllib.request

from production_probe import load_plugin


async def probe(directory, base, message_id, output):
    """Use actual source data and the real visual transport, exposing only safe proof flags."""
    module=load_plugin(directory)
    from release_probe_plugin.nontext_context import describe_event
    from release_probe_plugin.conversation_context import system_prompt
    from release_probe_plugin.tool_context import ToolContextArchive

    def read():
        """Fetch exactly the existing incident message, never a send or mutation action."""
        request=urllib.request.Request(base.rstrip('/')+'/get_msg',data=json.dumps({'message_id':message_id}).encode(),
                                       headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=30) as response:result=json.load(response)
        if result.get('retcode',0)!=0:raise RuntimeError('source_read_failed')
        return result.get('data',result)

    raw=await asyncio.to_thread(read)

    class Event:
        """Retain actual publisher IDs solely within the private test context."""
        def __init__(self):
            self.message_obj=SimpleNamespace(message_id=str(message_id),message=[],raw_message=raw,
                                             sender=raw.get('sender',{}))

        def get_messages(self):
            """Raw image metadata recovers adapter-independent image sources."""
            return []

        def get_message_str(self):
            """The incident is image-only, not an invented prose request."""
            return ''

        def get_sender_id(self):
            """Use the real source publisher for configured role lookup."""
            return str(raw.get('sender',{}).get('user_id') or raw.get('user_id') or '')

        def get_sender_name(self):
            """Keep the publisher's supplied display metadata distinct from identity."""
            return str(raw.get('sender',{}).get('nickname') or '')

    event=Event();event._higan_nontext_context=describe_event(event)
    bot=module.HiganPlugin.__new__(module.HiganPlugin)
    bot.persona=module.HiganPlugin._load_persona()
    bot._turn_context={}
    bot._tool_evidence_store=ToolContextArchive(Path(tempfile.mkdtemp(prefix='attachment_probe_',dir=output.parent)))
    question='只根据消息发布者的结构化身份，返回 JSON：{"publisher_role":"实际 role 值"}。不要将图片中的账户或成绩归属当作发布者身份，不调用工具。'
    prompt=bot._request_context(event,'attachment-source-probe',question,True)
    envelope=json.loads(prompt);origins=envelope.get('attachments',{}).get('origins',[])
    if not origins:raise RuntimeError('image_provenance_missing')
    role=origins[0]['source']['sender']['role']
    answer,calls=await bot._llm_tools(system_prompt(bot.persona),prompt,bot._image_refs(event),allow_tools=False)
    recognized=role in answer
    result={'message_id':str(message_id),'actual_publisher_is_owner':event.get_sender_id() in module.OWNER_QQS,
            'source_role':role,'source_records':len(origins),'image_refs':len(bot._image_refs(event)),
            'all_origins_bound_to_actual_publisher':all(item['source']['sender']['id']==event.get_sender_id() for item in origins),
            'real_model_identified_publisher_role':recognized,'answer_chars':len(answer),'requested_tools':len(calls),
            'qq_messages_sent':0,'score_ownership_inferred':False,'future_wording_guaranteed':False}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if not recognized:raise RuntimeError('publisher_role_not_verified')


def main():
    """Require explicit source/candidate paths, with no implicit messaging destination."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--plugin',type=Path,required=True)
    parser.add_argument('--onebot-base',required=True)
    parser.add_argument('--message-id',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(probe(args.plugin,args.onebot_base,args.message_id,args.output))


if __name__=='__main__':main()
