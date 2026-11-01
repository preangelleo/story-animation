"""Chapter insertion uses a single source clock, with half-open intervals.

No media is produced here. All streams are split at the same source boundaries;
inserted title cards occupy gaps on the delivery clock, never source audio time.
"""
import hashlib
import json
import subprocess
from html.parser import HTMLParser
from decimal import Decimal
from pathlib import Path
from .core import WorkflowError


def _d(value):
    n = Decimal(str(value))
    if not n.is_finite() or n < 0:
        raise WorkflowError('invalid nonnegative time')
    return n


def insertions(items):
    out = []
    for item in items:
        at, duration = _d(item['source_time']), _d(item['duration'])
        if not Decimal('0') < duration <= Decimal('2'):
            raise WorkflowError('transition duration must be >0 and <=2 seconds')
        if out and at <= out[-1][0]:
            raise WorkflowError('chapter boundaries must be strictly increasing')
        out.append((at, duration))
    return out


def map_time(value, items, side='after'):
    """At an insertion, end of prior clip uses before; next start uses after."""
    if side not in ('before', 'after'):
        raise WorkflowError('invalid boundary side')
    t = _d(value)
    return t + sum((duration for at, duration in insertions(items)
                    if at < t or (at == t and side == 'after')), Decimal(0))


def map_intervals(intervals, items):
    """Split all streams, preserving source offsets and all original metadata."""
    points = insertions(items)
    result = []
    for item in intervals:
        start, end = _d(item['start']), _d(item['end'])
        if end <= start:
            raise WorkflowError('interval end must exceed start')
        cuts = [start] + [at for at, _ in points if start < at < end] + [end]
        for a, b in zip(cuts, cuts[1:]):
            result.append({**item, 'source_start': float(a), 'source_end': float(b),
                           'source_offset': float(a-start),
                           'start': float(map_time(a, items, 'after')),
                           'end': float(map_time(b, items, 'before'))})
    return result


def timeline_plan(payload):
    items = payload['insertions']
    points = insertions(items)
    source_duration = _d(payload['source_duration'])
    if any(not 0 < at < source_duration for at, _ in points):
        raise WorkflowError('chapter boundary must be inside source duration')
    streams = payload.get('streams', {})
    unknown = set(streams) - {'narration', 'subtitles', 'shots', 'sfx'}
    if unknown:
        raise WorkflowError('unknown source streams: ' + ','.join(sorted(unknown)))
    for values in streams.values():
        for value in values:
            if _d(value['end']) > source_duration:
                raise WorkflowError('stream interval outside source duration')
    # Picture scenes are indivisible; only continuous audio/caption clocks split.
    for shot in streams.get('shots',[]):
        if any(_d(shot['start'])<at<_d(shot['end']) for at,_ in points):
            raise WorkflowError('chapter insertion inside scene; reviewed scene-boundary-replan required')
    gaps = []
    for item in items:
        start = map_time(item['source_time'], items, 'before')
        gaps.append({**item, 'start': float(start), 'end': float(start+_d(item['duration']))})
    return {'clock': 'source_to_delivery_half_open', 'source_duration': float(source_duration),
            'delivery_duration': float(map_time(source_duration, items)), 'transitions': gaps,
            'streams': {name: map_intervals(payload.get('streams', {}).get(name, []), items)
                        for name in ('narration', 'subtitles', 'shots', 'sfx')}}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


FONT_CONTRACT = 'templates/chapter-transition/design-contract.json'
FONT_MANIFEST = 'assets/fonts/chapter-title/manifest.json'
FONT_NAMES = {'F1': '字制区喜脉体', 'F2': '仓迹高德国妙黑', 'F3': '庞门正道细线体'}


def _local(root, value):
    path = (Path(root) / value).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise WorkflowError('chapter asset outside project')
    return path


def font_contract(root, contract=FONT_CONTRACT, font_id=None, title=None, combination=None):
    """Read-only verification of actual files and fontconfig cmap, never fallback."""
    try:
        design_path = _local(root, contract)
        design = json.loads(design_path.read_text())
        if design.get('schema_version') != 2:
            raise WorkflowError('chapter design requires dual-role font schema 2')
        if design.get('accepted_layout') != 'a' or design.get('accepted_sound') != 'S1' or len(design.get('accepted_sound_sha256','')) != 64 or len(design.get('accepted_base_clip_sha256','')) != 64:
            raise WorkflowError('chapter design missing accepted layout/sound binding')
        combinations = design['combinations']
        if not combinations or any(set(v) != {'title_font_id', 'number_font_id'} or any(fid not in FONT_NAMES for fid in v.values()) for v in combinations.values()):
            raise WorkflowError('invalid chapter font combinations')
        if combination is not None and combination not in combinations:
            raise WorkflowError('unknown chapter font combination')
        manifest_path = _local(root, design['fonts_manifest'])
        if manifest_path != _local(root, FONT_MANIFEST):
            raise WorkflowError('chapter font manifest must be project canonical manifest')
        if design['fonts_manifest_sha256'] != sha256(manifest_path):
            raise WorkflowError('chapter font manifest hash mismatch')
        if design['font_weight'] != 400 or design['font_synthesis'] != 'none':
            raise WorkflowError('chapter title requires weight 400 and synthesis none')
        manifest = json.loads(manifest_path.read_text())
        if manifest.get('schema_version') != 1:
            raise WorkflowError('invalid chapter font manifest schema')
        titles = manifest['chapter_titles']
        if not titles or any(not isinstance(t, str) or not t.strip() for t in titles):
            raise WorkflowError('chapter titles missing')
        if design.get('chapter_titles') != titles:
            raise WorkflowError('chapter font/design title mismatch')
        text = ''.join(titles) + '第一章第二章第三章' + (title or '')
        fonts = manifest['fonts']
        if len(fonts) != 3 or {f['id'] for f in fonts} != set(FONT_NAMES):
            raise WorkflowError('chapter font manifest requires unique F1/F2/F3')
        results = []
        for font in fonts:
            fid = font['id']
            path = _local(root, font['asset'])
            expected_suffix = '.woff2' if fid == 'F2' else '.ttf'
            if font['asset'] != 'assets/fonts/chapter-title/' + fid + expected_suffix:
                raise WorkflowError('chapter font asset path mismatch: ' + fid)
            if path.suffix != expected_suffix or font['format'] != ('woff2' if fid == 'F2' else 'truetype'):
                raise WorkflowError('chapter font format mismatch: ' + fid)
            if font['weight'] != 400 or font['synthesis'] != 'none':
                raise WorkflowError('chapter font requires real weight 400: ' + fid)
            if not font.get('family') or not font.get('provenance') or not font.get('source_path'):
                raise WorkflowError('chapter font provenance missing: ' + fid)
            signature = path.read_bytes()[:4]
            if signature != (b'wOF2' if fid == 'F2' else b'\x00\x01\x00\x00'):
                raise WorkflowError('chapter font binary format mismatch: ' + fid)
            if font['sha256'] != sha256(path):
                raise WorkflowError('chapter font hash mismatch: ' + fid)
            families = subprocess.run(['fc-scan', '--format', '%{family}', str(path)],
                                      check=True, capture_output=True, text=True).stdout.split(',')
            if FONT_NAMES[fid] not in families or font['internal_family'] not in families:
                raise WorkflowError('chapter font internal family mismatch: ' + fid)
            charset = subprocess.run(['fc-query', '--format', '%{charset}', str(path)],
                                     check=True, capture_output=True, text=True).stdout
            ranges = []
            for part in charset.split():
                lo, _, hi = part.partition('-')
                ranges.append((int(lo, 16), int(hi or lo, 16)))
            missing = sorted({c for c in text if not any(lo <= ord(c) <= hi for lo, hi in ranges)})
            if missing:
                raise WorkflowError('chapter font missing glyphs: ' + fid + ':' + ''.join(missing))
            results.append({**font, 'coverage_verified': True, 'verified_text': text})
        if font_id is not None and font_id not in FONT_NAMES:
            raise WorkflowError('unknown chapter title font: ' + str(font_id))
        return {'design_contract_sha256': sha256(design_path),
                'fonts_manifest_sha256': sha256(manifest_path),
                'design': design, 'combinations': combinations, 'font_combination': combination,
                'selected_title_font': next((f for f in results if combination and f['id'] == combinations[combination]['title_font_id']), None),
                'selected_number_font': next((f for f in results if combination and f['id'] == combinations[combination]['number_font_id']), None),
                'fonts': results, 'selected_font': next((f for f in results if f['id'] == font_id), None)}
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        raise WorkflowError('chapter font contract invalid: ' + str(exc)) from exc


def require_owner_choice(gate, template, sound, root=None):
    """Approval binds layout, concrete font, design contract and sound hashes."""
    if gate.get('status') != 'approved' or gate.get('actor') != 'owner' or not str(gate.get('owner_choice_evidence', '')).strip():
        raise WorkflowError('waiting_owner_font_choice: pending_font_choice')
    if gate.get('schema') != 3 or not gate.get('font_combination') or not gate.get('title_font_id') or not gate.get('number_font_id') or gate.get('design_contract_path') != FONT_CONTRACT:
        raise WorkflowError('owner choice legacy or missing font contract')
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    verified = font_contract(root, gate['design_contract_path'], combination=gate['font_combination'])
    if gate.get('design_contract_sha256') != verified['design_contract_sha256'] or any(gate.get(role + '_font_id') != verified['selected_' + role + '_font']['id'] or gate.get(role + '_font_sha256') != verified['selected_' + role + '_font']['sha256'] for role in ('title', 'number')):
        raise WorkflowError('owner choice stale: design/font hash mismatch')
    design = verified['design']
    if gate.get('design') != design.get('accepted_layout') or gate.get('sound') != design.get('accepted_sound') or gate.get('sound_sha256') != design['accepted_sound_sha256'] or gate.get('accepted_base_clip_sha256') != design['accepted_base_clip_sha256']:
        raise WorkflowError('owner choice differs from accepted layout/sound')
    require_template_font(template, verified)
    if gate.get('template_sha256') != sha256(template) or gate.get('sound_sha256') != sha256(sound):
        raise WorkflowError('owner choice stale: template/sound hash mismatch')
    from .chapter_clip_gate import validate_parameter_contract
    validate_parameter_contract(root,gate,required=False)
    return gate


def require_template_font(template, verified):
    """A selected F2 cannot authorize a frozen F1 instance, even with valid hashes."""
    try:
        template = Path(template).resolve()
        binding = json.loads(template.with_suffix('.design.json').read_text())
        if (binding.get('schema_version') != 2 or binding.get('font_combination') != verified['font_combination']
                or binding.get('design_contract_sha256') != verified['design_contract_sha256']
                or binding.get('template_sha256') != sha256(template)):
            raise WorkflowError('owner choice stale: template font binding hash mismatch')
        class ElementAttributes(HTMLParser):
            def __init__(self):
                super().__init__(); self.attributes = {}; self.elements = {}
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'html': self.attributes = attrs
                if tag == 'h1': self.elements['title'] = attrs
                if 'number' in attrs.get('class', '').split(): self.elements['number'] = attrs
        parser = ElementAttributes(); parser.feed(template.read_text())
        if (parser.attributes.get('data-design-contract-sha256') != verified['design_contract_sha256']
                or parser.attributes.get('data-layout') != verified['design']['accepted_layout']):
            raise WorkflowError('template selected font attributes mismatch')
        for role in ('title', 'number'):
            font = verified['selected_' + role + '_font']
            if binding.get(role + '_font_id') != font['id'] or binding.get(role + '_font_sha256') != font['sha256']:
                raise WorkflowError('owner choice stale: template font binding hash mismatch: ' + role)
            copied_font = _local(template.parent, binding[role + '_font_asset'])
            if sha256(copied_font) != font['sha256']:
                raise WorkflowError('owner choice stale: template font asset hash mismatch: ' + role)
            expected = {'data-' + role + '-font-id': font['id'], 'data-' + role + '-font-sha256': font['sha256']}
            if any(parser.attributes.get(k) != v for k, v in expected.items()):
                raise WorkflowError('template selected font attributes mismatch: ' + role)
            styles = dict(part.split(':', 1) for part in parser.elements.get(role, {}).get('style', '').split(';') if ':' in part)
            family = styles.get('font-family', '').strip().strip('\'"')
            if family != font['family']:
                raise WorkflowError('template actual element font family mismatch: ' + role)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WorkflowError('template font binding invalid: ' + str(exc)) from exc


def pending_gate():
    return {'schema': 3, 'status': 'pending_font_choice', 'design': None, 'sound': None,
            'template_sha256': None, 'sound_sha256': None, 'font_combination': None, 'title_font_id': None, 'title_font_sha256': None,
            'number_font_id': None, 'number_font_sha256': None,
            'design_contract_path': FONT_CONTRACT, 'design_contract_sha256': None, 'actor': None,
            'owner_choice_evidence': None}


def register(subparsers):
    p = subparsers.add_parser('chapters', help='source-clock mapping and owner layout/font/sound gate')
    s = p.add_subparsers(dest='chapter_action', required=True)
    from .chapter_render import register as register_render
    register_render(s)
    q = s.add_parser('fonts', help='verify real F1/F2/F3 assets, hashes and glyph coverage')
    q.add_argument('--contract', default=FONT_CONTRACT); q.add_argument('--font', choices=tuple(FONT_NAMES)); q.add_argument('--title'); q.add_argument('--combination')
    q.set_defaults(handler=_fonts)
    q = s.add_parser('map'); q.add_argument('--file', required=True); q.set_defaults(handler=_map)
    q = s.add_parser('gate'); q.add_argument('--file', required=True)
    q.add_argument('--template', required=True); q.add_argument('--sound', required=True)
    q.set_defaults(handler=_gate)


def _fonts(args):
    return font_contract(args.root, args.contract, args.font, args.title, args.combination)


def _map(args):
    return timeline_plan(json.loads(Path(args.file).read_text()))


def _gate(args):
    return require_owner_choice(json.loads(Path(args.file).read_text()), args.template, args.sound, args.root)
