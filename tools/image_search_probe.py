"""Verify native image discovery on actual network without sending candidate images."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile

from production_probe import load_plugin


async def probe(directory,output,query):
    """Require real discovered candidates and page extraction; no injected search responses."""
    module=load_plugin(directory)
    root=Path(tempfile.mkdtemp(prefix='image_discovery_probe_',dir=output.parent))
    module.HIGAN_ROOT=str(root)
    bot=module.HiganPlugin.__new__(module.HiganPlugin)
    bot._turn_context={};bot._recent={}
    result=await bot._dispatch_tool('image_search',{'query':query,'limit':4},'probe',event=None)
    safe={'query':query,'native_tools':len(bot._tool_schemas()),'ok':result.get('ok'),
          'status':result.get('status'),'image_candidates':len(result.get('images') or []),
          'attempts':result.get('attempts'),'qq_messages_sent':0,
          'candidate_image_bytes_not_fetched':True,'semantic_match_not_asserted':True}
    # A source page is explicitly opened only for image metadata; neither image
    # search nor extraction constitutes visual verification or public delivery.
    images=result.get('images') or []
    pages=[item.get('source_page') for item in images if item.get('source_page')]
    if pages:
        page=await bot._dispatch_tool('page_images',{'url':pages[0],'limit':4},'probe',event=None)
        safe['page_images']={'ok':page.get('ok'),'count':len(page.get('images') or []),
                             'error':page.get('error'),'status':page.get('status')}
    safe['candidate_url_hashes']=[hashlib.sha256(item['image_url'].encode()).hexdigest() for item in images]
    output.write_text(json.dumps(safe,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(safe,ensure_ascii=False),flush=True)
    if not result.get('ok') or not images:raise RuntimeError('real_image_search_no_candidates')


def main():
    """Use explicit query only for this diagnostic, never as a production topic rule."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--plugin',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--query',required=True)
    args=parser.parse_args();asyncio.run(probe(args.plugin,args.output,args.query))


if __name__=='__main__':main()
