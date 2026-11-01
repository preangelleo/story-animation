"""Public provider interfaces; no personal files, no implicit POST retries."""
from pathlib import Path
import json,os,re,urllib.request,urllib.error,urllib.parse,uuid
BASE='https://api.elevenlabs.io'
def request(path,payload=None,*,raw=False,content_type='application/json'):
    key=os.environ.get('ELEVENLABS_API_KEY')
    if not key:raise RuntimeError('missing ELEVENLABS_API_KEY; configure using your secret manager')
    body=json.dumps(payload).encode() if isinstance(payload,dict) else payload
    req=urllib.request.Request(BASE+path,data=body,headers={'xi-api-key':key,'Content-Type':content_type})
    try:
        with urllib.request.urlopen(req,timeout=300) as r:data=r.read()
    except urllib.error.HTTPError as e:raise RuntimeError('provider HTTP '+str(e.code)+'; inspect operation before retry') from None
    return data if raw else json.loads(data)
def eleven(action,text=None,audio=None,voice=None,model=None,seconds=None):
    if action=='speak':
        voice=(os.environ.get('ELEVENLABS_VOICE_ID') or voice) if not voice or voice=='user-voice' else voice
        if not voice or voice=='user-voice':raise RuntimeError('configure ELEVENLABS_VOICE_ID; no shared/default identity')
        return request('/v1/text-to-speech/'+urllib.parse.quote(voice,safe=''),{'text':text,'model_id':model or 'eleven_multilingual_v2'},raw=True)
    if action in ('sfx','music'):
        path='/v1/sound-generation' if action=='sfx' else '/v1/music'
        body={'text':text,'duration_seconds':seconds,'prompt_influence':.8} if action=='sfx' else {'prompt':text,'music_length_ms':int(seconds*1000),'force_instrumental':True}
        return request(path,body,raw=True)
    if action=='align':
        boundary='story'+uuid.uuid4().hex
        parts=[f'--{boundary}\r\nContent-Disposition: form-data; name="text"\r\n\r\n{text}\r\n'.encode(),f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode(),Path(audio).read_bytes(),f'\r\n--{boundary}--\r\n'.encode()]
        return request('/v1/forced-alignment',b''.join(parts),content_type='multipart/form-data; boundary='+boundary)
    raise ValueError('unknown provider action')
def youtube_service(profile):
    if not isinstance(profile,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',profile) or profile=='default':raise RuntimeError('explicit independent YouTube profile required')
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    # One explicit user profile, never another workflow's credentials or fallback account.
    root=Path(os.environ.get('STORY_YOUTUBE_TOKEN_DIR','~/.config/story-animation/youtube')).expanduser()
    path=root/(profile+'.json')
    if not path.is_file():raise RuntimeError('authorize your own YouTube OAuth profile first')
    token=json.loads(path.read_text())
    scopes=token.get('scopes',[])
    if isinstance(scopes,str):scopes=scopes.split()
    caption_scopes={'https://www.googleapis.com/auth/youtube.force-ssl','https://www.googleapis.com/auth/youtubepartner'}
    if not isinstance(scopes,list) or not caption_scopes.intersection(scopes):raise RuntimeError('YouTube captions require prior user-authorized youtube.force-ssl scope; reauthorize explicitly, no automatic scope expansion')
    credentials=Credentials.from_authorized_user_file(str(path),scopes=scopes)
    return build('youtube','v3',credentials=credentials,cache_discovery=False)
