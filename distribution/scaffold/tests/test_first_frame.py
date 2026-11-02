"""Actual local media tests: no provider calls and no production episodes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from storyflow import first_frame as f
from storyflow.core import Store, WorkflowError, atomic_json,digest
from storyflow.cli import parser,run

class FirstFrame(unittest.TestCase):
 def test_prepare_consume_exact_remaining_frames_audio_and_caption_zero_window(self):
  with tempfile.TemporaryDirectory() as tmp:
   r=Path(tmp);s=Store(r);ep=s.create('2030-11-02','synthetic','test')['id']
   try:
    atomic_json(r/'documentation/configuration.json',{'render':{'width':320,'height':180}});s.config['render']={'width':320,'height':180}
    for k in ('script','structure'):
     p=r/(k+'.txt');p.write_text('synthetic');s.register(ep,k,p)
    def ff(*args):return subprocess.check_output(['ffmpeg','-nostdin','-v','error',*map(str,args)])
    image=r/'thumbnail.png';ff('-f','lavfi','-i','color=orange:s=320x180','-frames:v','1','-threads','1',image)
    source=r/'source.mp4';ff('-f','lavfi','-i','testsrc2=s=320x180:r=30:d=2','-f','lavfi','-i','sine=frequency=447:sample_rate=48000:duration=2','-c:v','libx264','-crf','0','-bf','0','-c:a','aac',source)
    reference=r/'reference.txt';reference.write_text('synthetic reference')
    review=r/'review.json'
    payload={'episode_id':ep,'image_sha256':digest(image),'status':'pass','reviewer':'independent synthetic reviewer','reason':'synthetic fixture only','references_reviewed':True,'references':[f.file_record(reference)],'context':f.review_context(s,ep)}
    atomic_json(review,payload)
    self.assertRaises(WorkflowError,f.consume,s,ep)
    args=['--root',str(r),'thumbnail','prepare',ep,'--image',str(image),'--review',str(review)]
    plan=run(parser().parse_args(args));self.assertFalse(Path(plan['receipt_path']).exists())
    run(parser().parse_args(args+['--execute']));contract=f.consume(s,ep)
    before=Path(plan['receipt_path']).read_bytes();run(parser().parse_args(args+['--execute']));self.assertEqual(before,Path(plan['receipt_path']).read_bytes())
    out=r/'cover.mp4';receipt=f.replace(source,out,contract);atomic_json(r/'replacement.json',receipt);f.verify_replacement(receipt,contract)
    self.assertEqual(f.frames(source)[1:],f.frames(out)[1:]);self.assertEqual(len(f.frames(out)),60)
    def audio(path):return ff('-i',path,'-map','0:a:0','-c:a','copy','-f','adts','-')
    self.assertEqual(audio(source),audio(out))
    sub=r/'sub.srt';sub.write_text('1\n00:00:00,000 --> 00:00:02,000\n测试字幕\n')
    target=r/'final.mp4';work=r/'burn'
    tool=Path(__file__).resolve().parents[1]/'tools/burn_subtitles.py'
    subprocess.run(['python',str(tool),'--video',str(out),'--srt',str(sub),'--output',str(target),'--work',str(work),'--first-frame-cover','--illustrated-reconstruction','--execute'],check=True,capture_output=True)
    proof=f.final_pixels(target,contract);atomic_json(r/'final-frame-zero.json',proof)
    normal=r/'normal.mp4'
    subprocess.run(['python',str(tool),'--video',str(out),'--srt',str(sub),'--output',str(normal),'--work',str(r/'normal-burn'),'--illustrated-reconstruction','--execute'],check=True,capture_output=True)
    # Frame one must render the original t=1/30 cue, not shift it to t=2/30.
    def raw(path,n):return ff('-i',path,'-vf',f"select=eq(n\\,{n})",'-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-')
    clean,burned=raw(out,1),raw(target,1)
    ordinary=raw(normal,1)
    # Same t=1/30 cue, same libass placement; encoding noise has small mean error.
    self.assertLess(sum(abs(a-b) for a,b in zip(ordinary,burned))/len(burned),3)
    self.assertGreater(sum(abs(a-b) for a,b in zip(raw(normal,0),raw(target,0))),10000)
    # Caption background/text region differs substantially from no-caption clean.
    self.assertGreater(sum(abs(a-b) for a,b in zip(clean[-320*60*3:],burned[-320*60*3:])),10000)
    ff('-i',target,'-frames:v','1',r/'frame-zero.png')
    ff('-i',target,'-vf','select=eq(n\\,1)','-frames:v','1',r/'frame-one.png')
    original=reference.read_bytes();reference.write_text('changed');self.assertRaises(WorkflowError,f.consume,s,ep);reference.write_bytes(original)
    old=payload['context'];payload['context']={};atomic_json(review,payload);self.assertRaises(WorkflowError,f.prepare,s,ep,image,review);payload['context']=old;atomic_json(review,payload)
    (r/'script.txt').write_text('new script');s.register(ep,'script',r/'script.txt');self.assertRaises(WorkflowError,f.consume,s,ep)
    atomic_json(r/'summary.json',{'budget_usd':0,'network_calls':0,'replacement':receipt,'final_frame_zero':proof,'audio_packet_sha256':__import__('hashlib').sha256(audio(out)).hexdigest(),'srt_sha256':digest(sub),'caption_offset_seconds':0,'frame_one_original_cue_active':True})
    if os.environ.get('QY_THUMBNAIL_EVIDENCE'):
     dest=Path(os.environ['QY_THUMBNAIL_EVIDENCE'])/'first-frame'
     if not dest.exists():shutil.copytree(r,dest)
   finally:s.close()

 def test_policy_canvas_review_and_reference_negative_controls(self):
  from tests.test_integrations import IntegrationTests
  fx=IntegrationTests('test_voice_plan_never_executes');fx.setUp()
  try:
   fx.prepare_thumbnail();s=fx.store;ep=fx.ep;r=fx.root
   image=r/'thumbnail.png';review=r/'thumbnail-review.json';old=review.read_bytes()
   for mutation in ({'reviewer':s.episode(ep)['author']},{'image_sha256':'0'*64},{'references_reviewed':False},{'references':[{'path':str(image),'sha256':'0'*64}]},{'context':{}},{'status':'revise'}):
    v=json.loads(old);v.update(mutation);atomic_json(review,v)
    with self.subTest(mutation=mutation):self.assertRaises(WorkflowError,f.prepare,s,ep,image,review)
   review.write_bytes(old)
   for value in ({'enabled':'true'},{'frame_count':2},{'frame_count':True},{'fit':'crop'}):
    s.config['render']['first_frame']=value
    with self.subTest(policy=value):self.assertRaises(WorkflowError,f.policy,s)
   s.config['render'].pop('first_frame');s.config['render']['width']=66
   self.assertRaises(WorkflowError,f.prepare,s,ep,image,review)
  finally:fx.tearDown()
