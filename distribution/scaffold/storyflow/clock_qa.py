from pathlib import Path
import json,subprocess,hashlib,re
from .core import atomic_json, WorkflowError
from .vendor.smart_caption.convert import read_srt
from .vendor.smart_caption.segment import parse_time

def audit(video,audio,srt,out,chapter=None):
 import numpy as np
 video,audio,srt,out=map(Path,(video,audio,srt,out))
 sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
 receipt={'video_sha256':sha(video),'audio_sha256':sha(audio),'srt_sha256':sha(srt)}
 if out.exists() and any(out.iterdir()): raise WorkflowError('clock evidence exists; preserve and use a new output directory',409)
 cues=read_srt(str(srt))
 if len(cues)<3: raise WorkflowError('clock QA requires at least three subtitle cues')
 out.mkdir(parents=True,exist_ok=True)
 run=lambda a:subprocess.run(a,check=True,capture_output=True).stdout
 probe=json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)]));v=next(s for s in probe['streams'] if s['codec_type']=='video');a=next(s for s in probe['streams'] if s['codec_type']=='audio');duration=float(probe['format']['duration'])
 frames=json.loads(run(['ffprobe','-v','error','-select_streams','v:0','-show_frames','-show_entries','frame=pts','-of','json',str(video)]))['frames'];pts=[x['pts'] for x in frames];deltas=sorted(set(y-x for x,y in zip(pts,pts[1:])))
 report={'video_sha256':sha(video),'audio_sha256':sha(audio),'srt_sha256':sha(srt),'frame_pts':{'count':len(pts),'first':pts[0],'delta_ticks':deltas,'time_base':v['time_base'],'fps':v['avg_frame_rate'],'passed':pts[0]==0 and deltas==[512] and v['time_base']=='1/15360' and v['avg_frame_rate']=='30/1'},'av_clock':{'video_start':v['start_time'],'audio_start':a['start_time'],'video_duration':v['duration'],'audio_duration':a['duration'],'passed':float(v['start_time'])==0 and float(a['start_time'])==0}}
 raw=lambda p:np.frombuffer(run(['ffmpeg','-nostdin','-v','error','-i',str(p),'-map','0:a:0','-ac','1','-ar','16000','-f','f32le','-']),dtype='<f4')
 x,y=raw(audio),raw(video);windows=[];n=48000;r=1600
 for label,target in [('head',5),('middle',duration/2),('tail',duration-9)]:
  choices=range(max(r,int((target-3)*16000)),min(len(x)-n-r,len(y)-n-r,int((target+3)*16000)),8000)
  if not choices: raise WorkflowError('clock QA audio too short for correlation windows')
  base=max(choices,key=lambda i:float(np.mean(x[i:i+n]**2)))
  ref=x[base:base+n].astype('float64');ref-=ref.mean();buf=y[base-r:base+n+r].astype('float64');buf-=buf.mean();corr=np.correlate(buf,ref,mode='valid');cum=np.r_[0,np.cumsum(buf*buf)];score=corr/np.sqrt(np.maximum((cum[n:]-cum[:-n])*np.dot(ref,ref),1e-30));best=int(np.argmax(score));windows.append({'region':label,'source_seconds':base/16000,'lag_samples':best-r,'lag_ms':(best-r)/16,'correlation':float(score[best])})
 lags=[w['lag_ms'] for w in windows];report['pcm_alignment']={'windows':windows,'spread_ms':max(lags)-min(lags),'passed':all(abs(z)<=1 for z in lags) and max(lags)-min(lags)<=1 and all(w['correlation']>.9 for w in windows),'comparison':'current bound delivery WAV vs burned decoded AAC at 16k mono; source dry narration remains independent'}
 screens=[]
 for label,index in [('head',1),('middle',len(cues)//2),('tail',len(cues)-2)]:
  cue=cues[index];start,end=parse_time(cue['start']),parse_time(cue['end'])
  for point,t in [('before',start-.08),('inside',(start+end)/2),('after',end+.08)]:
   image=out/f'cue-{label}-{point}.png';run(['ffmpeg','-nostdin','-v','error','-y','-ss',str(t),'-i',str(video),'-frames:v','1','-vf','scale=960:540',str(image)])
   screens.append({'region':label,'point':point,'time':t,'expected':[c['text'] for c in cues if parse_time(c['start'])<=t<parse_time(c['end'])],'path':str(image),'sha256':sha(image)})
 report['screenshots']=screens
 result=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-v','info','-i',str(video),'-map','0:v:0','-vf','blackdetect=d=0.30:pix_th=0.10:pic_th=0.98','-an','-f','null','-'],capture_output=True);(out/'final-decode-black.log').write_bytes(result.stderr);black=re.findall(r'black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)',result.stderr.decode());expected_dark=[]
 if chapter:
  elapsed=0
  for card in chapter['mapping']['transitions']:
   start=card['source_frame']/30+elapsed
   expected_dark.append({'start':start,'end':start+card['frame_count']/30,'clip':card['clip']})
   elapsed+=card['frame_count']/30
 unexpected_black=[row for row in black if not any(float(row[0])>=gap['start']-1/30-.001 and float(row[1])<=gap['end']+1/30+.001 for gap in expected_dark)]
 report['chapter_revision_sha256']=chapter['binding_sha256'] if chapter else None
 report['full_decode']={'returncode':result.returncode,'black_intervals':black,'expected_dark_chapter_intervals':expected_dark,'unexpected_black_intervals':unexpected_black,'passed':result.returncode==0 and not unexpected_black}
 report['mechanical_passed']=all(report[k]['passed'] for k in ('frame_pts','av_clock','pcm_alignment','full_decode'));report['visual_review']='pending independent actual inspection of nine screenshots';report['input_hashes_before']=receipt;report['input_hashes_after']={'video_sha256':sha(video),'audio_sha256':sha(audio),'srt_sha256':sha(srt)};report['source_unchanged']=report['input_hashes_before']==report['input_hashes_after'];report['mechanical_passed']=report['mechanical_passed'] and report['source_unchanged'];atomic_json(out/'final-clock-review.json',report);return report
