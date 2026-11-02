"""Frame-exact body-caption exclusions derived only from a bound typed chapter EDL."""
import json
from pathlib import Path
import subprocess
from .core import WorkflowError,digest,read_json


def proof(path):
    path=Path(path).resolve(strict=True)
    return {'path':str(path),'sha256':digest(path)}


def checked(record):
    p=Path(record['path'])
    if p.is_symlink() or not p.is_absolute() or digest(p)!=record['sha256']:
        raise WorkflowError('caption mask source SHA/path changed')
    return p


def create_mask(chapter_binding,video):
    """Caller normal burn has already passed verify_revision; recheck source links here."""
    try:
        binding_path=Path(chapter_binding).resolve(strict=True);v=read_json(binding_path)
        if v['schema']!='qy.chapter.revision.v1' or not v['episode_id']:raise ValueError('chapter identity')
        video=Path(video).resolve(strict=True)
        if checked(v['delivery']['video'])!=video:raise ValueError('video binding')
        derivation=checked(v['evidence']['derivation']);derived=read_json(derivation)
        assembly_path=derivation.parent/'typed-assembly.json';assembly=read_json(assembly_path)
        if video!=derivation.parent/'clean.mp4' or derived['outputs']['clean.mp4']!=digest(video):raise ValueError('delivery source')
        if assembly!=derived['mapping']['assembly']:raise ValueError('typed assembly changed')
        if assembly['schema']!='qy.scene.assembly.v1' or type(assembly['fps']) is not int or assembly['fps']!=30:raise ValueError('assembly fps/schema')
        total=assembly['output_frames']
        if type(total) is not int or total<=0:raise ValueError('assembly frame count')
        cursor=0;ranges=[]
        for node in assembly['nodes']:
            a,b,count=(node[k] for k in ('output_start_frame','output_end_frame','frame_count'))
            if any(type(x) is not int for x in (a,b,count)) or a!=cursor or b<=a or b>total or count!=b-a:raise ValueError('noncontiguous/invalid frame node')
            if node['type']=='transition':ranges.append([a,b])
            elif node['type']!='scene':raise ValueError('unknown typed node')
            cursor=b
        if cursor!=total or not ranges:raise ValueError('incomplete assembly/no transitions')
        return {'schema':'qy.caption.frame-mask.v1','episode_id':v['episode_id'],'fps':30,'output_frames':total,
                'ranges':ranges,'source_video':proof(video),'chapter_binding':proof(binding_path),'typed_assembly':proof(assembly_path),
                'semantics':'zero-based integer half-open body-caption exclusions; source subtitles unchanged'}
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise WorkflowError('bound chapter caption mask required: '+str(exc)) from exc


def validate_mask(value,video,probe=False):
    try:
        binding=checked(value['chapter_binding']);checked(value['typed_assembly']);checked(value['source_video'])
        expected=create_mask(binding,video)
        if value!=expected:raise WorkflowError('caption mask differs from bound typed transition frames')
        if probe:
            rows=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-select_streams','v:0','-show_entries','stream=avg_frame_rate,nb_frames,nb_read_frames','-of','json',str(video)]))['streams']
            if len(rows)!=1 or rows[0]['avg_frame_rate']!='30/1' or int(rows[0]['nb_frames'])!=value['output_frames'] or int(rows[0]['nb_read_frames'])!=value['output_frames']:
                raise WorkflowError('caption mask actual decoded source frame count/fps mismatch')
        return expected
    except (OSError,ValueError,KeyError,TypeError,subprocess.CalledProcessError) as exc:
        raise WorkflowError('caption mask evidence required: '+str(exc)) from exc


def caption_filter(snapshot,font,style,mask,first_frame_cover):
    # Overlay enable's n is zero based, matching the typed EDL absolute frames.
    # Both branches retain every source frame and PTS; only caption pixels switch.
    allowed=['gte(n,1)'] if first_frame_cover else []
    allowed += [f'not(between(n,{a},{b-1}))' for a,b in mask['ranges']]
    enable='*'.join(allowed) or '1'
    caption=f"subtitles=filename='{snapshot}':fontsdir='{font.parent}':force_style='{style}'"
    return "setpts=PTS-STARTPTS,fps=30,format=yuv420p,split[plain][caption];[caption]"+caption+"[sub];[plain][sub]overlay=enable='"+enable+"':format=yuv420"


def notice_filter(font,first_frame_cover):
    enable='between(t,0.03333333333333333,5)' if first_frame_cover else 'lt(t,5)'
    return f",drawtext=fontfile='{font}':text='真实事件改编  部分情节演绎  卡通画面':fontsize=28:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=10:x=48:y=36:enable='{enable}'"
