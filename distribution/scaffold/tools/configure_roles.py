#!/usr/bin/env python3
"""Explicitly configure only this workspace's generated Codex thumbnail role."""
import argparse,json,re,tomllib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def configure(root,execute=False):
 root=Path(root).resolve();source=root/'roles/thumbnail-designer.toml';data=tomllib.loads(source.read_text());target=root/'.codex/agents/thumbnail-designer.toml'
 if data.get('name')!='thumbnail-designer' or data.get('model')!='gpt-6.1-sol':raise ValueError('canonical thumbnail role mismatch')
 if target.is_symlink() or any(p.is_symlink() for p in [root/'.codex',target.parent]):raise ValueError('role path may not be a symlink')
 if not execute:return {'ok':True,'executed':False,'target':str(target),'model':data['model'],'requires':'Codex with built-in image_gen; no external paid image fallback'}
 if target.exists():
  original=target.read_text();old=tomllib.loads(original)
  if old.get('name')!='thumbnail-designer':raise ValueError('different existing role; preserve and inspect')
  updated=re.sub(r'^model\s*=.*\n?','',original,flags=re.M)+'\nmodel = "gpt-6.1-sol"\n'
 else:updated=source.read_text()
 target.parent.mkdir(parents=True,exist_ok=True);target.write_text(updated)
 actual=tomllib.loads(target.read_text())
 if actual.get('model')!='gpt-6.1-sol':raise ValueError('role model readback failed')
 return {'ok':True,'executed':True,'target':str(target),'model':actual['model'],'image_generation_capability':'requires actual Codex image_gen; not inferred from file existence'}
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true');p.add_argument('--root',type=Path,default=ROOT);a=p.parse_args();print(json.dumps(configure(a.root,a.execute),ensure_ascii=False))
