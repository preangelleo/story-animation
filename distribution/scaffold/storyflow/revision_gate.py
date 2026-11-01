"""Owner-specific revision gate, configured only for explicitly gated episodes."""
import json
from pathlib import Path
from .core import WorkflowError, config
from .chapters import require_owner_choice

def check_revision(root, episode):
    root=Path(root).resolve()
    entry=config(root).get('revision_approval_gates',{}).get(episode)
    if not entry:return
    def local(value):
        path=(root/value).resolve()
        if not path.is_relative_to(root):raise WorkflowError('approval asset outside project')
        return path
    gate=json.loads(local(entry).read_text())
    if gate.get('status')!='approved':raise WorkflowError('waiting_owner_font_choice: pending_font_choice',409)
    require_owner_choice(gate,local(gate['template_path']),local(gate['sound_path']), root)
