"""File-bound character/style/repetition contract; never generates or purchases media.

Run: python -m storyflow.visuals production/visual-manifest.json --root .
A passing report confirms evidence integrity, not autonomous aesthetic judgement.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import re
from urllib.parse import urlparse
import hashlib
import json
import math
from pathlib import Path
import subprocess

LEGACY_STYLE_ID = 'scene-classic-rubber-hose'
STYLE_ID = LEGACY_STYLE_ID  # compatibility for immutable legacy manifests
NEAR_DISTANCE = 6  # fixed 64-bit dHash threshold; cannot be relaxed in a manifest
REQUIRED_VIEWS = {'front', 'side', 'back', 'expression_neutral', 'expression_tense'}


def image_dhash(path: Path) -> str:
    """Real decoded-pixel 64-bit difference hash, independent of file encoding.

    ffmpeg is an existing rendering dependency. Failure blocks validation rather
    than trusting a caller-supplied hash. Not an identity/semantic similarity model.
    """
    result = subprocess.run([
        'ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-frames:v', '1',
        '-vf', 'scale=9:8:flags=area,format=gray', '-f', 'rawvideo', '-threads', '1', '-'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=True)
    if len(result.stdout) != 72:
        raise ValueError('image decoder returned unexpected pixel count')
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (result.stdout[row * 9 + col] > result.stdout[row * 9 + col + 1])
    return f'{bits:016x}'


def image_pixel_sha256(path: Path) -> str:
    """Catch losslessly re-encoded copies, not just identical file bytes."""
    result = subprocess.run([
        'ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-frames:v', '1',
        '-vf', 'format=rgb24', '-f', 'hash', '-hash', 'sha256', '-threads', '1', '-'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=True)
    value = result.stdout.decode().strip()
    if not value.startswith('SHA256=') or len(value) != 71:
        raise ValueError('image decoder returned invalid pixel digest')
    return value[7:]


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def video_changes(path):
    """Real decoded frames must change; a still-image MP4 is not generated action."""
    probe=subprocess.run(['ffprobe','-v','error','-show_format','-of','json',str(path)],capture_output=True,check=True,timeout=30)
    duration=float(json.loads(probe.stdout)['format']['duration'])
    hashes=[]
    for fraction in (0.1,0.5,0.9):
        result=subprocess.run(['ffmpeg','-v','error','-ss',str(duration*fraction),'-i',str(path),'-frames:v','1','-vf','scale=64:64,format=rgb24','-f','rawvideo','-threads','1','-'],capture_output=True,check=True,timeout=30)
        if len(result.stdout)!=64*64*3: raise ValueError('video sample missing')
        hashes.append(hashlib.sha256(result.stdout).hexdigest())
    return len(set(hashes))>1


def evidence_json(proof, root):
    rel=Path(proof['path']); path=(root/rel).resolve()
    if rel.is_absolute() or not path.is_relative_to(root) or path.is_symlink(): raise ValueError('unsafe evidence')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=proof['sha256']: raise ValueError('evidence hash')
    return json.loads(path.read_text())


def official_output_uri(value):
    if not isinstance(value,str): return False
    uri=urlparse(value); host=uri.hostname or ''
    return uri.scheme=='https' and not uri.username and not uri.password and uri.port in (None,443) and (host=='replicate.delivery' or host.endswith('.replicate.delivery')) and bool(uri.path.strip('/'))


def verify_video_download(generation, receipt, item, root):
    download=evidence_json(generation['download_receipt_file'],root)
    outputs=receipt['output'] if isinstance(receipt['output'],list) else [receipt['output']]
    if not all(official_output_uri(u) for u in outputs): raise ValueError('nonofficial prediction output')
    if download.get('prediction_id')!=generation['prediction_id'] or download.get('url') not in outputs: raise ValueError('prediction download mismatch')
    if not official_output_uri(download.get('final_url')) or download.get('http_status')!=200: raise ValueError('download transport')
    if download.get('sha256')!=item['sha256'] or download.get('bytes')!=Path(item['resolved']).stat().st_size: raise ValueError('download bytes mismatch')


def verify_video_references(generation, actual, root):
    # These are the model's only image-bearing inputs. Arbitrary keys (especially
    # prompt) can never serve as upload evidence.
    uris=list(actual.get('reference_images') or [])
    for key in ('image','last_frame_image'):
        if actual.get(key): uris.append(actual[key])
    if actual.get('reference_images') and (actual.get('image') or actual.get('last_frame_image')): raise ValueError('conflicting image modes')
    decoded=[]
    for uri in uris:
        if not isinstance(uri,str): raise ValueError('invalid image URI')
        if uri.startswith('data:image/'):
            header,payload=uri.split(',',1)
            if not header.endswith(';base64'): raise ValueError('reference must be base64 image bytes')
            decoded.append(hashlib.sha256(base64.b64decode(payload,validate=True)).hexdigest())
        else:
            # Hosted input must have a real content download receipt bound to its
            # URI. This is a recorded transport fact, not an arbitrary input key.
            proof=next((p for p in generation.get('reference_download_receipts',[]) if p.get('url')==uri),None)
            if proof is None: raise ValueError('hosted reference content proof missing')
            download=evidence_json(proof['receipt_file'],root)
            parsed=urlparse(uri)
            if parsed.scheme!='https' or parsed.username or parsed.password or not parsed.hostname: raise ValueError('reference URL')
            if download.get('url')!=uri or download.get('http_status')!=200: raise ValueError('reference transport')
            content=download['content_file']; rel=Path(content['path']); path=(root/rel).resolve()
            if rel.is_absolute() or not path.is_relative_to(root) or path.is_symlink(): raise ValueError('reference content path')
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if digest!=download.get('sha256') or digest!=content.get('sha256') or path.stat().st_size!=download.get('bytes'): raise ValueError('reference content mismatch')
            decoded.append(digest)
    expected=sorted(r['sha256'] for r in generation.get('reference_images',[]))
    if sorted(decoded)!=expected: raise ValueError('actual reference content differs from local references')


def verify_prediction_input(generation, sent, prediction, root):
    """Only the provider's observed literal ellipsis echo may use a POST trace.

    This proves consistency with a recorded local submission, not that the API
    independently echoed the original image bytes. Raw API files stay untouched.
    """
    def exact(a,b):
        return json.dumps(a,sort_keys=True,separators=(',',':'))==json.dumps(b,sort_keys=True,separators=(',',':'))
    echoed=prediction.get('input')
    if exact(sent,echoed):
        return None
    if generation.get('reference_echo')!='provider_redacted_reference_echo':
        raise ValueError('prediction input mismatch')
    if not isinstance(sent,dict) or not isinstance(echoed,dict) or set(sent)!=set(echoed):
        raise ValueError('prediction keys mismatch')
    fields=[]
    for key,value in sent.items():
        echo=echoed[key]
        if exact(value,echo): continue
        if key=='reference_images':
            if not isinstance(value,list) or not isinstance(echo,list) or len(value)!=len(echo):
                raise ValueError('reference count mismatch')
            pairs=list(enumerate(zip(value,echo)))
        elif key in ('image','last_frame_image'):
            pairs=[(0,(value,echo))]
        else:
            raise ValueError('nonimage prediction input mismatch')
        for index,(uri,returned) in pairs:
            if exact(uri,returned): continue
            if not isinstance(uri,str) or not isinstance(returned,str) or ',' not in uri:
                raise ValueError('invalid redacted reference')
            header,payload=uri.split(',',1)
            if not re.fullmatch(r'data:image/[a-zA-Z0-9.+-]+;base64',header) or returned!=header+',...':
                raise ValueError('not exact observed provider redaction')
            content=base64.b64decode(payload,validate=True)
            if not content: raise ValueError('empty submitted reference')
            fields.append({'input_key':key,'input_index':index,'sha256':hashlib.sha256(content).hexdigest()})
    if not fields: raise ValueError('no redacted fields')
    # Still decode every original URI and bind it to the real local references.
    verify_video_references(generation,sent,root)
    trace=evidence_json(generation['submission_receipt_file'],root)
    if trace.get('reference_echo')!='provider_redacted_reference_echo' or trace.get('redacted_reference_fields')!=fields:
        raise ValueError('redaction trace positions mismatch')
    if trace.get('method')!='POST' or trace.get('endpoint')!='https://api.replicate.com/v1/predictions' or trace.get('status')!='submitted':
        raise ValueError('not recorded provider POST')
    if trace.get('model')!=generation['model'] or not re.fullmatch(r'[0-9a-f]{64}',str(trace.get('version',''))):
        raise ValueError('submission model/version mismatch')
    if trace.get('request_input_file')!=generation['input_file']:
        raise ValueError('submission request hash mismatch')
    if evidence_json(trace['request_input_file'],root)!=sent:
        raise ValueError('submission request changed')
    operation=evidence_json(trace['pre_submission_operation_file'],root)
    inputs=operation['inputs']
    key=hashlib.sha256(json.dumps(inputs,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if operation.get('key')!=key or operation.get('status')!='running' or not _text(operation.get('kind')):
        raise ValueError('pre-submission operation invalid')
    if inputs.get('input_sha256')!=generation['input_file']['sha256'] or inputs.get('model')!=trace['model'] or inputs.get('version')!=trace['version']:
        raise ValueError('operation input/version mismatch')
    # This is a read-only export of the existing DB row, not a newly asserted
    # historical timestamp. Its mutable-local-evidence limit remains explicit.
    ledger=evidence_json(trace['pre_submission_ledger_file'],root)
    if not _text(trace.get('episode_id')) or ledger.get('episode_id')!=trace['episode_id'] or ledger.get('kind')!=operation['kind'] or ledger.get('inputs_hash')!=key:
        raise ValueError('operation ledger identity mismatch')
    if not exact(ledger.get('data',{}).get('inputs'),inputs) or ledger.get('created_at')!=trace.get('pre_submission_created_at'):
        raise ValueError('operation ledger inputs/time mismatch')
    if ledger.get('data',{}).get('prediction_id') not in (None,trace.get('prediction_id')):
        raise ValueError('operation ledger prediction mismatch')
    # Snapshot of the actual shared CLI is evidence, never a new CLI implementation.
    cli=trace['shared_cli_file']; rel=Path(cli['path']); path=(root/rel).resolve()
    if rel.is_absolute() or not path.is_relative_to(root) or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=cli['sha256']:
        raise ValueError('shared CLI snapshot mismatch')
    creation=evidence_json(trace['creation_file'],root)
    for obj in (creation,prediction):
        if obj.get('id')!=trace.get('prediction_id') or obj.get('created_at')!=trace.get('created_at') or obj.get('model')!=trace['model'] or not exact(obj.get('input'),echoed):
            raise ValueError('created prediction mismatch')
        if obj.get('version') not in (trace['version'],'hidden'):
            raise ValueError('provider version mismatch')
    if trace['prediction_id']!=generation['prediction_id'] or creation.get('status') not in ('starting','processing','succeeded'):
        raise ValueError('created prediction id/status mismatch')
    before=dt.datetime.fromisoformat(trace['pre_submission_created_at'].replace('Z','+00:00'))
    created=dt.datetime.fromisoformat(trace['created_at'].replace('Z','+00:00'))
    if before.tzinfo is None or created.tzinfo is None or before>created:
        raise ValueError('operation not recorded before creation')
    return {'kind':'provider_redacted_reference_echo','prediction_id':trace['prediction_id'],
            'fields':fields,'submission_receipt_file':generation['submission_receipt_file'],
            'limitation':'API echo omits image bytes; local submission trace and exported operation ledger bind request/time. The local DB/export is editable, not cryptographic proof of prior recording. Offline JSON cannot authenticate a wholly forged evidence set; independent API and visual review required.'}


def _validate_manifest(manifest, root) -> dict:
    """Validate JSON-compatible manifest; all paths relative to a trusted root.

    Character references may be a multi-view sheet or separate files. Every
    character/outfit requires all REQUIRED_VIEWS, exact hash-bound generation
    inputs, and an explicit visual inspection bound to output and references.
    """
    root = Path(root).resolve()
    errors, images, near_pairs = [], [], []
    evidence_limits=[]
    if not isinstance(manifest, dict):
        return {'passed': False, 'errors': ['manifest_not_object'], 'images': [], 'near_pairs': []}
    if manifest.get('schema_version') != 1:
        errors.append('schema_version_mismatch')
    from .core import WorkflowError
    snapshot=manifest.get('style_snapshot')
    expected_style=LEGACY_STYLE_ID
    if snapshot is not None:
        from .style_registry import validate_snapshot
        errors.extend('style_snapshot:'+e for e in validate_snapshot(snapshot))
        expected_style=snapshot.get('style_id') if isinstance(snapshot,dict) else None
        if not errors:
            from .style_binding import check_binding
            try:check_binding(manifest,snapshot,'manifest')
            except (ValueError,WorkflowError) as exc:errors.append(str(exc))
    if manifest.get('style_id') != expected_style:
        errors.append('style_mismatch')
    if manifest.get('depiction') != 'illustrated_reconstruction':
        errors.append('depiction_mismatch: generated historical scenes must not claim archival photography')
    chars, scenes = manifest.get('characters'), manifest.get('scenes')
    if not isinstance(chars, list) or not isinstance(scenes, list) or not scenes:
        return {'passed': False, 'errors': errors + ['characters_or_scenes_invalid'], 'images': [], 'near_pairs': []}
    cache = {}
    origin_cache = {}  # one top-level validation only; source bytes rechecked per scene

    def asset(value, label):
        if not isinstance(value, dict) or not _text(value.get('image')):
            errors.append(label + ':image_missing')
            return None
        rel = Path(value['image'])
        path = (root / rel).resolve()
        if rel.is_absolute() or not path.is_relative_to(root):
            errors.append(label + ':unsafe_path')
            return None
        try:
            if path not in cache:
                cache[path] = (hashlib.sha256(path.read_bytes()).hexdigest(), image_dhash(path), image_pixel_sha256(path))
            digest, perceptual, pixel_digest = cache[path]
        except (OSError, ValueError, subprocess.SubprocessError):
            errors.append(label + ':image_unreadable')
            return None
        if value.get('sha256') != digest:
            errors.append(label + ':sha256_mismatch')
        return {'image': str(rel), 'resolved': str(path), 'sha256': digest, 'dhash': perceptual, 'pixel_sha256': pixel_digest}

    character_map = {}
    for char in chars:
        if not isinstance(char, dict) or not _text(char.get('id')):
            errors.append('character_invalid')
            continue
        cid = char['id']
        if snapshot is not None:
            from .style_binding import check_binding
            try:check_binding(char,snapshot,'character')
            except (ValueError,WorkflowError) as exc:errors.append(str(exc))
        if cid in character_map:
            errors.append(cid + ':duplicate_character_id')
        lock = char.get('lock', {})
        if not isinstance(lock, dict) or not all(_text(lock.get(k)) for k in ('age', 'face', 'hair', 'skin', 'build')):
            errors.append(cid + ':character_lock_incomplete')
        outfits, references = char.get('outfits', []), char.get('references', [])
        if not isinstance(outfits, list) or not outfits or not isinstance(references, list):
            errors.append(cid + ':outfits_or_references_missing')
            continue
        outfit_map = {}
        for outfit in outfits:
            if not isinstance(outfit, dict) or not _text(outfit.get('id')) or not _text(outfit.get('description')):
                errors.append(cid + ':outfit_invalid')
                continue
            oid = outfit['id']
            if oid in outfit_map:
                errors.append(cid + ':duplicate_outfit_id')
            sheet_refs, views = {}, set()
            for ref in references:
                if not isinstance(ref, dict):
                    errors.append(cid + ':reference_invalid')
                    continue
                if ref.get('outfit_id') != oid:
                    continue
                if snapshot is not None:
                    try:
                        check_binding(ref,snapshot,'character reference')
                        check_binding(ref.get('generation'),snapshot,'character generation',prompt=True)
                    except (ValueError,WorkflowError) as exc:errors.append(str(exc))
                item = asset(ref, cid + '/' + oid)
                if item:
                    sheet_refs[item['resolved']] = item['sha256']
                ref_views = ref.get('views', [])
                if isinstance(ref_views, list) and all(isinstance(v, str) for v in ref_views):
                    views.update(ref_views)
            if not REQUIRED_VIEWS.issubset(views):
                errors.append(cid + '/' + oid + ':missing_sheet_views')
            if not sheet_refs:
                errors.append(cid + '/' + oid + ':references_missing')
            outfit_map[oid] = sheet_refs
        character_map[cid] = outfit_map

    by_id, by_sha = {}, {}
    for scene in scenes:
        if not isinstance(scene, dict) or not _text(scene.get('id')):
            errors.append('scene_invalid')
            continue
        sid = scene['id']
        origin=scene.get('asset_origin')
        if origin is not None:
            scope=manifest.get('production_scope',{})
            if not isinstance(scope,dict) or scope.get('kind')!='owner_approved_narration_test' or not _text(scope.get('authorization')):
                errors.append(sid+':asset_origin_requires_explicit_test_scope'); continue
            if not isinstance(origin,dict) or not _text(origin.get('reason')):
                errors.append(sid+':asset_origin_incomplete'); continue
            rel=Path(origin.get('manifest','')); source=(root/rel).resolve()
            try:
                if rel.is_absolute() or not source.is_relative_to(root) or source.is_symlink(): raise ValueError('unsafe')
                raw=source.read_bytes(); source_sha=hashlib.sha256(raw).hexdigest()
                if source_sha!=origin.get('manifest_sha256'): raise ValueError('hash')
                cache_key=(source,source_sha)
                if cache_key not in origin_cache:
                    old=json.loads(raw)
                    if snapshot is not None and old.get('style_snapshot')!=snapshot:raise ValueError('origin frozen style differs')
                    # Provenance chains are forbidden: point at original generation.
                    if any('asset_origin' in row for row in old.get('scenes',[])): raise ValueError('chain')
                    result=_validate_manifest(old,root)
                    rows={row['id']:row for row in old['scenes']}
                    origin_cache[cache_key]=(result,rows)
                result,rows=origin_cache[cache_key]
                original=rows[origin.get('scene_id')]
                if not result['passed'] or original.get('reuse_of') or original.get('image')!=scene.get('image') or original.get('sha256')!=scene.get('sha256'): raise ValueError('source')
                if original.get('visual_review')==scene.get('visual_review'): raise ValueError('new review required')
                if 'generation' in scene: raise ValueError('must retain original generation only through origin')
                scene={**original,**scene,'generation':original['generation']}
            except (OSError,ValueError,KeyError,StopIteration,TypeError):
                errors.append(sid+':asset_origin_invalid'); continue
        if sid in by_id:
            errors.append(sid + ':duplicate_scene_id')
        if scene.get('style_id') != expected_style:
            errors.append(sid + ':style_mismatch')
        if snapshot is not None:
            from .style_binding import check_binding
            try:check_binding(scene,snapshot,'scene')
            except (ValueError,WorkflowError) as exc:errors.append(str(exc))
        item = asset(scene, sid)
        if not item:
            continue
        item['scene_id'] = sid
        from .scene_cleanup import normalize_cleanup
        from .core import WorkflowError
        cleanup=None
        try: cleanup=normalize_cleanup(scene,item['sha256'])
        except WorkflowError: errors.append(sid+':caption_band_cleanup_invalid')
        reuse = scene.get('reuse_of')
        if cleanup and reuse is not None: errors.append(sid+':cleanup_reuse_not_supported')
        if reuse is not None:
            original = by_id.get(reuse) if isinstance(reuse, str) else None
            if not original or original.get('reuse_of'):
                errors.append(sid + ':reuse_target_must_be_prior_original_scene')
            elif original['sha256'] != item['sha256'] or original['resolved'] != item['resolved']:
                errors.append(sid + ':reuse_must_reference_original_file')
            if not _text(scene.get('intent_reason')):
                errors.append(sid + ':reuse_reason_missing')
        else:
            if item['sha256'] in by_sha:
                errors.append(sid + ':duplicate_without_reuse:' + by_sha[item['sha256']])
            elif item['pixel_sha256'] in {r['pixel_sha256'] for r in by_id.values()}:
                errors.append(sid + ':duplicate_pixels_without_reuse')
            elif item['resolved'] in {r['resolved'] for r in by_id.values()}:
                errors.append(sid + ':duplicate_without_reuse')
            cast = scene.get('characters')
            expected_refs = set()
            expected_lock_texts = set()
            if not isinstance(cast, list):
                errors.append(sid + ':characters_missing: use explicit empty list for object/location shots')
                cast = []
            for actor in cast:
                if not isinstance(actor, dict):
                    errors.append(sid + ':character_invalid')
                    continue
                cid, oid = actor.get('character_id'), actor.get('outfit_id')
                sheets = character_map.get(cid, {}).get(oid) if isinstance(cid, str) and isinstance(oid, str) else None
                if not sheets:
                    errors.append(sid + ':unknown_character_outfit')
                    continue
                source_character = next(c for c in chars if isinstance(c, dict) and c.get('id') == cid)
                expected_lock_texts.update(source_character['lock'].values())
                expected_lock_texts.update(o['description'] for o in source_character['outfits'] if o['id'] == oid)
                refs = actor.get('reference_images')
                if not isinstance(refs, list) or not refs:
                    errors.append(sid + ':character_reference_missing')
                    continue
                for ref in refs:
                    checked = asset(ref, sid + '/character_reference')
                    if checked:
                        if sheets.get(checked['resolved']) != checked['sha256']:
                            errors.append(sid + ':reference_not_in_character_bible')
                        expected_refs.add((checked['resolved'], checked['sha256']))
            generation = scene.get('generation')
            if not isinstance(generation, dict):
                errors.append(sid + ':generation_missing')
            else:
                video=scene.get('media_type')=='video'
                if video:
                    try:
                        if generation.get('tool')!='replicate' or generation.get('model')!='bytedance/seedance-2.5' or not _text(generation.get('prediction_id')): raise ValueError('provider')
                        inp=generation['input_file']; rel=Path(inp['path']); path=(root/rel).resolve()
                        if rel.is_absolute() or not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest()!=inp['sha256']: raise ValueError('input')
                        response=generation['prediction_file']; rr=Path(response['path']); rp=(root/rr).resolve()
                        if rr.is_absolute() or not rp.is_relative_to(root) or hashlib.sha256(rp.read_bytes()).hexdigest()!=response['sha256']: raise ValueError('response')
                        receipt=json.loads(rp.read_text())
                        if receipt.get('id')!=generation['prediction_id'] or receipt.get('status')!='succeeded' or not receipt.get('output') or receipt.get('model')!=generation['model']: raise ValueError('prediction')
                        actual=json.loads(path.read_text())
                        if actual.get('prompt')!=generation.get('prompt') or actual.get('duration')!=10: raise ValueError('input mismatch')
                        limitation=verify_prediction_input(generation,actual,receipt,root)
                        if limitation: evidence_limits.append({'scene_id':sid,**limitation})
                        verify_video_download(generation,receipt,item,root)
                        verify_video_references(generation,actual,root)
                        if not video_changes(Path(item['resolved'])): raise ValueError('static video')
                    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError): errors.append(sid+':video_generation_evidence_invalid')
                if (not video and (generation.get('tool') != 'builtin.image_gen' or not _text(generation.get('call_id')))) or not _text(generation.get('prompt')):
                    errors.append(sid + ':generation_contract_incomplete')
                if any(text not in str(generation.get('prompt', '')) for text in expected_lock_texts):
                    errors.append(sid + ':frozen_character_lock_missing_from_prompt')
                if generation.get('output_sha256') != item['sha256']:
                    errors.append(sid + ':generation_output_mismatch')
                passed_refs = set()
                references = generation.get('reference_images', [])
                if not isinstance(references, list):
                    errors.append(sid + ':generation_references_invalid')
                    references = []
                for ref in references:
                    checked = asset(ref, sid + '/generation_reference')
                    if checked:
                        passed_refs.add((checked['resolved'], checked['sha256']))
                if not expected_refs.issubset(passed_refs):
                    errors.append(sid + ':reference_not_passed_to_generation')
            if snapshot is not None:
                from .style_binding import check_binding
                try:
                    check_binding(scene,snapshot,'scene')
                    check_binding(generation,snapshot,'scene generation',prompt=True)
                except (ValueError,WorkflowError) as exc:errors.append(str(exc))
            if isinstance(generation,dict):
                from .visual_prompt import check_prompt
                historical=scene.get('historical_source_record')
                historical_ok=False
                if historical is not None:
                    try:
                        if scene.get('media_type')=='video' or generation.get('tool')!='builtin.image_gen':raise ValueError('historical reuse limited to builtin still images')
                        scope=manifest.get('production_scope',{})
                        if scope.get('kind')!='owner_approved_reset_reuse' or not _text(scope.get('authorization')):raise ValueError('scope')
                        recorded=evidence_json(historical,root)
                        if any(recorded.get(k)!=scene.get(k) for k in ('image','sha256','generation')):raise ValueError('record drift')
                        if not _text(scene.get('narrative')):raise ValueError('narrative required')
                        # The current plan is always clean; only immutable old input is historical.
                        check_prompt(scene.get('current_prompt',''))
                        if not _text(scene.get('current_prompt')):raise ValueError('current prompt required')
                        historical_ok=True
                    except (OSError,ValueError,KeyError,TypeError,WorkflowError):errors.append(sid+':historical_source_record_invalid')
                if not historical_ok:
                    try:check_prompt(generation.get('prompt',''))
                    except WorkflowError:errors.append(sid+':retired_subtitle_reserve_prompt')
            review = scene.get('visual_review')
            if not isinstance(review, dict) or review.get('result') not in (('pass','revise') if cleanup else ('pass',)) or not _text(review.get('reviewer')) or not _text(review.get('notes')):
                errors.append(sid + ':visual_review_missing')
            elif review.get('image_sha256') != item['sha256'] or sorted(review.get('reference_sha256s', []) or []) != sorted({r[1] for r in expected_refs}):
                errors.append(sid + ':visual_review_stale')
            if cleanup:
                delivery=scene.get('delivery_visual_review')
                try:
                    if not isinstance(delivery,dict) or delivery.get('result')!='pass' or not _text(delivery.get('reviewer')) or not _text(delivery.get('notes')): raise ValueError('review')
                    if delivery.get('source_sha256')!=item['sha256'] or delivery.get('caption_band_cleanup')!=cleanup: raise ValueError('binding')
                    proof=delivery['preview_file'];rel=Path(proof['path']);preview=(root/rel).resolve()
                    if rel.is_absolute() or not preview.is_relative_to(root) or (root/rel).is_symlink() or not preview.is_file() or preview.suffix.lower()!='.mp4' or str(preview)==item['resolved'] or hashlib.sha256(preview.read_bytes()).hexdigest()!=proof['sha256']: raise ValueError('preview')
                    if not video_changes(preview): raise ValueError('preview must be actual decoded moving video')
                except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError): errors.append(sid+':cleanup_delivery_visual_review_invalid')
        review=scene.get('visual_review',{})
        if reuse is not None and (not isinstance(review,dict) or review.get('result')!='pass' or not _text(review.get('reviewer')) or not _text(review.get('notes')) or review.get('image_sha256')!=item['sha256']):
            errors.append(sid+':reuse_visual_review_missing_or_stale')
        composition=review.get('composition',{}) if isinstance(review,dict) else {}
        if not isinstance(composition,dict) or composition.get('full_frame') is not True or composition.get('subtitle_reserve') is not False or not _text(composition.get('bottom_edge_notes')):
            errors.append(sid+':full_frame_composition_review_missing')
        item['reuse_of'] = reuse
        by_id[sid] = item
        by_sha.setdefault(item['sha256'], sid)
        images.append(item)

    reviews = manifest.get('similarity_reviews', [])
    if not isinstance(reviews, list):
        reviews = []
        errors.append('similarity_reviews_invalid')
    originals = [i for i in images if i['reuse_of'] is None]
    for index, left in enumerate(originals):
        for right in originals[index + 1:]:
            if left['sha256'] == right['sha256']:
                continue  # already blocked as undeclared exact reuse
            distance = (int(left['dhash'], 16) ^ int(right['dhash'], 16)).bit_count()
            if distance > NEAR_DISTANCE:
                continue
            pair = [left['scene_id'], right['scene_id']]
            hashes = [left['sha256'], right['sha256']]
            accepted = any(isinstance(r, dict) and r.get('scenes') == pair and r.get('sha256s') == hashes
                and r.get('decision') == 'distinct_composition' and _text(r.get('reviewer')) and _text(r.get('notes')) for r in reviews)
            near_pairs.append({'scenes': pair, 'sha256s': hashes, 'distance': distance, 'reviewed': accepted})
            if not accepted:
                errors.append('/'.join(pair) + ':near_duplicate_unreviewed')
    return {'passed': not errors, 'errors': errors, 'images': images, 'near_pairs': near_pairs,
            'unique_images': len(by_sha), 'scene_count': len(scenes), 'evidence_limits':evidence_limits,
            'scope': 'file/input/review evidence integrity; perceptual candidates require real visual review'}


def validate_manifest(manifest, root) -> dict:
    """Fail closed on malformed external JSON shapes as well as failed gates."""
    try:
        return _validate_manifest(manifest, root)
    except (TypeError, KeyError, AttributeError):
        return {'passed': False, 'errors': ['contract_shape_invalid'], 'images': [], 'near_pairs': []}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        result = validate_manifest(json.loads(args.manifest.read_text()), args.root)
    except (OSError, ValueError) as exc:
        result = {'passed': False, 'errors': [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
