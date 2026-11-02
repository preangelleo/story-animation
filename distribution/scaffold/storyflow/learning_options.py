"""Optional public feature configuration; no account setup or network side effects."""
import copy,json
from pathlib import Path
from .core import config,atomic_json,WorkflowError
MODULES=('sql','vector','comments','reply','topics','dedup')
def validate(value):
    if not isinstance(value,dict): raise WorkflowError('learning must be an object')
    for name in ('vector','comments','topics','dedup'):
        if not isinstance(value.get(name,{}),dict): raise WorkflowError(name+' must be an object')
    if 'enabled' in value and type(value['enabled']) is not bool:raise WorkflowError('enabled must be boolean')
    for name in ('vector','comments','topics','dedup'):
        section=value.get(name,{})
        for key in ('enabled','auto_reply'):
            if key in section and type(section[key]) is not bool:raise WorkflowError(name+'.'+key+' must be boolean')
    for section,key,limit in (('vector','batch_size',100),('comments','lookback_hours',744),('comments','catchup_days',365),('comments','max_pages',100)):
        number=value.get(section,{}).get(key)
        if number is not None and (type(number) is not int or not 1<=number<=limit):raise WorkflowError(section+'.'+key+' out of range')
    dimensions=value.get('vector',{}).get('dimensions')
    if dimensions is not None and (type(dimensions) is not int or dimensions<1):raise WorkflowError('positive embedding dimensions required')
    if not value.get('enabled') and any([value.get('vector',{}).get('enabled'),value.get('comments',{}).get('enabled'),value.get('topics',{}).get('enabled'),value.get('dedup',{}).get('enabled')]):
        raise WorkflowError('Optional learning modules require SQL fact memory; enable sql first')
    if value.get('comments',{}).get('auto_reply') and not value.get('comments',{}).get('enabled'):
        raise WorkflowError('reply requires comments intake enabled')
    return value

def status(root):
    cfg=config(root);v=validate(cfg.get('learning',{}));vector=v.get('vector',{})
    semantic=bool(v.get('enabled') and vector.get('enabled') and vector.get('endpoint') and vector.get('model'))
    return {'ok':True,'learning':v,'semantic_configured':semantic,'semantic_verified':False,
            'work_ledger':'always SQLite; optional sql means long-term accepted-script facts',
            'degradation':{'sql':'no cross-episode accepted-script fact memory' if not v.get('enabled') else 'versioned facts; vectors may be pending',
            'vector':'lexical/SQL only; no semantic recall claim' if not semantic else 'configured; actual provider response required',
            'comments':'no audience intake' if not v.get('comments',{}).get('enabled') else 'read only unless reply explicitly enabled',
            'reply':'preview only' if not v.get('comments',{}).get('auto_reply') else 'explicitly enabled; safety and unknown ledger still apply',
            'topics':'manual frozen plan only' if not v.get('topics',{}).get('enabled') else 'feedback supplements frozen plan',
            'dedup':'manual editorial duplicate check' if not v.get('dedup',{}).get('enabled') else 'independent compact SQL/vector adviser'},'paid_calls':0}

def configure(args):
    path=Path(args.root)/'documentation/configuration.json';cfg=json.loads(path.read_text());v=copy.deepcopy(cfg.get('learning',{}))
    selected=set(filter(None,args.enable.split(',')))
    if selected-set(MODULES): raise WorkflowError('Unknown module: '+','.join(sorted(selected-set(MODULES))))
    v['enabled']='sql' in selected
    for n in ('vector','topics','dedup'):v.setdefault(n,{})['enabled']=n in selected
    v.setdefault('comments',{})['enabled']='comments' in selected
    v['comments']['auto_reply']='reply' in selected
    if args.embedding_endpoint:v['vector']['endpoint']=args.embedding_endpoint
    if args.embedding_model:v['vector']['model']=args.embedding_model
    if args.embedding_dimensions:v['vector']['dimensions']=args.embedding_dimensions
    validate(v)
    cfg['learning']=v
    if args.author_provider:cfg['author']={'provider':args.author_provider,'network_tools':False}
    if args.execute:atomic_json(path,cfg)
    return {'ok':True,'execute':args.execute,'configuration':cfg,'message':'Preview; add --execute to save' if not args.execute else 'Saved; no provider calls, replies, uploads or timers started'}

def guard(args):
    if args.command!='learning':return
    action=getattr(args,'learning_action',getattr(args,'action',''))
    v=validate(config(args.root).get('learning',{}))
    if action in ('status','capabilities'):return
    if not v.get('enabled'):raise WorkflowError('SQL learning disabled; use modules configure --enable sql --execute')
    if action in ('index',) and not v.get('vector',{}).get('enabled'):raise WorkflowError('Semantic index disabled; SQL/lexical search remains available')
    for command,key in (('comments','comments'),('feedback-packet','comments'),('assess','comments'),('reply','comments'),('reconcile-reply','comments'),('topics','topics'),('topic-state','topics'),('dedup','dedup')):
        if action==command and not v.get(key,{}).get('enabled'):raise WorkflowError(command+' module disabled')
    if action=='reply' and getattr(args,'execute',False) and not v.get('comments',{}).get('auto_reply'):raise WorkflowError('Reply preview only; auto_reply is disabled')

def register(sub):
    p=sub.add_parser('modules');a=p.add_subparsers(dest='modules_action',required=True)
    q=a.add_parser('status');q.set_defaults(handler=lambda x:status(x.root))
    q=a.add_parser('configure');q.add_argument('--enable',default='',help='Comma-separated sql,vector,comments,reply,topics,dedup; default all off')
    q.add_argument('--embedding-endpoint');q.add_argument('--embedding-model');q.add_argument('--embedding-dimensions',type=int);q.add_argument('--author-provider',help='Configured harness/provider, e.g. codex, claude, ark or custom')
    q.add_argument('--execute',action='store_true');q.set_defaults(handler=configure)
