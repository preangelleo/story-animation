"""Portable dependency checks and CEO work-plan; does not activate automation."""
import argparse,importlib.util,json,os,shutil,tomllib
from pathlib import Path
from .core import config,atomic_json,schedule,WorkflowError

def doctor(root):
 root=Path(root);cfg=config(root)
 commands={x:bool(shutil.which(x)) for x in ['python3','ffmpeg','ffprobe','fc-scan','fc-query','fc-match','node','npx','opencode']}
 def present(x):
  try:return importlib.util.find_spec(x) is not None
  except ModuleNotFoundError:return False
 modules={x:present(x) for x in ['numpy','PIL','opencc','faster_whisper','googleapiclient','google.auth']}
 configured={'voice_id':bool(os.environ.get('ELEVENLABS_VOICE_ID')),'elevenlabs_key':bool(os.environ.get('ELEVENLABS_API_KEY')),'author_key':bool(os.environ.get('ARK_AGENT_PLAN_API_KEY')),'replicate_key':bool(os.environ.get('REPLICATE_API_TOKEN')),'subtitle_font':Path(os.environ.get('STORY_SUBTITLE_FONT',str(root/'assets/fonts/subtitle.ttf'))).expanduser().is_file(),'channel_id':bool(cfg['channel'].get('id')),'chapter_fonts':all((root/'assets/fonts'/name).is_file() for name in ['title.ttf','number.ttf']),'chapter_sound':(root/'assets/sounds/chapter_dull_drum.mp3').is_file()}
 role=root/'.codex/agents/thumbnail-designer.toml'
 try:thumbnail_role_ready=tomllib.loads(role.read_text()).get('model')=='gpt-6.1-sol'
 except (OSError,ValueError):thumbnail_role_ready=False
 return {'ok':True,'offline_ready':all(commands[x] for x in ['python3','ffmpeg','ffprobe']) and all(modules[x] for x in ['numpy','PIL','opencc']),'production_ready':all(commands.values()) and all(modules.values()) and all(configured.values()),'thumbnail_role':{'configured_model_ready':thumbnail_role_ready,'required_model':'gpt-6.1-sol','required_tool':'built-in image_gen','actual_tool_available':'not probed; CEO must verify in Codex session'},'commands':commands,'python_modules':modules,'configured':configured,'paid_calls':0,'remote_capabilities_verified':False,'message':'Missing optional dependencies block only their corresponding production step; dry planning remains available.'}
def plan(args):
 root=Path(args.root);cfg=config(root)
 return {'ok':True,'execute':False,'schedule':schedule(args.production_date,cfg),'roles':['researcher','theme-planner','configured-author','independent-editor','character-designer','media-producer','chapter-designer','independent-final-review','ceo'],'steps':['research material dossier and classified sources','extract 1–3 themes and takeaway','freeze research bundle and fresh configured-author narration','independent outline/script/semantic-caption review','approved character reference sheets; unique whole-frame static scenes','voice; independent fulltext ASR; original-script forced alignment','at most three key ten-second generated video scenes','cached lowercase exact-name SFX/BGM; dry narration retained','chapter clips at complete scene boundaries; typed ordered EDL','CFR30 render; ultrasafe burn; full ordered-frame proof and head/mid/tail clock QA','owner review','optional independently verified channel scheduled submission and readback','table/vector feedback'],'publication_requires_explicit_execute':True,'timers_started':False}
def register(s):
 q=s.add_parser('doctor');q.set_defaults(handler=lambda a:doctor(a.root))
 q=s.add_parser('ceo');sub=q.add_subparsers(dest='ceo_action',required=True);p=sub.add_parser('plan');p.add_argument('--production-date',required=True);p.set_defaults(handler=plan)
