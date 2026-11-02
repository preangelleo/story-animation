"""所有终端输出为 JSON，失败退出非零。"""
import argparse
import importlib
import json
from pathlib import Path
import sqlite3
import sys
from .core import Store, WorkflowError, KINDS, schedule, config

class JsonParser(argparse.ArgumentParser):
    def error(self,message): raise WorkflowError(message)

def parser():
    p=JsonParser(prog='qy',description='Story Animation独立故事工作流')
    p.add_argument('--root',default=str(Path(__file__).resolve().parents[1]))
    s=p.add_subparsers(dest='command',required=True)
    s.add_parser('init'); s.add_parser('status')
    q=s.add_parser('schedule'); q.add_argument('--production-date',required=True)
    q=s.add_parser('idea'); a=q.add_subparsers(dest='action',required=True)
    z=a.add_parser('add'); z.add_argument('--title',required=True); z.add_argument('--body',default='')
    a.add_parser('list'); z=a.add_parser('use'); z.add_argument('id'); z.add_argument('--episode',required=True)
    q=s.add_parser('episode'); a=q.add_subparsers(dest='action',required=True)
    z=a.add_parser('create'); z.add_argument('--production-date',required=True); z.add_argument('--title',required=True); z.add_argument('--series'); z.add_argument('--style-index'); z.add_argument('--style-file'); z.add_argument('--mapping-file'); z.add_argument('--classification-evidence',help='Content classification reason; required for explicit other'); z.add_argument('--id'); z.add_argument('--author',default='writer'); z.add_argument('--verification-only',action='store_true'); z.add_argument('--review-only',action='store_true')
    a.add_parser('list'); z=a.add_parser('show'); z.add_argument('episode')
    z=a.add_parser('withdraw'); z.add_argument('episode'); z.add_argument('--reason',required=True)
    q=s.add_parser('artifact'); a=q.add_subparsers(dest='action',required=True); z=a.add_parser('register'); z.add_argument('episode'); z.add_argument('--kind',required=True,choices=KINDS); z.add_argument('--file',required=True)
    for name in ('review','publication','metrics'):
        q=s.add_parser(name); a=q.add_subparsers(dest='action',required=True); z=a.add_parser('record'); z.add_argument('episode'); z.add_argument('--file',required=True)
    for name in ('outline-review','media-review'):
        q=s.add_parser(name); q.add_argument('episode'); q.add_argument('--file',required=True)
    q=s.add_parser('qa'); q.add_argument('episode'); q.add_argument('--step',choices=('outline','script','review','assets'),required=True)
    q=s.add_parser('validate'); q.add_argument('episode')
    q=s.add_parser('workflow'); a=q.add_subparsers(dest='action',required=True); z=a.add_parser('next'); z.add_argument('episode')
    q=s.add_parser('claims'); a=q.add_subparsers(dest='action',required=True)
    for name in ('acquire','release'):
        z=a.add_parser(name); z.add_argument('episode'); z.add_argument('--owner',required=True)
        if name=='acquire': z.add_argument('--ttl',type=int,default=3600)
    q=s.add_parser('cost'); a=q.add_subparsers(dest='action',required=True); z=a.add_parser('record'); z.add_argument('episode'); z.add_argument('--amount',type=float,required=True); z.add_argument('--category',required=True)
    q=s.add_parser('search'); q.add_argument('--query',required=True)
    q=s.add_parser('memory'); a=q.add_subparsers(dest='action',required=True); z=a.add_parser('add'); z.add_argument('--kind',required=True,choices=('source','character','world','plot','lesson')); z.add_argument('--file',required=True)
    z=a.add_parser('list'); z.add_argument('--kind'); z=a.add_parser('show'); z.add_argument('id'); z.add_argument('--version',type=int)
    q=s.add_parser('vector'); a=q.add_subparsers(dest='action',required=True); z=a.add_parser('import'); z.add_argument('--file',required=True); z=a.add_parser('search'); z.add_argument('--query-file',required=True); a.add_parser('status')
    for name in ('integrations','production','sound_library','chapters','research_contract','styles_cli','voice_rewrite','portable','captions_multilingual','pilot_release','first_frame','learning','learning_options','author_route'):
        try: module=importlib.import_module('storyflow.'+name)
        except ModuleNotFoundError as e:
            if e.name!='storyflow.'+name: raise
        else: module.register(s)
    return p

def run(args):
    from .learning_options import guard
    guard(args)
    if hasattr(args,'handler'): return args.handler(args)
    if args.command=='schedule':
        cfg=config(args.root);dates=schedule(args.production_date,cfg)
        if cfg.get('style_registry_file'):
            from .style_registry import resolve_style
            dates['default_style_resolution']=resolve_style(args.root,release_date=dates['scheduled_at'])
        return dates
    store=Store(args.root)
    try:
        c=args.command; a=getattr(args,'action',None)
        if c=='init': return {'initialized':True,'database':str(store.state/'story.sqlite3'),'channel':store.channel()}
        if c=='status': return store.status()
        if c=='idea':
            if a=='add': return store.idea_add(args.title,args.body)
            if a=='list': return store.idea_list()
            return store.idea_use(args.id,args.episode)
        if c=='episode':
            if a=='withdraw': return store.withdraw(args.episode,args.reason)
            if a=='create': return store.create(args.production_date,args.title,args.series,args.id,args.author,args.verification_only,args.review_only,args.style_index,args.style_file,args.mapping_file,args.classification_evidence)
            if a=='list': return store.episodes()
            return {**store.episode(args.episode),'artifacts':store.artifacts(args.episode),'workflow':store.next(args.episode)}
        if c=='artifact': return store.register(args.episode,args.kind,args.file)
        if c=='review': return store.record_review(args.episode,args.file)
        if c=='outline-review': return store.outline_review(args.episode,args.file)
        if c=='media-review': return store.media_review(args.episode,args.file)
        if c=='qa': return store.qa_step(args.episode,args.step)
        if c=='validate': return store.validate(args.episode)
        if c=='workflow': return store.next(args.episode)
        if c=='claims': return store.acquire(args.episode,args.owner,args.ttl) if a=='acquire' else store.release(args.episode,args.owner)
        if c=='publication':
            from .integrations import import_publication
            return import_publication(args)
        if c=='metrics': return store.metric(args.episode,args.file)
        if c=='cost': return store.cost(args.episode,args.amount,args.category)
        if c=='search': return store.search(args.query)
        if c=='memory':
            if a=='list': return store.memory_list(args.kind)
            if a=='show': return store.memory_show(args.id,args.version)
            return store.memory_add(args.kind,args.file)
        if c=='vector' and a=='status': return store.vector_status()
        if c=='vector': return store.vector_import(args.file) if a=='import' else store.vector_search(args.query_file)
        raise WorkflowError('未知命令')
    finally: store.close()

def main(argv=None):
    try:
        value=run(parser().parse_args(argv))
        print(json.dumps(value,ensure_ascii=False,allow_nan=False))
        return 1 if isinstance(value,dict) and (value.get('passed') is False or value.get('ok') is False) else 0
    except (WorkflowError,OSError,sqlite3.Error,ValueError,AssertionError,KeyError,RuntimeError) as e:
        print(json.dumps({'error':str(e),'code':getattr(e,'code',400)},ensure_ascii=False))
        return 1
if __name__=='__main__': sys.exit(main())
