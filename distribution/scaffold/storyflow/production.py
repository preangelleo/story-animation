"""Mechanical production spine. Plans never contact providers; judgement stays human/model."""
import difflib
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
import unicodedata
import wave
from .core import Store, WorkflowError, atomic_json, digest, utcnow

def reserve_check(s,ep,category,minimum):
    rows=list(s.db.execute('SELECT * FROM costs WHERE episode_id=?',(ep,)))
    total=sum(x['amount'] for x in rows); amount=sum(x['amount'] for x in rows if x['category']=='reservation_'+category)
    month=utcnow()[:7]; monthly=s.db.execute('SELECT COALESCE(SUM(amount),0) FROM costs WHERE substr(created_at,1,7)=?',(month,)).fetchone()[0]
    if amount+1e-9<minimum or total>s.episode_budget_cap(ep) or monthly>s.config['budget']['monthly_usd']:
        raise WorkflowError('budget: explicit category allowance/episode/month cap required',409)
    return {'category':category,'reserved_usd':amount,'episode_reserved_usd':total,'monthly_reserved_usd':monthly,'actual_usd':None}

def reserve(s,ep,category,amount):
    """Category target, not additive; one SQLite transaction, no double reservation."""
    if not math.isfinite(amount) or amount<=0: raise WorkflowError('budget: positive amount required')
    s.episode(ep)
    with s.db:
        s.db.execute('BEGIN IMMEDIATE')
        prior=s.db.execute('SELECT COALESCE(SUM(amount),0) FROM costs WHERE episode_id=? AND category=?',(ep,'reservation_'+category)).fetchone()[0]
        if prior:
            if abs(prior-amount)>1e-9: raise WorkflowError('budget: existing category target differs; explicit review required',409)
            return {'ok':True,'idempotent':True,'reserved_usd':prior,'actual_usd':None}
        total=s.db.execute('SELECT COALESCE(SUM(amount),0) FROM costs WHERE episode_id=?',(ep,)).fetchone()[0]
        month=s.db.execute('SELECT COALESCE(SUM(amount),0) FROM costs WHERE substr(created_at,1,7)=?',(utcnow()[:7],)).fetchone()[0]
        if total+amount>s.episode_budget_cap(ep) or month+amount>s.config['budget']['monthly_usd']: raise WorkflowError('budget cap',409)
        s.db.execute('INSERT INTO costs(episode_id,amount,category,created_at) VALUES(?,?,?,?)',(ep,amount,'reservation_'+category,utcnow()))
    return {'ok':True,'reserved_usd':amount,'actual_usd':None}

def han(text):return sum('\u3400'<=c<='\u9fff' for c in text)
def calibrate(script,sample_text,sample_audio):
    text=Path(script).read_text();sample=Path(sample_text).read_text()
    if not sample.strip() or sample not in text or han(sample)<30: raise WorkflowError('calibration: sample must be >=30 Han exact script excerpt')
    with wave.open(str(sample_audio)) as w: seconds=w.getnframes()/w.getframerate()
    if seconds<=0: raise WorkflowError('calibration: empty audio')
    estimate=han(text)*seconds/han(sample)
    return {'ok':True,'script_sha256':digest(script),'sample_text_sha256':digest(sample_text),'sample_audio_sha256':digest(sample_audio),'sample_seconds':seconds,'han_characters':han(text),'estimated_seconds':estimate,'timing_use':'planning_reference_only','duration_upper_limit_seconds':None,'actual_full_audio_required':True,'subjective_review':'pending natural storytelling/proper names; no auto PASS'}

def preflight(root):
    from .portable import doctor
    return doctor(root)


def asr(audio,out,model):
    from faster_whisper import WhisperModel
    if out.exists(): raise WorkflowError('evidence exists; preserve and inspect',409)
    m=WhisperModel(model,device='cpu',compute_type='int8',cpu_threads=2,local_files_only=True)
    start=time.time();segments,info=m.transcribe(str(audio),language='zh',beam_size=5,vad_filter=True,word_timestamps=True)
    rows=[{'start':s.start,'end':s.end,'text':s.text,'words':[{'word':w.word,'start':w.start,'end':w.end,'probability':w.probability} for w in s.words]} for s in segments]
    v={'provider':'faster-whisper '+model+' CPU int8','model':'faster-whisper-'+model,'device':'cpu','compute_type':'int8','audio_sha256':digest(audio),'segments':rows,'text':'\n'.join(x['text'] for x in rows),'duration':info.duration,'elapsed_seconds':time.time()-start,'paid_calls':0};atomic_json(out,v);return {'ok':True,'asr_sha256':digest(out),'review':'pending'}

def compare(script,transcript):
    from opencc import OpenCC
    cc=OpenCC('t2s'); norm=lambda t:''.join(c for c in cc.convert(t) if c.isalnum()).lower()
    a=json.loads(Path(transcript).read_text());s,t=norm(Path(script).read_text()),norm(a['text']);matcher=difflib.SequenceMatcher(None,s,t,autojunk=False)
    differences=[{'kind':tag,'script_start':i,'script_end':j,'source':s[i:j],'recognized':t[k:l]} for tag,i,j,k,l in matcher.get_opcodes() if tag!='equal']
    return {'ok':True,'audio_sha256':a['audio_sha256'],'script_sha256':digest(script),'asr_sha256':digest(transcript),'similarity_ratio':matcher.ratio(),'changes':differences,'status':'requires_manual_fulltext_review'}

def share_readback(file,receipt,output):
    import urllib.request
    from urllib.parse import urlparse
    v=json.loads(Path(receipt).read_text());url=v['url'];u=urlparse(url)
    if u.scheme!='https' or not u.hostname or not u.path:raise WorkflowError('unexpected review URL')
    h=hashlib.sha256();count=0
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=60) as response:
        if urlparse(response.geturl()).hostname!=u.hostname or response.status!=200:raise WorkflowError('readback transport mismatch')
        while block:=response.read(1024*1024):h.update(block);count+=len(block)
    result={'ok':h.hexdigest()==v['sha256']==digest(file) and count==v['bytes']==Path(file).stat().st_size,'http_status':200,'bytes':count,'sha256':h.hexdigest(),'checked_at':utcnow()}
    atomic_json(output,result);return result

def share(root,ep,file,execute):
    """Use configured review upload adapter, persist intent before upload; never blind retry unknown."""
    from .integrations import begin,finish,operations
    s=Store(root)
    try:
        check=s.validate(ep)
        if not check['passed']: raise WorkflowError('final validate required before owner-review share')
        file=Path(file).resolve()
        work=root/'episodes'/ep/'production'
        derived=work/'burned.binding.json'
        clock=(Path(json.loads(derived.read_text())['clock_output'])/'final-clock-review.json') if derived.exists() else work/'burned-ultrasafe/final-clock-review.json'
        if (work/'render.binding.json').exists() and not derived.exists():raise WorkflowError('registered final burn derivation required')
        audit=json.loads(clock.read_text())
        arts=s.artifacts(ep)
        from .chapter_mix import delivery_inputs
        delivery=delivery_inputs(s,ep)
        expected={'video_sha256':arts['video']['sha256'],'audio_sha256':delivery['audio']['sha256'],'srt_sha256':delivery['subtitles']['sha256']}
        chapter=work/'chapter-revision.binding.json'
        if chapter.exists() and audit.get('chapter_revision_sha256')!=digest(chapter):raise WorkflowError('clock chapter revision binding mismatch')
        if digest(file)!=expected['video_sha256'] or any(audit.get(k)!=v for k,v in expected.items()) or audit.get('mechanical_passed') is not True or audit.get('source_unchanged') is not True or audit.get('input_hashes_before')!=expected or audit.get('input_hashes_after')!=expected:raise WorkflowError('bound final burned clock/decode audit required')
        inputs={'sha256':digest(file),'retention_days':7,'bytes':file.stat().st_size}
        if not execute:return {'ok':True,'executed':False,'inputs':inputs,'provider':'configured review upload adapter'}
        # Legacy fixed-path logs may be a crashed, unrecorded upload. A new
        # version directory must not turn missing provenance into retry permission.
        legacy=root/'episodes'/ep/'delivery'
        if (legacy/'r2-upload.log').exists():
            operations(s)
            receipt_file=legacy/'r2-upload-receipt.json'
            try: previous=json.loads(receipt_file.read_text())
            except (OSError,ValueError) as exc:raise WorkflowError('legacy upload outcome unknown; inspect, no retry') from exc
            completed=[json.loads(row['data']) for row in s.db.execute("SELECT data FROM external_operations WHERE episode_id=? AND kind='review-share' AND status='done'",(ep,))]
            if not any(all(row.get(k)==previous.get(k) for k in ('sha256','bytes','retention_days','url')) for row in completed):raise WorkflowError('legacy upload receipt lacks completed ledger; inspect, no retry')
        key,old=begin(s,ep,'review-share',inputs)
        if old:return {'ok':True,'idempotent':True,**old}
        folder=root/'episodes'/ep/'delivery'/key;folder.mkdir(parents=True,exist_ok=True);log=folder/'r2-upload.log'
        try:
            if log.exists():raise WorkflowError('prior upload evidence exists; inspect, no retry')
            argv=[sys.executable,str(Path(__file__).resolve().parents[1]/'adapters/review-upload.py'),str(file),'--7','--folder','share/'+secrets.token_hex(12),'--name','final-review.mp4']
            with log.open('x') as f:cp=subprocess.run(argv,stdout=f,stderr=subprocess.STDOUT)
            found=re.search(r'URL:\s*(https://\S+)',log.read_text())
            if cp.returncode or not found:raise WorkflowError('upload outcome unknown; no retry')
            receipt={**inputs,'url':found[1],'actual_usd':None,'uploaded_at':utcnow(),'receipt_path':str(folder/'r2-upload-receipt.json')};atomic_json(folder/'r2-upload-receipt.json',receipt);finish(s,ep,'review-share',key,'done',receipt);return {'ok':True,**receipt}
        except BaseException:finish(s,ep,'review-share',key,'unknown',{'inputs':inputs,'actual_usd':None});raise
    finally:s.close()

def handle(a):
    root=Path(a.root).expanduser().resolve(); op=a.production_action
    if op=='preflight':return preflight(root)
    if op=='share-readback':return share_readback(a.file,a.receipt,a.output)
    if op=='calibrate':return calibrate(a.script,a.sample_text,a.sample_audio)
    if op=='asr':
        if not a.execute:return {'ok':True,'executed':False,'model':a.model,'offline':True,'cpu_threads':2}
        return asr(Path(a.audio),Path(a.output),a.model)
    if op=='asr-compare':
        v=compare(a.script,a.transcript)
        if a.output:atomic_json(a.output,v)
        return v
    ep=a.episode
    if getattr(a,'execute',False) and op in ('voice','video','ingest','time-scenes','manifest','burn','chapter-revision','scene-boundary-replan'):
        from .revision_gate import check_revision
        check_revision(root,ep)
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',ep):raise WorkflowError('invalid episode id')
    if op=='scene-boundary-replan':
        from .scene_assembly import replan
        s=Store(root)
        try:return replan(s,ep,a.file,a.execute)
        finally:s.close()
    if op=='chapter-revision':
        from .chapter_mix import revise,archive_current
        s=Store(root)
        try:
            if a.archive_current:
                if a.file or a.output:raise WorkflowError('archive-current does not accept file/output')
                return archive_current(s,ep,a.execute)
            if not a.file or not a.output:raise WorkflowError('chapter-revision requires file and output')
            return revise(s,ep,json.loads(Path(a.file).read_text()),a.output,a.execute)
        finally:s.close()
    if op=='reserve':
        if not a.execute:return {'ok':True,'executed':False,'category':a.category,'target_usd':a.amount,'actual_usd':None}
        s=Store(root)
        try:return reserve(s,ep,a.category,a.amount)
        finally:s.close()
    if op=='voice':
        raise WorkflowError('Use voice EP --take N --chunk-chars 1100; plan first, execute requires confirm-cost and estimated-usd. Historical provider adapters are not distributed.')
    if op=='share':return share(root,ep,a.file,a.execute)
    if op=='burn':
        from .burn_ops import burn
        s=Store(root)
        try:return burn(s,ep,a.execute,a.illustrated_reconstruction)
        finally:s.close()
    if not a.execute:return {'ok':True,'executed':False,'operation':op,'episode':ep,'paid_calls_made':0}
    if op=='video':
        if not re.fullmatch(r'[a-zA-Z0-9_-]+',a.scene):raise WorkflowError('invalid scene id')
        if a.video_action=='submit' and not a.reference:raise WorkflowError('actual reference path required')
        from .video_ops import main
        return main(root,ep,a.video_action,a.scene,a.reference)
    if op=='time-scenes':
        from .scene_timing import build
        return build(root,ep)
    if op=='manifest':
        from .manifest_ops import assemble
        return assemble(root,ep)
    if op=='clock-qa':
        from .clock_qa import audit
        chapter=None
        path=root/'episodes'/ep/'production/chapter-revision.binding.json'
        if path.exists():
            from .chapter_mix import verify_revision,plan as chapter_plan
            s=Store(root)
            try:
                revision=verify_revision(s,ep)
                for kind,value in (('audio',a.audio),('subtitles',a.srt)):
                    if digest(value)!=revision['delivery'][kind]['sha256']:raise WorkflowError('clock requires current chapter delivery '+kind)
                if digest(a.video)!=s.artifacts(ep)['video']['sha256']:raise WorkflowError('clock requires current final video')
                chapter={'mapping':chapter_plan(revision['plan']),'binding_sha256':digest(path)}
            finally:s.close()
        return audit(a.video,a.audio,a.srt,a.output,chapter=chapter)
    if op=='ingest':
        if not re.fullmatch(r'[a-zA-Z0-9_-]+\.(png|jpg|jpeg|webp)',a.name):raise WorkflowError('invalid image name')
        source=Path(a.file).resolve();record=json.loads(Path(a.record).read_text());target=root/'episodes'/ep/'production/images'/a.name
        from .visual_prompt import check_prompt
        check_prompt(record.get('generation',{}).get('prompt',''))
        from .style_binding import snapshot_for_episode,check_binding
        style_store=Store(root)
        try:snapshot=snapshot_for_episode(style_store,ep)
        finally:style_store.close()
        check_binding(record,snapshot,'ingest record')
        check_binding(record.get('generation'),snapshot,'ingest generation',prompt=True)
        if record.get('sha256')!=digest(source) or not record.get('generation'):raise WorkflowError('authentic generation evidence required')
        if target.exists():
            if digest(target)!=digest(source):raise WorkflowError('existing image differs',409)
        else:target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        from .visual_prompt import check_prompt
        check_prompt(record.get('generation',{}).get('prompt',''))
        from .style_binding import snapshot_for_episode,check_binding
        style_store=Store(root)
        try:snapshot=snapshot_for_episode(style_store,ep)
        finally:style_store.close()
        check_binding(record,snapshot,'ingest record')
        check_binding(record.get('generation'),snapshot,'ingest generation',prompt=True)
        if record.get('sha256')!=digest(target) or not record.get('generation'):raise WorkflowError('authentic generation evidence required; not an invented approval')
        evidence=root/'episodes'/ep/'production/image-generation'/(Path(a.name).stem+'.json');record['image']=str(target.relative_to(root));atomic_json(evidence,record)
        return {'ok':True,'evidence':str(evidence),'review':'pending independent actual image inspection and full manifest validator'}
    raise WorkflowError('unknown production action')

def register(sub):
    p=sub.add_parser('production',help='项目媒体机械入口，默认plan；主观审查不自动PASS');s=p.add_subparsers(dest='production_action',required=True)
    for n in ('preflight','calibrate','asr','asr-compare','reserve','voice','video','time-scenes','manifest','ingest','clock-qa','burn','share','share-readback','chapter-revision','scene-boundary-replan'):
        q=s.add_parser(n);q.set_defaults(handler=handle)
        if n not in ('preflight','calibrate','asr-compare','share-readback'):q.add_argument('--execute',action='store_true')
        if n not in ('preflight','calibrate','asr','asr-compare','share-readback'):q.add_argument('episode')
        if n in ('calibrate','asr-compare'):q.add_argument('--script',required=True)
        if n=='calibrate':q.add_argument('--sample-text',required=True);q.add_argument('--sample-audio',required=True)
        if n=='asr':q.add_argument('--audio',required=True);q.add_argument('--output',required=True);q.add_argument('--model',choices=('small','medium'),default='medium')
        if n=='asr-compare':q.add_argument('--transcript',required=True);q.add_argument('--output')
        if n=='reserve':q.add_argument('--category',choices=('voice','alignment','video','images'),required=True);q.add_argument('--amount',type=float,required=True)
        if n=='voice':
            q.add_argument('--script-sha256',required=True);q.add_argument('--take',type=int,default=1);q.add_argument('--sample-chars',type=int);q.add_argument('--phoneme-json')
        if n=='video':q.add_argument('--video-action',choices=('submit','status'),required=True);q.add_argument('--scene',required=True);q.add_argument('--reference')
        if n=='ingest':q.add_argument('--file',required=True);q.add_argument('--record',required=True);q.add_argument('--name',required=True)
        if n=='scene-boundary-replan':q.add_argument('--file',required=True)
        if n=='chapter-revision':q.add_argument('--file');q.add_argument('--output');q.add_argument('--archive-current',action='store_true')
        if n=='burn':q.add_argument('--illustrated-reconstruction',action='store_true')
        if n=='share':q.add_argument('--file',required=True)
        if n=='share-readback':
            for k in ('file','receipt','output'):q.add_argument('--'+k,required=True)
        if n=='clock-qa':
            for k in ('video','audio','srt','output'):q.add_argument('--'+k,required=True)
