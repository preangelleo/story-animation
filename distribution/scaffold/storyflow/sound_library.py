"""Project-owned exact-name sound cache; shared el remains the only provider boundary."""
from __future__ import annotations
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import sys
from .core import Store, WorkflowError, atomic_json, digest, read_json, utcnow
from .integrations import begin, finish


def normalize_name(value):
    if not isinstance(value,str) or not value.strip() or not re.fullmatch(r'[A-Za-z0-9 _-]+',value):
        raise WorkflowError('sound name: only ASCII letters/digits/spaces/underscore/hyphen; paths forbidden')
    name=re.sub(r'[ _-]+','_',value.strip().lower()).strip('_')
    if not name or len(name)>100: raise WorkflowError('sound name empty or too long')
    return name


def _sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def _base(root):
    root=Path(root).resolve(); base=root/'sound-effects'
    if base.is_symlink(): raise WorkflowError('sound-effects symlink forbidden')
    base.mkdir(exist_ok=True)
    for name in ('.locks','.requests'):
        p=base/name
        if p.is_symlink(): raise WorkflowError('sound library control symlink forbidden')
        p.mkdir(exist_ok=True)
    return base


def _cached(base,name):
    folder=base/name
    if not folder.exists() and not folder.is_symlink(): return None
    if folder.is_symlink() or not folder.is_dir(): raise WorkflowError('sound cache corrupt: unsafe asset directory')
    if any(p.is_symlink() for p in folder.iterdir()): raise WorkflowError('sound cache corrupt: symlink')
    meta=read_json(folder/'metadata.json'); envelope=read_json(folder/'metadata.sha256.json')
    if envelope!={'sha256':digest(folder/'metadata.json')}: raise WorkflowError('sound metadata SHA mismatch; no regeneration')
    if meta.get('schema')!='qy.sound.v1' or meta.get('name')!=name or meta.get('audio_file')!='original.mp3':
        raise WorkflowError('sound metadata corrupt; no regeneration')
    audio=folder/'original.mp3'
    if not audio.is_file() or digest(audio)!=meta.get('audio_sha256'): raise WorkflowError('sound audio SHA mismatch; no regeneration')
    return {'name':name,'status':'cached','path':str(audio),'metadata_path':str(folder/'metadata.json'),'metadata_sha256':digest(folder/'metadata.json'),**meta}


def lookup(root,name):
    name=normalize_name(name); value=_cached(_base(root),name)
    if value is None: raise WorkflowError('sound not found: '+name,404)
    return value


def _duration(path):
    result=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(path)],capture_output=True,text=True,check=False)
    if result.returncode: raise WorkflowError('generated sound is not decodable')
    seconds=float(json.loads(result.stdout)['format']['duration'])
    if not math.isfinite(seconds) or seconds<=0: raise WorkflowError('generated sound duration invalid')
    return seconds


PREVIEW_SCOPE='sound-library-preview'


def _library_begin(store,scope,name,inputs,estimated):
    if scope!=PREVIEW_SCOPE: raise WorkflowError('unapproved project sound budget scope')
    if isinstance(estimated,bool) or not isinstance(estimated,(int,float)) or not math.isfinite(estimated) or not 0<estimated<=.10:
        raise WorkflowError('preview per-sound reservation must be positive and <=0.10 USD')
    authority=store.root/'config/sound-library-approval.json'
    authority_sha=digest(authority)
    store.db.execute('CREATE TABLE IF NOT EXISTS library_operations(scope TEXT NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(scope,name))'); store.db.commit()
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        if store.db.execute('SELECT 1 FROM library_operations WHERE name=?',(name,)).fetchone(): raise WorkflowError('sound ledger exists; no resend',409)
        prefix='reservation:sound_library:'+scope+':'
        total=store.db.execute('SELECT COALESCE(SUM(amount),0) FROM costs WHERE category LIKE ?',(prefix+'%',)).fetchone()[0]
        month=store.db.execute('SELECT COALESCE(SUM(amount),0) FROM costs WHERE substr(created_at,1,7)=?',(utcnow()[:7],)).fetchone()[0]
        if total+estimated>.30+1e-9 or month+estimated>store.config['budget']['monthly_usd']: raise WorkflowError('project sound scope/month budget exceeded',409)
        proof={'scope':scope,'authority_prompt_sha256':authority_sha,'reason':'explicitly authorized reusable sound preview assets; no episode creation or existing episode budget modification','inputs':inputs,'estimated_usd_reserved':estimated,'actual_usd':None}
        store.db.execute('INSERT INTO costs(episode_id,amount,category,created_at) VALUES(NULL,?,?,?)',(estimated,prefix+name,utcnow()))
        store.db.execute('INSERT INTO library_operations VALUES(?,?,?,?)',(scope,name,'running',json.dumps(proof,ensure_ascii=False)))
    return authority_sha,proof


def _library_finish(store,scope,name,status,value):
    with store.db:
        store.db.execute('UPDATE library_operations SET status=?,data=? WHERE scope=? AND name=?',(status,json.dumps(value,ensure_ascii=False),scope,name))


def get_sound(root,name,*,category='sfx',prompt=None,seconds=1.0,episode=None,scope=None,execute=False,confirm_cost=False,estimated_usd=None,runner=None):
    """Exact normalized name wins. Existing corrupt/unknown state never calls provider."""
    name=normalize_name(name)
    if category not in ('sfx','bgm'): raise WorkflowError('category must be sfx or bgm')
    base=_base(root); lock=base/'.locks'/f'{name}.lock'
    if lock.is_symlink(): raise WorkflowError('sound lock symlink forbidden')
    fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'a+') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        hit=_cached(base,name)
        if hit:
            if hit['category']!=category: raise WorkflowError('sound category mismatch; use a distinct name')
            return hit
        request=base/'.requests'/f'{name}.json'
        if request.exists() or request.is_symlink(): raise WorkflowError('sound request already exists; unknown/running/done requires explicit reconciliation, never auto-resend',409)
        if not prompt or not prompt.strip(): raise WorkflowError('new sound requires original prompt')
        if isinstance(seconds,bool) or not isinstance(seconds,(float,int)) or not math.isfinite(seconds) or not ((.5<=seconds<=30) if category=='sfx' else (3<=seconds<=600)):
            raise WorkflowError('invalid sound duration: sfx 0.5–30s; bgm 3–600s')
        inputs={'name':name,'category':category,'original_prompt':prompt,'seconds':seconds,'model':'eleven_text_to_sound_v2' if category=='sfx' else 'music_v1'}
        if not execute: return {'status':'planned','inputs':inputs,'cache_root':'sound-effects','post_attempts':0}
        if bool(episode)==bool(scope) or not confirm_cost or estimated_usd is None: raise WorkflowError('generation requires exactly one episode/scope, execute, confirm-cost and estimated-usd')
        store=Store(root)
        try:
            kind='sound:'+name
            if scope:
                key,proof=_library_begin(store,scope,name,inputs,estimated_usd); old=None
            else:
                store.episode(episode)
                key,old=begin(store,episode,kind,inputs,estimated_usd); proof=None
            if old is not None: raise WorkflowError('ledger has completed sound but cache missing: reconcile, do not resend',409)
            receipt={'schema':'qy.sound.request.v1','status':'running','inputs':inputs,'episode_id':episode,'scope':scope,'scope_authority':proof,'operation_kind':kind,'operation_key':key,'created_at':utcnow(),'estimated_usd_reserved':estimated_usd,'actual_usd':None,'post_attempts':1,'confirmed_successful_posts':0}
            atomic_json(request,receipt)
            stage=Path(tempfile.mkdtemp(prefix='.'+name+'-',dir=base))
            output=stage/'original.mp3'
            argv=[sys.executable,str(Path(__file__).resolve().parents[1]/'adapters/el.py'),'sfx' if category=='sfx' else 'music',prompt,'--seconds',str(seconds),'-o',str(output)]
            if category=='sfx': argv+=['--influence','0.8']
            else: argv+=['--model','music_v1']
            receipt['argv']=argv; atomic_json(request,receipt)
            try:
                # Shared CLI has exactly one sound/music POST and no retries. Provider request-id unavailable for sfx CLI.
                result=(runner or subprocess.run)(argv,capture_output=True,text=True,timeout=600,check=False)
                if result.returncode: raise WorkflowError('shared el failed; result unknown, no automatic resend')
                if not output.is_file() or output.stat().st_size==0: raise WorkflowError('shared el missing output; result unknown')
                measured=_duration(output)
                receipt.update(status='done',confirmed_successful_posts=1,completed_at=utcnow(),stdout=result.stdout,stderr=result.stderr)
                meta={'schema':'qy.sound.v1',**inputs,'audio_file':'original.mp3','audio_sha256':digest(output),'duration_seconds':measured,'created_at':receipt['created_at'],'source':'ElevenLabs via existing el CLI','provider_request_id':None,'provider_request_id_note':'shared sfx/music CLI does not expose request id; exact argv and local operation key retained','source_request':receipt,'estimated_usd_reserved':estimated_usd,'actual_usd':None,'actual_usd_status':'unknown; reservation is not actual bill'}
                atomic_json(stage/'metadata.json',meta)
                atomic_json(stage/'metadata.sha256.json',{'sha256':digest(stage/'metadata.json')})
                for p in stage.iterdir():
                    with p.open('rb') as f: os.fsync(f.fileno())
                stage_fd=os.open(stage,os.O_RDONLY)
                try: os.fsync(stage_fd)
                finally: os.close(stage_fd)
                os.rename(stage,base/name)
                parent_fd=os.open(base,os.O_RDONLY)
                try: os.fsync(parent_fd)
                finally: os.close(parent_fd)
                atomic_json(request,receipt)
                if scope: _library_finish(store,scope,name,'done',meta)
                else: finish(store,episode,kind,key,'done',meta)
            except BaseException as exc:
                receipt.update(status='unknown',error_type=type(exc).__name__,partial_output=str(output),completed_at=utcnow())
                atomic_json(request,receipt)
                if scope: _library_finish(store,scope,name,'unknown',receipt)
                else: finish(store,episode,kind,key,'unknown',receipt)
                raise
            value=_cached(base,name); value['status']='generated'; return value
        finally: store.close()


def validate_cue_sheet(root,path):
    """Dry narration clock remains authoritative; cue ranges use source and final timeline separately."""
    data=read_json(path)
    if data.get('schema')!='qy.sound.cues.v1' or data.get('alignment_source')!='dry_narration': raise WorkflowError('cue schema/alignment source invalid')
    if not isinstance(data.get('cues'),list): raise WorkflowError('cue list required')
    for cue in data['cues']:
        if not isinstance(cue,dict): raise WorkflowError('cue must be an object')
        required=('scene_id','name','source_audio_in','source_audio_out','source_timeline_in','final_timeline_in','gain_db','fade_in_seconds','fade_out_seconds','ducking','audio_sha256')
        if any(k not in cue for k in required): raise WorkflowError('cue missing required field')
        if not isinstance(cue['scene_id'],str) or not cue['scene_id'].strip() or normalize_name(cue['name'])!=cue['name']: raise WorkflowError('cue scene/name invalid')
        asset=lookup(root,cue['name'])
        if asset['audio_sha256']!=cue['audio_sha256']: raise WorkflowError('cue source hash mismatch')
        values=[cue[k] for k in ('source_audio_in','source_audio_out','source_timeline_in','final_timeline_in','gain_db','fade_in_seconds','fade_out_seconds')]
        if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in values): raise WorkflowError('cue nonfinite timing/gain')
        if not 0<=cue['source_audio_in']<cue['source_audio_out']<=asset['duration_seconds'] or min(cue['source_timeline_in'],cue['final_timeline_in'],cue['fade_in_seconds'],cue['fade_out_seconds'])<0: raise WorkflowError('cue timing out of bounds')
        if cue['fade_in_seconds']+cue['fade_out_seconds']>cue['source_audio_out']-cue['source_audio_in']: raise WorkflowError('cue fades exceed duration')
        d=cue['ducking']
        if not isinstance(d,dict) or d.get('sidechain')!='dry_narration' or any(k not in d for k in ('attenuation_db','attack_seconds','release_seconds')): raise WorkflowError('cue ducking contract incomplete')
        for k in ('attenuation_db','attack_seconds','release_seconds'):
            v=d[k]
            if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v): raise WorkflowError('cue ducking invalid')
        if d['attenuation_db']>0 or min(d['attack_seconds'],d['release_seconds'])<0: raise WorkflowError('cue ducking bounds invalid')
    return {'ok':True,'schema':data['schema'],'cue_count':len(data.get('cues',[])),'cue_sheet_sha256':digest(path),'alignment_source':'dry_narration','mixed_episode':False}


def _command(args):
    if args.action=='show': return lookup(args.root,args.name)
    if args.action=='cue-validate': return validate_cue_sheet(args.root,args.file)
    return get_sound(args.root,args.name,category=args.category,prompt=args.prompt,seconds=args.seconds,episode=args.episode,scope=args.scope,execute=args.execute,confirm_cost=args.confirm_cost,estimated_usd=args.estimated_usd)


def register(subparsers):
    parser=subparsers.add_parser('sound',help='exact-name reusable SFX/BGM library')
    actions=parser.add_subparsers(dest='action',required=True)
    show=actions.add_parser('show'); show.add_argument('name'); show.set_defaults(handler=_command)
    cues=actions.add_parser('cue-validate'); cues.add_argument('--file',required=True); cues.set_defaults(handler=_command)
    get=actions.add_parser('get'); get.add_argument('name'); get.add_argument('--category',choices=('sfx','bgm'),default='sfx'); get.add_argument('--prompt'); get.add_argument('--seconds',type=float,default=1); get.add_argument('--episode'); get.add_argument('--scope'); get.add_argument('--execute',action='store_true'); get.add_argument('--confirm-cost',action='store_true'); get.add_argument('--estimated-usd',type=float); get.set_defaults(handler=_command)
