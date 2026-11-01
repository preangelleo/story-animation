#!/usr/bin/env python3
"""Small public Replicate prediction API client. GET polling never retries POST."""
import argparse,json,os,sys,urllib.request,urllib.parse
p=argparse.ArgumentParser();s=p.add_subparsers(dest='action',required=True)
a=s.add_parser('generate');a.add_argument('--version');a.add_argument('--model');a.add_argument('--input-json',required=True);a.add_argument('--json',action='store_true');a.add_argument('--no-wait',action='store_true')
a=s.add_parser('status');a.add_argument('id');a.add_argument('--json',action='store_true')
x=p.parse_args();key=os.environ.get('REPLICATE_API_TOKEN')
if not key:raise SystemExit('missing REPLICATE_API_TOKEN')
url='https://api.replicate.com/v1/predictions';data=None
if x.action=='status':url+='/'+urllib.parse.quote(x.id,safe='')
else:
 payload={'input':json.loads(__import__('pathlib').Path(x.input_json).read_text()),'version':x.version};data=json.dumps(payload).encode()
req=urllib.request.Request(url,data=data,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
try:
 with urllib.request.urlopen(req,timeout=120) as r:sys.stdout.write(r.read().decode())
except Exception as e:raise SystemExit('prediction request '+type(e).__name__+'; inspect existing intent, do not resubmit') from None
