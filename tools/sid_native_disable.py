"""Disable the native sid handler using reviewed ORM and exact built-in plugin reload.

Fallback for installed command API's unsupported tool scope. No SQL writes, forged
JWT, service restart or business-plugin reload are used. Existing release-access
helper owns SQLite backups, hash/import checks and narrow legitimate credentials.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from release_access import backup_database
from release_apply import dashboard,command

HANDLER='astrbot.builtin_stars.builtin_commands.main_sid'
PLUGIN='builtin_commands'


async def apply(root,credential,backup,report):
    """Preserve prior config fields, persist enabled false, reload only a verified registered plugin."""
    # Native endpoint must resolve BEFORE any state mutation; never let the plugin
    # manager's missing-name fallback reload every plugin.
    registered=dashboard('/plugins/'+PLUGIN,credential)
    if not registered.get('data'):raise RuntimeError('exact_builtin_plugin_not_registered')
    pid=command(['systemctl','show','astrbot-186.service','-p','MainPID','--value'])
    backup_database(root/'data/data_v4.db',backup)
    sys.path.insert(0,str(root))
    from astrbot.core import db_helper
    # Existing DB schema is already used by the live service; prohibit migration.
    async def no_migration():
        """Prevent administrative import from initializing a parallel database schema."""
        raise RuntimeError('database_migration_forbidden')
    db_helper.inited=True;db_helper.initialize=no_migration
    try:
        if Path(db_helper.db_path).resolve()!=(root/'data/data_v4.db').resolve():raise RuntimeError('database_path_mismatch')
        existing=await db_helper.get_command_config(HANDLER)
        before=None if existing is None else bool(existing.enabled)
        kwargs={}
        if existing is not None:
            for name in ('resolved_command','keep_original_alias','conflict_key','resolution_strategy','note','extra_data'):
                kwargs[name]=getattr(existing,name)
        await db_helper.upsert_command_config(handler_full_name=HANDLER,plugin_name=PLUGIN,
            module_path='astrbot.builtin_stars.builtin_commands.main',original_command='sid',
            enabled=False,auto_managed=False,**kwargs)
        dashboard('/plugins/'+PLUGIN+'/reload',credential,payload={})
        if not dashboard('/plugins/'+PLUGIN,credential).get('data'):raise RuntimeError('builtin_plugin_missing_after_reload')
        current=await db_helper.get_command_config(HANDLER)
        if current is None or current.enabled:raise RuntimeError('sid_disable_not_persistent')
        if command(['systemctl','show','astrbot-186.service','-p','MainPID','--value'])!=pid:raise RuntimeError('unexpected_service_restart')
        result={'command':'sid','handler_full_name':HANDLER,'enabled':False,
                'previous_enabled':before,'plugin_reloaded':PLUGIN,'service_pid':pid,
                'service_restart':False,'business_plugin_reloaded':False,
                'native_database_configuration':True,'qq_messages_sent':0}
        report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False),flush=True)
    finally:await db_helper.engine.dispose()


def main():
    """Require explicit installed source/data root and narrow legitimate key file."""
    parser=argparse.ArgumentParser()
    for name in ('root','credential','backup','report'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(apply(args.root,args.credential,args.backup,args.report))


if __name__=='__main__':main()
