#!/usr/bin/env python3
"""Package-owned ElevenLabs API boundary implementing required qy speak/align/sfx/music calls."""
import sys,json,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from storyflow.providers import eleven
p=argparse.ArgumentParser();p.add_argument('action',choices=['speak','align','sfx','music']);p.add_argument('input');p.add_argument('--text-file');p.add_argument('--language');p.add_argument('-v');p.add_argument('-m','--model');p.add_argument('--seconds',type=float);p.add_argument('--influence');p.add_argument('-o',required=True)
a=p.parse_args();out=Path(a.o)
if out.exists():raise SystemExit('preserve existing provider artifact')
try:
 result=eleven(a.action,text=Path(a.text_file).read_text() if a.text_file else a.input,audio=a.input if a.action=='align' else None,voice=a.v,model=a.model,seconds=a.seconds)
 out.parent.mkdir(parents=True,exist_ok=True)
 with out.open('xb') as f:f.write(json.dumps(result,ensure_ascii=False).encode() if isinstance(result,dict) else result)
except Exception as e:raise SystemExit('provider failure: '+type(e).__name__+'; inspect ledger; no automatic retry') from None
