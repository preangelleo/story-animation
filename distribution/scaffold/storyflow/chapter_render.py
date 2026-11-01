"""Portable, local-only parameterized HyperFrames chapter clip rendering.

This command does not approve or mutate an episode. Episode approval is separate.
"""
import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from .core import WorkflowError

VERSION = 'qy.chapter-render.v1'
HF_VERSION = '0.8.225'
PRESET = 'templates/chapter-transition/default-preset.json'
DEFAULTS = dict(background_color='#141414', title_color='#faf9f5', number_color='#faf9f5',
    line_color='#d97757', title_text='故事的新一章', chapter_number='第二章', layout='a',
    title_font='../../assets/fonts/title.ttf', title_family='QYChapterF1',
    number_font='../../assets/fonts/number.ttf', number_family='QYChapterF3',
    font_weight=400, title_size=116, number_size=34, title_line_height=1.25,
    number_line_height="normal", title_letter_spacing=12, number_letter_spacing=10,
    number_gap=34, group_x=0, group_y=382, group_width=1920, text_align='center',
    group_padding_left=0, line_enabled=True, line_width=64, line_height=4,
    line_gap=36, line_x=0, line_y=0, duration=1, width=1920, height=1080, fps=30,
    sound_path=None,
    sound_name=None, sound_volume=1, muted=True, entrance_y=12, entrance_duration=.13)
LAYOUT_B = dict(group_x=370, group_y=393, group_width=1180, text_align='left',
    group_padding_left=66, number_size=30, number_letter_spacing=8, number_gap=30,
    title_letter_spacing=10, line_width=5, line_height=250, line_gap=0)


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def run(command, cwd=None):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise WorkflowError('chapter command failed: '+str(command[0])+': '+result.stderr[-3000:]+result.stdout[-3000:])
    return result.stdout


def local(value, base):
    if not isinstance(value, str) or not value.strip() or '\x00' in value or '://' in value:
        raise WorkflowError('a nonempty local file path is required')
    path=Path(value).expanduser()
    path=(base/path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_file(): raise WorkflowError('missing local asset: '+str(path))
    return path


def font_info(path, text):
    # fc-scan must parse real font bytes, not trust suffix or user metadata.
    raw=run(['fc-scan','--format','%{family}\n',str(path)]).strip()
    if not raw: raise WorkflowError('invalid font file')
    ranges=[]
    for part in run(['fc-query','--format','%{charset}',str(path)]).split():
        lo,_,hi=part.partition('-'); ranges.append((int(lo,16),int(hi or lo,16)))
    missing=''.join(sorted({c for c in text if not c.isspace() and not any(lo<=ord(c)<=hi for lo,hi in ranges)}))
    if missing: raise WorkflowError('font missing glyphs: '+missing)
    return dict(sha256=sha(path), internal_family=raw, source=str(path))


def effective(root, preset=None, overrides=None):
    root=Path(root).resolve(); preset=Path(preset or root/PRESET).resolve()
    values=json.loads(preset.read_text())
    if not isinstance(values,dict): raise WorkflowError('preset must be an object')
    overrides=overrides or {}
    unknown=(values.keys() | overrides.keys())-DEFAULTS.keys()
    if unknown: raise WorkflowError('unknown chapter parameters: '+','.join(sorted(unknown)))
    if (overrides.get('sound_name') and overrides.get('sound_path')) or (values.get('sound_name') and values.get('sound_path')):
        raise WorkflowError('sound_name and sound_path are mutually exclusive')
    supplied={**values,**overrides}
    if supplied.get('sound_name'): supplied['sound_path']=None
    v={**DEFAULTS,**(LAYOUT_B if values.get('layout')=='b' else {}),**values}
    if 'layout' in overrides:
        v.update(LAYOUT_B if overrides['layout']=='b' else {k:DEFAULTS[k] for k in LAYOUT_B})
    v.update(overrides)
    if 'sound_path' in overrides and 'sound_name' not in overrides: v['sound_name']=None
    if v['sound_name']: v['sound_path']=None
    if v['muted']: v['sound_path']=None
    # Relative user flags are relative to cwd; relative preset entries to preset.
    for key in ('title_font','number_font','sound_path'):
        if v[key] is not None:
            base=Path.cwd() if key in overrides else preset.parent
            v[key]=str(local(v[key],base))
    if v['sound_name'] and not v['muted']:
        if supplied.get('sound_path') is not None:
            raise WorkflowError('sound_name and sound_path are mutually exclusive; set sound_path null')
        from .sound_library import lookup
        v['sound_path']=lookup(root,v['sound_name'])['path']
    for key in ('background_color','title_color','number_color','line_color'):
        if not isinstance(v[key],str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',v[key]):
            raise WorkflowError('invalid color: '+key+' (use #RRGGBB)')
    for key in ('title_text','chapter_number'):
        if not isinstance(v[key],str) or not v[key].strip() or len(v[key])>100 or any(ord(c)<32 for c in v[key]):
            raise WorkflowError('invalid text: '+key)
    for key in ('title_family','number_family'):
        if (not isinstance(v[key],str) or not 1<=len(v[key])<=80
                or not v[key][0].isalpha()
                or not all(c.isalpha() or c.isdecimal() or c in ' _-' for c in v[key])):
            raise WorkflowError('unsafe font family: '+key)
    if v['layout'] not in ('a','b') or v['text_align'] not in ('left','center','right'):
        raise WorkflowError('invalid layout/text_align')
    if v['font_weight'] != 400: raise WorkflowError('font_weight must be 400; synthetic bold is forbidden')
    for key in ('muted','line_enabled'):
        if type(v[key]) is not bool: raise WorkflowError('boolean required: '+key)
    numeric={k for k in DEFAULTS if type(DEFAULTS[k]) in (int,float)}
    for key in numeric:
        value=v[key]
        if type(value) not in (int,float) or not math.isfinite(value): raise WorkflowError('finite number required: '+key)
    for key in ('width','height','fps'):
        if type(v[key]) is not int: raise WorkflowError('integer required: '+key)
    if not (64<=v['width']<=7680 and 64<=v['height']<=4320 and v['width']%2==0 and v['height']%2==0 and 1<=v['fps']<=120):
        raise WorkflowError('invalid dimensions/fps')
    if not .1<=v['duration']<=10 or abs(v['duration']*v['fps']-round(v['duration']*v['fps']))>1e-7:
        raise WorkflowError('duration must be .1..10 seconds and an exact frame count')
    if not 0<=v['sound_volume']<=4: raise WorkflowError('sound_volume must be 0..4')
    for key in numeric-{'width','height','fps','duration','sound_volume','font_weight'}:
        if abs(v[key])>10000: raise WorkflowError('parameter out of range: '+key)
    for key in ('title_size','number_size','title_line_height','number_line_height','group_width','line_width','line_height'):
        if key=='number_line_height' and v[key]=='normal': continue
        if type(v[key]) not in (int,float) or not math.isfinite(v[key]) or v[key]<=0: raise WorkflowError('positive value required: '+key)
    if not 0<v['entrance_duration']<=v['duration']: raise WorkflowError('invalid entrance_duration')
    if v['title_family']==v['number_family'] and sha(v['title_font'])!=sha(v['number_font']):
        raise WorkflowError('different fonts require distinct CSS family aliases')
    fonts={role:font_info(Path(v[role+'_font']),v['title_text'] if role=='title' else v['chapter_number']) for role in ('title','number')}
    sound=None
    if v['sound_path']:
        probe=json.loads(run(['ffprobe','-v','error','-show_streams','-of','json',v['sound_path']]))
        stream=next((x for x in probe['streams'] if x['codec_type']=='audio'),None)
        if stream is None: raise WorkflowError('sound has no audio stream')
        sound=dict(source=v['sound_path'],sha256=sha(v['sound_path']),codec=stream['codec_name'])
    return v,dict(preset_sha256=sha(preset),fonts=fonts,sound=sound)


def composition(v):
    e=lambda key:html.escape(str(v[key]),quote=True)
    faces=''.join('@font-face{font-family:"'+e(role+'_family')+'";src:url("assets/'+role+'.font");font-weight:400;font-style:normal;font-display:block}' for role in ('title','number'))
    line=''
    if v['line_enabled']:
        line='' # Decoration is an independently configurable real element.
    mark=f'height:{v["line_height"]}px;width:{v["line_width"]}px;background:{v["line_color"]};margin:{v["line_gap"]}px auto 0;position:relative;left:{v["line_x"]}px;top:{v["line_y"]}px;'
    if v['layout']=='b': mark+=f'position:absolute;margin:0;margin-top:{v["line_gap"]}px;'
    if not v['line_enabled']: mark+='display:none;'
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width={v['width']}, height={v['height']}"><script src="assets/gsap.min.js"></script><style>
{faces}*{{box-sizing:border-box;margin:0;font-weight:400;font-synthesis:none}}html,body,#root{{width:{v['width']}px;height:{v['height']}px;overflow:hidden;background:{v['background_color']}}}#root{{position:relative}}.group{{position:absolute;left:{v['group_x']}px;top:{v['group_y']}px;width:{v['group_width']}px;text-align:{v['text_align']};padding-left:{v['group_padding_left']}px;{line}}}.number{{font-family:"{e('number_family')}";font-size:{v['number_size']}px;color:{v['number_color']};letter-spacing:{v['number_letter_spacing']}px;line-height:{v['number_line_height']};margin-bottom:{v['number_gap']}px}}h1{{font-family:"{e('title_family')}";font-size:{v['title_size']}px;color:{v['title_color']};letter-spacing:{v['title_letter_spacing']}px;line-height:{v['title_line_height']}}}.mark{{{mark + ''}}}
</style></head><body><div id="root" data-composition-id="chapter" data-width="{v['width']}" data-height="{v['height']}" data-duration="{v['duration']}" data-fps="{v['fps']}"><section class="clip" data-start="0" data-duration="{v['duration']}"><div class="group" id="group"><p class="number">{e('chapter_number')}</p><h1>{e('title_text')}</h1><div class="mark"></div></div></section></div><script>
window.__timelines=window.__timelines||{{}};
Promise.all([document.fonts.load('400 {v['title_size']}px "{e('title_family')}"',document.querySelector('h1').textContent),document.fonts.load('400 {v['number_size']}px "{e('number_family')}"',document.querySelector('.number').textContent)]).then(()=>document.fonts.ready).then(()=>{{const tl=gsap.timeline({{paused:true}});tl.fromTo('#group',{{y:{v['entrance_y']}}},{{y:0,duration:{v['entrance_duration']},ease:'power2.out'}},0);window.__timelines.chapter=tl;}});
</script></body></html>'''


def render(root, output, preset=None, overrides=None, execute=False):
    v,source=effective(root,preset,overrides)
    output=Path(output).expanduser().absolute()
    if output.suffix.lower()!='.mp4': raise WorkflowError('output must have .mp4 suffix')
    bundle=output.with_suffix('.composition')
    if output.exists() or bundle.exists(): raise WorkflowError('output/bundle already exists; preserve previous evidence')
    result=dict(schema=VERSION,effective=v,source=source,output=str(output),composition=str(bundle),executed=False)
    if not execute: return result
    output.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive directory creation claims the output name; failures preserve evidence.
    bundle.mkdir(); (bundle/'assets').mkdir()
    def save(name,value): (bundle/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    save('request.json',result)
    for role in ('title','number'): shutil.copyfile(v[role+'_font'],bundle/'assets'/f'{role}.font')
    # Pinned npm package supplies the local JS runtime; no CDN at render time.
    run(['npx','--offline','--yes','hyperframes@'+HF_VERSION,'--version'])
    packages=list((Path.home()/'.npm/_npx').glob('*/node_modules/hyperframes/package.json'))
    package=next((p.parent for p in packages if json.loads(p.read_text()).get('version')==HF_VERSION and (p.parent/'dist/vendor/npm/gsap@3.15.0/dist/gsap.min.js').is_file()),None)
    if package is None: raise WorkflowError('pinned HyperFrames GSAP runtime missing; install hyperframes@'+HF_VERSION)
    shutil.copyfile(package/'dist/vendor/npm/gsap@3.15.0/dist/gsap.min.js',bundle/'assets/gsap.min.js')
    (bundle/'index.html').write_text(composition(v))
    save('hyperframes.json',dict(paths=dict(blocks='compositions',assets='assets'),authoringSkill='motion-graphics'))
    save('index.motion.json',dict(duration=v['duration'],assertions=[dict(kind='staysInFrame',selector='h1'),dict(kind='staysInFrame',selector='.number')]))
    commands=[]
    def command(args):
        commands.append(args); save('commands.json',commands)
        log=run(args,bundle); (bundle/('command-'+str(len(commands))+'.log')).write_text(log)
        return log
    prefix=['npx','--offline','--yes','hyperframes@'+HF_VERSION]
    command(prefix+['check'])
    command(prefix+['render','--quality','high','--output',str(bundle/'picture.mp4')])
    candidate=bundle/'final.mp4'
    sound=source['sound']
    if sound and not v['muted']:
        # Preserve accepted AAC bytes at unity; other inputs are decoded at explicit gain.
        cmd=['ffmpeg','-v','error','-y','-i',str(bundle/'picture.mp4'),'-i',v['sound_path'],'-map','0:v:0','-map','1:a:0','-c:v','copy']
        mode='stream_copy' if sound['codec']=='aac' and v['sound_volume']==1 else 'explicit_gain_aac'
        cmd+=['-c:a','copy'] if mode=='stream_copy' else ['-af','volume='+str(v['sound_volume']),'-c:a','aac','-b:a','192k']
        cmd+=['-t',str(v['duration']),'-movflags','+faststart',str(candidate)]
        command(cmd)
    else:
        mode='muted'; shutil.copyfile(bundle/'picture.mp4',candidate)
    # Verify assets did not drift while rendering.
    for role in ('title','number'):
        if sha(v[role+'_font'])!=source['fonts'][role]['sha256']: raise WorkflowError('font changed during render')
    if sound and sha(v['sound_path'])!=sound['sha256']: raise WorkflowError('sound changed during render')
    probe=json.loads(command(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(candidate)]))
    video=next(x for x in probe['streams'] if x['codec_type']=='video')
    if (video['width'],video['height'])!=(v['width'],v['height']) or abs(float(probe['format']['duration'])-v['duration'])>.1:
        raise WorkflowError('render dimensions/duration mismatch')
    result.update(executed=True,audio_mode=mode,output_sha256=sha(candidate),html_sha256=sha(bundle/'index.html'),renderer=dict(hyperframes=HF_VERSION,module_sha256=sha(__file__),gsap_sha256=sha(bundle/'assets/gsap.min.js'),ffmpeg_version=run(['ffmpeg','-version']).splitlines()[0]),probe=probe)
    save('manifest.json',result)
    # Link is atomic and fails if a competing writer claimed the final path.
    os.link(candidate,output)
    return result


def register(subparsers):
    p=subparsers.add_parser('render',help='render portable HyperFrames MP4; no episode approval or provider POST')
    p.add_argument('--preset'); p.add_argument('--output',required=True); p.add_argument('--execute',action='store_true',help='otherwise resolve and validate only')
    for key,value in DEFAULTS.items():
        flag='--'+key.replace('_','-')
        if type(value) is bool: p.add_argument(flag,action=argparse.BooleanOptionalAction,default=None)
        else: p.add_argument(flag,type=(lambda x: 'normal' if x=='normal' else float(x)) if key=='number_line_height' else (int if key in ('width','height','fps','font_weight') else float if type(value) in (int,float) else str),default=None)
    p.set_defaults(handler=handle)


def handle(args):
    overrides={k:getattr(args,k) for k in DEFAULTS if getattr(args,k) is not None}
    return render(args.root,args.output,args.preset,overrides,args.execute)
