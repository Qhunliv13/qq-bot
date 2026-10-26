"""Verify real file metadata reaches the model without fetching or executing file contents."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

from nontext_read_probe import read_source
from production_probe import load_plugin


async def probe(directory, base, message_id, output):
    """Use only the actual existing native file segment and the deployed context/transport."""
    module=load_plugin(directory)
    from release_probe_plugin.nontext_context import describe_event
    from release_probe_plugin.message_batch import message_record
    from release_probe_plugin.conversation_context import system_prompt
    from release_probe_plugin.tool_context import ToolContextArchive
    raw=await asyncio.to_thread(read_source,base,message_id)

    class Event:
        """Preserve a real file source while keeping the diagnostic actor explicitly synthetic."""
        def __init__(self):
            self.message_obj=SimpleNamespace(message_id=str(message_id),message=[],raw_message=raw)

        def get_messages(self):
            """Exercise metadata recovery when canonical components do not render text."""
            return []

        def get_sender_id(self):
            """Do not impersonate any real source user for diagnostic requests."""
            return 'probe'

        def get_sender_name(self):
            """Identify this isolated request as a probe, not a QQ reply opportunity."""
            return 'probe'

    event=Event();context=describe_event(event)
    files=[item for item in context['segments'] if item['type']=='file']
    if len(files)!=1:raise RuntimeError('expected_single_real_file_source')
    file=files[0];name=file.get('file') or file.get('name') or file.get('file_name')
    size=file.get('file_size') or file.get('size')
    if not isinstance(name,str) or not size:raise RuntimeError('file_metadata_incomplete')
    event._higan_nontext_context=context
    question='只根据消息段元数据，返回 JSON，字段 filename 和 bytes 为原始文件名称和字节数。不打开或下载文件，不调用工具。'
    data=json.dumps({'source':'message_components','trust':'untrusted_data','segments':context['segments']},ensure_ascii=False)
    bot=module.HiganPlugin.__new__(module.HiganPlugin)
    bot.persona=module.HiganPlugin._load_persona()
    bot._tool_evidence_store=ToolContextArchive(Path(tempfile.mkdtemp(prefix='file_source_probe_',dir=output.parent)))
    bot._turn_context={'file-source-probe':{'history':(),'messages':[message_record(event,question+'\n'+data)],
                                          'clock':bot._clock_context(),'image_refs':[],'image_sources':[]}}
    prompt=bot._request_context(event,'file-source-probe',question,False)
    answer,calls=await bot._llm_tools(system_prompt(bot.persona),prompt,[],allow_tools=False)
    recognized=name in answer and str(size) in answer
    result={'message_id':str(message_id),'recognized_file':context['recognized'],'source_type':'file',
            'source_bytes':int(size),'file_name_preserved':name in prompt,
            'real_model_recognized_filename_and_bytes':recognized,'image_refs':len(context['image_refs']),
            'requested_tools':len(calls),'qq_messages_sent':0,'file_downloaded':False,'html_executed':False,
            'diagnostic_not_natural_reply_guarantee':True}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if not recognized:raise RuntimeError('file_source_model_verification_failed')


def main():
    """Require explicit existing source ID and deployed source directory."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--plugin',type=Path,required=True)
    parser.add_argument('--onebot-base',required=True)
    parser.add_argument('--message-id',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(probe(args.plugin,args.onebot_base,args.message_id,args.output))


if __name__=='__main__':main()
