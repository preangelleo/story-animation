#!/usr/bin/env python3
"""Deterministic format/size export only; never generates a thumbnail or crops composition."""
import argparse,hashlib,json
from pathlib import Path
from PIL import Image

def export(source,output,width=1280,height=720,max_bytes=2000000):
 source=Path(source);output=Path(output)
 if output.exists() or source.resolve()==output.resolve():raise ValueError('preserve existing output/source')
 with Image.open(source) as im:
  im.load()
  if width<1 or height<1 or abs(im.width/im.height-width/height)>.01:raise ValueError('ratio mismatch; regenerate this ratio, never crop another composition')
  if width>im.width or height>im.height:raise ValueError('do not upscale; request a suitable source image')
  rgb=im.convert('RGB').resize((width,height),Image.Resampling.LANCZOS)
  import io
  data=None
  for quality in (94,90,86,82,78,74,70):
   out=io.BytesIO();rgb.save(out,format='JPEG',quality=quality,optimize=True)
   if len(out.getvalue())<=max_bytes:data=out.getvalue();break
  if data is None:raise ValueError('size limit unavailable without further quality loss')
 output.parent.mkdir(parents=True,exist_ok=True)
 with output.open('xb') as f:f.write(data)
 return {'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'output':str(output),'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'width':width,'height':height,'quality':quality,'scope':'mechanical export only; requires separate actual 320/160px visual review'}
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',required=True);p.add_argument('--output',required=True);p.add_argument('--width',type=int,default=1280);p.add_argument('--height',type=int,default=720);a=p.parse_args();print(json.dumps(export(a.input,a.output,a.width,a.height),ensure_ascii=False))
