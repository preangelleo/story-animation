"""Reviewed local prose revision: one complete paragraph, preserved source and PCM map."""
import fcntl
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
import wave
from .core import Store, WorkflowError, CHECKS, read_json, digest, atomic_json
from . import integrations as it


def text_sha(text): return hashlib.sha256(text.encode()).hexdigest()


def decode_pcm(source,out):
    it.run(['ffmpeg','-v','error','-nostdin','-y','-i',str(source),'-map','0:a:0','-ar','44100','-ac','1','-c:a','pcm_s16le',str(out)])
    with wave.open(str(out)) as audio: return audio.getnframes()/audio.getframerate()


def candidate_review(store,ep,file,new_sha):
    value=read_json(file); arts=store.artifacts(ep)
    if value.get('episode_id')!=ep or value.get('verdict')!='pass': raise WorkflowError('replacement script needs passing independent review')
    reviewer=value.get('reviewer')
    if not isinstance(reviewer,str) or not reviewer.strip() or reviewer.strip().casefold()==store.episode(ep)['author'].strip().casefold(): raise WorkflowError('replacement script reviewer must be independent')
    for kind in ('outline','structure'):
        if value.get(kind+'_sha256')!=arts[kind]['sha256']: raise WorkflowError('replacement review '+kind+' mismatch')
    if value.get('script_sha256')!=new_sha: raise WorkflowError('replacement review script mismatch')
    for key in CHECKS:
        check=value.get('checks',{}).get(key,{})
        if check.get('status') not in ('pass','na') or not isinstance(check.get('reason'),str) or not check['reason'].strip(): raise WorkflowError('replacement review missing '+key)
    return digest(file)


def context(store,args):
    inputs,old,arts,voice=it.patch_context(store,args.episode,args.patch_id,args.start,args.end)
    script=Path(args.replacement_script).expanduser().resolve(); replacement=Path(args.replacement_text).expanduser().resolve()
    if any(not p.is_relative_to(store.root) or not p.is_file() for p in (script,replacement)): raise WorkflowError('replacement input must be a project file')
    new=script.read_text().strip(); target=replacement.read_text().strip(); source=it.artifact_path(store,arts,'script').read_text().strip()
    if not target or len(target)>1200 or target[-1] not in '。！？；' or new!=source[:args.start]+target+source[args.end:]: raise WorkflowError('new script must replace exactly the selected complete paragraph')
    if target==old: raise WorkflowError('rewrite needs a real prose change; use voice-patch for unchanged text')
    review_sha=candidate_review(store,args.episode,args.script_review_file,digest(script))
    # Optional cuts may only extend into the immediate neighbouring silence gap.
    pair={'script_sha256':arts['script']['sha256'],'audio_sha256':arts['audio']['sha256']}
    chars=read_json(it.folder(store,args.episode)/'align'/it.fingerprint(pair)/'alignment.json')['characters']
    pos=0; before=0; after=store._probe(it.artifact_path(store,arts,'audio'),'audio')['duration']
    for c in chars:
        nxt=pos+len(c['text'])
        if any(x.isalnum() for x in c['text']):
            if nxt<=args.start: before=max(before,c['end'])
            if pos>=args.end: after=c['start']; break
        pos=nxt
    start=inputs['cut_start'] if args.cut_start is None else args.cut_start
    end=inputs['cut_end'] if args.cut_end is None else args.cut_end
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in (start,end)) or not before<=start<=inputs['cut_start']<inputs['cut_end']<=end<=after: raise WorkflowError('rewrite cuts must preserve selected phonemes and neighbouring speech')
    inputs.update(source_script_sha256=inputs['script_sha256'],script_sha256=digest(script),old_text_sha256=inputs['text_sha256'],text_sha256=text_sha(target),script_review_sha256=review_sha,cut_start=start,cut_end=end)
    return inputs,target,arts,voice,script,replacement


def execute(args):
    if args.execute:
        from .revision_gate import check_revision
        check_revision(args.root,args.episode)
    store=Store(args.root)
    try:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',args.patch_id): raise WorkflowError('invalid patch-id')
        with it.voice_lock_path(store,args.episode).open('a') as lock:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise WorkflowError('episode voice worker active',409) from None
            try: return apply(store,args) if args.apply else generate(store,args)
            finally: fcntl.flock(lock,fcntl.LOCK_UN)
    finally: store.close()


def generate(store,args):
    ep=args.episode; inputs,target,arts,voice,script,replacement=context(store,args); key=it.fingerprint(inputs)
    kind='voice-rewrite-generate-'+args.patch_id; work=it.folder(store,ep)/'voice-rewrites'/args.patch_id/key; out=work/'patch.mp3'
    plan={'episode_id':ep,'mode':'plan','inputs':inputs,'text':target,'output':str(out)}
    if not args.execute: return plan
    it.paid_options(args); it.operations(store)
    rows=store.db.execute('SELECT inputs_hash FROM external_operations WHERE episode_id=? AND kind=?',(ep,kind)).fetchall()
    if any(r['inputs_hash']!=key for r in rows): raise WorkflowError('rewrite id already binds different inputs',409)
    key,prior=it.begin(store,ep,kind,inputs,args.estimated_usd)
    if prior is not None:
        if digest(out)!=prior['audio_sha256']: raise WorkflowError('rewrite generated audio drift')
        return {'idempotent':True,**prior}
    try:
        work.mkdir(parents=True,exist_ok=False)
        for name,path in {'old-script.txt':it.artifact_path(store,arts,'script'),'new-script.txt':script,'replacement.txt':replacement,'script-review.json':Path(args.script_review_file),'source.voice.binding.json':it.folder(store,ep)/'voice.binding.json','source.alignment.binding.json':it.folder(store,ep)/'alignment.binding.json'}.items(): shutil.copyfile(path,work/name)
        atomic_json(work/'source-artifacts.json',arts)
        it.run(['el','speak',target,'--language',inputs['language'],'-v',inputs['voice'],'-m',inputs['model'],'-o',str(out)])
        if context(store,args)[0]!=inputs: raise WorkflowError('rewrite inputs changed during provider call')
        decoded=work/'patch.pcm.wav'; duration=decode_pcm(out,decoded)
        result={'episode_id':ep,'status':'done','operation':'voice-rewrite-generate','inputs':inputs,'output':str(out),'audio_sha256':digest(out),'duration':duration,'decoded_pcm_path':str(decoded),'decoded_pcm_sha256':digest(decoded),'estimated_usd_reserved':args.estimated_usd,'actual_usd':None}
        atomic_json(work/'generated.json',result); it.finish(store,ep,kind,key,'done',result); return result
    except BaseException:
        it.finish(store,ep,kind,key,'unknown',{'inputs':inputs,'output':str(out),'actual_usd':None}); raise


def apply(store,args):
    ep=args.episode; it.operations(store)
    rows=store.db.execute('SELECT * FROM external_operations WHERE episode_id=? AND kind=?',(ep,'voice-rewrite-generate-'+args.patch_id)).fetchall()
    if len(rows)!=1 or rows[0]['status']!='done': raise WorkflowError('rewrite requires one completed generation; unknown must not retry')
    gen=json.loads(rows[0]['data']); inputs=gen['inputs']; genkey=rows[0]['inputs_hash']
    if it.fingerprint(inputs)!=genkey: raise WorkflowError('rewrite generation fingerprint mismatch')
    work=it.folder(store,ep)/'voice-rewrites'/args.patch_id/genkey; out=work/'rewritten.wav'; patch=Path(gen['output'])
    if patch!=work/'patch.mp3' or digest(patch)!=gen['audio_sha256']: raise WorkflowError('rewrite generated audio mismatch')
    target=(work/'replacement.txt').read_text().strip()
    if text_sha(target)!=inputs['text_sha256'] or digest(work/'new-script.txt')!=inputs['script_sha256'] or digest(work/'old-script.txt')!=inputs['source_script_sha256']: raise WorkflowError('rewrite frozen scripts drift')
    if not args.review_file: raise WorkflowError('rewrite apply requires two-provider ASR review')
    if digest(work/'script-review.json')!=inputs['script_review_sha256']: raise WorkflowError('rewrite frozen script review drift')
    review_sha=it.patch_review(store,args.review_file,gen,target)
    apply_inputs={'script_sha256':inputs['script_sha256'],'source_script_sha256':inputs['source_script_sha256'],'source_audio_sha256':inputs['audio_sha256'],'generate_operation_hash':genkey,'patch_audio_sha256':gen['audio_sha256'],'review_sha256':review_sha}
    key=it.fingerprint(apply_inputs); kind='voice-rewrite-apply-'+args.patch_id
    prior=store.db.execute('SELECT * FROM external_operations WHERE episode_id=? AND kind=?',(ep,kind)).fetchone(); arts=store.artifacts(ep)
    if prior and prior['status']=='done':
        result=json.loads(prior['data'])
        if prior['inputs_hash']!=key or arts['script']['sha256']!=inputs['script_sha256'] or arts['audio']['sha256']!=result['audio_sha256'] or digest(out)!=result['audio_sha256']: raise WorkflowError('rewrite apply replay inputs drift')
        return {'idempotent':True,**result}
    current,_,_,voice,_,_=context(store,args)
    if current!=inputs: raise WorkflowError('rewrite source context changed')
    source=it.artifact_path(store,arts,'audio'); decoded=Path(gen['decoded_pcm_path'])
    if decoded!=work/'patch.pcm.wav' or digest(decoded)!=gen['decoded_pcm_sha256']: raise WorkflowError('decoded replacement PCM mismatch')
    with tempfile.TemporaryDirectory(dir=work,prefix='measure-') as temp:
        source_duration=decode_pcm(source,Path(temp)/'source.wav')
    patch_duration=gen['duration']
    duration=source_duration-inputs['cut_end']+inputs['cut_start']+patch_duration
    mapping=[{'source':'old','source_start':0,'source_end':inputs['cut_start'],'output_start':0,'output_end':inputs['cut_start']},{'source':'replacement','source_start':0,'source_end':patch_duration,'output_start':inputs['cut_start'],'output_end':inputs['cut_start']+patch_duration},{'source':'old','source_start':inputs['cut_end'],'source_end':source_duration,'output_start':inputs['cut_start']+patch_duration,'output_end':duration}]
    plan={'episode_id':ep,'mode':'plan','inputs':apply_inputs,'output':str(out),'duration_expected':duration,'narration_time_map':mapping,'paid_calls':0}
    if not args.execute: return plan
    it.begin(store,ep,kind,apply_inputs)
    try:
        with tempfile.TemporaryDirectory(dir=work,prefix='splice-') as temp:
            staged=Path(temp)/'rewritten.wav'; canonical=Path(temp)/'canonical.wav'; original=Path(temp)/'source.wav'
            if decode_pcm(source,original)!=source_duration: raise WorkflowError('source decoded duration drift')
            it.splice_audio(original,decoded,inputs['cut_start'],inputs['cut_end'],source_duration,staged)
            if abs(store._probe(staged,'audio')['duration']-duration)>.05: raise WorkflowError('rewrite splice duration mismatch')
            it.splice_audio(original,decoded,inputs['cut_start'],inputs['cut_end'],source_duration,canonical)
            pcm=it.pcm_hash(staged)
            if pcm!=it.pcm_hash(canonical): raise WorkflowError('rewrite full-track PCM projection mismatch')
            if context(store,args)[0]!=inputs or it.patch_review(store,args.review_file,gen,target)!=review_sha: raise WorkflowError('rewrite inputs changed during local splice')
            staged.replace(out)
        script_art=store.register(ep,'script',work/'new-script.txt')
        store.record_review(ep,work/'script-review.json')
        audio_art=store.register(ep,'audio',out); sha=digest(out)
        result={**plan,'mode':'execute','operation':'voice-rewrite-apply','status':'done','audio_sha256':sha,'artifact':audio_art,'script_artifact':script_art,'duration':duration,'fulltrack_pcm_sha256':pcm,'canonical_pcm_sha256':pcm}
        binding={**voice,'inputs':{**voice['inputs'],'script_sha256':inputs['script_sha256']},'audio_sha256':sha,'artifact':audio_art,'rewrite':{'id':args.patch_id,'operation_hash':key}}
        # Previous patch identity belongs to the source snapshot, not this current track.
        binding.pop('patch',None); binding.pop('take',None)
        atomic_json(work/'voice.binding.json',binding); atomic_json(it.folder(store,ep)/'voice.binding.json',binding)
        atomic_json(work/'applied.json',result); it.finish(store,ep,kind,key,'done',result); return result
    except BaseException:
        it.finish(store,ep,kind,key,'unknown',{'inputs':apply_inputs}); raise


def register(subparsers):
    p=subparsers.add_parser('voice-rewrite',help='Reviewed complete-paragraph prose/audio replacement, preserving old sources')
    p.add_argument('episode'); p.add_argument('--patch-id',required=True)
    p.add_argument('--start',type=int,required=True); p.add_argument('--end',type=int,required=True)
    p.add_argument('--replacement-script',required=True); p.add_argument('--replacement-text',required=True); p.add_argument('--script-review-file',required=True)
    p.add_argument('--cut-start',type=float); p.add_argument('--cut-end',type=float)
    a=p.add_mutually_exclusive_group(); a.add_argument('--generate',action='store_true'); a.add_argument('--apply',action='store_true')
    p.add_argument('--review-file'); p.add_argument('--execute',action='store_true'); p.add_argument('--confirm-cost',action='store_true'); p.add_argument('--estimated-usd',type=float)
    p.set_defaults(handler=execute)
