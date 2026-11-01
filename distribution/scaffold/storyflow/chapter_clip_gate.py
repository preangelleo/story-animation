"""Episode-only consumption of owner-selected portable chapter parameters.

The public renderer remains owner-independent. An outer, hash-bound episode
contract proves that its actual clips derive from the already selected preset.
"""
import json
from pathlib import Path
import re
from .core import WorkflowError,config,digest


def _file(root,value):
    if not isinstance(value,str) or not value.strip() or '\x00' in value or '://' in value:
        raise WorkflowError('chapter approval requires local file path')
    p=Path(value).expanduser();p=p if p.is_absolute() else Path(root)/p
    if p.is_symlink() or not p.is_file():raise WorkflowError('chapter approval file missing or symlink: '+str(p))
    return p.resolve()


def _same(value,expected,label):
    def equal(a,b):
        if type(a) is not type(b):return False
        if isinstance(b,dict):return a.keys()==b.keys() and all(equal(a[k],b[k]) for k in b)
        if isinstance(b,(list,tuple)):return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
        return a==b
    if not equal(value,expected):raise WorkflowError('chapter parameters approval mismatch: '+label)


def _validate_parameter_contract(root,gate,clips=None,required=True):
    """Verify actual files and every effective parameter, including asset bytes."""
    from .chapter_render import effective,composition
    root=Path(root).resolve()
    fields=('parameter_contract_path','parameter_contract_sha256')
    if not any(k in gate for k in fields):
        if required:raise WorkflowError('gated chapter consumption requires parameter contract')
        return None
    if not all(gate.get(k) for k in fields):raise WorkflowError('chapter parameter contract incomplete')
    contract_path=_file(root,gate['parameter_contract_path'])
    _same(digest(contract_path),gate['parameter_contract_sha256'],'contract SHA')
    contract=json.loads(contract_path.read_text())
    _same(contract.get('schema'),'qy.chapter.parameters.approval.v1','contract schema')
    preset=_file(root,contract['preset_path'])
    _same(digest(preset),contract.get('preset_sha256'),'preset SHA')
    rows=contract.get('clips')
    if not isinstance(rows,list) or not rows:raise WorkflowError('chapter approval needs ordered clips')
    if clips is not None and len(clips)!=len(rows):raise WorkflowError('chapter approval clip count mismatch')
    seen=set();relocations=[]
    for i,row in enumerate(rows):
        clip=_file(root,row['clip_path']);manifest_path=_file(root,row['manifest_path'])
        if clip in seen:raise WorkflowError('duplicate approved chapter clip')
        seen.add(clip)
        _same(digest(clip),row.get('clip_sha256'),'clip SHA')
        _same(digest(manifest_path),row.get('manifest_sha256'),'manifest SHA')
        if clips is not None:
            actual=clips[i]['clip'] if isinstance(clips[i],dict) else clips[i]
            _same(_file(root,actual),clip,'ordered episode clip')
            _same(digest(_file(root,actual)),row['clip_sha256'],'ordered episode clip SHA')
        manifest=json.loads(manifest_path.read_text())
        _same(manifest.get('schema'),'qy.chapter-render.v1','renderer schema')
        if manifest.get('executed') is not True:raise WorkflowError('chapter clip was not actually rendered')
        expected,source=effective(root,preset,{'title_text':row['title_text'],'chapter_number':row['chapter_number']})
        actual=manifest.get('effective',{})
        path_keys={'title_font','number_font','sound_path'}
        _same({k:v for k,v in actual.items() if k not in path_keys},
              {k:v for k,v in expected.items() if k not in path_keys},'every non-path effective parameter')
        _same(set(actual),set(expected),'complete effective parameter keys')
        actual_source=manifest.get('source',{})
        _same(set(actual_source),set(source),'source provenance fields')
        _same(actual_source['preset_sha256'],source['preset_sha256'],'source preset SHA')
        _same(set(actual_source['fonts']),set(source['fonts']),'source font roles')
        for role in ('title','number'):
            actual_font=actual_source['fonts'][role];canonical_font=source['fonts'][role]
            _same(actual.get(role+'_font'),actual_font.get('source'),role+' original path provenance')
            _same({k:v for k,v in actual_font.items() if k!='source'},
                  {k:v for k,v in canonical_font.items() if k!='source'},role+' content identity')
            relocations.append({'clip':str(clip),'role':role,'original_asset_path':actual_font['source'],
                                'canonical_asset_path':canonical_font['source'],'sha256':canonical_font['sha256']})
        actual_sound=actual_source['sound'];canonical_sound=source['sound']
        if not isinstance(actual_sound,dict) or not isinstance(canonical_sound,dict):raise WorkflowError('approved episode chapter requires original sound provenance')
        _same(actual.get('sound_path'),actual_sound.get('source'),'sound original path provenance')
        _same({k:v for k,v in actual_sound.items() if k!='source'},
              {k:v for k,v in canonical_sound.items() if k!='source'},'sound content identity')
        relocations.append({'clip':str(clip),'role':'sound','original_asset_path':actual_sound['source'],
                            'canonical_asset_path':canonical_sound['source'],'sha256':canonical_sound['sha256']})
        _same(source['preset_sha256'],contract['preset_sha256'],'source preset SHA')
        for role in ('title','number'):
            _same(source['fonts'][role]['sha256'],gate.get(role+'_font_sha256'),role+' owner-selected font SHA')
        sound=source['sound']
        if not sound:raise WorkflowError('approved episode chapter sound cannot be muted')
        _same(sound['sha256'],gate.get('accepted_base_clip_sha256'),'accepted S1 base clip SHA')
        if expected['sound_volume']!=1 or expected['muted'] is not False or manifest.get('audio_mode')!='stream_copy':
            raise WorkflowError('approved S1 audio must preserve unity stream copy')
        _same(_file(root,manifest['output']),clip,'manifest output path')
        _same(manifest.get('output_sha256'),row['clip_sha256'],'manifest output SHA')
        bundle=Path(manifest['composition']).resolve()
        _same(manifest_path,bundle/'manifest.json','composition manifest path')
        html=_file(root,str(bundle/'index.html'))
        _same(digest(html),manifest.get('html_sha256'),'actual HTML SHA')
        _same(html.read_text(),composition(expected),'actual HTML parameters')
        for role in ('title','number'):
            copied=_file(root,str(bundle/'assets'/f'{role}.font'))
            _same(digest(copied),source['fonts'][role]['sha256'],role+' copied font SHA')
        renderer=manifest.get('renderer',{})
        if not isinstance(renderer,dict) or not isinstance(renderer.get('hyperframes'),str) or not renderer['hyperframes'] or not re.fullmatch('[0-9a-f]{64}',str(renderer.get('module_sha256',''))):
            raise WorkflowError('chapter renderer provenance missing')
        gsap=_file(root,str(bundle/'assets/gsap.min.js'))
        _same(digest(gsap),renderer.get('gsap_sha256'),'actual renderer GSAP SHA')
    return {**contract,'asset_relocations':relocations}


def require_episode_clips(root,episode,clips):
    """Only explicitly gated episodes require their approved clip derivation."""
    root=Path(root).resolve()
    entry=config(root).get('revision_approval_gates',{}).get(episode)
    if not entry:return None
    # Also preserve the original owner/font/template/audio gate; never replace it.
    from .revision_gate import check_revision
    check_revision(root,episode)
    gate=json.loads(_file(root,entry).read_text())
    return validate_parameter_contract(root,gate,clips,required=True)


def validate_parameter_contract(root,gate,clips=None,required=True):
    try:return _validate_parameter_contract(root,gate,clips,required)
    except WorkflowError:raise
    except (OSError,KeyError,TypeError,ValueError) as exc:
        raise WorkflowError('chapter parameters approval evidence invalid: '+str(exc)) from exc
