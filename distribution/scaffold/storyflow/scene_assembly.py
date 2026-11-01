"""Whole-scene CFR30 edit lists. A transition can never split a scene node."""
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import copy
import json
import subprocess
from .core import WorkflowError, read_json, digest, atomic_json


def frame(value):
    number=Decimal(str(value))
    if not number.is_finite() or number<0:raise WorkflowError('invalid scene clock')
    return int((number*30).to_integral_value(rounding=ROUND_HALF_UP))


def load_manifest(record,check_files=True):
    if not isinstance(record,dict) or not record.get('path') or not record.get('sha256'):raise WorkflowError('whole scene clip manifest required; rerender source at CFR30')
    path=Path(record['path'])
    if path.is_symlink() or digest(path)!=record['sha256']:raise WorkflowError('scene clip manifest hash mismatch')
    value=read_json(path)
    if value.get('schema')!='qy.scene.clips.v1' or value.get('fps')!=30:raise WorkflowError('scene clips require CFR30 schema')
    source=value['scene_manifest']
    if digest(source['path'])!=source['sha256']:raise WorkflowError('replanned scene table changed')
    if value.get('visual_contract'):
        visual=value['visual_contract']
        if digest(visual['path'])!=visual['sha256']:raise WorkflowError('scene visual manifest changed')
    source_table=read_json(source['path'])
    source_rows=source_table.get('scenes',[])
    if source_table.get('scene_boundary_replan'):
        review=source_table['scene_boundary_replan']
        if digest(review['path'])!=review['sha256']:raise WorkflowError('scene boundary independent review changed')
    rows=value.get('scenes',[])
    if len(rows)!=len(source_rows):raise WorkflowError('scene table/clip count mismatch')
    elapsed=0
    for row,original in zip(rows,source_rows):
        end=elapsed+float(original['duration'])
        if row['id']!=original['id'] or abs(row['source_start']-elapsed)>1e-8 or abs(row['source_end']-end)>1e-8 or digest(row['source_path'])!=row['source_sha256']:raise WorkflowError('scene table timing/source does not match whole clips')
        if Path(row['source_path']).resolve()!=(Path(value['project_root'])/original['image']).resolve():raise WorkflowError('scene source path differs from source table')
        if 'start' in original and (abs(original['start']-elapsed)>1e-8 or abs(original['end']-end)>1e-8):raise WorkflowError('scene table explicit clock differs from durations')
        elapsed=end
    rows=value.get('scenes',[]); previous_frame=0; previous_time=0; ids=set()
    if not rows:raise WorkflowError('empty scene clip manifest')
    for row in rows:
        if row['id'] in ids or abs(row['source_start']-previous_time)>1e-8 or row['start_frame']!=previous_frame or row['start_frame']!=frame(row['source_start']) or row['end_frame']!=frame(row['source_end']) or row['frame_count']!=row['end_frame']-row['start_frame'] or row['frame_count']<1:raise WorkflowError('scene ids/absolute frame boundaries invalid')
        ids.add(row['id']);previous_time=row['source_end'];previous_frame=row['end_frame']
        if check_files and (digest(row['source_path'])!=row['source_sha256'] or digest(row['clip_path'])!=row['clip_sha256']):raise WorkflowError('whole scene source/clip hash mismatch')
    if value.get('visual_contract'):
        manifest=read_json(value['visual_contract']['path'])
        if source_table.get('scene_boundary_replan'):
            expected=[{'id':r['id'],'start':r['source_start'],'end':r['source_end'],'start_frame':r['start_frame'],'end_frame':r['end_frame']} for r in rows]
            timeline=manifest.get('scene_timeline',{})
            # Float arithmetic may vary in JSON by a few ulps; compare times numerically.
            actual=timeline.get('scenes',[])
            if timeline.get('review_sha256')!=source_table['scene_boundary_replan']['sha256'] or timeline.get('fps')!=30 or len(actual)!=len(expected):raise WorkflowError('replanned visual scene timeline required')
            for a,b in zip(actual,expected):
                if a['id']!=b['id'] or a['start_frame']!=b['start_frame'] or a['end_frame']!=b['end_frame'] or abs(a['start']-b['start'])>1e-8 or abs(a['end']-b['end'])>1e-8:raise WorkflowError('replanned visual scene timeline mismatch')
    return value


def playlist(record,insertions,source_duration):
    value=load_manifest(record); rows=value['scenes']
    if abs(rows[-1]['source_end']-float(source_duration))>1e-6:raise WorkflowError('scene clip source duration mismatch')
    by_previous={};nodes=[];offset=0
    for insertion in insertions:
        at=float(insertion['source_time']); matches=[i for i in range(len(rows)-1) if abs(rows[i]['source_end']-at)<1e-8 and abs(rows[i+1]['source_start']-at)<1e-8]
        if len(matches)!=1:raise WorkflowError('transition is inside scene; explicit reviewed scene-boundary-replan required')
        i=matches[0]
        if insertion.get('previous_scene_id')!=rows[i]['id'] or insertion.get('next_scene_id')!=rows[i+1]['id']:raise WorkflowError('chapter previous/next scene ids mismatch')
        if rows[i]['id'] in by_previous:raise WorkflowError('duplicate transition at scene boundary')
        by_previous[rows[i]['id']]=insertion
    for row in rows:
        nodes.append({'type':'scene',**row,'output_start_frame':row['start_frame']+offset,'output_end_frame':row['end_frame']+offset})
        if row['id'] in by_previous:
            t=by_previous[row['id']];count=frame(t['duration'])
            if abs(count/30-float(t['duration']))>1e-9:raise WorkflowError('transition duration must align to frame clock')
            nodes.append({'type':'transition','previous_scene_id':row['id'],'next_scene_id':t['next_scene_id'],'clip_path':str(Path(t['clip']).resolve()),'clip_sha256':digest(t['clip']),'frame_count':count,'source_frame':row['end_frame'],'source_time':t['source_time'],'output_start_frame':row['end_frame']+offset,'output_end_frame':row['end_frame']+offset+count})
            offset+=count
    return {'schema':'qy.scene.assembly.v1','fps':30,'scene_clips':record,'nodes':nodes,'output_frames':rows[-1]['end_frame']+offset}


def validate_playlist(actual,record,insertions,source_duration):
    expected=playlist(record,insertions,source_duration)
    if actual!=expected:raise WorkflowError('typed whole scene assembly order/frame/id/hash mismatch')
    return expected


def framemd5(path):
    result=subprocess.run(['ffmpeg','-v','error','-nostdin','-i',str(path),'-map','0:v:0','-an','-f','framemd5','-'],capture_output=True,text=True,timeout=1800)
    if result.returncode:raise WorkflowError('frame-sequence decode failed')
    return [line.rsplit(',',1)[-1].strip() for line in result.stdout.splitlines() if line and not line.startswith('#')]


def prove_frames(paths,output,assembly,evidence_path=None):
    evidence_path=Path(evidence_path) if evidence_path else Path(output).parent/'whole-scene-frameproof.failed.json'
    if len(paths)!=len(assembly['nodes']):raise WorkflowError('frame proof must cover every typed node')
    expected=[]; nodes=[]; count_errors=[]
    for path,node in zip(paths,assembly['nodes']):
        hashes=framemd5(path)
        if len(hashes)!=node['frame_count']:count_errors.append({'path':str(path),'expected_frames':node['frame_count'],'actual_frames':len(hashes)})
        nodes.append({'type':node['type'],'id':node.get('id'),'path':str(path),'sha256':digest(path),'start_frame':len(expected),'end_frame':len(expected)+len(hashes),'first_frame_md5':hashes[0] if hashes else None,'last_frame_md5':hashes[-1] if hashes else None})
        expected.extend(hashes)
    actual=framemd5(output)
    mismatches=[i for i in range(max(len(expected),len(actual))) if (expected[i] if i<len(expected) else None)!=(actual[i] if i<len(actual) else None)]
    first=mismatches[0] if mismatches else None
    failed=bool(count_errors or mismatches)
    result={'schema':'qy.scene.frameproof.v1','status':'fail' if failed else 'pass','method':'all decoded frame pixel MD5s; exact whole-clip concatenation','output_sha256':digest(output),'frame_count':len(actual),'expected_frame_count':len(expected),'count_errors':count_errors,'expected_frame_md5':expected,'actual_frame_md5':actual,'nodes':nodes,'mismatch_count':len(mismatches),'first_mismatch_frame':first,'first_mismatch_node':next((n for n in nodes if first is not None and n['start_frame']<=first<n['end_frame']),None),'mismatch_frames_first_100':mismatches[:100]}
    if failed:
        # Preserve the real decoder result before raising; never discard a red
        # observation or rerender a full film merely to rediscover its first diff.
        if evidence_path.exists():raise WorkflowError('frameproof failure evidence exists; preserve and choose a fresh path')
        atomic_json(evidence_path,result)
        raise WorkflowError('assembled video pixels differ from typed whole clip frame sequence; evidence '+str(evidence_path))
    return result


def replan(store,ep,file,execute=False):
    work=store.root/'episodes'/ep/'production'; scenes=work/'scenes.json'; visual=work/'visual-manifest.json'
    proof=read_json(file);old=read_json(scenes);manifest=read_json(visual);arts=store.artifacts(ep)
    if proof.get('scenes_sha256')!=digest(scenes) or proof.get('visual_manifest_sha256')!=digest(visual) or proof.get('source_audio_sha256')!=arts['audio']['sha256']:raise WorkflowError('scene boundary replan source hashes mismatch')
    review=proof.get('review',{})
    if review.get('status')!='pass' or not isinstance(review.get('reviewer'),str) or not review['reviewer'].strip() or review['reviewer'].strip().casefold()==store.episode(ep)['author'].strip().casefold() or not review.get('reason') or not review.get('evidence'):raise WorkflowError('independent semantic/acoustic scene-boundary review required')
    for record in review['evidence']:
        path=(store.root/record['path']).resolve()
        if not path.is_relative_to(store.root) or digest(path)!=record['sha256']:raise WorkflowError('scene boundary review evidence mismatch')
    if (work/'chapter-revision.binding.json').exists():raise WorkflowError('archive-current before scene-boundary-replan')
    revised=copy.deepcopy(old); rows=revised['scenes']; indexes={row['id']:i for i,row in enumerate(rows)}
    if not proof.get('boundaries'):raise WorkflowError('no scene boundary changes')
    for change in proof['boundaries']:
        i=indexes[change['previous_scene_id']]
        if i+1>=len(rows) or rows[i+1]['id']!=change['next_scene_id']:raise WorkflowError('scene boundary ids must be adjacent')
        a,b=rows[i:i+2];at=float(change['source_time'])
        if not a['start']<at<b['end']:raise WorkflowError('scene boundary outside adjacent scenes')
        # Replanning changes timing only; no media path or trim is regenerated.
        if a.get('media_type')=='video' or b.get('media_type')=='video':raise WorkflowError('video boundary replan requires explicit trim contract')
        a['end']=at;a['duration']=at-a['start'];b['start']=at;b['duration']=b['end']-at
    if len(indexes)!=len(rows):raise WorkflowError('duplicate scene ids')
    for i,row in enumerate(rows):
        if frame(row['end'])<=frame(row['start']) or abs(row['duration']-(row['end']-row['start']))>1e-8 or (i and abs(rows[i-1]['end']-row['start'])>1e-8):raise WorkflowError('replanned scene continuity invalid')
    token=digest(file);history=work/'scene-replans'/token
    revised['scene_boundary_replan']={'path':str(history/'review.json'),'sha256':token,'old_scenes_sha256':digest(scenes),'old_visual_manifest_sha256':digest(visual)}
    manifest['scene_timeline']={'fps':30,'review_sha256':token,'scenes':[{'id':r['id'],'start':r['start'],'end':r['end'],'start_frame':frame(r['start']),'end_frame':frame(r['end'])} for r in rows]}
    result={'ok':True,'execute':execute,'scenes':revised,'scene_timeline':manifest['scene_timeline'],'history':str(history)}
    if not execute:return result
    from .revision_gate import check_revision
    check_revision(store.root,ep)
    history.mkdir(parents=True,exist_ok=False)
    import shutil
    for path,name in [(scenes,'old-scenes.json'),(visual,'old-visual-manifest.json'),(Path(file),'review.json')]:shutil.copyfile(path,history/name)
    atomic_json(scenes,revised);atomic_json(visual,manifest)
    with store.db:store.invalidate(ep);store.db.execute('DELETE FROM media_reviews WHERE episode_id=?',(ep,))
    return result
