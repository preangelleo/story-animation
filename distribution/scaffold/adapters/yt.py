#!/usr/bin/env python3
"""Explicit-profile YouTube adapter. Identity checked upstream; upload intent is durable."""
import argparse,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from storyflow.providers import youtube_service
p=argparse.ArgumentParser();p.add_argument('action',choices=['whoami','upload']);p.add_argument('--profile',required=True);p.add_argument('--file');p.add_argument('--title');p.add_argument('--description-file');p.add_argument('--privacy',default='private');p.add_argument('--publish-at');p.add_argument('--language',default='zh-CN');p.add_argument('--thumbnail');a=p.parse_args()
try:
 service=youtube_service(a.profile)
 if a.action=='whoami':print(json.dumps(service.channels().list(part='id,snippet',mine=True).execute().get('items',[])));raise SystemExit(0)
 from googleapiclient.http import MediaFileUpload
 f=Path(a.file);receipt=f.with_suffix(f.suffix+'.youtube-upload.json')
 if receipt.exists():raise RuntimeError('upload receipt/intent exists; verify before retry')
 with receipt.open('x') as out:json.dump({'status':'running','profile':a.profile,'publish_at':a.publish_at},out)
 body={'snippet':{'title':a.title,'description':Path(a.description_file).read_text(),'defaultLanguage':a.language},'status':{'privacyStatus':a.privacy,'publishAt':a.publish_at,'selfDeclaredMadeForKids':False}}
 request=service.videos().insert(part='snippet,status',body=body,media_body=MediaFileUpload(str(f),mimetype='video/mp4',resumable=True))
 response=None
 while response is None:_,response=request.next_chunk(num_retries=0)
 result={'status':'accepted','video_id':response['id'],'profile':a.profile};receipt.write_text(json.dumps(result))
 if a.thumbnail:service.thumbnails().set(videoId=response['id'],media_body=MediaFileUpload(a.thumbnail)).execute(num_retries=0)
 print(json.dumps(result))
except Exception as e:raise SystemExit('YouTube '+type(e).__name__+'; inspect intent and verify existing ID, do not upload again') from None
