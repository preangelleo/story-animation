import unittest
from storyflow.chapters import timeline_plan
from storyflow.core import WorkflowError

class BoundaryRegression(unittest.TestCase):
    def payload(self,at):
        return {'source_duration':40,'insertions':[{'source_time':at,'duration':1}], 'streams':{'shots':[{'id':'s11','start':0,'end':19.32},{'id':'s12','start':19.32,'end':40}]}}
    def test_original_interior_scene_must_fail(self):
        with self.assertRaises(WorkflowError):timeline_plan(self.payload(19.22))
    def test_boundary_positive_control(self):
        self.assertEqual(len(timeline_plan(self.payload(19.32))['streams']['shots']),2)

class TypedAssemblyRegression(unittest.TestCase):
    def test_real_whole_clips_and_reject_same_duration_interior_flash(self):
        import tempfile,subprocess,copy
        from pathlib import Path
        from storyflow.core import atomic_json,digest
        from storyflow import scene_assembly as a
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            def ff(argv):subprocess.run(['ffmpeg','-v','error','-nostdin',*map(str,argv)],check=True,capture_output=True)
            clips=[]
            for name,color in [('A','red'),('B','green'),('card','black')]:
                p=root/(name+'.mp4');ff(['-f','lavfi','-i',f'color=c={color}:s=64x64:r=30:d=1','-c:v','libx264','-bf','0','-threads','1',p]);clips.append(p)
            table=root/'scenes.json';atomic_json(table,{'scenes':[{'id':name,'image':str(p),'duration':1} for name,p in zip(['A','B'],clips[:2])]})
            manifest=root/'clips.json';rows=[{'id':name,'source_start':i,'source_end':i+1,'start_frame':i*30,'end_frame':(i+1)*30,'frame_count':30,'source_path':str(p),'source_sha256':digest(p),'clip_path':str(p),'clip_sha256':digest(p)} for i,(name,p) in enumerate(zip(['A','B'],clips[:2]))]
            atomic_json(manifest,{'schema':'qy.scene.clips.v1','fps':30,'project_root':str(root),'scene_manifest':{'path':str(table),'sha256':digest(table)},'scenes':rows})
            record={'path':str(manifest),'sha256':digest(manifest)};insertions=[{'source_time':1,'duration':1,'clip':str(clips[2]),'previous_scene_id':'A','next_scene_id':'B'}]
            edl=a.playlist(record,insertions,2)
            self.assertEqual([n['type'] for n in edl['nodes']],['scene','transition','scene'])
            for mutation in ('ids','frames','order','split'):
                bad=copy.deepcopy(edl)
                if mutation=='ids':bad['nodes'][1]['next_scene_id']='A'
                elif mutation=='frames':bad['nodes'][2]['output_start_frame']-=1
                elif mutation=='order':bad['nodes'][0],bad['nodes'][2]=bad['nodes'][2],bad['nodes'][0]
                else:
                    # Same total frames, but A resumes after the title card.
                    tail=copy.deepcopy(bad['nodes'][0]);bad['nodes'][0]['frame_count']=15;tail['frame_count']=15;bad['nodes'].insert(2,tail)
                with self.subTest(mutation=mutation),self.assertRaises(WorkflowError):a.validate_playlist(bad,record,insertions,2)
            for key,val in [('source_time',.9),('next_scene_id','A')]:
                bad=copy.deepcopy(insertions);bad[0][key]=val
                with self.assertRaises(WorkflowError):a.playlist(record,bad,2)
            def concat(paths,out):
                listing=root/'list.txt';listing.write_text(''.join(f"file '{p}'\n" for p in paths));ff(['-f','concat','-safe','0','-i',listing,'-c','copy',out])
            ordered=[clips[0],clips[2],clips[1]];good=root/'good.mp4';concat(ordered,good)
            self.assertEqual(a.prove_frames(ordered,good,edl)['frame_count'],90)
            front=root/'front.mp4';tail=root/'tail.mp4'
            for p,st,en in [(front,0,15),(tail,15,30)]:ff(['-i',clips[0],'-vf',f'trim=start_frame={st}:end_frame={en},setpts=PTS-STARTPTS','-c:v','libx264','-bf','0','-threads','1',p])
            wrong=root/'wrong.mp4';concat([front,clips[2],tail,clips[1]],wrong)
            self.assertEqual(len(a.framemd5(wrong)),90)
            with self.assertRaises(WorkflowError):a.prove_frames(ordered,wrong,edl)
            table.write_text('{}')
            with self.assertRaises(WorkflowError):a.playlist(record,insertions,2)
    def test_scene_replan_preserves_old_indexes_and_consumes_review_sha(self):
        from tests import test_integrations as fixtures
        from storyflow import scene_assembly as a,integrations as it
        from storyflow.core import atomic_json,digest,read_json
        fx=fixtures.IntegrationTests('test_voice_plan_never_executes');fx.setUp()
        try:
            store,ep=fx.store,fx.ep;work=it.folder(store,ep)
            audio=store.register(ep,'audio',fx.file('old-audio.wav','offline source'))
            table=work/'scenes.json';visual=work/'visual-manifest.json'
            atomic_json(table,{'scenes':[{'id':'A','start':0,'end':1.1,'duration':1.1,'image':'A.png'},{'id':'B','start':1.1,'end':2,'duration':.9,'image':'B.png'}]});atomic_json(visual,{'scenes':['untouched source identity']})
            evidence=fx.file('semantic-review.json',{'status':'pass','reason':'synthetic acoustic/semantic fixture'})
            proof={'scenes_sha256':digest(table),'visual_manifest_sha256':digest(visual),'source_audio_sha256':audio['sha256'],'boundaries':[{'previous_scene_id':'A','next_scene_id':'B','source_time':1}],'review':{'status':'pass','reviewer':'independent','reason':'synthetic only','evidence':[{'path':str(evidence),'sha256':digest(evidence)}]}}
            file=fx.file('replan.json',proof);old=table.read_bytes();result=a.replan(store,ep,file,False);self.assertEqual(table.read_bytes(),old)
            result=a.replan(store,ep,file,True)
            from pathlib import Path
            self.assertEqual((Path(result['history'])/'old-scenes.json').read_bytes(),old)
            self.assertEqual(read_json(table)['scenes'][0]['end'],1)
            self.assertEqual(read_json(visual)['scene_timeline']['scenes'][1]['start_frame'],30)
            with self.assertRaises(WorkflowError):a.replan(store,ep,file,True)
        finally:fx.tearDown()

class FrameproofDiagnostics(unittest.TestCase):
    def test_mismatch_preserves_full_expected_actual_and_first_node_before_raising(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from storyflow import scene_assembly as a
        from storyflow.core import read_json
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'A.mp4';source.write_bytes(b'offline digest source');out=root/'clean.mp4';out.write_bytes(b'offline output digest')
            assembly={'nodes':[{'type':'scene','id':'A','frame_count':3}]}
            proof=root/'failure.json'
            with patch.object(a,'framemd5',side_effect=[['a','b','c'],['a','BAD','c']]):
                with self.assertRaises(WorkflowError):a.prove_frames([source],out,assembly,proof)
            value=read_json(proof)
            self.assertEqual(value['status'],'fail');self.assertEqual(value['expected_frame_md5'],['a','b','c']);self.assertEqual(value['actual_frame_md5'],['a','BAD','c'])
            self.assertEqual(value['first_mismatch_frame'],1);self.assertEqual(value['first_mismatch_node']['id'],'A')
            saved=proof.read_bytes()
            with patch.object(a,'framemd5',side_effect=[['a','b','c'],['a','b','c','extra']]):
                with self.assertRaises(WorkflowError):a.prove_frames([source],out,assembly,proof)
            self.assertEqual(proof.read_bytes(),saved)
    def test_short_node_count_fails_even_if_output_sequence_matches(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from storyflow import scene_assembly as a
        from storyflow.core import read_json
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'A.mp4';source.write_bytes(b'source');out=root/'out.mp4';out.write_bytes(b'output')
            with patch.object(a,'framemd5',side_effect=[['a'],['a']]):
                with self.assertRaises(WorkflowError):a.prove_frames([source],out,{'nodes':[{'type':'scene','id':'A','frame_count':2}]})
            value=read_json(root/'whole-scene-frameproof.failed.json');self.assertEqual(value['count_errors'][0]['expected_frames'],2);self.assertEqual(value['count_errors'][0]['actual_frames'],1)

class MixedProfileFrameRegression(unittest.TestCase):
    def test_actual_continuous_decode_profile_failure_and_lossless_card_repair(self):
        import tempfile,subprocess
        from pathlib import Path
        from storyflow import scene_assembly as a,chapter_mix as mix
        from storyflow.core import read_json
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);scene=root/'scene.mp4';old_card=root/'old-card.mp4'
            # 1080p is material to this decoder context regression; at 64x64
            # the same 32-frame clip does not expose the mismatch.
            def ff(argv):subprocess.run(['ffmpeg','-v','error','-nostdin',*map(str,argv)],capture_output=True,check=True)
            for out,source,frames,crf in [(scene,'color=c=cyan:s=1920x1080:r=30',2,0),(old_card,'testsrc2=s=1920x1080:r=30',30,18)]:
                ff(['-f','lavfi','-i',source,'-frames:v',frames,'-an','-c:v','libx264','-preset','fast','-crf',crf,'-bf','0','-threads','4','-pix_fmt','yuv420p','-video_track_timescale','15360','-color_range','tv','-colorspace','bt709','-color_trc','bt709','-color_primaries','bt709',out])
            def concat(card,name):
                listing=root/(name+'.txt');listing.write_text(f"file '{scene}'\nfile '{card}'\n")
                out=root/(name+'.mp4');ff(['-f','concat','-safe','0','-i',listing,'-c:v','copy',out]);return out
            nodes={'nodes':[{'type':'scene','id':'A','frame_count':2},{'type':'transition','frame_count':30}]}
            old=concat(old_card,'mixed')
            # Exact count and well-formed clocks alone cannot mask actual pixels.
            with self.assertRaises(WorkflowError):a.prove_frames([scene,old_card],old,nodes,root/'old-failure.json')
            red=read_json(root/'old-failure.json');self.assertEqual(red['frame_count'],32);self.assertEqual(red['expected_frame_count'],32);self.assertGreater(red['mismatch_count'],0)
            self.assertEqual(red['first_mismatch_node']['type'],'transition')
            new_card=root/'canonical.mp4';mix.canonical_card(old_card,new_card,30,root/'canonical.log')
            self.assertEqual(a.framemd5(old_card),a.framemd5(new_card))
            good=concat(new_card,'canonical-concat');green=a.prove_frames([scene,new_card],good,nodes)
            self.assertEqual(green['status'],'pass');self.assertEqual(green['mismatch_count'],0)
