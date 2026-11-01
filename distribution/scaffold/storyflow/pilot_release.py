"""Explicit new publication record for accepted review media, never mode promotion.

All mutating requests get a durable intent before network I/O. Unknown requests
never auto-retry. Run readonly reconciliation before any further action.
"""
import datetime as dt
import fcntl
import json
from pathlib import Path
from .core import WorkflowError, atomic_json, digest, validate_cover
from .captions_multilingual import LANGUAGES, validate as validate_tracks
from .providers import youtube_service

ACTIONS=('upload','thumbnail','captions','playlist','privacy','readback')

def record_file(path):
    p=Path(path).resolve()
    return {'path':str(p),'sha256':digest(p)}

def checked(record):
    path=Path(record['path'])
    if digest(path)!=record['sha256']:raise WorkflowError('发布输入已改变：'+path.name)
    return path

def load_json(path):return json.loads(Path(path).read_text())

def load_metadata(path):
    path=Path(path).resolve();meta=load_json(path)
    if meta.get('description_file'):
        description=(path.parent/meta['description_file']).resolve()
        if not description.is_relative_to(path.parent):raise WorkflowError('说明文件须在metadata目录内')
        text=description.read_text(encoding='utf-8')
        if 'description' in meta and meta['description']!=text:raise WorkflowError('metadata说明正文与外部说明文件冲突')
        meta['description']=text;meta['_description_path']=str(description)
    if 'made_for_kids' not in meta and 'self_declared_made_for_kids' in meta:meta['made_for_kids']=meta['self_declared_made_for_kids']
    return meta

def accepted_media_report(path):
    """Public receipt independent of historical producer report layout."""
    path=Path(path).resolve();value=load_json(path)
    if value.get('schema')!='story-animation.accepted-media.v1':return value, None
    if value.get('source_review_only') is not True:raise WorkflowError('accepted-media source must remain review_only')
    def resolve(record):
        record=dict(record);p=Path(record['path']);p=p if p.is_absolute() else path.parent/p
        record['path']=str(p.resolve());checked(record);return record
    video=resolve(value['clean_video']);subtitle=resolve(value['subtitles']);reviews={}
    for role in ('A','B'):
        row=value['reviews'][role]
        if row.get('verdict')!='pass' or not row.get('reviewer'):raise WorkflowError('public receipt requires real A/B reviews')
        reviews[role]={**row,**resolve(row)}
    if reviews['A']['reviewer']==reviews['B']['reviewer']:raise WorkflowError('public A/B review contexts must be independent')
    report={'final_media':{'episode_id':value['episode_id'],'mechanical_clock_passed':value.get('mechanical_clock_passed'),'final_video':{'sha256':value['accepted_burned_sha256']},'chapter_clean':{'sha256':video['sha256']},'delivery':{'video':video,'subtitles':subtitle}},'independent_reviews':{'A_verdict':'A-PASS','B_verdict':'B-PASS'}}
    return report,reviews


def create(contract_path,output,validate_only=False):
    output=Path(output)
    if output.exists() and not validate_only:raise WorkflowError('独立pilot发布记录已存在；请续跑或只读回查，不另建逃避未知操作')
    contract=load_json(contract_path)
    required=('publication_id','episode_id','expected_channel_id','profile','source_report','owner_acceptance','upload_authority','thumbnail','captions_manifest','captions_review','metadata','playlist_id')
    if any(not contract.get(k) for k in required):raise WorkflowError('pilot发布合同字段缺失')
    if contract.get('kind')!='accepted-review-pilot' or contract.get('source_review_only') is not True:raise WorkflowError('pilot必须新建独立发布记录且保留原review_only')
    if not contract['expected_channel_id'].startswith('UC') or len(contract['expected_channel_id'])!=24:raise WorkflowError('必须显式指定完整频道ID')
    if contract['profile']=='default':raise WorkflowError('必须显式独立频道profile，不能回退')
    report,public_reviews=accepted_media_report(contract['source_report']);media=report['final_media']
    if media['episode_id']!=contract['episode_id'] or media.get('mechanical_clock_passed') is not True or report['independent_reviews'].get('A_verdict')!='A-PASS' or report['independent_reviews'].get('B_verdict')!='B-PASS':raise WorkflowError('缺少绑定同一集的既有真实QA和两路审查')
    if media['final_video']['sha256']!=contract.get('accepted_burned_sha256'):raise WorkflowError('主人接受的成片与原审查SHA不匹配')
    video=record_file(media['delivery']['video']['path'])
    if video['sha256']!=media['chapter_clean']['sha256'] or video['sha256']!=media['delivery']['video']['sha256']:raise WorkflowError('无烧录母片不是已审同一章节派生')
    subtitle=record_file(media['delivery']['subtitles']['path'])
    if subtitle['sha256']!=media['delivery']['subtitles']['sha256']:raise WorkflowError('既有FA交付字幕漂移')
    validate_tracks(contract['captions_manifest']);tracks=load_json(contract['captions_manifest'])
    if tracks['source_sha256']!=subtitle['sha256'] or set(tracks['tracks'])!=set(LANGUAGES):raise WorkflowError('本次pilot须齐全五轨且绑定已审交付字幕')
    review=load_json(contract['captions_review'])
    if review.get('status')!='pass' or not isinstance(review.get('reviewer'),str) or not review['reviewer'].strip() or review.get('source_sha256')!=subtitle['sha256'] or review.get('tracks_manifest_sha256')!=digest(contract['captions_manifest']) or any(review.get('checks',{}).get(k)!='pass' for k in ('coverage','names_numbers','faithfulness','readability')):raise WorkflowError('缺少绑定同一五轨版本的实际独立语言审稿')
    if review.get('translator') and review['translator']==review['reviewer']:raise WorkflowError('译者不能自签独立语言审稿')
    thumb=Path(contract['thumbnail'])
    if thumb.suffix.lower() not in ('.jpg','.jpeg','.png') or thumb.stat().st_size>2*1024*1024:raise WorkflowError('YouTube封面须JPEG/PNG且不超过2MiB')
    validate_cover(thumb)
    files={key:record_file(contract[key]) for key in ('source_report','owner_acceptance','upload_authority','thumbnail','captions_manifest','captions_review','metadata')}
    for role,path in report['independent_reviews'].items():
        if role in ('A','B'):
            evidence=Path(contract['source_report']).resolve().parent.parent/str(path)
            # Normal source reports live at project/.jarvis/REPORT; preserve actual review paths.
            files['review_'+role]=record_file(evidence)
    if public_reviews:
        for role,record in public_reviews.items():files['review_'+role]={'path':record['path'],'sha256':record['sha256']}
    files['video']=video
    meta=load_metadata(contract['metadata'])
    if not isinstance(meta.get('title'),str) or not 1<=len(meta['title'])<=100 or not isinstance(meta.get('description'),str) or len(meta['description'])>5000:raise WorkflowError('标题/说明不符合YouTube长度限制')
    if meta.get('made_for_kids') is not False:raise WorkflowError('本发布合同须显式非面向儿童')
    if meta.get('_description_path'):files['description']=record_file(meta['_description_path'])
    marker='[publication:'+contract['publication_id']+']'
    plan={'schema':'qy.pilot.plan.v1','contract':contract,'contract_input':record_file(contract_path),'files':files,'marker':marker,'source_review_only_unchanged':True,'initial_privacy':'private','order':['upload','thumbnail','captions','playlist','privacy'],'status':'planned','operations':{}}
    if validate_only:return {'ok':True,'mode':'preflight','video_sha256':video['sha256'],'initial_privacy':'private','languages':list(LANGUAGES),'files':files,'source_review_only_unchanged':True}
    # One accepted source/channel has one durable publication even if callers
    # accidentally choose a new output path after an ambiguous upload.
    root=Path(contract.get('project_root') or Path(contract['source_report']).resolve().parent.parent)
    registry=root/'state'/'pilot-publications.json';registry.parent.mkdir(parents=True,exist_ok=True)
    with registry.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        index=load_json(registry) if registry.exists() else {'schema':'qy.pilot.index.v1','entries':{}}
        key=contract['expected_channel_id']+':'+video['sha256']
        claim={'plan_path':str(output.resolve()),'contract_sha256':digest(contract_path),'publication_id':contract['publication_id']}
        previous=index['entries'].get(key)
        if previous and previous!=claim:raise WorkflowError('同频道同母片已有独立发布记录，禁止换ID或路径重复上传')
        index['entries'][key]=claim;atomic_json(registry,index)
        plan['registry_path']=str(registry.resolve());plan['registry_key']=key
        atomic_json(output,plan)
    return {'ok':True,'plan':str(output.resolve()),'video_sha256':video['sha256'],'initial_privacy':'private','languages':list(LANGUAGES),'source_review_only_unchanged':True}

def identity(service,expected):
    rows=service.channels().list(part='id',mine=True).execute().get('items',[])
    if [r.get('id') for r in rows]!=[expected]:raise WorkflowError('远端频道身份不符；拒绝所有写操作')

def load_plan(path):
    plan=load_json(path)
    if plan.get('schema')!='qy.pilot.plan.v1':raise WorkflowError('发布schema无效')
    checked(plan['contract_input'])
    registry=load_json(plan['registry_path'])
    claim=registry.get('entries',{}).get(plan['registry_key'],{})
    if claim.get('plan_path')!=str(Path(path).resolve()) or claim.get('contract_sha256')!=plan['contract_input']['sha256']:raise WorkflowError('发布唯一索引不匹配')
    for rec in plan['files'].values():checked(rec)
    validate_tracks(plan['files']['captions_manifest']['path'])
    return plan

def remote_video(service,plan):
    vid=plan.get('video_id')
    if not vid:raise WorkflowError('尚无已确认video_id；先完成上传或人工只读恢复')
    rows=service.videos().list(part='snippet,status,contentDetails',id=vid).execute().get('items',[])
    if len(rows)!=1 or rows[0]['snippet']['channelId']!=plan['contract']['expected_channel_id'] or plan['marker'] not in rows[0]['snippet'].get('description',''):raise WorkflowError('远端视频/频道/发布标识不匹配')
    return rows[0]

def inspect(service,plan):
    video=remote_video(service,plan);vid=plan['video_id']
    caps=service.captions().list(part='snippet',videoId=vid).execute().get('items',[])
    expected=plan.get('caption_ids',{})
    caprows={row['id']:row['snippet'] for row in caps}
    serving={lang:bool(cid in caprows and caprows[cid].get('status')=='serving' and caprows[cid].get('language')==lang and caprows[cid].get('videoId')==vid) for lang,cid in expected.items()}
    playlist=service.playlistItems().list(part='snippet,contentDetails',playlistId=plan['contract']['playlist_id'],videoId=vid,maxResults=50).execute().get('items',[])
    return {'video_id':vid,'channel_id':video['snippet']['channelId'],'privacy':video['status']['privacyStatus'],'thumbnail_urls':{k:v.get('url') for k,v in video['snippet'].get('thumbnails',{}).items()},'caption_ids':expected,'captions_serving':serving,'all_five_captions_serving':len(serving)==5 and all(serving.values()),'playlist_matches':sum(r.get('contentDetails',{}).get('videoId')==vid for r in playlist),'checked_at':dt.datetime.now(dt.timezone.utc).isoformat()}

def run(path,action,execute=False,privacy=None,service_factory=None,media_factory=None):
    path=Path(path)
    # Separate per-publication lock guards all intent/receipt updates, including crash recovery.
    with path.with_suffix(path.suffix+'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        plan=load_plan(path);contract=plan['contract']
        if action not in ACTIONS:raise WorkflowError('未知发布阶段')
        if action=='privacy' and privacy not in ('private','unlisted','public'):raise WorkflowError('最终可见性必须显式指定')
        if not execute and action!='readback':return {'ok':True,'mode':'dry-run','action':action,'privacy':privacy or 'private','profile':contract['profile'],'expected_channel_id':contract['expected_channel_id'],'video_sha256':plan['files']['video']['sha256'],'source_review_only_unchanged':True}
        factory=service_factory or youtube_service;service=factory(contract['profile']);identity(service,contract['expected_channel_id'])
        if action=='readback':
            result=inspect(service,plan);plan['last_readback']=result;atomic_json(path,plan);return result
        operations=plan['operations']
        pending=[k for k,v in operations.items() if v.get('status')=='unknown']
        if pending:raise WorkflowError('已有未决远端操作，禁止进一步写入：'+', '.join(pending))
        def mutate(key,callback):
            if key in operations:
                if operations[key]['status']=='done':return operations[key]['result']
                raise WorkflowError('已有未决远端操作，禁止重新POST：'+key)
            identity(service,contract['expected_channel_id'])
            operations[key]={'status':'unknown','started_at':dt.datetime.now(dt.timezone.utc).isoformat()};atomic_json(path,plan)
            try:result=callback()
            except Exception:
                # Never persist exception text: provider errors may contain sensitive request data.
                operations[key]['error_type']='remote_result_unknown';atomic_json(path,plan);raise WorkflowError('远端结果未知，已持久化；仅只读核查，不重发') from None
            operations[key]={'status':'done','result':result,'completed_at':dt.datetime.now(dt.timezone.utc).isoformat()};atomic_json(path,plan);return result
        if media_factory is None:
            try:
                from googleapiclient.http import MediaFileUpload
            except ImportError:raise WorkflowError('请用共享leo-tools Python运行YouTube操作') from None
            media_factory=MediaFileUpload
        if action=='upload':
            if plan.get('video_id'):return {'video_id':plan['video_id'],'reused_receipt':True}
            meta=load_metadata(plan['files']['metadata']['path'])
            body={'snippet':{'title':meta['title'],'description':meta['description']+'\n\n'+plan['marker'],'categoryId':str(meta.get('category_id','1')),'defaultLanguage':'zh-Hans','defaultAudioLanguage':'zh-Hans','tags':meta.get('tags',[])},'status':{'privacyStatus':'private','selfDeclaredMadeForKids':False}}
            if len(body['snippet']['description'])>5000:raise WorkflowError('发布标识加入后说明超长')
            def upload():
                request=service.videos().insert(part='snippet,status',body=body,media_body=media_factory(plan['files']['video']['path'],mimetype='video/mp4',resumable=True,chunksize=8*1024*1024))
                result=None
                while result is None:_,result=request.next_chunk(num_retries=0)
                return {'id':result['id']}
            result=mutate('upload',upload);plan['video_id']=result['id'];atomic_json(path,plan)
            remote_video(service,plan);return {'ok':True,'video_id':plan['video_id'],'privacy':'private'}
        video=remote_video(service,plan)
        if action=='thumbnail':
            if operations.get('upload',{}).get('status')!='done':raise WorkflowError('先上传视频')
            def set_thumbnail():
                result=service.thumbnails().set(videoId=plan['video_id'],media_body=media_factory(plan['files']['thumbnail']['path'])).execute()
                if not result.get('items'):raise WorkflowError('缩略图响应缺少实际图像回执')
                return result
            result=mutate('thumbnail',set_thumbnail)
            # Persist only public thumbnail URLs, not transport metadata.
            return {'ok':True,'video_id':plan['video_id'],'thumbnail_submitted':bool(result)}
        if action=='captions':
            if operations.get('thumbnail',{}).get('status')!='done':raise WorkflowError('先上传并确认缩略图')
            tracks=load_json(plan['files']['captions_manifest']['path'])['tracks'];plan.setdefault('caption_ids',{})
            for lang in LANGUAGES:
                track=tracks[lang]
                def upload_caption(lang=lang,track=track):
                    result=service.captions().insert(part='snippet',body={'snippet':{'videoId':plan['video_id'],'language':lang,'name':track['name'],'isDraft':False}},media_body=media_factory(track['path'],mimetype='application/octet-stream')).execute()
                    if not isinstance(result.get('id'),str) or not result['id']:raise WorkflowError('字幕响应缺少caption_id')
                    return result
                result=mutate('caption:'+lang,upload_caption)
                plan['caption_ids'][lang]=result['id'];atomic_json(path,plan)
            return inspect(service,plan)
        if action=='playlist':
            if any(operations.get('caption:'+l,{}).get('status')!='done' for l in LANGUAGES):raise WorkflowError('先提交五轨字幕')
            rows=service.playlists().list(part='snippet',id=contract['playlist_id']).execute().get('items',[])
            if len(rows)!=1 or rows[0]['snippet']['channelId']!=contract['expected_channel_id']:raise WorkflowError('播放列表不属于目标频道')
            existing=service.playlistItems().list(part='contentDetails',playlistId=contract['playlist_id'],videoId=plan['video_id'],maxResults=50).execute().get('items',[])
            if not existing:mutate('playlist',lambda:service.playlistItems().insert(part='snippet',body={'snippet':{'playlistId':contract['playlist_id'],'resourceId':{'kind':'youtube#video','videoId':plan['video_id']}}}).execute())
            return inspect(service,plan)
        if action=='privacy':
            readback=inspect(service,plan)
            if not readback['all_five_captions_serving'] or readback['playlist_matches']!=1 or operations.get('thumbnail',{}).get('status')!='done':raise WorkflowError('字幕尚未全部serving/播放列表未确认/缩略图未确认，保留private')
            # videos.update replaces writable fields in the status part. Preserve
            # actual remote values, including explicit False; never echo readonly
            # uploadStatus/madeForKids or invent absent optional flags.
            writable=('license','embeddable','publicStatsViewable','selfDeclaredMadeForKids','containsSyntheticMedia')
            status={key:video['status'][key] for key in writable if key in video['status']}
            status['privacyStatus']=privacy
            result=mutate('privacy:'+privacy,lambda:service.videos().update(part='status',body={'id':plan['video_id'],'status':status}).execute())
            result=inspect(service,plan)
            if result['privacy']!=privacy:raise WorkflowError('最终可见性回读不一致')
            return result

def recover(path,operation,remote_id,reason,service_factory=None):
    """Readonly explicit reconciliation. Never creates another upload/caption."""
    import io
    from .captions_multilingual import cues
    path=Path(path)
    if not reason or not reason.strip():raise WorkflowError('恢复必须保留人工核查理由')
    with path.with_suffix(path.suffix+'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);plan=load_plan(path)
        old=plan['operations'].get(operation)
        if not old or old.get('status')!='unknown':raise WorkflowError('只能核查已持久化的unknown操作')
        service=(service_factory or youtube_service)(plan['contract']['profile']);identity(service,plan['contract']['expected_channel_id'])
        if operation=='upload':
            candidate={**plan,'video_id':remote_id};row=remote_video(service,candidate)
            meta=load_metadata(plan['files']['metadata']['path'])
            if row['snippet'].get('title')!=meta['title'] or row['status'].get('uploadStatus') not in ('uploaded','processed'):raise WorkflowError('候选视频标题/上传状态不符')
            if row['status'].get('privacyStatus')!='private':raise WorkflowError('恢复初次上传只接受private视频')
            plan['video_id']=remote_id
        elif operation.startswith('caption:'):
            lang=operation.split(':',1)[1]
            if lang not in LANGUAGES:raise WorkflowError('未知字幕语言')
            remote_video(service,plan)
            rows=service.captions().list(part='snippet',videoId=plan['video_id']).execute().get('items',[])
            found=[r for r in rows if r['id']==remote_id]
            track=load_json(plan['files']['captions_manifest']['path'])['tracks'][lang]
            if len(found)!=1 or found[0]['snippet'].get('language')!=lang or found[0]['snippet'].get('name')!=track['name'] or found[0]['snippet'].get('status')!='serving':raise WorkflowError('候选字幕不匹配/尚未serving')
            # Server SRT serialization may differ. Compare all timestamps and texts.
            data=service.captions().download(id=remote_id,tfmt='srt').execute()
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/'readback.srt';p.write_bytes(data)
                if cues(p)!=cues(track['path']):raise WorkflowError('回下载字幕与本地冻结轨不相同')
            plan.setdefault('caption_ids',{})[lang]=remote_id
        elif operation=='playlist':
            remote_video(service,plan)
            rows=service.playlistItems().list(part='contentDetails',playlistId=plan['contract']['playlist_id'],videoId=plan['video_id'],maxResults=50).execute().get('items',[])
            found=[r for r in rows if r.get('id')==remote_id and r.get('contentDetails',{}).get('videoId')==plan['video_id']]
            if len(rows)!=1 or len(found)!=1:raise WorkflowError('播放列表只读回查不唯一或条目ID不匹配')
        elif operation.startswith('privacy:'):
            privacy=operation.split(':',1)[1];row=remote_video(service,plan)
            if remote_id!=plan['video_id'] or row['status']['privacyStatus']!=privacy:raise WorkflowError('可见性只读回查不匹配')
        else:raise WorkflowError('此操作不能自动恢复；保留unknown并人工只读核查')
        plan['operations'][operation]={'status':'done','result':{'id':remote_id},'reconciled_readonly':True,'reason':reason,'completed_at':dt.datetime.now(dt.timezone.utc).isoformat()}
        atomic_json(path,plan);return {'ok':True,'operation':operation,'remote_id':remote_id,'readonly_recovery':True}

def register(subparsers):
    p=subparsers.add_parser('pilot-release',help='已接受review媒体的新发布记录，不改变原episode模式');s=p.add_subparsers(dest='action',required=True)
    a=s.add_parser('preflight');a.add_argument('--contract',required=True);a.set_defaults(handler=lambda x:create(x.contract,Path(x.contract).with_name('pilot-plan.json'),True))
    a=s.add_parser('create');a.add_argument('--contract',required=True);a.add_argument('--output',required=True);a.set_defaults(handler=lambda x:create(x.contract,x.output))
    a=s.add_parser('recover');a.add_argument('--plan',required=True);a.add_argument('--operation',required=True);a.add_argument('--remote-id',required=True);a.add_argument('--reason',required=True);a.set_defaults(handler=lambda x:recover(x.plan,x.operation,x.remote_id,x.reason))
    for action in ACTIONS:
        a=s.add_parser(action);a.add_argument('--plan',required=True);a.add_argument('--execute',action='store_true')
        if action=='privacy':a.add_argument('--privacy',required=True,choices=('private','unlisted','public'))
        a.set_defaults(handler=lambda x:run(x.plan,x.action,x.execute,getattr(x,'privacy',None)))
