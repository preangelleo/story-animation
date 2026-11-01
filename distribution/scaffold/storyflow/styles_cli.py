"""Read-only registry and frozen-prompt CLI."""
import json
from pathlib import Path
from .core import Store,WorkflowError

def handle(a):
    from .style_registry import load_catalog,load_mapping,resolve_style
    if a.style_action=='resolve':
        return resolve_style(a.root,release_date=a.release_date,series=a.series,style_index=a.style_index,style_file=a.style_file,mapping_file=a.mapping_file,classification_evidence=a.classification_evidence)
    catalog=load_catalog(a.style_file,root=a.root)
    if a.style_action=='validate':
        mapping=load_mapping(a.mapping_file,root=a.root)
        # Resolve each mapping entry through the same strict consumer.
        for row in mapping['series']:
            resolve_style(a.root,series=row['key'],style_file=a.style_file,mapping_file=a.mapping_file)
        return {'valid':True,'catalog':catalog,'mapping':mapping}
    if a.style_action=='list':return {**catalog,'default_mapping':load_mapping(a.mapping_file,root=a.root)}
    for style in catalog['styles']:
        if str(style['index'])==str(a.style_index):return style
    raise WorkflowError('unknown style index: '+str(a.style_index)+'; valid indices: '+', '.join(s['index'] for s in catalog['styles']))

def register(sub):
    p=sub.add_parser('styles',help='Editable style registry; stable index is not an array position')
    actions=p.add_subparsers(dest='style_action',required=True)
    for name in ('list','show','validate','resolve'):
        q=actions.add_parser(name);q.set_defaults(handler=handle)
        q.add_argument('--style-file');q.add_argument('--mapping-file')
        if name=='show':q.add_argument('--style-index',required=True)
        if name=='resolve':
            q.add_argument('--style-index');q.add_argument('--series');q.add_argument('--classification-evidence');q.add_argument('--release-date',help='Tokyo release date or timezone-aware timestamp; required unless series is explicit')
