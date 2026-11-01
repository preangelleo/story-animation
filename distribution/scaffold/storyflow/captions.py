"""Display captions from immutable character alignment, using MacroAlpha components.

Semantic units are the editorial contract. The deterministic fallback is for
local diagnosis only; production should supply reviewed caption_units.json.
No operation retimes, stretches, or mutates the source character alignment.
"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unicodedata
from .vendor.smart_caption import segment as upstream
from .vendor.smart_caption.convert import punctuation_to_space, read_srt, write_srt
from .vendor.smart_caption.caption_units import caption_units_to_srt

PIPELINE = 'macroalpha-caption-units-v1'


def clean(text):
    # Exact MacroAlpha punctuation-to-space first, then cover Unicode punctuation
    # outside its historical finite set (e.g. full-width slash, dash, ellipsis).
    return ' '.join(''.join(' ' if unicodedata.category(c).startswith('P') else c
                            for c in punctuation_to_space(text)).split())


def compact(text):
    return ''.join(clean(text).split())


def _word_entries(chars):
    result=[]; contiguous=False
    for c in chars:
        text=c['text']
        if not text.strip():
            contiguous=False
            continue
        # Upstream smart_join expects Latin words, not individual Latin letters.
        if (contiguous and result and text.isascii() and text.isalnum()
                and result[-1]['text'].isascii() and result[-1]['text'].isalnum()):
            result[-1]['text']+=text;result[-1]['end']=upstream.format_time(c['end'])
        else:
            result.append({'start':upstream.format_time(c['start']),
                           'end':upstream.format_time(c['end']),'text':text})
        contiguous=True
    return result


def display_captions(chars, caption_units=None):
    with tempfile.TemporaryDirectory(prefix='qy-captions-') as directory:
        root=Path(directory);words=root/'words.srt';segments=root/'segmented.srt'
        write_srt(_word_entries(chars),str(words))
        with redirect_stdout(io.StringIO()):
            if caption_units is not None:
                units=root/'caption_units.json'
                units.write_text(json.dumps(caption_units,ensure_ascii=False))
                # A bad semantic contract is an error, never silently fallback.
                caption_units_to_srt(words,units,segments)
                mode='caption_units'
            else:
                upstream.segment(str(words),str(segments),target_chars=16,max_chars=24)
                mode='deterministic_review_required'
        entries=read_srt(str(segments))
    # Match all display characters back to the source in sequence. Never trust
    # interpolated/overlap-adjusted upstream times: take the actual endpoints.
    letters=[(i,c) for i,c in enumerate(chars) if compact(c['text'])]
    pointer=0;bound=[]
    for entry in entries:
        target=compact(entry['text'])
        if not target: continue
        start=pointer;acc=''
        while pointer<len(letters) and target.startswith(acc+compact(letters[pointer][1]['text'])):
            acc+=compact(letters[pointer][1]['text']);pointer+=1
            if acc==target:break
        if acc!=target:raise ValueError('展示字幕与逐字原稿不匹配，禁止容错改写')
        begin=letters[start][0];end=letters[pointer-1][0]
        # Include source trailing punctuation timestamps, without displaying it.
        while end+1<len(chars) and not compact(chars[end+1]['text']):end+=1
        bound.append({'start':chars[begin]['start'],'end':chars[end]['end'],
                      'text':clean(entry['text'])})
    if pointer!=len(letters):raise ValueError('展示字幕丢失原稿实字')
    merged=0;i=0
    while i<len(bound):
        cue=bound[i]
        bad=len(compact(cue['text']))<2 or cue['end']-cue['start']<.3
        if not bad:i+=1;continue
        choices=[]
        for j in (i-1,i+1):
            if not 0<=j<len(bound):continue
            left,right=(bound[j],cue) if j<i else (cue,bound[j])
            text=left['text']+' '+right['text']
            if len(compact(text))<=26 and right['start']-left['end']<=1.2:
                choices.append((len(compact(text)),j,left,right,text))
        if not choices:raise ValueError('孤字或过短字幕无法安全合并，请重审 caption_units')
        _,j,left,right,text=min(choices,key=lambda x:x[0])
        low=min(i,j);bound[low:low+2]=[{'start':left['start'],'end':right['end'],'text':text}]
        merged+=1;i=max(0,low-1)
    for i,cue in enumerate(bound):
        if len(compact(cue['text']))>26:raise ValueError('字幕超长，请语义切分 caption_units')
        if cue['end']<=cue['start']:raise ValueError('字幕无有效时长')
        if i and cue['start']<bound[i-1]['end']-.001:
            raise ValueError('原始字时戳跨字幕重叠，禁止平移掩盖')
    if compact(''.join(c['text'] for c in bound))!=compact(''.join(c['text'] for c in chars)):
        raise ValueError('字幕实字守恒失败')
    report={'pipeline':PIPELINE,'mode':mode,'total_segments':len(bound),
            'short_cues_merged':merged,'srt_offset_seconds':0,
            'timing':'source character endpoints; no interpolation or cumulative shift'}
    return bound,report


def to_srt(entries):
    return '\n'.join(f'{i+1}\n{upstream.format_time(c["start"])} --> {upstream.format_time(c["end"])}\n{c["text"]}\n'
                     for i,c in enumerate(entries))


def validate_srt(path):
    """Gate the exact SRT that a burner/uploader will consume; never auto-repair."""
    entries=read_srt(str(path))
    if not entries:raise ValueError('字幕为空')
    last=0
    for cue in entries:
        start=upstream.parse_time(cue['start']);end=upstream.parse_time(cue['end'])
        text=cue['text']
        if clean(text)!=text:raise ValueError('展示字幕仍有标点或非标准空白')
        if not 2<=len(compact(text))<=26:raise ValueError('字幕孤字或超长')
        if end-start<.299 or start<last-.001:raise ValueError('字幕过短或重叠')
        last=end
    return {'pipeline':PIPELINE,'total_segments':len(entries),'punctuation':0,'orphan_cues':0,'short_cues':0}


BINDING_VERSION = 1


def validated_characters(alignment, text):
    """Validate the immutable supplier timeline before any display processing."""
    import math
    chars=alignment.get('characters')
    if not isinstance(chars,list) or not chars:raise ValueError('对齐结果没有逐字符时间戳')
    if any(not isinstance(c,dict) or not isinstance(c.get('text'),str) for c in chars):raise ValueError('逐字符对齐结构无效')
    if ''.join(c['text'] for c in chars)!=text:raise ValueError('逐字符对齐文字与原稿不一致，不能估算补齐')
    last=0;silent=0
    for c in chars:
        start,end=c.get('start'),c.get('end')
        if (isinstance(start,bool) or isinstance(end,bool) or not isinstance(start,(int,float))
                or not isinstance(end,(int,float)) or not all(math.isfinite(x) for x in (start,end))
                or start<last-.01 or end<start or start<0):raise ValueError('逐字符时间戳无效或倒序')
        if start==end:
            silent+=sum(not ch.isspace() and unicodedata.category(ch)[0] not in ('P','C','Z') for ch in c['text'])
            if silent>=8:raise ValueError('连续实字缺少有效时间戳，疑似配音漏读，禁止字幕恢复')
        else:silent=0
        last=end
    return chars


def verify_semantic_review(root, episode, artifacts, units_sha, registered_review, author):
    """Same independent-review contract before FA and at SRT consumption."""
    import hashlib
    root=Path(root).resolve()
    def entity(value):
        path=Path(value);path=path if path.is_absolute() else root/path
        if not path.is_relative_to(root) or path.resolve()!=path or path.is_symlink() or not path.is_file():
            raise ValueError('字幕绑定文件必须为项目内实体文件：'+str(path))
        return path
    def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    def obj(path):
        value=json.loads(entity(path).read_text())
        if not isinstance(value,dict):raise ValueError('字幕绑定JSON必须为对象')
        return value
    review=registered_review
    if not isinstance(review,dict) or review.get('verdict')!='pass' or review.get('episode_id')!=episode:
        raise ValueError('缺少已登记独立语义字幕审稿')
    reviewer=review.get('reviewer')
    if not isinstance(reviewer,str) or not reviewer.strip() or not isinstance(author,str) or reviewer.strip().casefold()==author.strip().casefold():
        raise ValueError('字幕语义审稿人与作者必须独立')
    review_path=entity(artifacts['review']['path']);source_review=obj(review_path)
    review_sha=sha(review_path)
    if review_sha!=artifacts['review']['sha256'] or review.get('review_sha256')!=review_sha:
        raise ValueError('独立语义审稿文件哈希过期')
    for field in ('episode_id','reviewer','verdict','caption_units_sha256','caption_review','script_sha256','outline_sha256','structure_sha256'):
        if review.get(field)!=source_review.get(field):raise ValueError('登记审稿与原始审稿文件不符')
    for kind in ('script','outline','structure'):
        if review.get(kind+'_sha256')!=artifacts[kind]['sha256']:raise ValueError('语义审稿冻结稿输入已过期')
    caption_review=review.get('caption_review')
    if review.get('caption_units_sha256')!=units_sha or not isinstance(caption_review,dict) or caption_review.get('status')!='pass' or not isinstance(caption_review.get('reason'),str) or not caption_review['reason'].strip():
        raise ValueError('缺少当前语义单元哈希的独立审核通过记录')
    return review_sha


def verify_store_semantic_review(store, episode, artifacts=None):
    """Read-only pre-FA validation, with the exact consumption review schema."""
    artifacts=artifacts if artifacts is not None else store.artifacts(episode)
    try:
        errors=store.review_errors(episode)
        if errors:raise ValueError('独立语义审稿绑定无效：'+'；'.join(errors))
        units=store.root/'episodes'/episode/'production'/'caption_units.json'
        if units.is_symlink() or units.resolve()!=units or not units.is_file():raise ValueError('字幕语义单元必须为项目内实体文件')
        from .vendor.smart_caption.caption_units import _load_caption_units
        texts=_load_caption_units(units)
        script=store.root/artifacts['script']['path']
        if compact(''.join(texts))!=compact(script.read_text()):raise ValueError('字幕语义单元与冻结稿不匹配')
        import hashlib
        units_sha=hashlib.sha256(units.read_bytes()).hexdigest()
        row=store.db.execute('SELECT data FROM reviews WHERE episode_id=?',(episode,)).fetchone()
        return verify_semantic_review(store.root,episode,artifacts,units_sha,json.loads(row['data']) if row else None,store.episode(episode).get('author'))
    except (OSError,KeyError,TypeError,AttributeError,json.JSONDecodeError) as exc:
        raise ValueError('语义字幕审稿预检缺失或无效：'+str(exc)) from exc


def verify_caption_binding(root, episode, artifacts, *, require_semantic=False,
                           expected_receipt=None, allow_legacy=False, registered_review=None, author=None):
    """Pure local consumption gate: re-read files and rebuild the exact display SRT.

    Never writes, charges, calls an API or trusts a historical passed flag.
    allow_legacy only permits missing schema/path/input fields for an explicit
    local receipt upgrade; historical mode/units hashes must still match.
    """
    import hashlib
    root=Path(root).resolve();work=root/'episodes'/episode/'production'
    def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    def entity(path):
        path=Path(path);path=path if path.is_absolute() else root/path
        if not path.is_relative_to(root) or path.resolve()!=path or path.is_symlink() or not path.is_file():
            raise ValueError('字幕绑定文件必须为项目内实体文件：'+str(path))
        return path
    def obj(path):
        value=json.loads(entity(path).read_text())
        if not isinstance(value,dict):raise ValueError('字幕绑定JSON必须为对象')
        return value
    try:
        receipt=obj(work/'alignment.binding.json')
        if expected_receipt is not None and receipt!=expected_receipt:raise ValueError('字幕账本与实体绑定回执不符')
        if receipt.get('episode_id')!=episode or receipt.get('status')!='done':raise ValueError('字幕绑定episode/status不符')
        paths={}
        for kind in ('script','audio','subtitles'):
            artifact=artifacts[kind];paths[kind]=entity(artifact['path'])
            if sha(paths[kind])!=artifact['sha256']:raise ValueError('字幕输入产物哈希过期：'+kind)
        inputs={'script_sha256':artifacts['script']['sha256'],'audio_sha256':artifacts['audio']['sha256']}
        if receipt.get('inputs')!=inputs:raise ValueError('字幕绑定冻结稿/音轨已过期')
        artifact=receipt.get('artifact',{})
        if artifact.get('path')!=artifacts['subtitles']['path'] or artifact.get('sha256')!=artifacts['subtitles']['sha256']:
            raise ValueError('字幕绑定与当前注册SRT不符')
        key=hashlib.sha256(json.dumps(inputs,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        target=work/'align'/key
        expected_paths={'alignment_path':target/'alignment.json','display_path':target/'subtitles.srt','quality_path':target/'subtitles.quality.json'}
        if not allow_legacy and receipt.get('caption_binding_version')!=BINDING_VERSION:raise ValueError('旧字幕绑定须先本地align --reconcile升级')
        for field,path in expected_paths.items():
            if not (allow_legacy and field not in receipt) and receipt.get(field)!=str(path.relative_to(root)):
                raise ValueError('字幕绑定源文件路径不符：'+field)
        source=entity(expected_paths['alignment_path']);display=entity(expected_paths['display_path'])
        if sha(source)!=receipt.get('alignment_sha256'):raise ValueError('原始强制对齐哈希已变')
        if sha(display)!=artifacts['subtitles']['sha256']:raise ValueError('operation展示字幕与注册SRT不符')
        quality=receipt.get('caption_quality')
        if not isinstance(quality,dict):raise ValueError('缺少字幕质量绑定')
        if receipt.get('caption_pipeline')!=PIPELINE or quality.get('pipeline')!=PIPELINE:raise ValueError('字幕组件版本不符')
        actual_quality=obj(expected_paths['quality_path'])
        if actual_quality!=quality:raise ValueError('字幕质量旁车与回执不符')
        if quality.get('alignment_sha256')!=sha(source) or quality.get('srt_sha256')!=sha(display) or quality.get('srt_offset_seconds')!=0:
            raise ValueError('字幕质量源时间轴/SRT哈希绑定不符')
        if not allow_legacy and (quality.get('binding_version')!=BINDING_VERSION or quality.get('inputs')!=inputs):
            raise ValueError('字幕质量缺少冻结稿/音轨绑定')
        units=work/'caption_units.json'
        units_present=units.exists() or units.is_symlink()
        mode=quality.get('mode')
        if require_semantic and mode!='caption_units':raise ValueError('正式渲染要求真实语义字幕单元，确定性结果仅限诊断')
        if mode=='caption_units':
            units=entity(units)
            if quality.get('caption_units_sha256')!=sha(units):raise ValueError('字幕语义单元哈希过期')
            unit_values=json.loads(units.read_text())
            if not isinstance(unit_values,list):raise ValueError('字幕语义单元必须为列表')
        elif mode=='deterministic_review_required' and not units_present and quality.get('caption_units_sha256') is None:
            unit_values=None
        else:raise ValueError('字幕语义模式/单元文件绑定不符')
        if require_semantic:
            review_sha=verify_semantic_review(root,episode,artifacts,quality.get('caption_units_sha256'),registered_review,author)
            if not allow_legacy and quality.get('semantic_review_sha256')!=review_sha:
                raise ValueError('字幕质量未绑定当前独立语义审稿')
        alignment=obj(source);chars=validated_characters(alignment,paths['script'].read_text().strip())
        entries,_=display_captions(chars,unit_values)
        if to_srt(entries)!=display.read_text() or display.read_bytes()!=paths['subtitles'].read_bytes():
            raise ValueError('当前语义单元/逐字时戳不能重建实际SRT')
        measured=validate_srt(display)
        if any(quality.get(k)!=v for k,v in measured.items()):raise ValueError('字幕质量指标与实际SRT不符')
        return {'contract':{'binding_version':BINDING_VERSION,'alignment_sha256':sha(source),'srt_sha256':sha(display),'caption_units_sha256':quality.get('caption_units_sha256'),'semantic_review_sha256':quality.get('semantic_review_sha256'),'quality_sha256':sha(expected_paths['quality_path'])},'receipt':receipt,'inputs':inputs,'paths':{k:str(v.relative_to(root)) for k,v in expected_paths.items()},
                'mode':mode,'caption_units_sha256':quality.get('caption_units_sha256'),
                'alignment_sha256':sha(source),'srt_sha256':sha(display),'max_alignment_end':max(c['end'] for c in chars)}
    except (OSError,KeyError,TypeError,AttributeError,json.JSONDecodeError) as exc:
        raise ValueError('字幕消费绑定缺失或无效：'+str(exc)) from exc


def verify_store_caption_binding(store, episode, artifacts=None, **options):
    """Read-only adapter supplies the registered independent review to the gate."""
    artifacts=artifacts if artifacts is not None else store.artifacts(episode)
    if options.get('require_semantic'):
        errors=store.review_errors(episode)
        if errors:raise ValueError('独立语义审稿绑定无效：'+'；'.join(errors))
        row=store.db.execute('SELECT data FROM reviews WHERE episode_id=?',(episode,)).fetchone()
        options.update(registered_review=json.loads(row['data']) if row else None,author=store.episode(episode).get('author'))
    return verify_caption_binding(store.root,episode,artifacts,**options)
