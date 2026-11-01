"""Frozen episode style consumption, independent of mutable catalogs."""
from .core import WorkflowError

LEGACY_STYLE_ID = 'scene-classic-rubber-hose'

def snapshot_for_episode(store, episode):
    value=store.episode(episode)
    snapshot=value.get('style_snapshot')
    if snapshot is not None:
        from .style_registry import validate_snapshot
        errors=validate_snapshot(snapshot)
        if errors: raise WorkflowError('invalid frozen style: '+'; '.join(errors))
    elif value.get('style_resolution_required'):
        raise WorkflowError('episode frozen style missing')
    return snapshot

def check_binding(value, snapshot, label='visual', *, prompt=False):
    """Check the exact freeze, never re-resolve against an edited registry."""
    if not isinstance(value,dict): raise WorkflowError(label+': invalid style binding')
    if snapshot is None:
        if value.get('style_id',LEGACY_STYLE_ID)!=LEGACY_STYLE_ID:
            raise WorkflowError(label+': legacy style mismatch')
        return
    if value.get('style_id')!=snapshot['style_id'] or value.get('style_snapshot_sha256')!=snapshot['snapshot_sha256']:
        raise WorkflowError(label+': frozen style mismatch')
    if prompt and snapshot['prompt'] not in value.get('prompt',''):
        raise WorkflowError(label+': frozen style missing from actual generation prompt')

def check_plan(plan, snapshot):
    check_binding(plan,snapshot,'visual plan')
    if snapshot is None:return
    if plan.get('style_snapshot')!=snapshot:raise WorkflowError('visual plan snapshot differs from episode')
    for char in plan.get('characters',[]):
        check_binding(char,snapshot,'character')
    for scene in plan.get('scenes',[]):
        check_binding(scene,snapshot,'scene')
        if snapshot['prompt'] not in scene.get('prompt',''):raise WorkflowError('scene prompt omits frozen style')
