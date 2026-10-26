"""Read one actual forwarded source through candidate admission and real model without QQ sends."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import urllib.request

from production_probe import load_plugin


async def probe(directory, base, message_id, output):
    """Keep source body/URLs private; emit only source counts and exact-evidence checks."""
    module=load_plugin(directory)
    from release_probe_plugin.tool_context import ToolContextArchive

    def read(action, params):
        """Only reviewed OneBot read actions are permitted, never platform sends."""
        if action not in ('get_msg','get_forward_msg'):
            raise RuntimeError('readonly_probe_boundary')
        request=urllib.request.Request(base.rstrip('/')+'/'+action,data=json.dumps(params).encode(),
                                       headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=30) as response:
            return json.load(response)

    raw=await asyncio.to_thread(read,'get_msg',{'message_id':message_id})
    data=raw.get('data',raw)
    if raw.get('retcode',0)!=0 or not isinstance(data,dict):
        raise RuntimeError('source_read_failed')

    class Event:
        """Use the actual raw message with a lossy normalized Forward like the real adapter."""
        def __init__(self):
            self.message_obj=SimpleNamespace(message_id=str(message_id),message=[],raw_message=data,
                                             sender=SimpleNamespace(user_id='probe',nickname='probe'))
            for item in data.get('message') or []:
                if item.get('type')=='forward':
                    self.message_obj.message.append(SimpleNamespace(type='Forward',id=item['data'].get('id')))
            self.bot=SimpleNamespace(api=SimpleNamespace(call_action=self.call_action))

        async def call_action(self, action, **params):
            """Resolve only existing source messages through the real read-only platform API."""
            return await asyncio.to_thread(read,action,params)

        def get_messages(self):
            """Expose the same normalized components used by admission."""
            return self.message_obj.message

        def get_message_str(self):
            """A forwarded card has no rendered prose in the framework."""
            return ''

        def get_sender_id(self):
            """Use an isolated unprivileged source actor in model context."""
            return 'probe'

        def get_sender_name(self):
            """Keep real participant identities inside the source records, not test actor labels."""
            return 'probe'

    bot=module.HiganPlugin.__new__(module.HiganPlugin)
    bot.persona=module.HiganPlugin._load_persona()
    bot._turn_context={}
    bot._tool_evidence_store=ToolContextArchive(Path(tempfile.mkdtemp(prefix='forward_read_',dir=output.parent)))
    event=Event()
    source=await bot._forward_to_text(event)
    resolved=json.loads(source)['result']
    if not resolved.get('ok') or not resolved.get('items'):
        raise RuntimeError('candidate_source_unreadable')
    question='请读取这份转发聊天记录，原样引用其中一条非空的文字消息；只需引用原文，不调用工具。'
    from release_probe_plugin.message_batch import message_record
    bot._turn_context['forward-read-probe']={'history':(), 'messages':[message_record(event,question+'\n'+source)],
                                            'clock':bot._clock_context(),'image_refs':[]}
    context=bot._request_context(event,'forward-read-probe',question,False)
    from release_probe_plugin.conversation_context import system_prompt
    answer,calls=await bot._llm_tools(system_prompt(bot.persona),context,[],allow_tools=False)
    texts=[item['text'].strip() for item in resolved['items'] if item.get('text','').strip()]
    matched=[text for text in texts if len(text)>=2 and text in answer]
    result={'message_id':str(message_id),'source_ok':resolved['ok'],'leaf_items':len(resolved['items']),
            'text_items':len(texts),'image_metadata_items':sum(m.get('type')=='image' for item in resolved['items'] for m in item.get('media',[])),
            'request_preserves_all_texts':all(text in context for text in texts),
            'real_model_answer_chars':len(answer),'real_model_exact_source_quote':bool(matched),
            'matching_source_texts':len(matched),'requested_tools':len(calls),'qq_messages_sent':0,
            'all_image_pixels_inspected':False}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if not matched:raise RuntimeError('real_model_did_not_quote_source')


def main():
    """Require explicit source endpoint and ID; do not create policy defaults or send capabilities."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--plugin',type=Path,required=True)
    parser.add_argument('--onebot-base',required=True)
    parser.add_argument('--message-id',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(probe(args.plugin,args.onebot_base,args.message_id,args.output))


if __name__=='__main__':main()
