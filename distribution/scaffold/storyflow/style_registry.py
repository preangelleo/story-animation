"""Portable style-only registry. No clock, supplier, or source-project dependency."""
from __future__ import annotations
import copy
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
_INDEX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_BAD = re.compile(r"caption|subtitle|lower.?third|lower\s+\d+\s*percent|reserved?.{0,30}(?:band|space)|字幕|底带|运镜|pan[- ](?:left|right)|dolly|zoom|camera movement|handheld|frames per second|recognizable real people|real public figures|ignore.{0,30}instructions|system prompt|(?:system|assistant|developer|tool)\s*[:=]|(?:call|execute|invoke|run)\s+(?:tools?|commands?|shell|python|curl)|api[_ -]?key|password|bearer|<script|https?://|\$\(|```", re.I)
# A style registry owns aesthetics only. Layout, motion and executable/instructional
# payloads are invalid even when they avoid the word "caption". Artistic ink
# negative space remains valid because it does not reserve a frame region.
_NON_STYLE = re.compile(
    r"\b(?:camera|tracking\s+shot|tilt(?:s|ing)?|dolly(?:ing)?|zoom(?:s|ing)?|pan(?:s|ned|ning)?|orbit(?:s|ing)?|frame\s+rate|fps)\b"
    r"|\bslow\s+(?:push|pull)[- ](?:in|out)\b"
    r"|\b(?:bottom|lower|upper|top)\b.{0,50}\b(?:empty|blank|clear|reserve|space|percent|%)"
    r"|\b(?:reserve|leave|keep)\b.{0,50}\b(?:bottom|lower|upper|top|strip|band)\b"
    r"|(?:底部|下方|上方|最下缘|底边).{0,30}(?:空|白|预留|保留|百分|%)"
    r"|镜头|摄影机|摄像机|推拉|平移|摇摄|(?:缓慢|慢速)(?:推近|拉远)"
    r"|\b(?:disregard|ignore|forget|override|bypass)\b.{0,60}\b(?:rules?|instructions?|prompts?|previous|prior|above)\b"
    r"|\b(?:reveal|expose|print|send|return|upload)\b.{0,40}\b(?:secrets?|credentials?|tokens?|passwords?|keys?)\b"
    r"|忽略.{0,30}(?:指令|规则|要求)|泄露|执行命令|调用工具",
    re.I,
)
_STYLE_KEYS = {'index','id','name','description','prompt','palette','lighting','source'}

def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f'{label}: required keys are {sorted(keys)}')

def _text(value, label, safe=True):
    if not isinstance(value, str) or not value.strip() or len(value)>10000 or any(ord(c)<32 and c not in '\n\t' for c in value):
        raise ValueError(f'{label}: expected nonempty text')
    if safe and (_BAD.search(value) or _NON_STYLE.search(value)):
        raise ValueError(f'{label}: unsafe or non-style instruction')

def _style(style):
    _object(style, _STYLE_KEYS, 'style')
    if not isinstance(style['index'],str) or not _INDEX.fullmatch(style['index']): raise ValueError('invalid stable style index')
    if not isinstance(style['id'],str) or not _SLUG.fullmatch(style['id']): raise ValueError('invalid style slug')
    _object(style['name'], {'zh','en'}, 'name')
    for name in style['name'].values(): _text(name, 'name')
    for key in ('description','prompt','lighting'): _text(style[key], key)
    colors=style['palette']
    if not isinstance(colors,list) or not colors or len(colors)>32 or any(not isinstance(c,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',c) for c in colors): raise ValueError('invalid palette colors')
    _object(style['source'], {'path','sha256','version','url'}, 'source')
    for key in ('path','url'): _text(style['source'][key], f'source.{key}', safe=False)
    if not isinstance(style['source']['version'],int) or isinstance(style['source']['version'],bool) or style['source']['version']<1: raise ValueError('invalid source version')
    if not isinstance(style['source']['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',style['source']['sha256']): raise ValueError('invalid source hash')
    return style

def validate_catalog(catalog):
    _object(catalog, {'schema_version','styles'}, 'catalog')
    if type(catalog['schema_version']) is not int or catalog['schema_version'] != 1 or not isinstance(catalog['styles'],list) or not catalog['styles']: raise ValueError('invalid catalog schema')
    indices=set(); ids=set()
    for style in catalog['styles']:
        _style(style)
        if style['index'] in indices or style['id'] in ids: raise ValueError('duplicate style index or id')
        indices.add(style['index']); ids.add(style['id'])
    return catalog

def validate_mapping(mapping, catalog=None):
    _object(mapping, {'schema_version','timezone','series'}, 'mapping')
    if type(mapping['schema_version']) is not int or mapping['schema_version'] != 1 or mapping['timezone'] != 'Asia/Tokyo' or not isinstance(mapping['series'],list): raise ValueError('invalid mapping schema/timezone')
    seen=set(); days=set()
    for row in mapping['series']:
        _object(row, {'key','display_name','weekday','default_style_index','description'}, 'series')
        if not isinstance(row['key'],str) or not _SLUG.fullmatch(row['key']) or row['key'] in seen: raise ValueError('invalid or duplicate series key')
        if type(row['weekday']) is not int or row['weekday'] not in range(1,8) or row['weekday'] in days: raise ValueError('invalid or duplicate weekday')
        for key in ('display_name','description'): _text(row[key], key)
        if not isinstance(row['default_style_index'],str) or not _INDEX.fullmatch(row['default_style_index']): raise ValueError('invalid default index')
        if catalog and row['default_style_index'] not in {s['index'] for s in catalog['styles']}: raise ValueError('unknown default style index')
        seen.add(row['key']); days.add(row['weekday'])
    if days != set(range(1,8)) or 'other' not in seen: raise ValueError('mapping requires seven days and explicit other series')
    return mapping

def _path(root, explicit, config_key, default):
    root=Path(root or DEFAULT_ROOT).expanduser().resolve()
    if explicit is None:
        config=root/'documentation/configuration.json'
        settings=json.loads(config.read_text()) if config.exists() else {}
        explicit=settings.get(config_key,default)
    path=Path(explicit).expanduser()
    return path if path.is_absolute() else root/path

def load_catalog(style_file=None, *, root=None):
    return validate_catalog(json.loads(_path(root,style_file,'style_registry_file','config/styles.json').read_text()))

def load_mapping(mapping_file=None, *, root=None):
    return validate_mapping(json.loads(_path(root,mapping_file,'series_mapping_file','config/weekly_series.json').read_text()))

def _release_day(value):
    if isinstance(value,dt.datetime):
        if value.tzinfo is None: raise ValueError('release datetime requires explicit timezone')
        return value.astimezone(ZoneInfo('Asia/Tokyo')).date()
    if type(value) is dt.date: return value
    if not isinstance(value,str): raise ValueError('explicit release_date required; machine date is never used')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value): return dt.date.fromisoformat(value)
    parsed=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    return _release_day(parsed)

def _effective_prompt(style):
    return style['prompt'].rstrip() + '\nPalette: ' + ', '.join(style['palette']) + '.\nLighting: ' + style['lighting']

def resolve_style(root=None, *, release_date=None, series=None, style_index=None, style_file=None, mapping_file=None, classification_evidence=None):
    catalog=load_catalog(style_file,root=root); mapping=load_mapping(mapping_file,root=root)
    validate_mapping(mapping,catalog)
    rows={r['key']:r for r in mapping['series']}
    if series is not None and series not in rows: raise ValueError(f'unknown series: {series}')
    day=_release_day(release_date) if release_date is not None else None
    if series is None:
        if day is None: raise ValueError('series or explicit release_date required to preserve content classification')
        row=next(r for r in rows.values() if r['weekday']==day.isoweekday())
    else: row=rows[series]
    index=style_index if style_index is not None else row['default_style_index']
    matches=[s for s in catalog['styles'] if s['index']==index]
    if not matches: raise ValueError(f'unknown style index: {index}')
    style=copy.deepcopy(matches[0])
    if classification_evidence is not None: _text(classification_evidence,'classification_evidence',safe=False)
    snap={'schema_version':1,'style_index':index,'style_id':style['id'],'prompt':_effective_prompt(style),
          'series':row['key'],'series_key':row['key'],'series_snapshot':copy.deepcopy(row),
          'release_date_tokyo':day.isoformat() if day else None,
          'classification_evidence':classification_evidence,
          'resolution_source':'explicit_style' if style_index is not None else 'explicit_series' if series is not None else 'release_weekday',
          'catalog_sha256':_sha(catalog),'mapping_sha256':_sha(mapping),'style':style}
    snap['snapshot_sha256']=_sha(snap)
    return snap

def validate_snapshot(snapshot):
    try:
        _object(snapshot, {'schema_version','style_index','style_id','prompt','series','series_key','series_snapshot','release_date_tokyo','classification_evidence','resolution_source','catalog_sha256','mapping_sha256','style','snapshot_sha256'},'snapshot')
        if type(snapshot['schema_version']) is not int or snapshot['schema_version'] != 1: raise ValueError('invalid snapshot schema')
        _style(snapshot['style'])
        if snapshot['style_index'] != snapshot['style']['index'] or snapshot['style_id'] != snapshot['style']['id'] or snapshot['prompt'] != _effective_prompt(snapshot['style']): raise ValueError('snapshot style binding mismatch')
        row=snapshot['series_snapshot']
        _object(row, {'key','display_name','weekday','default_style_index','description'}, 'series snapshot')
        if not isinstance(row['key'],str) or not _SLUG.fullmatch(row['key']): raise ValueError('invalid frozen series key')
        if type(row['weekday']) is not int or row['weekday'] not in range(1,8): raise ValueError('invalid frozen weekday')
        for key in ('display_name','description'): _text(row[key], key)
        if not isinstance(row['default_style_index'],str) or not _INDEX.fullmatch(row['default_style_index']): raise ValueError('invalid frozen default index')
        if snapshot['classification_evidence'] is not None: _text(snapshot['classification_evidence'],'classification_evidence',safe=False)
        if snapshot['resolution_source'] != 'explicit_style' and row['default_style_index'] != snapshot['style_index']: raise ValueError('frozen default style mismatch')
        if snapshot['series'] != snapshot['series_key'] or snapshot['series_key'] != row['key']: raise ValueError('snapshot series binding mismatch')
        if snapshot['resolution_source'] not in ('explicit_style','explicit_series','release_weekday'): raise ValueError('invalid resolution source')
        day=_release_day(snapshot['release_date_tokyo']) if snapshot['release_date_tokyo'] is not None else None
        if snapshot['resolution_source'] == 'release_weekday' and (day is None or day.isoweekday() != row['weekday']): raise ValueError('frozen release weekday mismatch')
        for key in ('catalog_sha256','mapping_sha256','snapshot_sha256'):
            if not isinstance(snapshot[key],str) or not re.fullmatch('[0-9a-f]{64}',snapshot[key]): raise ValueError(f'invalid {key}')
        body={k:v for k,v in snapshot.items() if k!='snapshot_sha256'}
        if _sha(body) != snapshot['snapshot_sha256']: raise ValueError('snapshot hash mismatch')
        return []
    except (ValueError,TypeError,KeyError) as exc: return [str(exc)]

def snapshot_style(snapshot):
    errors=validate_snapshot(snapshot)
    if errors: raise ValueError('; '.join(errors))
    return copy.deepcopy(snapshot['style'])

def style_prompt(snapshot):
    snapshot_style(snapshot)
    return snapshot['prompt']
