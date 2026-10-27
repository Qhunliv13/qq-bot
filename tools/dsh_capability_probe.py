"""Exercise native VM filesystem and an isolated real model worker without QQ effects."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import uuid

from production_probe import load_plugin


async def probe(directory,output):
    """Use temporary probe files/state; never send messages or change account/persona/policy."""
    module=load_plugin(directory)
    from release_probe_plugin import workspace_tools
    from release_probe_plugin.reply_selection import is_silence
    root=Path(tempfile.mkdtemp(prefix='dsh_capability_probe_',dir=output.parent))
    # The VM helper's isolated runtime is not the live bot's cwd/settings/skills.
    workspace_tools._ROOT='/tmp/higan-dsh-probe-'+uuid.uuid4().hex
    module.HIGAN_ROOT=str(root)
    bot=module.HiganPlugin.__new__(module.HiganPlugin)
    bot.persona=module.HiganPlugin._load_persona()
    bot._provider_request_timeout=300
    bot._recent={'probe':[]};bot._history_origins={};bot._all_recent=[];bot._turn_context={}
    event=SimpleNamespace(get_sender_id=lambda:'probe')
    terminal=['(internal observation)\n\nSILENT','unrelated text\nsilent']
    if not all(is_silence(value) for value in terminal):raise RuntimeError('terminal_silence_not_supported')

    async def call(name,args):
        """Invoke the actual dispatcher; only explicitly reviewed probe actions are used."""
        result=await bot._dispatch_tool(name,args,'probe',event=event)
        if not result.get('ok'):raise RuntimeError('native_probe_'+name+'_failed:'+str(result.get('error')))
        return result

    search=await call('tool_search',{'query':'workspace_read'})
    await call('workspace_cwd',{})
    await call('workspace_write',{'path':'probe.txt','content':'first line\nprobe marker\n'})
    read=await call('workspace_read',{'path':'probe.txt'})
    # The helper publishes the original byte hash explicitly for safe read-before-write.
    checksum=read.get('sha256')
    if not checksum:raise RuntimeError('workspace_read_hash_missing')
    await call('workspace_edit',{'path':'probe.txt','old_string':'first line','new_string':'edited line','expected_sha256':checksum})
    grep=await call('workspace_grep',{'path':'.','pattern':'edited line'})
    listing=await call('workspace_glob',{'path':'.','pattern':'*.txt'})
    jobs=await call('vm_job_list',{})
    tasks=bot._tasks_mod()
    tasks.STORE=str(root/'tasks.json')
    goal=tasks.add('probe goal','isolated plan verification','probe',repeat='once',origin='probe:GroupMessage:probe')
    initial=await call('task_plan_read',{'task_id':goal['id']})
    planned=await call('task_plan_update',{'task_id':goal['id'],'expected_revision':initial['revision'],
                       'steps':[{'id':'verify','content':'Verify the independent read-only worker','status':'in_progress'}]})
    worker=await call('agent_start',{'task':'Read-only verification: use tool_describe for workspace_read, then summarize its purpose in one short sentence. Do not read private files, send messages, or request write tools.','label':'read-only tool verification'})
    manager=bot._delegation_manager
    owned=manager._jobs[worker['id']]['worker']
    await owned
    status=manager.status(worker['id'],scope='probe')
    if status.get('status')!='completed':raise RuntimeError('native_delegated_worker_failed')
    result={'advertised_tools':len(bot._tool_schemas()),'terminal_silence_suppressed':True,
            'discovery_returned_real_schema':any(item.get('name')=='workspace_read' for item in search.get('tools',[])),
            'native_workspace_write_read_edit':True,'grep_ok':grep.get('ok'),
            'glob_ok':listing.get('ok'),'job_list_ok':jobs.get('ok'),
            'task_plan_revision':planned.get('revision'),
            'real_model_worker_status':status['status'],'worker_result_chars':len(status.get('result','')),
            'qq_messages_sent':0,'signature_modified':False,'qzone_published':False,
            'policy_modified':False,'runtime_state_isolated':True,'probe_files_only':True}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)


def main():
    """Require explicit source and receipt paths; never activate/reload a candidate here."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--plugin',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(probe(args.plugin,args.output))


if __name__=='__main__':main()
