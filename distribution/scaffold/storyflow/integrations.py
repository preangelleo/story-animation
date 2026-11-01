"""Story Animation的媒体和 YouTube 边界：默认计划、外部操作先记账、未知结果禁止重发。"""
from __future__ import annotations
import datetime as dt
import hashlib
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unicodedata
from zoneinfo import ZoneInfo
from .core import Store, WorkflowError, atomic_json, digest, read_json, timestamp, utcnow


def run(argv, timeout=3600):
    if argv[0] in ('el','yt'):
        argv=[sys.executable,str(Path(__file__).resolve().parents[1]/'adapters'/(argv[0]+'.py')),*argv[1:]]
    """不使用 shell，也不向异常输出可能含 token 的工具 stderr。"""
    try:
        result = subprocess.run([str(v) for v in argv], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkflowError('外部命令未完成：'+type(exc).__name__) from None
    if result.returncode:
        raise WorkflowError(f'外部命令失败：{Path(argv[0]).name}（exit {result.returncode}，结果需核对）')
    return result.stdout


def operations(store):
    store.db.execute('''CREATE TABLE IF NOT EXISTS external_operations(
        episode_id TEXT NOT NULL, kind TEXT NOT NULL, inputs_hash TEXT NOT NULL,
        status TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(episode_id,kind,inputs_hash))''')
    store.db.commit()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def begin(store, ep, kind, inputs, estimated=None):
    """同事务内持久化预算预留和 operation；任意中断后都不隐式重试。"""
    operations(store); key = fingerprint(inputs)
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        # 与撤稿事务串行化：先撤稿则本次不能建立运行中上传；
        # 先建立上传则撤稿端必须保守拒绝释放排期。
        if kind=='ship' and store.episode(ep).get('review_only'): raise WorkflowError('review_only 永久禁止上传',409)
        if kind=='ship' and store.episode(ep).get('editorial_status')=='withdrawn':
            raise WorkflowError('已退稿故事禁止启动上传',409)
        # 不同输入也不能绕过同一集未决外部操作或已经上传的身份。
        rows = store.db.execute('SELECT * FROM external_operations WHERE episode_id=? AND kind=?', (ep,kind)).fetchall()
        for row in rows:
            if row['inputs_hash']==key and row['status']=='done':
                return key, json.loads(row['data'])
            if row['status']!='done' or kind=='ship':
                raise WorkflowError(f'{kind} 已有外部操作（{row["status"]}），禁止重发；请 verify/reconcile',409)
        if estimated is not None:
            if isinstance(estimated,bool) or not isinstance(estimated,(int,float)) or not math.isfinite(estimated) or estimated<=0:
                raise WorkflowError('--estimated-usd 必须是正实数')
            now=utcnow(); b=store.config['budget']
            per=store.db.execute('SELECT COALESCE(SUM(amount),0) FROM costs WHERE episode_id=?',(ep,)).fetchone()[0]
            month=store.db.execute('SELECT COALESCE(SUM(amount),0) FROM costs WHERE substr(created_at,1,7)=?',(now[:7],)).fetchone()[0]
            if per+estimated>store.episode_budget_cap(ep) or month+estimated>b['monthly_usd']:
                raise WorkflowError('预算预留超过单集或月度上限',409)
            store.db.execute('INSERT INTO costs(episode_id,amount,category,created_at) VALUES(?,?,?,?)',(ep,estimated,'reservation:'+kind,now))
        value={'inputs':inputs,'estimated_usd_reserved':estimated,'actual_usd':None}
        store.db.execute('INSERT INTO external_operations VALUES(?,?,?,?,?,?)',(ep,kind,key,'running',json.dumps(value,ensure_ascii=False),utcnow()))
    return key, None


def finish(store, ep, kind, key, status, value):
    with store.db:
        store.db.execute('UPDATE external_operations SET status=?,data=? WHERE episode_id=? AND kind=? AND inputs_hash=?',(status,json.dumps(value,ensure_ascii=False),ep,kind,key))


def checked(store, ep, kinds):
    episode=store.episode(ep); errors=store.verify_hashes(ep)+store.review_errors(ep)
    arts=store.artifacts(ep)
    errors += ['缺少 '+k for k in kinds if k not in arts]
    if errors: raise WorkflowError('；'.join(errors))
    return episode,arts


def artifact_path(store, arts, kind): return store.root/arts[kind]['path']


def folder(store, ep):
    path=store.root/'episodes'/ep/'production'; path.mkdir(parents=True,exist_ok=True); return path


def paid_options(args):
    if not args.confirm_cost or args.estimated_usd is None:
        raise WorkflowError('收费调用需要 --execute --confirm-cost --estimated-usd；费用先保守预留')


def voice(args):
    if getattr(args,'execute',False):
        from .revision_gate import check_revision
        check_revision(args.root,args.episode)
    with_store=Store(args.root)
    try: return _voice(with_store,args)
    finally: with_store.close()


def _voice(store,args):
    if store.config.get("voice",{}).get("provider")=="fish_audio":
        raise WorkflowError("请配置本包支持的ElevenLabs声音，禁止把其他供应商voice id发给ElevenLabs")
    if getattr(args,'take',None) is not None or getattr(args,'chunk_chars',None) is not None:
        return _voice_take(store,args)
    ep=args.episode; _,arts=checked(store,ep,('script',))
    script=artifact_path(store,arts,'script'); text=script.read_text().strip(); work=folder(store,ep)
    cfg=store.config.get('voice',{})
    model=cfg.get('model','eleven_multilingual_v2'); speaker=(os.environ.get('ELEVENLABS_VOICE_ID') or 'user-voice') if cfg.get('voice','user-voice')=='user-voice' else cfg.get('voice'); language=cfg.get('language','zh')
    argv=['el','speak',text,'--language',language,'-v',speaker,'-m',model,'-o','PENDING_OUTPUT']
    inputs={'script_sha256':digest(script),'voice':speaker,'model':model,'language':language}
    out=work/'voice'/fingerprint(inputs)/'voice.mp3'; out.parent.mkdir(parents=True,exist_ok=True)
    argv[-1]=str(out)
    plan={'episode_id':ep,'mode':'plan','operation':'voice','characters':len(text),'inputs':inputs,'output':str(out),'requires':['execute','confirm-cost','estimated-usd']}
    if not args.execute: return plan
    paid_options(args); key,old=begin(store,ep,'voice',inputs,args.estimated_usd)
    if old is not None: return {'idempotent':True,**old}
    try:
        if out.exists(): raise WorkflowError('输出已有文件，请先核对来源，禁止覆盖')
        run(argv); store._probe(out,'audio')
        if digest(script)!=inputs['script_sha256']: raise WorkflowError('配音期间脚本变化')
        result={'episode_id':ep,'operation':'voice','status':'done','inputs':inputs,'audio_sha256':digest(out),'estimated_usd_reserved':args.estimated_usd,'actual_usd':None,'artifact':store.register(ep,'audio',out)}
        atomic_json(work/'voice.binding.json',result); finish(store,ep,'voice',key,'done',result); return result
    except BaseException:
        finish(store,ep,'voice',key,'unknown',{'inputs':inputs,'output':str(out),'estimated_usd_reserved':args.estimated_usd,'actual_usd':None}); raise


def split_voice_text(text,limit):
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=1200: raise WorkflowError('chunk-chars必须为1到1200的整数')
    chunks=[]; offset=0
    while offset<len(text):
        if len(text)-offset<=limit: chunks.append(text[offset:]); break
        window=text[offset:offset+limit]
        boundaries=list(re.finditer(r'[。！？；.!?;\n][”’」』"]*\s*',window))
        if not boundaries: raise WorkflowError('单句超过chunk-chars，不能在句中截断；请缩短已审稿或提高至1200')
        end=boundaries[-1].end(); chunks.append(text[offset:offset+end]); offset+=end
    if not chunks or any(not c.strip() for c in chunks) or ''.join(chunks)!=text: raise WorkflowError('分段文本不能完整覆盖原稿')
    return chunks


def voice_lock_path(store,ep):
    return store.state/('voice-'+hashlib.sha256(ep.encode()).hexdigest()+'.lock')


def _voice_take(store,args):
    # 内核锁跨进程且崩溃自动释放；持锁证明其他遵循协议的执行者已退出。
    with voice_lock_path(store,args.episode).open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise WorkflowError('本集配音仍有执行者持锁，禁止并发或恢复',409) from None
        try: return _voice_take_locked(store,args)
        finally: fcntl.flock(lock,fcntl.LOCK_UN)


def completed_take_chunks(store,ep,kind,key,inputs,chunks,target):
    receipts=[]
    for index,chunk in enumerate(chunks):
        expected={'take_inputs_hash':key,'index':index,'text_sha256':inputs['chunk_sha256'][index]}
        row=store.db.execute('SELECT * FROM external_operations WHERE episode_id=? AND kind=? AND inputs_hash=?',(ep,f'{kind}-chunk-{index+1:04d}',fingerprint(expected))).fetchone()
        if row is None or row['status']!='done': raise WorkflowError('running take仅能纯本地恢复全部已done块；缺失或未决块禁止重发')
        receipt=json.loads(row['data']); path=target/f'chunk-{index+1:04d}.mp3'
        if receipt.get('inputs')!=expected or receipt.get('output')!=str(path) or path.is_symlink() or path.resolve()!=path or digest(path)!=receipt.get('audio_sha256'):
            raise WorkflowError('已完成分块文本或音频绑定无效')
        actual=store._probe(path,'audio')['duration']
        if not isinstance(receipt.get('duration'),(int,float)) or abs(actual-receipt['duration'])>0.05: raise WorkflowError('已完成分块时长不符')
        receipts.append(receipt)
    return receipts


def _voice_take_locked(store,args):
    ep=args.episode; _,arts=checked(store,ep,('script',)); take=getattr(args,'take',None)
    if isinstance(take,bool) or not isinstance(take,int) or take<1: raise WorkflowError('分段配音须显式指定正整数--take')
    limit=getattr(args,'chunk_chars',None)
    if limit is None: limit=1200
    script=artifact_path(store,arts,'script'); text=script.read_text(); chunks=split_voice_text(text,limit)
    cfg=store.config.get('voice',{}); model=cfg.get('model','eleven_multilingual_v2'); speaker=(os.environ.get('ELEVENLABS_VOICE_ID') or 'user-voice') if cfg.get('voice','user-voice')=='user-voice' else cfg.get('voice'); language=cfg.get('language','zh')
    inputs={'script_sha256':digest(script),'voice':speaker,'model':model,'language':language,'take':take,'chunk_chars':limit,'chunk_sha256':[hashlib.sha256(c.encode()).hexdigest() for c in chunks]}
    key=fingerprint(inputs); kind=f'voice-take-{take}'; target=folder(store,ep)/'voice'/key; out=target/'voice.wav'
    plan={'episode_id':ep,'mode':'plan','operation':'voice','inputs':inputs,'chunks':len(chunks),'characters':len(text),'output':str(out),'requires':['execute','confirm-cost','estimated-usd']}
    if not args.execute: return plan
    paid_options(args)
    estimated=args.estimated_usd
    if isinstance(estimated,bool) or not isinstance(estimated,(int,float)) or not math.isfinite(estimated) or estimated<=0: raise WorkflowError('--estimated-usd必须为正实数')
    operations(store)
    row=store.db.execute('SELECT * FROM external_operations WHERE episode_id=? AND kind=?',(ep,kind)).fetchone()
    local_receipts=None
    if row:
        prior=json.loads(row['data'])
        if row['inputs_hash']!=key: raise WorkflowError('同一take输入已变化；须明确另选take，不覆盖旧操作')
        if row['status']=='done':
            if digest(out)!=prior.get('audio_sha256'): raise WorkflowError('已完成take音频哈希不符')
            return {'idempotent':True,**prior}
        if row['status'] not in ('unknown','running') or not getattr(args,'resume',False): raise WorkflowError('take已有操作，核实后仅用--resume恢复已完成块；unknown块不会重发')
        if prior.get('estimated_usd_total')!=estimated: raise WorkflowError('恢复take预算必须与原操作一致')
        if row['status']=='running':
            if prior.get('exclusive_lock')!='voice-episode-flock-v1': raise WorkflowError('旧running操作无互斥协议证据，不能证明原执行者退出，禁止自动恢复')
            local_receipts=completed_take_chunks(store,ep,kind,key,inputs,chunks,target)
    else:
        # 整次报价先查预算，收费实际按每个chunk分别原子预留。
        per=store.db.execute('SELECT COALESCE(SUM(amount),0) FROM costs WHERE episode_id=?',(ep,)).fetchone()[0]
        month=store.db.execute('SELECT COALESCE(SUM(amount),0) FROM costs WHERE substr(created_at,1,7)=?',(utcnow()[:7],)).fetchone()[0]
        budget=store.config['budget']
        if per+estimated>store.episode_budget_cap(ep) or month+estimated>budget['monthly_usd']: raise WorkflowError('分段take总预留超过预算')
        begin(store,ep,kind,inputs)
    finish(store,ep,kind,key,'running',{'inputs':inputs,'estimated_usd_total':estimated,'actual_usd':None,'exclusive_lock':'voice-episode-flock-v1'})
    target.mkdir(parents=True,exist_ok=True)
    try:
        receipts=[] if local_receipts is None else local_receipts; spent=0.0
        for index,chunk in enumerate(chunks if local_receipts is None else []):
            chunk_inputs={'take_inputs_hash':key,'index':index,'text_sha256':inputs['chunk_sha256'][index]}
            chunk_kind=f'{kind}-chunk-{index+1:04d}'; chunk_out=target/f'chunk-{index+1:04d}.mp3'
            amount=estimated-spent if index==len(chunks)-1 else estimated/len(chunks); spent+=amount
            chunk_key,old=begin(store,ep,chunk_kind,chunk_inputs,amount)
            if old is not None:
                if old.get('inputs')!=chunk_inputs or digest(chunk_out)!=old.get('audio_sha256'): raise WorkflowError('已完成分块输入或音频哈希不符')
                store._probe(chunk_out,'audio'); receipts.append(old); continue
            try:
                if chunk_out.exists(): raise WorkflowError('分块输出已存在，禁止覆盖或再次收费')
                run(['el','speak',chunk,'--language',language,'-v',speaker,'-m',model,'-o',str(chunk_out)])
                duration=store._probe(chunk_out,'audio')['duration']
                if digest(script)!=inputs['script_sha256']: raise WorkflowError('分块配音期间原稿变化')
                receipt={'inputs':chunk_inputs,'output':str(chunk_out),'audio_sha256':digest(chunk_out),'duration':duration,'estimated_usd_reserved':amount,'actual_usd':None}
                finish(store,ep,chunk_kind,chunk_key,'done',receipt); receipts.append(receipt)
            except BaseException:
                finish(store,ep,chunk_kind,chunk_key,'unknown',{'inputs':chunk_inputs,'output':str(chunk_out),'estimated_usd_reserved':amount,'actual_usd':None}); raise
        # 每块先解码到相同PCM参数，再无损拼接；不拼MP3头，不加速/拉伸旁白。
        with tempfile.TemporaryDirectory(dir=target,prefix='concat-') as tmp:
            paths=[]
            for index,receipt in enumerate(receipts):
                pcm=Path(tmp)/f'{index}.wav'; paths.append(pcm)
                run(['ffmpeg','-v','error','-nostdin','-y','-i',receipt['output'],'-ar','44100','-ac','1','-c:a','pcm_s16le',str(pcm)])
            listing=Path(tmp)/'chunks.txt'; listing.write_text(''.join(f"file '{p.name}'\n" for p in paths))
            staged=Path(tmp)/'voice.wav'
            run(['ffmpeg','-v','error','-nostdin','-y','-f','concat','-safe','1','-i',str(listing),'-c:a','copy',str(staged)])
            actual=store._probe(staged,'audio')['duration']
            if abs(actual-sum(r['duration'] for r in receipts))>0.5: raise WorkflowError('拼接音轨时长与分块总和不符')
            if digest(script)!=inputs['script_sha256'] or any(digest(r['output'])!=r['audio_sha256'] for r in receipts): raise WorkflowError('拼接期间原稿或分块变化')
            staged.replace(out)
        result={'episode_id':ep,'operation':'voice','status':'done','inputs':inputs,'take':take,'chunks':receipts,'audio_sha256':digest(out),'estimated_usd_reserved':estimated,'actual_usd':None,'artifact':store.register(ep,'audio',out)}
        atomic_json(target/'voice.binding.json',result); atomic_json(folder(store,ep)/'voice.binding.json',result)
        finish(store,ep,kind,key,'done',result); return result
    except BaseException:
        finish(store,ep,kind,key,'unknown',{'inputs':inputs,'estimated_usd_total':estimated,'actual_usd':None,'exclusive_lock':'voice-episode-flock-v1'}); raise


def srt_time(seconds):
    total=round(seconds*1000); sec,ms=divmod(total,1000); minute,sec=divmod(sec,60); hour,minute=divmod(minute,60)
    return f'{hour:02}:{minute:02}:{sec:02},{ms:03}'


def alignment_srt(alignment, text, caption_units=None):
    """只用供应商逐字符时间戳；文字必须与冻结原稿完全一致。"""
    from .captions import display_captions, to_srt, validated_characters
    try:
        chars=validated_characters(alignment,text)
        entries,_=display_captions(chars,caption_units)
        return to_srt(entries)
    except ValueError as exc:
        raise WorkflowError(str(exc)) from exc


def _alignment_result(store,ep,arts,inputs,out,sub,key,estimated,reconciled=False,kind='align'):
    script=artifact_path(store,arts,'script'); audio=artifact_path(store,arts,'audio')
    if out.is_symlink() or out.resolve()!=out: raise WorkflowError('对齐结果必须为本operation实体文件')
    before=digest(out); value=read_json(out)
    units=folder(store,ep)/'caption_units.json'
    if (units.exists() or units.is_symlink()) and (units.is_symlink() or units.resolve()!=units or not units.is_file()): raise WorkflowError('字幕语义单元必须为实体文件')
    if sub.is_symlink() or sub.resolve()!=sub: raise WorkflowError('展示字幕必须为实体文件')
    units_sha=digest(units) if units.is_file() else None
    try: unit_values=json.loads(units.read_text()) if units.is_file() else None
    except (OSError,ValueError) as exc: raise WorkflowError('无法读取字幕语义单元：'+str(exc)) from exc
    subtitles=alignment_srt(value,script.read_text().strip(),unit_values)
    duration=store._probe(audio,'audio')['duration']
    if max(c['end'] for c in value['characters'])>duration+0.5: raise WorkflowError('对齐超出音轨时长')
    if digest(script)!=inputs['script_sha256'] or digest(audio)!=inputs['audio_sha256'] or digest(out)!=before or (digest(units) if units.is_file() else None)!=units_sha:
        raise WorkflowError('对齐恢复期间输入变化')
    sub.write_text(subtitles)
    from .captions import validate_srt, PIPELINE, BINDING_VERSION
    try: quality=validate_srt(sub)
    except ValueError as exc: raise WorkflowError(str(exc)) from exc
    quality.update(binding_version=BINDING_VERSION,inputs=inputs,semantic_review_sha256=arts.get('review',{}).get('sha256') if store.config.get('quality',{}).get('require_semantic_caption_units') else None,mode='caption_units' if units.is_file() else 'deterministic_review_required',caption_units_sha256=units_sha,alignment_sha256=before,srt_sha256=digest(sub),srt_offset_seconds=0)
    atomic_json(sub.with_suffix('.quality.json'),quality)
    result={'episode_id':ep,'status':'done','inputs':inputs,'alignment_sha256':before,'caption_binding_version':BINDING_VERSION,'alignment_path':str(out.relative_to(store.root)),'display_path':str(sub.relative_to(store.root)),'quality_path':str(sub.with_suffix('.quality.json').relative_to(store.root)),'caption_pipeline':PIPELINE,'caption_quality':quality,'estimated_usd_reserved':estimated,'actual_usd':None,'reconciled_locally':reconciled,'artifact':store.register(ep,'subtitles',sub)}
    atomic_json(folder(store,ep)/'alignment.binding.json',result)
    if store.config.get('quality',{}).get('require_semantic_caption_units'):
        try:
            from .captions import verify_store_caption_binding
            verify_store_caption_binding(store,ep,store.artifacts(ep),require_semantic=True,expected_receipt=result)
        except ValueError as exc: raise WorkflowError(str(exc)) from exc
    finish(store,ep,kind,key,'done',result); return result


def fulltext_asr_gate(store,work,inputs):
    """Provider-independent original-script FA prerequisite; local evidence only."""
    try:
        audit=read_json(work/'asr/fulltext-audit.json')
        if audit.get('status')!='pass' or audit.get('script_sha256')!=inputs['script_sha256'] or audit.get('audio_sha256')!=inputs['audio_sha256'] or not isinstance(audit.get('reviewer'),str) or not audit['reviewer'].strip():
            raise WorkflowError('independent ASR fulltext audit binding required before FA')
        asr=store.root/audit.get('asr_path',str((work/'asr/whisper-medium.json').relative_to(store.root)))
        comparison=store.root/audit.get('comparison_path',str((work/'asr/comparison.json').relative_to(store.root)))
        if not asr.resolve().is_relative_to(store.root) or not comparison.resolve().is_relative_to(store.root):raise WorkflowError('ASR evidence outside project')
        transcript=read_json(asr);diff=read_json(comparison)
        if digest(asr)!=audit.get('asr_sha256') or digest(comparison)!=audit.get('comparison_sha256') or transcript.get('audio_sha256')!=inputs['audio_sha256'] or diff.get('asr_sha256')!=digest(asr) or diff.get('script_sha256')!=inputs['script_sha256']:
            raise WorkflowError('ASR actual transcript/comparison evidence binding required before FA')
        return digest(work/'asr/fulltext-audit.json')
    except (OSError,ValueError,TypeError,AttributeError,WorkflowError) as exc:
        raise WorkflowError('independent ASR fulltext audit evidence missing or invalid before FA') from exc


def align(args):
    if getattr(args,'execute',False):
        from .revision_gate import check_revision
        check_revision(args.root,args.episode)
    store=Store(args.root)
    try:
        ep=args.episode; _,arts=checked(store,ep,('script','audio'))
        script=artifact_path(store,arts,'script'); audio=artifact_path(store,arts,'audio'); work=folder(store,ep)
        inputs={'script_sha256':digest(script),'audio_sha256':digest(audio)}
        target=work/'align'/fingerprint(inputs); out=target/'alignment.json'; sub=target/'subtitles.srt'
        binding=read_json(work/'voice.binding.json')
        if binding.get('inputs',{}).get('script_sha256')!=inputs['script_sha256'] or binding.get('audio_sha256')!=inputs['audio_sha256']:
            raise WorkflowError('音频与当前原稿缺少有效SHA256绑定')
        kind=voice_binding_kind(store,ep,binding,inputs)
        audit_sha=None
        if args.execute or getattr(args,'reconcile',False):audit_sha=fulltext_asr_gate(store,work,inputs)
        if (args.execute or getattr(args,'reconcile',False)) and store.config.get('quality',{}).get('require_semantic_caption_units'):
            from .captions import verify_store_semantic_review
            try:verify_store_semantic_review(store,ep,arts)
            except ValueError as exc:raise WorkflowError(str(exc)) from exc
        if getattr(args,'reconcile',False):
            # 只能恢复同一已记账操作；不调用begin、不新预留、不访问供应商。
            operations(store); key=fingerprint(inputs)
            row=store.db.execute('SELECT * FROM external_operations WHERE episode_id=? AND kind=? AND inputs_hash=?',(ep,kind,key)).fetchone()
            if row is None: raise WorkflowError('没有匹配的已记账align操作，禁止本地恢复')
            value=json.loads(row['data'])
            if row['status'] not in ('unknown','running','done') or value.get('inputs')!=inputs or fingerprint(value['inputs'])!=key:
                raise WorkflowError('align操作指纹与当前输入不符')
            if row['status']=='done' and 'artifact' in value:
                from .captions import verify_store_caption_binding, BINDING_VERSION
                required=store.config.get('quality',{}).get('require_semantic_caption_units',False)
                legacy=value.get('caption_binding_version')!=BINDING_VERSION
                try:
                    proof=verify_store_caption_binding(store,ep,arts,require_semantic=required,expected_receipt=value,allow_legacy=legacy)
                except ValueError as exc: raise WorkflowError(str(exc)) from exc
                if proof['max_alignment_end']>store._probe(audio,'audio')['duration']+0.5: raise WorkflowError('对齐超出音轨时长')
                if legacy:
                    # Metadata-only migration, after proving current files match
                    # historical semantic mode/hash and exact regenerated SRT.
                    value.update(caption_binding_version=BINDING_VERSION,**proof['paths'])
                    value['caption_quality'].update(binding_version=BINDING_VERSION,inputs=inputs,semantic_review_sha256=arts.get('review',{}).get('sha256') if required else None)
                    atomic_json(store.root/proof['paths']['quality_path'],value['caption_quality'])
                    atomic_json(work/'alignment.binding.json',value)
                    finish(store,ep,kind,key,'done',value)
                return {'idempotent':True,**value}
            return _alignment_result(store,ep,arts,inputs,out,sub,key,value.get('estimated_usd_reserved'),True,kind)
        if not args.execute: return {'episode_id':ep,'mode':'plan','operation':'align','inputs':inputs,'output':str(out)}
        paid_options(args)
        if getattr(args,'use_reservation',False):
            from .production import reserve_check
            reserve_check(store,ep,'alignment',args.estimated_usd)
        key,old=begin(store,ep,kind,inputs,None if getattr(args,'use_reservation',False) else args.estimated_usd)
        if old is not None:
            try:
                from .captions import verify_store_caption_binding
                verify_store_caption_binding(store,ep,arts,require_semantic=store.config.get('quality',{}).get('require_semantic_caption_units',False),expected_receipt=old)
            except ValueError as exc: raise WorkflowError(str(exc)) from exc
            return {'idempotent':True,**old}
        try:
            target.mkdir(parents=True,exist_ok=True)
            if out.exists(): raise WorkflowError('对齐输出已经存在，禁止覆盖')
            run(['el','align',str(audio),'--text-file',str(script),'-o',str(out)])
            if fulltext_asr_gate(store,work,inputs)!=audit_sha:raise WorkflowError('ASR fulltext audit changed during FA')
            return _alignment_result(store,ep,arts,inputs,out,sub,key,args.estimated_usd,kind=kind)
        except BaseException:
            finish(store,ep,kind,key,'unknown',{'inputs':inputs,'output':str(out),'estimated_usd_reserved':args.estimated_usd,'actual_usd':None}); raise
    finally: store.close()


def voice_binding_kind(store,ep,binding,inputs):
    """只有已完成正式voice/patch操作能开启新的对齐命名空间。"""
    operations(store)
    rewrite=binding.get('rewrite')
    if rewrite is not None:
        if not isinstance(rewrite,dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',str(rewrite.get('id',''))): raise WorkflowError('invalid rewrite binding')
        key=rewrite.get('operation_hash')
        row=store.db.execute('SELECT data,status FROM external_operations WHERE episode_id=? AND kind=? AND inputs_hash=?',(ep,'voice-rewrite-apply-'+rewrite['id'],key)).fetchone()
        if row is None or row['status']!='done': raise WorkflowError('rewrite alignment requires completed apply')
        result=json.loads(row['data'])
        if result.get('audio_sha256')!=inputs['audio_sha256'] or result.get('inputs',{}).get('script_sha256')!=inputs['script_sha256'] or fingerprint(result.get('inputs'))!=key: raise WorkflowError('rewrite alignment binding mismatch')
        return 'align-rewrite-'+key[:16]
    patch=binding.get('patch')
    if patch is not None:
        if not isinstance(patch,dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',str(patch.get('id',''))): raise WorkflowError('voice patch绑定无效')
        revision=patch.get('revision_id')
        if revision is not None and (not isinstance(revision,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',revision)): raise WorkflowError('voice patch revision绑定无效')
        kind=('voice-patch-revision-'+patch['id']+'-'+revision) if revision else 'voice-patch-apply-'+patch['id']
        key=patch.get('revision_operation_hash') if revision else patch.get('operation_hash')
        row=store.db.execute('SELECT data,status FROM external_operations WHERE episode_id=? AND kind=? AND inputs_hash=?',(ep,kind,key)).fetchone()
        if row is None or row['status']!='done': raise WorkflowError('patch对齐缺少已完成补丁操作证据')
        result=json.loads(row['data'])
        if result.get('audio_sha256')!=inputs['audio_sha256'] or result.get('inputs',{}).get('script_sha256')!=inputs['script_sha256'] or fingerprint(result.get('inputs'))!=key:
            raise WorkflowError('patch对齐操作与当前音轨不符')
        return ('align-patch-revision-' if revision else 'align-patch-')+key[:16]
    take=binding.get('take')
    if take is None: return 'align'
    if isinstance(take,bool) or not isinstance(take,int) or take<1: raise WorkflowError('voice take绑定无效')
    row=store.db.execute('SELECT data,status FROM external_operations WHERE episode_id=? AND kind=? AND inputs_hash=?',(ep,f'voice-take-{take}',fingerprint(binding.get('inputs',{})))).fetchone()
    if row is None or row['status']!='done' or json.loads(row['data']).get('audio_sha256')!=inputs['audio_sha256']:
        raise WorkflowError('新take对齐缺少已完成配音操作证据')
    return f'align-take-{take}'


def patch_context(store,ep,patch_id,start,end):
    _,arts=checked(store,ep,('script','audio','subtitles'))
    script=artifact_path(store,arts,'script'); audio=artifact_path(store,arts,'audio'); text=script.read_text().strip()
    if any(isinstance(v,bool) or not isinstance(v,int) for v in (start,end)) or not 0<=start<end<=len(text): raise WorkflowError('补丁范围必须为冻结script.strip()的有效Python字符offset')
    target=text[start:end]
    # Closing dialogue marks follow sentence punctuation in Chinese prose.
    prefix=text[:start].rstrip().rstrip('”’」』').rstrip()
    ending=target.rstrip().rstrip('”’」』').rstrip()
    if len(target)>1200 or not target.strip() or (prefix and prefix[-1] not in '。！？；') or not ending or ending[-1] not in '。！？；':
        raise WorkflowError('补丁必须提取原稿完整句子，最多1200字')
    work=folder(store,ep); voice_file=work/'voice.binding.json'; voice=read_json(voice_file)
    pair={'script_sha256':arts['script']['sha256'],'audio_sha256':arts['audio']['sha256']}
    if voice.get('inputs',{}).get('script_sha256')!=pair['script_sha256'] or voice.get('audio_sha256')!=pair['audio_sha256']: raise WorkflowError('补丁原音轨与原稿绑定过期')
    voice_binding_kind(store,ep,voice,pair)
    alignment=read_json(work/'alignment.binding.json')
    if alignment.get('inputs')!=pair or alignment.get('artifact',{}).get('sha256')!=arts['subtitles']['sha256']: raise WorkflowError('补丁必须基于当前已绑定的逐字对齐')
    source=work/'align'/fingerprint(pair)/'alignment.json'
    if source.is_symlink() or source.resolve()!=source or digest(source)!=alignment.get('alignment_sha256'): raise WorkflowError('原始对齐JSON哈希或路径无效')
    value=read_json(source); alignment_srt(value,text)
    chars=value['characters']; selected=[]; pos=0
    for char in chars:
        nxt=pos+len(char['text'])
        if pos<start<nxt or pos<end<nxt: raise WorkflowError('补丁offset没有落在供应商字符边界')
        if pos>=start and nxt<=end: selected.append(char)
        pos=nxt
    if ''.join(c['text'] for c in selected)!=target: raise WorkflowError('补丁范围与逐字对齐原文不一致')
    cut_start=selected[0]['start']; cut_end=selected[-1]['end']
    if cut_end<=cut_start or cut_end>store._probe(audio,'audio')['duration']+0.01: raise WorkflowError('补丁真实切点无效')
    cfg=voice.get('inputs',{})
    if any(not isinstance(cfg.get(k),str) or not cfg[k] for k in ('voice','model','language')): raise WorkflowError('补丁缺原配音settings')
    inputs={'patch_id':patch_id,**pair,'alignment_sha256':digest(source),'voice_binding_sha256':digest(voice_file),'start':start,'end':end,'text_sha256':hashlib.sha256(target.encode()).hexdigest(),'cut_start':cut_start,'cut_end':cut_end,**{k:cfg[k] for k in ('voice','model','language')}}
    return inputs,target,arts,voice


def speech_normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text) if not c.isspace() and unicodedata.category(c)[0] not in ('P','Z','C')).translate(str.maketrans({'她':'他','它':'他','〇':'零'}))


def evidence_json(store,path,sha):
    if not isinstance(path,str) or not isinstance(sha,str): raise WorkflowError('ASR证据路径或哈希缺失')
    source=Path(path).expanduser(); source=source if source.is_absolute() else store.root/source
    source=source.resolve()
    if not source.is_relative_to(store.root) or not source.is_file() or digest(source)!=sha: raise WorkflowError('ASR证据路径或哈希不符')
    return source,read_json(source)


def patch_review(store,file,generated,target,context_parts=None):
    value=read_json(file)
    if value.get('audio_sha256')!=generated['audio_sha256'] or value.get('expected_text_sha256')!=hashlib.sha256(target.encode()).hexdigest(): raise WorkflowError('补丁ASR审查未绑定当前补丁音频及目标原文')
    checks=value.get('checks'); engines=set(); paths=set(); hashes=set(); providers=set(); exact_providers=set(); adjudicated=False
    if not isinstance(checks,list) or len(checks)<2: raise WorkflowError('补丁必须有至少两路独立ASR证据')
    for check in checks:
        if not isinstance(check,dict) or check.get('verdict')!='pass' or not isinstance(check.get('reason'),str) or not check['reason'].strip(): raise WorkflowError('补丁ASR审查不通过或缺少依据')
        engine=check.get('engine')
        if not isinstance(engine,str) or not engine.strip() or engine.strip().casefold() in engines: raise WorkflowError('ASR引擎须独立且非空')
        engines.add(engine.strip().casefold())
        source,response=evidence_json(store,check.get('response_path'),check.get('response_sha256'))
        response_sha=check['response_sha256']
        if source in paths or response_sha in hashes: raise WorkflowError('同一ASR响应路径或哈希不能冒充两路独立证据')
        paths.add(source); hashes.add(response_sha)
        _,receipt=evidence_json(store,check.get('receipt_path'),check.get('receipt_sha256'))
        raw,_=evidence_json(store,receipt.get('raw_response_path'),receipt.get('raw_response_sha256'))
        provider=receipt.get('provider'); model=receipt.get('model'); request=receipt.get('request')
        if raw!=source or receipt.get('raw_response_sha256')!=response_sha or receipt.get('audio_sha256')!=generated['audio_sha256']:
            raise WorkflowError('ASR请求/原始响应封装未绑定本补丁音频')
        if not isinstance(provider,str) or provider in providers: raise WorkflowError('ASR provider无效或同族不能冒充独立识别器')
        if not isinstance(request,dict) or request.get('audio_sha256')!=generated['audio_sha256'] or request.get('model')!=model or not isinstance(request.get('audio_path'),str): raise WorkflowError('ASR请求音频或model证据不符')
        requested=Path(request['audio_path']).expanduser(); requested=requested if requested.is_absolute() else store.root/requested
        if requested.resolve()!=Path(generated['output']).resolve() or digest(requested)!=generated['audio_sha256']: raise WorkflowError('ASR识别的音频不是当前补丁')
        if provider=='qwen':
            if not isinstance(model,str) or not model.startswith('Qwen/Qwen3-ASR-') or not isinstance(response.get('language'),str) or not response['language']: raise WorkflowError('Qwen原始响应或模型身份无效')
            _,health=evidence_json(store,receipt.get('model_identity_path'),receipt.get('model_identity_sha256'))
            if health.get('status')!='ok' or health.get('model')!=model: raise WorkflowError('Qwen真实health模型身份不符')
        elif provider=='faster-whisper':
            if not isinstance(model,str) or not model.startswith('faster-whisper-') or response.get('model')!=model or response.get('audio_sha256')!=generated['audio_sha256'] or not isinstance(response.get('device'),str): raise WorkflowError('Whisper原始模型或音频身份不符')
            segments=response.get('segments')
            if not isinstance(segments,list) or not segments: raise WorkflowError('Whisper原始分段响应缺失')
            for seg in segments:
                if not isinstance(seg,dict) or not isinstance(seg.get('text'),str) or any(isinstance(seg.get(k),bool) or not isinstance(seg.get(k),(int,float)) or not math.isfinite(seg[k]) for k in ('start','end')) or not 0<=seg['start']<seg['end']<=generated['duration']+0.5: raise WorkflowError('Whisper原始分段时间或文字无效')
            if speech_normalize(''.join(seg['text'] for seg in segments))!=speech_normalize(response.get('text','')): raise WorkflowError('Whisper分段与完整响应文字不一致')
        else: raise WorkflowError('未支持的ASR provider，不能仅凭engine标签认定独立')
        providers.add(provider)
        actual=response.get('text')
        if not isinstance(actual,str): raise WorkflowError('ASR响应文字缺失')
        if context_parts is None:
            if speech_normalize(actual)==speech_normalize(target):exact_providers.add(provider)
            else:
                from .asr_variants import adjudicate
                adjudicate(value,actual,target,store.episode(generated.get('episode_id',value.get('episode_id')))['author'],speech_normalize)
                adjudicated=True
        else:
            normalized=speech_normalize(actual); joined=''.join(speech_normalize(part) for part in context_parts)
            if normalized.count(joined)!=1 or any(normalized.count(speech_normalize(part))!=1 for part in context_parts): raise WorkflowError('接缝ASR没有连续且唯一地兑现目标句与下一句，疑似残字/重复/吞字')
    if adjudicated and not exact_providers:raise WorkflowError('ASR adjudication requires another independent exact full transcript')
    return digest(file)


def patch_spoken_text(target,file):
    spoken=Path(file).expanduser().read_text() if file else target
    if ''.join(c for c in spoken if not c.isspace())!=''.join(c for c in target if not c.isspace()):
        raise WorkflowError('speech-text仅可调整空白，不能更改冻结原句汉字、标点或添加音频标签')
    return spoken


def voice_patch(args):
    if getattr(args,'execute',False):
        from .revision_gate import check_revision
        check_revision(args.root,args.episode)
    store=Store(args.root)
    try:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',args.patch_id): raise WorkflowError('patch-id仅允许1到40位字母数字下划线短横线')
        with voice_lock_path(store,args.episode).open('a') as lock:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise WorkflowError('本集配音有活动执行者，禁止补丁并发',409) from None
            try: return _voice_patch(store,args)
            finally: fcntl.flock(lock,fcntl.LOCK_UN)
    finally: store.close()


def _voice_patch(store,args):
    ep=args.episode; patch_id=args.patch_id; generate_kind='voice-patch-generate-'+patch_id
    if getattr(args,'revise',False): return voice_patch_revision(store,args)
    if not args.apply:
        inputs,target,arts,voice=patch_context(store,ep,patch_id,args.start,args.end)
        spoken=patch_spoken_text(target,getattr(args,'speech_text_file',None)); inputs['spoken_text_sha256']=hashlib.sha256(spoken.encode()).hexdigest(); key=fingerprint(inputs)
        work=folder(store,ep)/'voice-patches'/patch_id/key; out=work/'patch.mp3'
        plan={'episode_id':ep,'operation':'voice-patch-generate','mode':'plan','inputs':inputs,'text':target,'speech_text':spoken,'output':str(out)}
        existing=store.db.execute('SELECT inputs_hash FROM external_operations WHERE episode_id=? AND kind=?',(ep,generate_kind)).fetchall()
        if any(row['inputs_hash']!=key for row in existing): raise WorkflowError('patch-id已绑定其他原音轨或范围，不能复用')
        if not args.execute: return plan
        paid_options(args); key,old=begin(store,ep,generate_kind,inputs,args.estimated_usd)
        if old is not None:
            if digest(out)!=old.get('audio_sha256'): raise WorkflowError('已完成补丁音频哈希不符')
            return {'idempotent':True,**old}
        try:
            work.mkdir(parents=True,exist_ok=True)
            if out.exists(): raise WorkflowError('补丁输出已存在，禁止再次收费')
            snapshot=work/'source.voice.binding.json'; snapshot.write_bytes((folder(store,ep)/'voice.binding.json').read_bytes())
            (work/'speech.txt').write_text(spoken)
            run(['el','speak',spoken,'--language',inputs['language'],'-v',inputs['voice'],'-m',inputs['model'],'-o',str(out)])
            duration=store._probe(out,'audio')['duration']
            current,_,_,_=patch_context(store,ep,patch_id,args.start,args.end)
            current_spoken=patch_spoken_text(target,getattr(args,'speech_text_file',None)); current['spoken_text_sha256']=hashlib.sha256(current_spoken.encode()).hexdigest()
            if current!=inputs: raise WorkflowError('补丁生成期间原音轨或范围变化')
            result={'episode_id':ep,'status':'done','operation':'voice-patch-generate','inputs':inputs,'output':str(out),'audio_sha256':digest(out),'duration':duration,'estimated_usd_reserved':args.estimated_usd,'actual_usd':None}
            atomic_json(work/'generated.json',result); finish(store,ep,generate_kind,key,'done',result); return result
        except BaseException:
            finish(store,ep,generate_kind,key,'unknown',{'inputs':inputs,'output':str(out),'estimated_usd_reserved':args.estimated_usd,'actual_usd':None}); raise
    operations(store)
    row=store.db.execute('SELECT * FROM external_operations WHERE episode_id=? AND kind=?',(ep,generate_kind)).fetchone()
    if row is None or row['status']!='done': raise WorkflowError('补丁未完成生成，unknown不能重发或应用')
    generated=json.loads(row['data']); inputs=generated['inputs']; gen_key=row['inputs_hash']
    if fingerprint(inputs)!=gen_key: raise WorkflowError('补丁生成操作指纹不符')
    if (args.start is not None and args.start!=inputs['start']) or (args.end is not None and args.end!=inputs['end']): raise WorkflowError('应用范围与已生成补丁不匹配')
    work=folder(store,ep)/'voice-patches'/patch_id/gen_key; patch=work/'patch.mp3'; out=work/'patched.wav'
    if generated.get('output')!=str(patch) or patch.is_symlink() or digest(patch)!=generated.get('audio_sha256'): raise WorkflowError('补丁音频哈希不符')
    arts=store.artifacts(ep); script=artifact_path(store,arts,'script'); target=script.read_text().strip()[inputs['start']:inputs['end']]
    if digest(script)!=inputs['script_sha256'] or hashlib.sha256(target.encode()).hexdigest()!=inputs['text_sha256']: raise WorkflowError('补丁冻结原稿或范围不匹配')
    if not args.review_file: raise WorkflowError('应用补丁必须提供--review-file两路ASR证据')
    review_sha=patch_review(store,args.review_file,generated,target)
    apply_inputs={'script_sha256':inputs['script_sha256'],'source_audio_sha256':inputs['audio_sha256'],'generate_operation_hash':gen_key,'patch_audio_sha256':generated['audio_sha256'],'review_sha256':review_sha}
    kind='voice-patch-apply-'+patch_id; key=fingerprint(apply_inputs)
    prior=store.db.execute('SELECT * FROM external_operations WHERE episode_id=? AND kind=?',(ep,kind)).fetchone()
    if prior and prior['status']=='done':
        value=json.loads(prior['data'])
        if prior['inputs_hash']!=key or arts.get('audio',{}).get('sha256')!=value.get('audio_sha256') or digest(out)!=value.get('audio_sha256'): raise WorkflowError('已应用补丁与当前音轨不符')
        return {'idempotent':True,**value}
    current,_,_,voice=patch_context(store,ep,patch_id,inputs['start'],inputs['end'])
    if current!={k:v for k,v in inputs.items() if k!='spoken_text_sha256'}: raise WorkflowError('补丁原音轨、范围或绑定已变化')
    if 'spoken_text_sha256' in inputs:
        spoken=patch_spoken_text(target,work/'speech.txt')
        if hashlib.sha256(spoken.encode()).hexdigest()!=inputs['spoken_text_sha256']: raise WorkflowError('补丁发音文本哈希不符')
    source=artifact_path(store,arts,'audio'); source_duration=store._probe(source,'audio')['duration']; patch_duration=store._probe(patch,'audio')['duration']
    plan={'episode_id':ep,'operation':'voice-patch-apply','mode':'plan','inputs':apply_inputs,'cut':[inputs['cut_start'],inputs['cut_end']],'duration_expected':source_duration-(inputs['cut_end']-inputs['cut_start'])+patch_duration,'output':str(out)}
    if not args.execute: return plan
    if prior and prior['status']=='unknown' and args.resume:
        if prior['inputs_hash']!=key: raise WorkflowError('补丁恢复输入不符')
    else: begin(store,ep,kind,apply_inputs)
    try:
        with tempfile.TemporaryDirectory(dir=work,prefix='splice-') as tmp:
            filters=[]; labels=[]
            if inputs['cut_start']>0:
                filters.append(f"[0:a]atrim=end={inputs['cut_start']},asetpts=PTS-STARTPTS[a]"); labels.append('[a]')
            filters.append('[1:a]asetpts=PTS-STARTPTS[b]'); labels.append('[b]')
            if inputs['cut_end']<source_duration:
                filters.append(f"[0:a]atrim=start={inputs['cut_end']},asetpts=PTS-STARTPTS[c]"); labels.append('[c]')
            filters.append(''.join(labels)+f'concat=n={len(labels)}:v=0:a=1[out]')
            staged=Path(tmp)/'patched.wav'
            run(['ffmpeg','-v','error','-nostdin','-y','-i',str(source),'-i',str(patch),'-filter_complex',';'.join(filters),'-map','[out]','-ar','44100','-ac','1','-c:a','pcm_s16le',str(staged)])
            actual=store._probe(staged,'audio')['duration']
            if abs(actual-plan['duration_expected'])>0.5: raise WorkflowError('补丁拼接时长不符，不允许变速凑时长')
            if digest(source)!=inputs['audio_sha256'] or digest(patch)!=generated['audio_sha256'] or digest(script)!=inputs['script_sha256'] or patch_review(store,args.review_file,generated,target)!=review_sha: raise WorkflowError('补丁拼接期间输入或审查证据变化')
            staged.replace(out)
        sha=digest(out)
        result={'episode_id':ep,'operation':'voice-patch-apply','status':'done','inputs':apply_inputs,'source_audio_sha256':inputs['audio_sha256'],'patch_audio_sha256':generated['audio_sha256'],'audio_sha256':sha,'output':str(out),'cut':plan['cut'],'duration':actual,'artifact':store.register(ep,'audio',out)}
        binding={**voice,'audio_sha256':sha,'artifact':result['artifact'],'patch':{'id':patch_id,'operation_hash':key,'source_audio_sha256':inputs['audio_sha256'],'patch_audio_sha256':generated['audio_sha256']}}
        atomic_json(work/'voice.binding.json',binding); atomic_json(folder(store,ep)/'voice.binding.json',binding)
        atomic_json(work/'applied.json',result); finish(store,ep,kind,key,'done',result); return result
    except BaseException:
        finish(store,ep,kind,key,'unknown',{'inputs':apply_inputs}); raise


def evidence_audio(store,path,sha):
    if not isinstance(path,str) or not isinstance(sha,str): raise WorkflowError('音频证据路径或SHA缺失')
    source=Path(path).expanduser(); source=source if source.is_absolute() else store.root/source
    if not source.resolve().is_relative_to(store.root) or source.is_symlink() or not source.is_file() or digest(source)!=sha: raise WorkflowError('音频证据路径或SHA不符')
    return source


def pcm_hash(path,start=None,duration=None):
    argv=['ffmpeg','-v','error','-nostdin','-i',str(path),'-map','0:a:0']
    if start is not None: argv+=['-af',f'atrim=start={start}:end={start+duration},asetpts=PTS-STARTPTS']
    value=run(argv+['-ar','44100','-ac','1','-c:a','pcm_s16le','-f','hash','-hash','sha256','-'])
    found=re.search(r'SHA256=([0-9a-f]{64})',value)
    if not found: raise WorkflowError('无法核验PCM逐样本哈希')
    return found.group(1)


def splice_audio(source,patch,start,end,source_duration,out):
    filters=[]; labels=[]
    if start>0: filters.append(f'[0:a]atrim=end={start},asetpts=PTS-STARTPTS[a]'); labels.append('[a]')
    filters.append('[1:a]asetpts=PTS-STARTPTS[b]'); labels.append('[b]')
    if end<source_duration: filters.append(f'[0:a]atrim=start={end},asetpts=PTS-STARTPTS[c]'); labels.append('[c]')
    filters.append(''.join(labels)+f'concat=n={len(labels)}:v=0:a=1[out]')
    run(['ffmpeg','-v','error','-nostdin','-y','-i',str(source),'-i',str(patch),'-filter_complex',';'.join(filters),'-map','[out]','-ar','44100','-ac','1','-c:a','pcm_s16le',str(out)])


def revision_silence(store,source,cut_end,evidence):
    if not isinstance(evidence,dict) or evidence.get('source_audio_sha256')!=digest(source) or evidence.get('noise_db')!=-35: raise WorkflowError('静音证据未绑定原take或-35dB阈值')
    path=Path(evidence.get('log_path','')); path=path if path.is_absolute() else store.root/path
    if not path.resolve().is_relative_to(store.root) or not path.is_file() or digest(path)!=evidence.get('log_sha256'): raise WorkflowError('静音日志路径或SHA不符')
    window=evidence.get('window_start')
    if isinstance(window,bool) or not isinstance(window,(int,float)) or not math.isfinite(window) or window<0: raise WorkflowError('静音日志起始窗无效')
    events=re.findall(r'silence_(start|end):\s*([0-9.]+)',path.read_text()); opened=None; contained=False
    for event,value in events:
        point=window+float(value)
        if event=='start': opened=point
        elif opened is not None:
            if opened<=cut_end<=point: contained=True
            opened=None
    if not contained: raise WorkflowError('扩展切点不在已记录静音区间内')
    # 不信任手写静音区间；在原take实际切点周围再做本地80ms核验。
    start=max(0,cut_end-0.04); duration=0.08
    try: result=subprocess.run(['ffmpeg','-v','info','-nostdin','-ss',str(start),'-t',str(duration),'-i',str(source),'-af','silencedetect=noise=-35dB:d=0.03','-f','null','-'],capture_output=True,text=True,timeout=30)
    except (OSError,subprocess.TimeoutExpired): raise WorkflowError('切点静音实测未完成') from None
    starts=[float(v) for v in re.findall(r'silence_start:\s*([0-9.]+)',result.stderr)]
    ends=[float(v) for v in re.findall(r'silence_end:\s*([0-9.]+)',result.stderr)]
    if result.returncode or not starts or not ends or min(starts)>0.005 or max(ends)<duration-0.005: raise WorkflowError('原take扩展切点附近不是连续-35dB静音')
    return hashlib.sha256(result.stderr.encode()).hexdigest()


def voice_patch_revision(store,args):
    ep=args.episode; patch_id=args.patch_id; rev=args.revision_id
    if not isinstance(rev,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',rev) or not args.revision_file: raise WorkflowError('revision须有合法--revision-id及--revision-file')
    _,arts=checked(store,ep,('script','audio')); text=artifact_path(store,arts,'script').read_text().strip(); operations(store)
    gen=store.db.execute('SELECT * FROM external_operations WHERE episode_id=? AND kind=?',(ep,'voice-patch-generate-'+patch_id)).fetchone()
    base=store.db.execute('SELECT * FROM external_operations WHERE episode_id=? AND kind=?',(ep,'voice-patch-apply-'+patch_id)).fetchone()
    if gen is None or base is None or gen['status']!='done' or base['status']!='done': raise WorkflowError('revision须保留已done的原生成与apply操作')
    generated=json.loads(gen['data']); original_apply=json.loads(base['data']); original=generated['inputs']
    if fingerprint(original)!=gen['inputs_hash'] or fingerprint(original_apply['inputs'])!=base['inputs_hash'] or arts['script']['sha256']!=original['script_sha256']: raise WorkflowError('revision原稿或原操作指纹不符')
    work=folder(store,ep)/'voice-patches'/patch_id/gen['inputs_hash']
    snapshot=work/'source.voice.binding.json'
    if digest(snapshot)!=original['voice_binding_sha256']: raise WorkflowError('revision原take不可变快照已变化')
    source_binding=read_json(snapshot); source_art=source_binding.get('artifact',{})
    source=evidence_audio(store,str(store.root/source_art.get('path','')),original['audio_sha256'])
    patch=evidence_audio(store,generated['output'],generated['audio_sha256'])
    original_binding=read_json(work/'voice.binding.json')
    if original_binding.get('audio_sha256')!=original_apply['audio_sha256']: raise WorkflowError('原apply绑定已变化')
    pair={'script_sha256':original['script_sha256'],'audio_sha256':original['audio_sha256']}
    alignment=folder(store,ep)/'align'/fingerprint(pair)/'alignment.json'
    if digest(alignment)!=original['alignment_sha256']: raise WorkflowError('原take逐字对齐已变化')
    value=read_json(alignment); alignment_srt(value,text)
    position=0; next_start=None
    for char in value['characters']:
        if position>=original['end'] and any(c.isalnum() for c in char['text']): next_start=char['start']; break
        position+=len(char['text'])
    if next_start is None: raise WorkflowError('没有下一句实字边界，不能扩展切点')
    tail=text[original['end']:]; match=re.match(r'\s*(.*?[。！？；])',tail,re.S)
    if not match: raise WorkflowError('下一句原文边界缺失')
    first=text[original['start']:original['end']]; following=match.group(1); expected=first+match.group(0)
    proof=read_json(args.revision_file); proof_sha=digest(args.revision_file); cut=proof.get('cut_end')
    if isinstance(cut,bool) or not isinstance(cut,(int,float)) or not math.isfinite(cut) or not original['cut_end']<cut<next_start: raise WorkflowError('扩展切点必须在原句尾之后且下一实字之前')
    candidate=evidence_audio(store,proof.get('candidate_path'),proof.get('candidate_sha256')); seam=evidence_audio(store,proof.get('seam_path'),proof.get('seam_sha256'))
    seam_start=proof.get('seam_start')
    if isinstance(seam_start,bool) or not isinstance(seam_start,(int,float)) or not math.isfinite(seam_start) or seam_start<0: raise WorkflowError('接缝片段起点无效')
    seam_duration=store._probe(seam,'audio')['duration']; silence_sha=revision_silence(store,source,cut,proof.get('silence'))
    _,review=evidence_json(store,proof.get('review_file'),proof.get('review_sha256'))
    asr_source={'audio_sha256':proof['seam_sha256'],'output':str(seam),'duration':seam_duration}
    review_sha=patch_review(store,proof['review_file'],asr_source,expected,context_parts=(first,following))
    if not seam_start<=original['cut_start']<seam_start+seam_duration or not seam_start<next_start<seam_start+seam_duration: raise WorkflowError('ASR接缝片段未覆盖完整替换句及下一句')
    if pcm_hash(candidate,seam_start,seam_duration)!=pcm_hash(seam): raise WorkflowError('ASR片段不是该候选音轨的真实切片')
    source_duration=store._probe(source,'audio')['duration']; patch_duration=store._probe(patch,'audio')['duration']
    expected_duration=source_duration-(cut-original['cut_start'])+patch_duration
    if abs(store._probe(candidate,'audio')['duration']-expected_duration)>0.5: raise WorkflowError('候选时长不是原take定点替换结果')
    kind='voice-patch-revision-'+patch_id+'-'+rev; target=work/'revisions'/rev; out=target/'revised.wav'
    inputs={'script_sha256':original['script_sha256'],'source_audio_sha256':original['audio_sha256'],'base_apply_operation_hash':base['inputs_hash'],'patch_audio_sha256':generated['audio_sha256'],'cut_start':original['cut_start'],'cut_end':cut,'next_word_start':next_start,'candidate_sha256':proof['candidate_sha256'],'seam_sha256':proof['seam_sha256'],'revision_evidence_sha256':proof_sha,'review_sha256':review_sha,'silence_log_sha256':proof['silence']['log_sha256']}
    key=fingerprint(inputs); prior=store.db.execute('SELECT * FROM external_operations WHERE episode_id=? AND kind=?',(ep,kind)).fetchone()
    if prior and prior['status']=='done':
        old=json.loads(prior['data'])
        if prior['inputs_hash']!=key or arts['audio']['sha256']!=old.get('audio_sha256') or digest(out)!=old.get('audio_sha256'): raise WorkflowError('revision已完成但当前音轨或输入不符')
        return {'idempotent':True,**old}
    if arts['audio']['sha256']!=original_apply['audio_sha256']: raise WorkflowError('当前音轨已不是原apply，禁止revision覆盖其他改动')
    # 验证整个候选确实只替换了原take指定区间，而非只凭片段ASR通过。
    with tempfile.TemporaryDirectory(dir=work,prefix='verify-revision-') as tmp:
        canonical=Path(tmp)/'canonical.wav'; splice_audio(source,patch,original['cut_start'],cut,source_duration,canonical)
        if pcm_hash(canonical)!=pcm_hash(candidate): raise WorkflowError('候选整轨PCM与原take受控定点拼接不一致')
    plan={'episode_id':ep,'operation':'voice-patch-revision','mode':'plan','inputs':inputs,'expected_context_text':expected,'output':str(out),'paid_calls':0}
    if not args.execute: return plan
    if prior and prior['status']=='unknown' and args.resume:
        if prior['inputs_hash']!=key: raise WorkflowError('revision恢复输入不符')
    else: begin(store,ep,kind,inputs)
    try:
        target.mkdir(parents=True,exist_ok=True)
        if digest(candidate)!=proof['candidate_sha256'] or digest(args.revision_file)!=proof_sha or patch_review(store,proof['review_file'],asr_source,expected,context_parts=(first,following))!=review_sha: raise WorkflowError('revision候选或审查证据执行期间变化')
        # 沿用已经逐样本验证的候选，不重编码、不变速。
        import shutil
        shutil.copyfile(candidate,out)
        if digest(out)!=proof['candidate_sha256']: raise WorkflowError('revision复制期间候选变化')
        sha=digest(out); result={'episode_id':ep,'operation':'voice-patch-revision','status':'done','inputs':inputs,'audio_sha256':sha,'artifact':store.register(ep,'audio',out),'output':str(out),'duration':store._probe(out,'audio')['duration'],'paid_calls':0}
        binding={**original_binding,'audio_sha256':sha,'artifact':result['artifact'],'patch':{**original_binding['patch'],'revision_id':rev,'revision_operation_hash':key}}
        atomic_json(target/'voice.binding.json',binding); atomic_json(folder(store,ep)/'voice.binding.json',binding); atomic_json(target/'revision.json',result)
        finish(store,ep,kind,key,'done',result); return result
    except BaseException:
        finish(store,ep,kind,key,'unknown',{'inputs':inputs}); raise


def scene_framing(scene):
    motion=scene.get('motion','static')
    if motion not in ('push_in','pull_out','pan_left','pan_right','static'):
        raise WorkflowError('镜头motion须为push_in/pull_out/pan_left/pan_right/static')
    framing=scene.get('framing',{})
    if not isinstance(framing,dict) or set(framing)-{'zoom','x','y'}: raise WorkflowError('framing只支持zoom/x/y')
    values={key:framing.get(key,default) for key,default in (('zoom',1.0),('x',0.5),('y',0.5))}
    for key,value in values.items():
        low,high=(1,1.5) if key=='zoom' else (0,1)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
            raise WorkflowError('framing.'+key+'超出允许范围')
    return motion,values


def scene_filter(scene,width,height):
    """Static by default; video is sampled on the same CFR30 absolute picture clock."""
    base=(f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
          f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1')
    from .scene_cleanup import normalize_cleanup
    normalize_cleanup(scene,scene.get('sha256'))
    if scene.get('media_type')=='video' or scene['motion']=='static':
        return base+',fps=30,setpts=PTS-STARTPTS'
    # Historical explicit motion stays reproducible; new static scenes never use zoompan.
    motion=scene['motion']; framing=scene['framing']; zoom=framing['zoom']
    frames=max(2,math.ceil(scene['duration']*30)); progress=f'min(on/{frames-1},1)'
    z=f'{zoom}+0.08*{progress}' if motion=='push_in' else f'{zoom}+0.08*(1-{progress})' if motion=='pull_out' else str(zoom+0.08)
    x=str(framing['x']); y=str(framing['y'])
    if motion.startswith('pan_'):
        x=f'max(0,min(1,{x}+0.25*({progress}-0.5)))' if motion=='pan_right' else f'max(0,min(1,{x}-0.25*({progress}-0.5)))'
    return (f'scale={width*2}:{height*2}:force_original_aspect_ratio=decrease,'
            f'pad={width*2}:{height*2}:(ow-iw)/2:(oh-ih)/2,setsar=1,'
            f"zoompan=z='{z}':x='(iw-iw/zoom)*({x})':y='(ih-ih/zoom)*({y})':d=1:s={width}x{height}:fps=30")


def render(args):
    if getattr(args,'execute',False):
        from .revision_gate import check_revision
        check_revision(args.root,args.episode)
    store=Store(args.root)
    try:
        ep=args.episode; _,arts=checked(store,ep,('structure','audio','script','subtitles'))
        caption_contract=None
        if store.config.get('quality',{}).get('require_semantic_caption_units'):
            try:
                from .captions import verify_store_caption_binding
                caption_contract=verify_store_caption_binding(store,ep,arts,require_semantic=True)['contract']
            except ValueError as exc: raise WorkflowError(str(exc)) from exc
        work=folder(store,ep)
        manifest=work/'scenes.json'
        if manifest.exists() or manifest.is_symlink():
            # 技术镜头时长在配音后确定，不能迫使冻结叙事结构重新审批。
            if manifest.is_symlink() or not manifest.is_file() or not manifest.resolve().is_relative_to(work.resolve()) or work.resolve()!=work:
                raise WorkflowError('镜头清单必须是本集 production/scenes.json 实体文件')
            source='production'
        else:
            manifest=artifact_path(store,arts,'structure'); source='structure'
        manifest_sha=digest(manifest); scenes=read_json(manifest).get('scenes')
        if not isinstance(scenes,list) or not scenes: raise WorkflowError('production/scenes.json 或 structure.scenes 必须有真实镜头')
        visual_contract=store.visual_contract(ep)
        audio=artifact_path(store,arts,'audio'); total=0; inputs=[]; scene_ids=set()
        for scene in scenes:
            if not isinstance(scene,dict): raise WorkflowError('镜头必须是对象')
            sid=scene.get('id'); image=scene.get('image'); duration=scene.get('duration')
            if not isinstance(sid,str) or not sid or sid in scene_ids: raise WorkflowError('镜头ID必须非空且唯一')
            scene_ids.add(sid)
            if scene.get('placeholder') or scene.get('status') in ('placeholder','draft') or scene.get('provenance') in (None,'','placeholder'):
                raise WorkflowError('镜头必须有真实素材provenance，不能使用占位图')
            media_type=scene.get('media_type','image')
            if media_type not in ('image','video'): raise WorkflowError('media_type须为image/video')
            if not isinstance(image,str): raise WorkflowError('镜头image缺失')
            path=Path(image).expanduser(); path=path if path.is_absolute() else store.root/path
            path=path.resolve()
            if not path.is_relative_to(store.root) or not path.is_file() or path.suffix.lower() not in (('.mp4','.mov','.webm') if media_type=='video' else ('.png','.jpg','.jpeg','.webp')): raise WorkflowError('镜头图必须为项目内真实图片')
            if any(w in path.stem.casefold() for w in ('placeholder','fixture','dummy')): raise WorkflowError('正式镜头不能使用占位/测试图')
            if isinstance(duration,bool) or not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<=0: raise WorkflowError('镜头duration无效')
            motion,framing=scene_framing(scene)
            item={'id':sid,'image':str(path),'sha256':digest(path),'duration':duration,'motion':motion,'framing':framing}
            if media_type=='video':
                start=scene.get('trim_start',0)
                if isinstance(start,bool) or not isinstance(start,(int,float)) or not math.isfinite(start) or start<0 or duration>10: raise WorkflowError('视频裁取须为非负起点且不超过10秒')
                if motion!='static' or scene.get('framing'): raise WorkflowError('视频禁止附加Ken Burns或裁切动效')
                if start+duration>store._probe(path,'video')['duration']+0.001: raise WorkflowError('视频裁取超出真实素材')
                item.update(media_type='video',trim_start=start)
            from .scene_cleanup import normalize_cleanup
            cleanup=normalize_cleanup(scene,item['sha256'])
            if cleanup:
                if not visual_contract: raise WorkflowError('caption_band_cleanup 必须有独立交付视觉审查合同')
                item['caption_band_cleanup']=cleanup
            inputs.append(item); total+=duration
        probe=store._probe(audio,'audio')
        from .scene_cleanup import scene_identity
        if visual_contract and visual_contract['scenes']!=[scene_identity(s,store.root) for s in inputs]:
            raise WorkflowError('实际渲染镜头必须逐项匹配已审视觉合同')
        if abs(total-probe['duration'])>(.04 if any(s.get('media_type')=='video' for s in inputs) else .5): raise WorkflowError('镜头总时长与音频相差超过0.5秒')
        binding=read_json(work/'alignment.binding.json')
        if binding.get('inputs')!={'script_sha256':arts['script']['sha256'],'audio_sha256':arts['audio']['sha256']} or binding.get('artifact',{}).get('sha256')!=arts['subtitles']['sha256']:
            raise WorkflowError('字幕对齐绑定已过期或缺失')
        cfg=store.config.get('render',{}); width=cfg.get('width',1920); height=cfg.get('height',1080)
        if any(isinstance(n,bool) or not isinstance(n,int) or n<=0 or n%2 for n in (width,height)): raise WorkflowError('视频尺寸必须为正偶数')
        plan={'episode_id':ep,'mode':'plan','operation':'render','scenes':inputs,'duration':total,'size':[width,height],'burn_subtitles':False,'fps':30,'scene_manifest':{'source':source,'path':str(manifest),'sha256':manifest_sha},'artifact_hashes':{k:arts[k]['sha256'] for k in ('structure','script','audio','subtitles')}}
        if visual_contract: plan['visual_contract']=visual_contract
        if caption_contract: plan['caption_contract']=caption_contract
        if not args.execute: return plan
        target=work/'render'/fingerprint({'plan':plan,'audio':arts['audio']['sha256']}); target.mkdir(parents=True,exist_ok=True)
        out=target/'video.mp4'
        clip_dir=target/'clips';clip_dir.mkdir(exist_ok=True)
        scene_clips=[]
        with tempfile.TemporaryDirectory(dir=target,prefix='render-') as tmp:
            clips=[]; elapsed=0; frame_end=0
            for i,scene in enumerate(inputs):
                clip=clip_dir/f'{i:03d}.mp4'; clips.append(clip)
                from .scene_assembly import frame
                start_time=elapsed;start_frame=frame_end
                elapsed+=scene['duration']; next_end=frame(elapsed); count=next_end-frame_end; frame_end=next_end
                if count<1: raise WorkflowError('镜头短于一帧')
                source_args=(['-ss',str(scene['trim_start']),'-i',scene['image']] if scene.get('media_type')=='video' else ['-loop','1','-framerate','30','-i',scene['image']])
                run(['ffmpeg','-v','error','-nostdin','-y',*source_args,'-vf',scene_filter(scene,width,height)+',scale=iw:ih:out_range=tv:out_color_matrix=bt709,format=yuv420p,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709','-frames:v',str(count),'-r','30','-c:v','libx264','-preset','fast','-crf',('0' if scene['motion']=='static' and scene.get('media_type')!='video' else '18'),'-bf','0','-threads','4','-pix_fmt','yuv420p','-video_track_timescale','15360','-color_range','tv','-colorspace','bt709','-color_trc','bt709','-color_primaries','bt709','-an',str(clip)])
                scene_clips.append({'id':scene['id'],'source_start':start_time,'source_end':elapsed,'start_frame':start_frame,'end_frame':next_end,'frame_count':count,'source_path':scene['image'],'source_sha256':scene['sha256'],'clip_path':str(clip),'clip_sha256':digest(clip)})
            from .chapter_mix import validate_picture_segments
            validate_picture_segments(clips,[r['frame_count'] for r in scene_clips])
            listing=Path(tmp)/'clips.txt'; listing.write_text(''.join(f"file '{p.resolve()}'\n" for p in clips))
            staged=Path(tmp)/'final.mp4'
            run(['ffmpeg','-v','error','-nostdin','-y','-f','concat','-safe','0','-i',str(listing),'-i',str(audio),'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-movflags','+faststart',str(staged)])
            actual=store._probe(staged,'video')
            if abs(actual['duration']-probe['duration'])>0.5 or not any(s.get('codec_type')=='audio' for s in actual['streams']): raise WorkflowError('合成视频时长或音频不合格')
            if store.verify_hashes(ep) or digest(manifest)!=manifest_sha or any(digest(s['image'])!=s['sha256'] for s in inputs): raise WorkflowError('渲染期间输入素材发生变化')
            if visual_contract and store.visual_contract(ep)!=visual_contract: raise WorkflowError('渲染期间视觉合同发生变化')
            if caption_contract:
                try:
                    if verify_store_caption_binding(store,ep,arts,require_semantic=True)['contract']!=caption_contract: raise ValueError('渲染期间字幕消费绑定变化')
                except ValueError as exc: raise WorkflowError(str(exc)) from exc
            staged.replace(out)
        result={'episode_id':ep,'status':'done','duration':actual['duration'],'burn_subtitles':False,'artifact':store.register(ep,'video',out),'inputs':plan}
        clip_manifest=target/'scene-clips.json'
        atomic_json(clip_manifest,{'schema':'qy.scene.clips.v1','fps':30,'project_root':str(store.root),'scene_manifest':plan['scene_manifest'],'visual_contract':plan.get('visual_contract'),'artifact_hashes':plan['artifact_hashes'],'scenes':scene_clips})
        result['scene_clips']={'path':str(clip_manifest),'sha256':digest(clip_manifest)}
        atomic_json(work/'render.binding.json',result); return result
    finally: store.close()


def youtube_service(profile):
    from .providers import youtube_service as service
    return service(profile)


def api_execute(request):
    try: return request.execute()
    except Exception as exc: raise WorkflowError('YouTube API调用失败：'+type(exc).__name__+'；请核对原操作，不要重发') from None


def channel_config(store):
    cfg=store.config['channel']
    channel=cfg.get('id'); profile=cfg.get('youtube_profile')
    if not isinstance(channel,str) or not re.fullmatch(r'UC[A-Za-z0-9_-]{22}',channel): raise WorkflowError('执行上传需要明确YouTube channel_id（UC开头24字符）')
    if not isinstance(profile,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',profile): raise WorkflowError('执行上传需要独立YouTube profile')
    if profile in store.config.get('publication',{}).get('forbidden_profiles',[]): raise WorkflowError('profile is explicitly forbidden')
    return channel,profile


def shipping_plan(store,ep):
    episode=store.episode(ep)
    if episode.get('review_only'): raise WorkflowError('review_only 永久禁止上传',409)
    if episode.get('editorial_status')=='withdrawn': raise WorkflowError('已退稿故事禁止生成上传计划',409)
    arts=store.artifacts(ep)
    meta=read_json(artifact_path(store,arts,'metadata')) if 'metadata' in arts else {'title':episode['title'],'description':''}
    scheduled=timestamp(episode['scheduled_at']); local=scheduled.astimezone(ZoneInfo('Asia/Tokyo'))
    produced=dt.date.fromisoformat(episode['production_date'])
    if local.date()!=produced+dt.timedelta(days=2) or (local.hour,local.minute,local.second)!=(20,0,0): raise WorkflowError('Story Animation排期必须制作日+2天东京20点整')
    return {'episode_id':ep,'channel':episode['channel'],'title':meta.get('title'),'description':meta.get('description'),'scheduled_at':episode['scheduled_at'],'privacy':'private','artifacts':arts,'youtube':{'channel_id':store.config['channel'].get('id'),'profile':store.config['channel'].get('youtube_profile')},'mode':'plan'}


def check_remote(store,plan,video_id):
    channel,profile=channel_config(store)
    if not isinstance(video_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id): raise WorkflowError('video_id无效')
    result=api_execute(youtube_service(profile).videos().list(part='snippet,status',id=video_id))
    items=result.get('items',[])
    if len(items)!=1: raise WorkflowError('远端视频不存在或不可访问')
    item=items[0]; sn=item.get('snippet',{}); status=item.get('status',{})
    if sn.get('channelId')!=channel or sn.get('title')!=plan['title']: raise WorkflowError('远端频道或标题不符')
    receipt={'episode_id':plan['episode_id'],'channel':plan['channel'],'channel_id':channel,'video_id':video_id,'scheduled_at':plan['scheduled_at'],'verified_at':utcnow(),'verification':'youtube_api_readback','actual_public_at':None,'artifact_hashes':{k:v['sha256'] for k,v in plan['artifacts'].items()}}
    if status.get('privacyStatus')=='private':
        if not status.get('publishAt') or timestamp(status['publishAt'])!=timestamp(plan['scheduled_at']): raise WorkflowError('远端视频未按精确时间private定时发布')
        receipt['status']='scheduled'
    elif status.get('privacyStatus')=='public':
        actual=timestamp(sn.get('publishedAt'))
        if timestamp(plan['scheduled_at'])>dt.datetime.now(dt.timezone.utc) or actual<timestamp(plan['scheduled_at']) or actual>dt.datetime.now(dt.timezone.utc): raise WorkflowError('视频提前公开，排期合同不符')
        receipt.update(status='public',actual_public_at=actual.isoformat(),actual_public_at_source='youtube.snippet.publishedAt',public_observed_at=utcnow())
    else: raise WorkflowError('远端视频不是private scheduled或public')
    return receipt


def persist_receipt(store,receipt):
    path=folder(store,receipt['episode_id'])/'publication.json'
    existing=store.db.execute('SELECT data FROM publications WHERE episode_id=?',(receipt['episode_id'],)).fetchone()
    if existing:
        old=json.loads(existing['data'])
        if old.get('artifact_hashes') and old['artifact_hashes']!=receipt['artifact_hashes']: raise WorkflowError('已发布视频的产物绑定不符，禁止换稿后恢复旧视频')
        if old.get('video_id')==receipt['video_id'] and old.get('scheduled_at')==receipt['scheduled_at'] and old.get('channel_id')==receipt['channel_id'] and old.get('status')==receipt['status']:
            return old
    atomic_json(path,receipt); store.publication(receipt['episode_id'],path,verified=True)
    return receipt


def ship(args):
    store=Store(args.root)
    try:
        ep=args.episode; plan=shipping_plan(store,ep); work=folder(store,ep)
        atomic_json(work/'upload-plan.json',plan)
        if not args.execute: return plan
        existing=store.db.execute('SELECT data FROM publications WHERE episode_id=?',(ep,)).fetchone()
        if existing: return {'idempotent':True,**json.loads(existing['data'])}
        if store.episode(ep).get('review_only') or store.episode(ep).get('verification_only'): raise WorkflowError('验收样片禁止正式上传')
        validation=store.validate(ep)
        if not validation['passed']: raise WorkflowError('上传前验证失败：'+'；'.join(validation['errors']))
        if timestamp(plan['scheduled_at'])<=dt.datetime.now(dt.timezone.utc): raise WorkflowError('定时发布时间已过，不自动改期')
        channel,profile=channel_config(store)
        budget=store.config['budget']
        per=store.db.execute('SELECT COALESCE(SUM(amount),0) FROM costs WHERE episode_id=?',(ep,)).fetchone()[0]
        month=store.db.execute('SELECT COALESCE(SUM(amount),0) FROM costs WHERE substr(created_at,1,7)=?',(utcnow()[:7],)).fetchone()[0]
        if per>store.episode_budget_cap(ep) or month>budget['monthly_usd']: raise WorkflowError('当前费用/保守预留已超过预算，拒绝上传')
        # 明确whoami核对，不让yt默认profile参与。
        who=json.loads(run(['yt','whoami','--profile',profile],timeout=120))
        # 共享yt输出是channels列表；容忍其单频道dict输出。
        candidates=who if isinstance(who,list) else who.get('channels',[who])
        if channel not in [c.get('id') or c.get('channel_id') for c in candidates]: raise WorkflowError('OAuth身份与Story Animation目标频道不符')
        inputs={k:plan[k] for k in ('episode_id','channel','title','description','scheduled_at','artifacts')}; inputs['youtube_channel_id']=channel; inputs['profile']=profile
        key,old=begin(store,ep,'ship',inputs)
        if old is not None: return {'idempotent':True,**old}
        arts=plan['artifacts']; description=work/'description.txt'; description.write_text(plan['description'])
        argv=['yt','upload','--profile',profile,'--file',str(artifact_path(store,arts,'video')),'--title',plan['title'],'--description-file',str(description),'--privacy','private','--publish-at',plan['scheduled_at'],'--language','zh-CN','--thumbnail',str(artifact_path(store,arts,'cover'))]
        video_id=None
        try:
            response=json.loads(run(argv)); video_id=response.get('video_id')
            # 上传成功ID即刻落盘，后续readback失败也不得重传。
            finish(store,ep,'ship',key,'accepted',{'inputs':inputs,'video_id':video_id})
            receipt=check_remote(store,plan,video_id); receipt=persist_receipt(store,receipt)
            subtitle_result=caption_sync(store,plan,video_id,True)
            finish(store,ep,'ship',key,'done',receipt); return {**receipt,'captions':subtitle_result}
        except BaseException:
            finish(store,ep,'ship',key,'unknown',{'inputs':inputs,'video_id':video_id,'next':'verify --video-id（禁止重传）'}); raise
    finally: store.close()


def verify(args):
    store=Store(args.root)
    try:
        ep=args.episode; plan=shipping_plan(store,ep); operations(store)
        validation=store.validate(ep)
        if not validation['passed']: raise WorkflowError('本地验证不通过，拒绝恢复发布收据')
        receipt=check_remote(store,plan,args.video_id)
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            for row in store.db.execute('SELECT * FROM external_operations WHERE episode_id=? AND kind=?',(ep,'ship')).fetchall():
                value=json.loads(row['data'])
                original=value.get('inputs',{})
                if original.get('artifacts') and original['artifacts']!=plan['artifacts']: raise WorkflowError('未决上传的输入已变化，不能将旧视频记到新稿')
                if value.get('video_id') and value['video_id']!=args.video_id: raise WorkflowError('已有video_id不符，不能替换',409)
        receipt=persist_receipt(store,receipt)
        with store.db:
            store.db.execute('UPDATE external_operations SET status=?,data=? WHERE episode_id=? AND kind=?',('done',json.dumps(receipt,ensure_ascii=False),ep,'ship'))
        return receipt
    finally: store.close()


def caption_media(path):
    try: from googleapiclient.http import MediaFileUpload
    except ImportError: raise WorkflowError('YouTube适配需要google-api-python-client 与 google-auth Python依赖') from None
    return MediaFileUpload(str(path),mimetype='application/octet-stream',resumable=False)


def import_publication(args):
    """导入文件只是核查线索，正式事实仍须实时向平台核实。"""
    from argparse import Namespace
    store=Store(args.root)
    try:
        candidate=read_json(args.file); episode=store.episode(args.episode)
        for key in ('episode_id','channel','scheduled_at'):
            expected=episode['id' if key=='episode_id' else key]
            if candidate.get(key)!=expected: raise WorkflowError('待核查收据身份/排期不符：'+key,409)
        if episode.get('review_only') or episode.get('verification_only'): raise WorkflowError('验收样片禁止导入正式发布记录',409)
        channel,_=channel_config(store)
        if candidate.get('channel_id')!=channel: raise WorkflowError('待核查收据频道不符',409)
        video_id=candidate.get('video_id')
    finally: store.close()
    # 不采信文件自报的status、actual_public_at或verification标志。
    return verify(Namespace(root=args.root,episode=args.episode,video_id=video_id))


def caption_sync(store,plan,video_id,execute=False):
    arts=plan['artifacts']
    if 'subtitles' not in arts: raise WorkflowError('缺少外挂字幕')
    inputs={'video_id':video_id,'subtitles_sha256':arts['subtitles']['sha256'],'language':'zh-CN'}
    name='qy-'+plan['episode_id']+'-'+inputs['subtitles_sha256'][:12]
    service=youtube_service(channel_config(store)[1]); operations(store)
    tracks=api_execute(service.captions().list(part='snippet',videoId=video_id)).get('items',[])
    matches=[track for track in tracks if track.get('snippet',{}).get('name')==name and track.get('snippet',{}).get('language')=='zh-CN']
    if len(matches)>1: raise WorkflowError('远端同名字幕重复，需要人工核对')
    if matches:
        track=matches[0]; status=track['snippet'].get('status')
        if status=='failed': raise WorkflowError('远端字幕处理失败，禁止自动重传')
        result={'video_id':video_id,'caption_id':track['id'],'status':'verified' if status=='serving' else 'processing','inputs':inputs}
        if status=='serving':
            with store.db: store.db.execute('UPDATE external_operations SET status=?,data=? WHERE episode_id=? AND kind=? AND inputs_hash=?',('done',json.dumps(result),plan['episode_id'],'captions',fingerprint(inputs)))
        return result
    if not execute: return {'video_id':video_id,'status':'missing','inputs':inputs}
    key,old=begin(store,plan['episode_id'],'captions',inputs)
    if old is not None: raise WorkflowError('先前字幕已处理但远端丢失，禁止自动重传')
    try:
        track=api_execute(service.captions().insert(part='snippet',body={'snippet':{'videoId':video_id,'language':'zh-CN','name':name,'isDraft':False}},media_body=caption_media(artifact_path(store,arts,'subtitles'))))
        finish(store,plan['episode_id'],'captions',key,'accepted',{'caption_id':track['id'],'inputs':inputs})
        return caption_sync(store,plan,video_id,False)
    except BaseException:
        finish(store,plan['episode_id'],'captions',key,'unknown',{'inputs':inputs,'next':'captions（先只读核对，不重传）'}); raise


def captions(args):
    store=Store(args.root)
    try:
        ep=args.episode; row=store.db.execute('SELECT data FROM publications WHERE episode_id=?',(ep,)).fetchone()
        if not row: raise WorkflowError('先 ship/verify 建立发布身份')
        old=json.loads(row['data']); plan=shipping_plan(store,ep)
        if not args.execute: return {'mode':'plan','operation':'captions','episode_id':ep,'video_id':old['video_id'],'subtitles':plan['artifacts'].get('subtitles')}
        check_remote(store,plan,old['video_id'])
        return caption_sync(store,plan,old['video_id'],True)
    finally: store.close()


def observe(args):
    """只读采样，先证实公开身份；未知指标保留 null。"""
    store=Store(args.root)
    try:
        ep=args.episode; row=store.db.execute('SELECT data FROM publications WHERE episode_id=?',(ep,)).fetchone()
        if not row: raise WorkflowError('先 ship/verify 建立正式发布收据')
        old=json.loads(row['data']); plan=shipping_plan(store,ep)
        receipt=check_remote(store,plan,old['video_id'])
        receipt=persist_receipt(store,receipt)
        if receipt['status']!='public': return {'episode_id':ep,'status':'scheduled','metrics':None,'eligible_for_retro':False}
        channel,profile=channel_config(store)
        response=api_execute(youtube_service(profile).videos().list(part='snippet,status,statistics',id=old['video_id']))
        items=response.get('items',[])
        if len(items)!=1 or items[0].get('snippet',{}).get('channelId')!=channel or items[0].get('status',{}).get('privacyStatus')!='public': raise WorkflowError('采样时频道或公开状态已变化')
        stats=items[0].get('statistics',{}); metrics={}
        for key,remote in (('views','viewCount'),('likes','likeCount'),('comments','commentCount')):
            raw=stats.get(remote)
            if raw is None: metrics[key]=None
            elif not isinstance(raw,bool) and re.fullmatch(r'[0-9]+',str(raw)): metrics[key]=int(raw)
            else: raise WorkflowError('远端指标不是非负整数：'+remote)
        metrics.update(shares=None,favorites=None,retention=None,returning_viewers=None)
        value={'episode_id':ep,'video_id':old['video_id'],'platform':'youtube','actual_public_at':receipt['actual_public_at'],'actual_public_at_source':receipt.get('actual_public_at_source'),'captured_at':utcnow(),'metrics':metrics,'source':'youtube_data_api_v3'}
        path=folder(store,ep)/('metrics-'+fingerprint(value)+'.json'); atomic_json(path,value)
        return store.metric(ep,path)
    finally: store.close()


def register(subparsers):
    patch=subparsers.add_parser('voice-patch',help='从冻结原文生成短句补丁；双ASR通过后本地应用')
    patch.add_argument('episode'); patch.add_argument('--patch-id',required=True)
    action=patch.add_mutually_exclusive_group(); action.add_argument('--generate',action='store_true'); action.add_argument('--apply',action='store_true'); action.add_argument('--revise',action='store_true')
    patch.add_argument('--revision-id'); patch.add_argument('--revision-file'); patch.add_argument('--start',type=int); patch.add_argument('--end',type=int); patch.add_argument('--review-file'); patch.add_argument('--speech-text-file',help='仅允许对冻结目标句调整空白的发音文本')
    patch.add_argument('--execute',action='store_true'); patch.add_argument('--confirm-cost',action='store_true'); patch.add_argument('--estimated-usd',type=float); patch.add_argument('--resume',action='store_true')
    patch.set_defaults(handler=voice_patch)
    for name,fn in (('voice',voice),('align',align),('render',render),('ship',ship),('verify',verify),('observe',observe),('captions',captions)):
        parser=subparsers.add_parser(name,help={'voice':'配音（默认计划）','align':'逐字符字幕对齐（默认计划）','render':'本地真实素材合成（默认计划）','ship':'YouTube定时上传（默认计划）','verify':'只读远端核实并恢复上传收据','observe':'只读公开状态及表现采样','captions':'外挂字幕上传/恢复（默认计划）'}[name])
        parser.add_argument('episode')
        if name=='verify': parser.add_argument('--video-id',required=True)
        elif name!='observe': parser.add_argument('--execute',action='store_true')
        if name in ('voice','align'):
            parser.add_argument('--confirm-cost',action='store_true'); parser.add_argument('--estimated-usd',type=float)
            if name=='align': parser.add_argument('--use-reservation',action='store_true',help='reuse explicit reservation_alignment; no duplicate charge')
        if name=='voice':
            parser.add_argument('--take',type=int,help='显式新take，分段上限默认1200字')
            parser.add_argument('--chunk-chars',type=int,help='按句/段边界切分，1到1200字')
            parser.add_argument('--resume',action='store_true',help='恢复unknown take已done块，不重发unknown块')
        if name=='align': parser.add_argument('--reconcile',action='store_true',help='仅用同一已记账操作的本地JSON恢复，不请求API或新预留')
        parser.set_defaults(handler=fn)
