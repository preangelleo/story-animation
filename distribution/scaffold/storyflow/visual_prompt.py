"""Project-owned style-only composition contract. No supplier calls."""
import argparse,json,re
from pathlib import Path
from .core import WorkflowError
LEGACY_STYLE = 'Original adult narrative rubber-hose 2D cartoon, thick flowing ink lines, muted flat colors #f0eee6 #2c2b26 #d97757 #dbb293, hand-drawn environment, simplified shadows.'
STYLE = LEGACY_STYLE  # compatibility import for frozen legacy consumers
FRAME = '16:9 complete narrative frame. Environment continues naturally to all four edges including the lowest edge. No subtitle reserve, blank caption strip, color band, generated text or out-of-frame border. Subtitles are a separate postproduction overlay.'
def check_prompt(text):
    # Negation applies only to its own comma/semicolon-delimited instruction.
    for clause in re.split(r'[。；;.!\n，,]|\b(?:and|but|also)\b|但是|但|并且|而且|同时|且|并',str(text),flags=re.I):
        if re.match(r'\s*(no\b|without\b|never\b|do not\b|禁止|不要|不得|不留|无|取消)',clause,re.I): continue
        if re.search(r'(预留|留出|留底|保留).*(字幕|底部|下方|留白|空白)|(底部|下方).*(字幕空间|空白|留白)|(reserve|leave|keep).*(subtitle|caption)|(bottom|lower).*(18\s*(%|percent)|eighteen|blank|empty|clear).*(subtitle|caption|empty|blank|space)',clause,re.I):
            raise WorkflowError('positive subtitle reserve instruction is retired')
    return text

def compose_prompt(brief, *, snapshot=None, root=None, style_index=None, style_file=None, mapping_file=None, release_date=None, series=None):
    check_prompt(brief)
    if snapshot is None and any(x is not None for x in (root,style_index,style_file,mapping_file,release_date,series)):
        from .style_registry import resolve_style
        snapshot=resolve_style(root,release_date=release_date,series=series,style_index=style_index,style_file=style_file,mapping_file=mapping_file)
    if snapshot is not None:
        from .style_registry import validate_snapshot
        errors=validate_snapshot(snapshot)
        if errors:raise WorkflowError('invalid style snapshot: '+'; '.join(errors))
    style=snapshot['prompt'] if snapshot is not None else LEGACY_STYLE
    check_prompt(style)
    return style+' '+str(brief).strip()+' '+FRAME

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--brief',type=Path,required=True)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--style-index');p.add_argument('--style-file');p.add_argument('--mapping-file');p.add_argument('--release-date');p.add_argument('--series');p.add_argument('--episode')
    a=p.parse_args(argv)
    try:
        if a.episode:
            if any((a.style_index,a.style_file,a.mapping_file,a.release_date,a.series)):raise WorkflowError('episode uses frozen snapshot; overrides only at episode create')
            from .core import Store
            from .style_binding import snapshot_for_episode
            st=Store(a.root)
            try:snapshot=snapshot_for_episode(st,a.episode)
            finally:st.close()
        else:
            from .style_registry import resolve_style
            snapshot=resolve_style(a.root,release_date=a.release_date,series=a.series,style_index=a.style_index,style_file=a.style_file,mapping_file=a.mapping_file)
        out={'prompt':compose_prompt(a.brief.read_text(),snapshot=snapshot),'aspect_ratio':'16:9'}
        if snapshot:out.update(style_id=snapshot['style_id'],style_index=snapshot['style_index'],style_snapshot_sha256=snapshot['snapshot_sha256'],style_snapshot=snapshot)
        print(json.dumps(out,ensure_ascii=False));return 0
    except (WorkflowError,ValueError,OSError) as exc:p.error(str(exc))
if __name__=='__main__':raise SystemExit(main())
