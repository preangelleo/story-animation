from pathlib import Path
import json,unicodedata,wave
from .core import atomic_json,WorkflowError

def build(root,episode):
 R=Path(root).resolve();P=R/'episodes'/episode/'production'
 read=lambda p:json.loads(p.read_text());from .core import digest as sha
 b=read(P/'alignment.binding.json');alignment=R/b['alignment_path'];assert sha(alignment)==b['alignment_sha256'];assert sha(P/'script.txt')==b['inputs']['script_sha256']
 plan=read(P/'visual-plan.json');raw=read(alignment)['characters'];keep=lambda c:not c.isspace() and not unicodedata.category(c).startswith('P');norm=lambda t:''.join(c for c in t if keep(c));chars=[c for c in raw if keep(c['text'])];text=''.join(c['text'] for c in chars)
 if norm((P/'script.txt').read_text())!=text:raise WorkflowError('alignment must cover exact frozen script')
 if len({s['id'] for s in plan['scenes']})!=len(plan['scenes']):raise WorkflowError('duplicate scene ids')
 voice=read(P/'voice.binding.json');audio=R/voice['artifact']['path'];assert sha(audio)==b['inputs']['audio_sha256']
 with wave.open(str(audio)) as w:duration=w.getnframes()/w.getframerate()
 starts=[]
 for s in plan['scenes']:
  anchor=norm(s['anchor']);assert text.count(anchor)==1,(s['id'],anchor,text.count(anchor));pos=text.index(anchor);starts.append(chars[pos]['start'])
 starts[0]=0
 for i,s in enumerate(plan['scenes']):
  if s['media_type']=='video':
   assert i+1<len(starts)
   # Each exact ten-second insert replaces an interval on the unchanged narration clock.
   # Advance next still if its own anchor is later, instead of extending video or duplicating its reference.
   starts[i+1]=starts[i]+10
 assert all(x<y for x,y in zip(starts,starts[1:])),starts
 scenes=[]
 for i,s in enumerate(plan['scenes']):
  start=starts[i];end=starts[i+1] if i+1<len(starts) else duration;kind=s['media_type'];sid=s['id'];assert end>start
  row={'id':sid,'image':s.get('image',str((P/('images/'+sid+'.png' if kind=='image' else 'video-inserts/'+sid+'/clip.mp4')).relative_to(R))),'media_type':kind,'duration':end-start,'start':start,'end':end,'aligned_anchor':s['anchor'],'trim_start':0,'provenance':'force-aligned reviewed still; actual origin/generation is bound in visual manifest' if kind=='image' else 'actual Seedance2.5 prediction; original downloaded video trimmed to ten seconds'}
  from .scene_cleanup import normalize_cleanup
  if 'caption_band_cleanup' in s:
   cleanup=normalize_cleanup(s,sha(R/row['image']));row['caption_band_cleanup']=cleanup
  if kind=='image':row['motion']='static'
  else:assert abs(row['duration']-10)<1e-8
  scenes.append(row)
 out={'script_sha256':b['inputs']['script_sha256'],'audio_sha256':b['inputs']['audio_sha256'],'alignment_sha256':b['alignment_sha256'],'duration':duration,'scenes':scenes,'timing_note':'next still starts immediately after each exact ten-second insert; aligned_anchor is its narrative target, actual start may precede it; audio timeline is never stretched'}
 atomic_json(P/'scenes.json',out);return ({'duration':duration,'scenes':len(scenes),'videos':[(s['id'],s['start'],s['end']) for s in scenes if s['media_type']=='video'],'max_still_duration':max(s['duration'] for s in scenes if s['media_type']=='image')})
