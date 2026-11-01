"""Version-bound, translation-only subtitles; the accepted FA clock is immutable."""
import json
import math
import re
from pathlib import Path
from .core import WorkflowError, atomic_json, digest, parse_srt

LANGUAGES=('zh-Hans','zh-Hant','en','ja','ko')
NAMES={'zh-Hans':'简体中文','zh-Hant':'繁體中文','en':'English','ja':'日本語','ko':'한국어'}

def cues(path):
    text=Path(path).read_text(encoding='utf-8-sig')
    times=parse_srt(text)
    blocks=re.split(r'\n\s*\n',text.replace('\r\n','\n').strip())
    return [{'id':i+1,'start':a,'end':b,'text':'\n'.join(blocks[i].splitlines()[2:])} for i,(a,b) in enumerate(times)]

def boundaries(value):
    result=[]
    for row in value:
        a=float(row['start']);b=float(row['end'])
        if not math.isfinite(a+b) or a<0 or b<=a:raise WorkflowError('章节间隙无效')
        result.append({'start':a,'end':b})
    if any(b['start']<a['end'] for a,b in zip(result,result[1:])):raise WorkflowError('章节间隙重叠或乱序')
    return result

def crosses(start,end,gaps):
    # FA is millisecond audio time; chapter pictures are quantized CFR30.
    # Only a one-frame edge overlap is tolerated, never grouping across a card.
    return any(min(end,g['end'])-max(start,g['start'])>1/30+.000001 for g in gaps)

def prepare(source,output,chapter_gaps=(),languages=LANGUAGES):
    source=Path(source).resolve();output=Path(output)
    if output.exists():raise WorkflowError('目标材料已存在，禁止覆盖冻结翻译材料')
    if len(set(languages))!=len(languages) or any(l not in LANGUAGES for l in languages):raise WorkflowError('重复或不支持的字幕语言')
    values=cues(source);gaps=boundaries(chapter_gaps)
    if any(crosses(c['start'],c['end'],gaps) for c in values):raise WorkflowError('源字幕跨章节卡，先修正原始交付时轴')
    packet={'schema':'qy.caption.packet.v1','source_path':str(source),'source_sha256':digest(source),'languages':list(languages),'chapter_gaps':gaps,'chapter_edge_tolerance_seconds':1/30,'cues':values,'instructions':'Translate the complete story with ID coverage. Groups contain 1–4 consecutive source IDs, never cross chapter gaps. Do not summarize, invent facts or rewrite timing.'}
    atomic_json(output,packet);return packet

def load_packet(path):
    packet=json.loads(Path(path).read_text());source=Path(packet['source_path'])
    if packet.get('schema')!='qy.caption.packet.v1' or digest(source)!=packet['source_sha256'] or cues(source)!=packet['cues']:raise WorkflowError('冻结源字幕或翻译材料漂移')
    packet['chapter_gaps']=boundaries(packet['chapter_gaps'])
    if any(crosses(c['start'],c['end'],packet['chapter_gaps']) for c in packet['cues']):raise WorkflowError('源字幕跨章节卡')
    return packet

def stamp(seconds):
    ms=round(seconds*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

def srt(values):
    return '\n'.join(f'{n}\n{stamp(c["start"])} --> {stamp(c["end"])}\n{c["text"]}\n' for n,c in enumerate(values,1))

def translation(packet,language,value):
    if value.get('schema')!='qy.caption.translation.v1' or value.get('language')!=language or value.get('source_sha256')!=packet['source_sha256']:raise WorkflowError('翻译来源SHA/语言/schema不匹配')
    groups=value.get('groups',[]);flat=[];out=[]
    for g in groups:
        ids=g.get('source_ids');text=g.get('text')
        if not isinstance(ids,list) or not 1<=len(ids)<=4 or any(type(i)!=int for i in ids) or ids!=list(range(ids[0],ids[0]+len(ids))):raise WorkflowError('翻译组须含1–4个连续源字幕ID')
        if ids[0]<1 or ids[-1]>len(packet['cues']):raise WorkflowError('源字幕ID越界')
        if not isinstance(text,str) or not text.strip() or '\x00' in text or '\r' in text or '\n\n' in text or '-->' in text:raise WorkflowError('翻译正文为空或包含非法SRT结构')
        if len(text.splitlines())>2:raise WorkflowError('字幕最多两行')
        start=packet['cues'][ids[0]-1]['start'];end=packet['cues'][ids[-1]-1]['end']
        if crosses(start,end,packet['chapter_gaps']):raise WorkflowError('翻译组跨章节转场')
        # Conservative technical limits. Translation/editor review remains separate.
        size=len(''.join(text.split()));limit=30 if language=='en' else 24
        if size/(end-start)>limit or size>160 or end-start<.299:raise WorkflowError('翻译字幕阅读速度/长度/持续时间不合理，须编辑改分组')
        out.append({'start':start,'end':end,'text':text.strip(),'source_ids':ids});flat.extend(ids)
    if flat!=list(range(1,len(packet['cues'])+1)):raise WorkflowError('翻译须按序覆盖全部源字幕且不得重复/遗漏')
    return out

def render(packet_path,output,translations,converter=None):
    packet=load_packet(packet_path);output=Path(output)
    if output.exists():raise WorkflowError('字幕输出目录已存在，不能覆盖已冻结版本')
    translated={}
    for lang in packet['languages']:
        if lang in ('zh-Hans','zh-Hant'):
            if lang=='zh-Hant':
                if converter is None:
                    try:
                        from opencc import OpenCC
                    except ImportError:raise WorkflowError('繁体字幕需要共享leo-tools环境的OpenCC，不回退伪转换') from None
                    converter=OpenCC('s2t').convert
                values=[{**c,'text':converter(c['text'])} for c in packet['cues']]
            else:values=packet['cues']
            translated[lang]=[{**c,'source_ids':[c['id']]} for c in values]
        else:
            if lang not in translations:raise WorkflowError('缺少真实译稿：'+lang)
            translated[lang]=translation(packet,lang,json.loads(Path(translations[lang]).read_text()))
    output.mkdir(parents=True)
    tracks={}
    for lang,values in translated.items():
        path=output/(lang+'.srt');path.write_text(srt(values),encoding='utf-8')
        tracks[lang]={'path':str(path.resolve()),'sha256':digest(path),'name':NAMES[lang],'cue_count':len(values),'groups':[{'source_ids':c['source_ids'],'text':c['text']} for c in values]}
    manifest={'schema':'qy.caption.tracks.v1','packet_path':str(Path(packet_path).resolve()),'packet_sha256':digest(packet_path),'source_sha256':packet['source_sha256'],'tracks':tracks,'translation_inputs':{lang:{'path':str(Path(p).resolve()),'sha256':digest(p)} for lang,p in translations.items()},'translation_quality':'requires_independent_editor_review'}
    atomic_json(output/'tracks.json',manifest);validate(output/'tracks.json');return manifest

def validate(path):
    manifest=json.loads(Path(path).read_text());packet=load_packet(manifest['packet_path'])
    if manifest.get('schema')!='qy.caption.tracks.v1' or digest(manifest['packet_path'])!=manifest['packet_sha256'] or packet['source_sha256']!=manifest['source_sha256']:raise WorkflowError('字幕绑定无效')
    if set(manifest['tracks'])!=set(packet['languages']):raise WorkflowError('字幕轨道缺失或多余')
    for language,track in manifest['tracks'].items():
        if digest(track['path'])!=track['sha256']:raise WorkflowError('已渲染字幕SHA变化')
        parsed=cues(track['path'])
        if language in ('zh-Hans','zh-Hant'):
            expected=packet['cues'];groups=track['groups']
            if [g['source_ids'] for g in groups]!=[[c['id']] for c in expected]:raise WorkflowError('中文字幕必须逐cue绑定原始FA时轴')
            if language=='zh-Hans' and [c['text'] for c in parsed]!=[c['text'] for c in expected]:raise WorkflowError('简体字幕不得改写')
            if language=='zh-Hant':
                try:
                    from opencc import OpenCC
                    texts=[OpenCC('s2t').convert(c['text']) for c in expected]
                except ImportError:raise WorkflowError('验证繁体需要OpenCC') from None
                if [c['text'] for c in parsed]!=texts:raise WorkflowError('繁体不是冻结简体的OpenCC转换')
        else:
            record=manifest['translation_inputs'][language]
            if digest(record['path'])!=record['sha256']:raise WorkflowError('翻译原件SHA变化')
            expected=translation(packet,language,json.loads(Path(record['path']).read_text()))
        if len(parsed)!=len(expected) or any((a['start'],a['end'],a['text'])!=(b['start'],b['end'],b.get('text',a['text'])) for a,b in zip(parsed,expected) if language not in ('zh-Hant',)):raise WorkflowError('输出字幕与冻结译稿/原始时轴不一致')
        if language=='zh-Hant' and [(c['start'],c['end']) for c in parsed]!=[(c['start'],c['end']) for c in expected]:raise WorkflowError('繁体时轴漂移')
    return {'ok':True,'languages':list(manifest['tracks']),'source_cues':len(packet['cues']),'source_sha256':packet['source_sha256']}

def register(subparsers):
    p=subparsers.add_parser('multilingual',help='冻结FA字幕、多语翻译材料及外挂轨验证');s=p.add_subparsers(dest='action',required=True)
    a=s.add_parser('prepare');a.add_argument('--source',required=True);a.add_argument('--output',required=True);a.add_argument('--chapter-gaps',help='JSON array file');a.add_argument('--languages',nargs='+',default=list(LANGUAGES));a.set_defaults(handler=lambda x:prepare(x.source,x.output,json.loads(Path(x.chapter_gaps).read_text()) if x.chapter_gaps else (),x.languages))
    a=s.add_parser('render');a.add_argument('--packet',required=True);a.add_argument('--output',required=True);a.add_argument('--translation',action='append',default=[],help='LANG=JSON_PATH');a.set_defaults(handler=lambda x:render(x.packet,x.output,dict(v.split('=',1) for v in x.translation)))
    a=s.add_parser('validate');a.add_argument('--manifest',required=True);a.set_defaults(handler=lambda x:validate(x.manifest))
