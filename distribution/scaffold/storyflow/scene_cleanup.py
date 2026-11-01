"""Explicit, source-bound video caption-band cleanup; no arbitrary masking."""
from pathlib import Path
from .core import WorkflowError

KEY='caption_band_cleanup'

def normalize_cleanup(scene,source_sha256):
    if KEY not in scene: return None
    raise WorkflowError('caption_band_cleanup 已退役；禁止整幅底带遮盖，须精确修复并重新独审或重生成必要片段')

def scene_identity(scene,root):
    result={'id':scene['id'],'image':str((Path(root)/scene['image']).resolve()),'sha256':scene['sha256']}
    if scene.get('media_type')=='video':result['media_type']='video'
    cleanup=normalize_cleanup(scene,scene['sha256'])
    if cleanup:result[KEY]=cleanup
    return result
