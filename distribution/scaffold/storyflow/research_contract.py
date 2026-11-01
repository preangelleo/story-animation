"""Frozen source/theme handoff; the writer receives data, never browsing tools."""
from pathlib import Path
import hashlib
import json

REQUIRED = ('dossier.md', 'themes.json', 'sources.json')

def material_bundle(directory):
    root = Path(directory).expanduser().resolve()
    files = {}
    for name in REQUIRED:
        path = root / name
        if path.is_symlink() or not path.is_file() or not path.read_text().strip():
            raise ValueError('research handoff missing/unsafe: ' + name)
        files[name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'text': path.read_text()}
    themes = json.loads(files['themes.json']['text'])
    if not isinstance(themes.get('themes'), list) or not 1 <= len(themes['themes']) <= 3 or not themes.get('takeaway'):
        raise ValueError('themes must contain 1–3 themes and an editorial takeaway')
    sources = json.loads(files['sources.json']['text'])
    entries = sources.get('sources', [])
    if not entries or any(not isinstance(e, dict) or not e.get('url', '').startswith(('https://','http://')) or not e.get('accessed_at') or not e.get('classification') for e in entries):
        raise ValueError('sources need URL, accessed_at and fact/claim classification')
    body = '\n\n'.join('=== ' + name + ' ===\n' + files[name]['text'] for name in REQUIRED)
    return {'directory': str(root), 'files': {k:v['sha256'] for k,v in files.items()}, 'text': body}

def writer_permissions():
    return {'*': 'deny'}

def register(subparsers):
    p=subparsers.add_parser('research',help='validate frozen researcher/theme handoff')
    actions=p.add_subparsers(dest='research_action',required=True)
    q=actions.add_parser('check');q.add_argument('--material-dir',required=True);q.set_defaults(handler=_check)

def _check(args):
    value=material_bundle(args.material_dir)
    return {'ok':True,**{k:v for k,v in value.items() if k!='text'},'writer_tools_allowed':[]}
