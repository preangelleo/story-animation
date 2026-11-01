"""本地、确定性、无付费调用的故事生产事实库。"""
from __future__ import annotations
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from zoneinfo import ZoneInfo

KINDS = ('outline','script','review','structure','audio','video','subtitles','cover','metadata')
CHECKS = ('logic','clue_fairness','promise_payoff','continuity','sources')
DEFAULT = {'channel': {'id':None,'slug':'story-animation','timezone':'Asia/Tokyo','publish_hour':20,'lead_days':2,'language':'zh'}, 'budget': {'per_episode_usd':15,'monthly_usd':200,'max_retries':2},'quality':{'min_script_chars':2500,'min_duration_seconds':600}}
class WorkflowError(Exception):
    def __init__(self, message, code=400): self.code=code; super().__init__(message)
def utcnow(): return dt.datetime.now(dt.timezone.utc).isoformat()
def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()
def read_json(path):
    try:
        value=json.loads(Path(path).read_text())
    except (OSError,ValueError) as e: raise WorkflowError(f'无法读取 JSON：{e}')
    if not isinstance(value,dict): raise WorkflowError('JSON 顶层必须是对象')
    return value

def config(root):
    result=json.loads(json.dumps(DEFAULT))
    path=Path(root)/'documentation/configuration.json'
    if path.exists():
        supplied=read_json(path)
        for key,value in supplied.items():
            if isinstance(value,dict) and isinstance(result.get(key),dict): result[key].update(value)
            else: result[key]=value
    return result

def schedule(production_date, cfg=None):
    cfg=cfg or DEFAULT
    c=cfg['channel']
    try:
        day=dt.date.fromisoformat(production_date)
        release=day+dt.timedelta(days=int(c.get('lead_days',2)))
        scheduled=dt.datetime.combine(release, dt.time(int(c.get('publish_hour',20))), ZoneInfo(c.get('timezone','Asia/Tokyo')))
    except (ValueError,TypeError) as e: raise WorkflowError(f'无效制作日期或排期配置：{e}')
    return {'production_date':day.isoformat(),'release_date':release.isoformat(),'scheduled_at':scheduled.isoformat(),'timezone':c.get('timezone','Asia/Tokyo'),'actual_public_at':None}

def timestamp(value):
    try:
        t=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
        if t.tzinfo is None: raise ValueError('需要时区')
        return t
    except (ValueError,TypeError,AttributeError) as e: raise WorkflowError(f'无效时间戳：{value} ({e})')

def atomic_json(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.writing-')
    try:
        with os.fdopen(fd,'w') as f: json.dump(value,f,ensure_ascii=False,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(name,path)
        parent_fd=os.open(path.parent,os.O_RDONLY)
        try: os.fsync(parent_fd)
        finally: os.close(parent_fd)
    finally:
        if os.path.exists(name): os.unlink(name)

class Store:
    def __init__(self, root):
        self.root=Path(root).expanduser().resolve(); self.config=config(self.root)
        self.state=self.root/'state'; self.state.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.state/'story.sqlite3',timeout=30)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON'); self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS episodes(id TEXT PRIMARY KEY, channel TEXT NOT NULL, release_date TEXT, data TEXT NOT NULL, UNIQUE(channel,release_date));
        CREATE TABLE IF NOT EXISTS ideas(id TEXT PRIMARY KEY, data TEXT NOT NULL, episode_id TEXT REFERENCES episodes(id));
        CREATE TABLE IF NOT EXISTS artifacts(episode_id TEXT REFERENCES episodes(id),kind TEXT,path TEXT,sha256 TEXT,registered_at TEXT,PRIMARY KEY(episode_id,kind));
        CREATE TABLE IF NOT EXISTS reviews(episode_id TEXT PRIMARY KEY REFERENCES episodes(id),data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outline_reviews(episode_id TEXT PRIMARY KEY REFERENCES episodes(id),data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS media_reviews(episode_id TEXT PRIMARY KEY REFERENCES episodes(id),data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS qa(episode_id TEXT,step TEXT,data TEXT,PRIMARY KEY(episode_id,step));
        CREATE TABLE IF NOT EXISTS validations(episode_id TEXT PRIMARY KEY,data TEXT);
        CREATE TABLE IF NOT EXISTS claims(episode_id TEXT PRIMARY KEY,owner TEXT,expires_at TEXT);
        CREATE TABLE IF NOT EXISTS publications(episode_id TEXT PRIMARY KEY REFERENCES episodes(id),data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY,episode_id TEXT REFERENCES episodes(id),data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS costs(id INTEGER PRIMARY KEY,episode_id TEXT REFERENCES episodes(id),amount REAL,category TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS facts(id TEXT,version INTEGER,kind TEXT,data TEXT NOT NULL,sha256 TEXT NOT NULL,created_at TEXT,PRIMARY KEY(id,version));
        CREATE TABLE IF NOT EXISTS vectors(id TEXT PRIMARY KEY,model TEXT NOT NULL,version TEXT NOT NULL,source_hash TEXT NOT NULL,dimensions INTEGER NOT NULL,data TEXT NOT NULL);
        ''')
        if 'input_hashes' not in [r[1] for r in self.db.execute('PRAGMA table_info(artifacts)')]:
            self.db.execute("ALTER TABLE artifacts ADD COLUMN input_hashes TEXT NOT NULL DEFAULT '{}'")
        self.db.commit()
        if any(r['name']=='release_date' and r['notnull'] for r in self.db.execute('PRAGMA table_info(episodes)')):
            # A withdrawn attempt keeps its original dates in data but releases
            # its scheduling slot (NULL). All artifact/cost foreign keys survive.
            backup=self.state/'backups'/'before-withdrawal-migration.sqlite3'
            backup.parent.mkdir(parents=True,exist_ok=True)
            if not backup.exists():
                with sqlite3.connect(backup) as out: self.db.backup(out)
            self.db.execute('PRAGMA foreign_keys=OFF')
            try:
                with self.db:
                    self.db.execute('BEGIN IMMEDIATE')
                    self.db.execute('CREATE TABLE episodes_withdrawal_migration(id TEXT PRIMARY KEY,channel TEXT NOT NULL,release_date TEXT,data TEXT NOT NULL,UNIQUE(channel,release_date))')
                    self.db.execute('INSERT INTO episodes_withdrawal_migration SELECT id,channel,release_date,data FROM episodes')
                    self.db.execute('DROP TABLE episodes')
                    self.db.execute('ALTER TABLE episodes_withdrawal_migration RENAME TO episodes')
                    if self.db.execute('PRAGMA foreign_key_check').fetchone(): raise WorkflowError('撤稿迁移外键检查失败')
            finally: self.db.execute('PRAGMA foreign_keys=ON')
    def close(self): self.db.close()
    def channel(self):
        c=self.config['channel']; return c.get('slug') or 'story-animation'
    def episode(self, ep):
        row=self.db.execute('SELECT data FROM episodes WHERE id=?',(ep,)).fetchone()
        if not row: raise WorkflowError('故事不存在：'+ep,404)
        return json.loads(row['data'])
    def episodes(self): return [json.loads(r['data']) for r in self.db.execute('SELECT data FROM episodes ORDER BY release_date,id')]
    def create(self, production_date, title, series=None, ep=None, author='writer', verification_only=False, review_only=False, style_index=None, style_file=None, mapping_file=None, classification_evidence=None):
        if review_only and verification_only: raise WorkflowError('review_only cannot skip normal quality gates')
        dates=schedule(production_date,self.config)
        ep=ep or 'qy-'+dates['release_date'].replace('-','')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}',ep): raise WorkflowError('无效故事 ID')
        snapshot=None
        if series=='other' and not (isinstance(classification_evidence,str) and classification_evidence.strip()):raise WorkflowError('explicit other series requires classification_evidence')
        if self.config.get('style_registry_file') or self.config.get('series_mapping_file') or self.config.get('styles') or style_index is not None or style_file or mapping_file or series is None:
            from .style_registry import resolve_style
            snapshot=resolve_style(self.root,release_date=dates['scheduled_at'],series=series,style_index=style_index,style_file=style_file,mapping_file=mapping_file,classification_evidence=classification_evidence)
            series=snapshot['series_key']
        if not title.strip() or not series.strip() or not author.strip(): raise WorkflowError('标题、系列和作者不能为空')
        value={'id':ep,'channel':self.channel(),'title':title,'series':series,'author':author,'verification_only':bool(verification_only),'review_only':bool(review_only),**dates,'created_at':utcnow()}
        if snapshot is not None: value.update(style_snapshot=snapshot,style_index=snapshot['style_index'],style_resolution_required=True)
        try:
            with self.db: self.db.execute('INSERT INTO episodes VALUES(?,?,?,?)',(ep,self.channel(),None if review_only else dates['release_date'],json.dumps(value,ensure_ascii=False)))
        except sqlite3.IntegrityError: raise WorkflowError('故事 ID 或该频道发布日期已存在',409)
        (self.root/'episodes'/ep).mkdir(parents=True,exist_ok=True)
        return value
    def artifacts(self, ep):
        self.episode(ep)
        return {r['kind']:dict(r) for r in self.db.execute('SELECT * FROM artifacts WHERE episode_id=?',(ep,))}
    def withdraw(self, ep, reason):
        if not reason.strip(): raise WorkflowError('撤稿必须记录原因')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            value=self.episode(ep)
            if self.db.execute('SELECT 1 FROM publications WHERE episode_id=?',(ep,)).fetchone():
                raise WorkflowError('已有平台发布记录，须先核实平台撤回，不得本地释放排期',409)
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='external_operations'").fetchone():
                if self.db.execute("SELECT 1 FROM external_operations WHERE episode_id=? AND kind='ship'",(ep,)).fetchone():
                    raise WorkflowError('存在上传操作或未决回执，须先核实平台撤回，不得释放排期',409)
            if value.get('editorial_status')=='withdrawn': return value
            value.update(editorial_status='withdrawn',withdrawal_reason=reason,withdrawn_at=utcnow())
            self.db.execute('UPDATE episodes SET release_date=NULL,data=? WHERE id=?',(json.dumps(value,ensure_ascii=False),ep))
            self.invalidate(ep)
            self.db.execute('DELETE FROM claims WHERE episode_id=?',(ep,))
        return value
    def invalidate(self, ep):
        self.db.execute('DELETE FROM validations WHERE episode_id=?',(ep,)); self.db.execute('DELETE FROM qa WHERE episode_id=?',(ep,))
    def register(self,ep,kind,file):
        self.episode(ep)
        if kind not in KINDS: raise WorkflowError('不支持的产物种类')
        source=Path(file).expanduser().resolve()
        if not source.is_file() or source.stat().st_size==0: raise WorkflowError('产物文件不存在或为空')
        sha=digest(source)
        suffix=source.suffix.lower()[:12]
        folder=self.root/'episodes'/ep/'artifacts'; folder.mkdir(parents=True,exist_ok=True)
        target=folder/f'{kind}-{sha}{suffix}'
        if source!=target:
            fd,tmp=tempfile.mkstemp(dir=folder,prefix='.copy-')
            try:
                with os.fdopen(fd,'wb') as out,source.open('rb') as inp: shutil.copyfileobj(inp,out); out.flush(); os.fsync(out.fileno())
                if digest(tmp)!=sha: raise WorkflowError('复制期间源文件发生变化，拒绝登记')
                os.replace(tmp,target)
            finally:
                if os.path.exists(tmp): os.unlink(tmp)
        rel=str(target.relative_to(self.root))
        dependencies=('outline','script','structure','audio') if kind in ('video','subtitles') else ('outline','script','structure')
        inputs={k:a['sha256'] for k,a in self.artifacts(ep).items() if k in dependencies and k!=kind}
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO artifacts VALUES(?,?,?,?,?,?)',(ep,kind,rel,sha,utcnow(),json.dumps(inputs)))
            self.invalidate(ep)
            self.db.execute('DELETE FROM media_reviews WHERE episode_id=?',(ep,))
            if kind in ('script','outline','structure','review'): self.db.execute('DELETE FROM reviews WHERE episode_id=?',(ep,))
            if kind in ('outline','structure'): self.db.execute('DELETE FROM outline_reviews WHERE episode_id=?',(ep,))
        return {'episode_id':ep,'kind':kind,'path':rel,'sha256':sha,'validation_invalidated':True}
    def verify_hashes(self,ep):
        issues=[]; arts=self.artifacts(ep)
        for kind,item in arts.items():
            p=self.root/item['path']
            if not p.is_file() or digest(p)!=item['sha256']: issues.append(kind+' 文件丢失或 SHA256 不符')
        return issues
    def outline_review_errors(self,ep):
        row=self.db.execute('SELECT data FROM outline_reviews WHERE episode_id=?',(ep,)).fetchone()
        if not row: return ['缺少独立大纲初审记录']
        value=json.loads(row['data']); arts=self.artifacts(ep); errors=[]
        receipt=self.root/value.get('receipt_path','missing')
        if not receipt.is_file() or digest(receipt)!=value.get('receipt_sha256'): errors.append('大纲初审回执丢失或哈希不符')
        if value.get('verdict')!='pass': errors.append('大纲初审要求修改')
        for kind in ('outline','structure'):
            if value.get(kind+'_sha256')!=arts.get(kind,{}).get('sha256'): errors.append(kind+' 大纲初审绑定已过期')
        return errors
    def outline_review(self,ep,file):
        episode=self.episode(ep); value=read_json(file); arts=self.artifacts(ep)
        if self.verify_hashes(ep): raise WorkflowError('文件已改变，请先重新登记')
        for kind in ('outline','structure'):
            if kind not in arts or value.get(kind+'_sha256')!=arts[kind]['sha256']: raise WorkflowError(kind+'_sha256 必须匹配已登记产物')
        if value.get('episode_id')!=ep: raise WorkflowError('初审 episode_id 不匹配')
        reviewer=value.get('reviewer')
        if not isinstance(reviewer,str) or not reviewer.strip() or reviewer.strip().casefold()==episode['author'].strip().casefold(): raise WorkflowError('reviewer 必须明确且与 author 不同')
        if value.get('verdict') not in ('pass','revise'): raise WorkflowError('verdict 必须是 pass 或 revise')
        checks=value.get('checks',{})
        if not isinstance(checks,dict): raise WorkflowError('checks 必须是对象')
        for check in CHECKS:
            item=checks.get(check,{})
            if not isinstance(item,dict) or item.get('status') not in ('pass','revise','na') or not isinstance(item.get('reason'),str) or not item['reason'].strip(): raise WorkflowError(f'checks.{check} 需要 status(pass/revise/na) 和非空 reason')
        if value['verdict']=='pass' and any(checks[c]['status']=='revise' for c in CHECKS): raise WorkflowError('存在 revise 核查项，不能通过')
        sha=digest(file); receipt_dir=self.root/'episodes'/ep/'outline-reviews'
        version=max([int(p.name.split('-',1)[0]) for p in receipt_dir.glob('[0-9]*-*.json')] or [0])+1
        receipt_path=receipt_dir/(str(version)+'-'+sha+'.json')
        # 保存独立初审原文及其内容哈希；不占用完整稿终审产物。
        atomic_json(receipt_path,value)
        value['source_sha256']=sha; value['receipt_sha256']=digest(receipt_path)
        value['receipt_path']=str(receipt_path.relative_to(self.root)); value['recorded_at']=utcnow()
        with self.db:
            value['version']=version
            self.db.execute('INSERT OR REPLACE INTO outline_reviews VALUES(?,?)',(ep,json.dumps(value,ensure_ascii=False)))
            self.invalidate(ep)
            self.db.execute('DELETE FROM reviews WHERE episode_id=?',(ep,))
        return value
    def record_review(self,ep,file):
        episode=self.episode(ep); value=read_json(file); arts=self.artifacts(ep)
        initial_errors=self.outline_review_errors(ep)
        if initial_errors: raise WorkflowError('；'.join(initial_errors))
        if self.verify_hashes(ep): raise WorkflowError('文件已改变，请先重新登记')
        for kind in ('outline','script','structure'):
            if kind not in arts: raise WorkflowError('先登记 '+kind)
        reviewer=value.get('reviewer')
        if not isinstance(reviewer,str) or not reviewer.strip() or reviewer.strip().casefold()==episode['author'].strip().casefold(): raise WorkflowError('reviewer 必须明确且与 author 不同')
        if value.get('verdict') not in ('pass','revise'): raise WorkflowError('verdict 必须是 pass 或 revise')
        checks=value.get('checks',{})
        if not isinstance(checks,dict): raise WorkflowError('checks 必须是对象')
        for check in CHECKS:
            item=checks.get(check,{})
            if not isinstance(item,dict) or item.get('status') not in ('pass','revise','na') or not isinstance(item.get('reason'),str) or not item['reason'].strip(): raise WorkflowError(f'checks.{check} 需要 status(pass/revise/na) 和非空 reason')
        if value['verdict']=='pass' and any(checks[c]['status']=='revise' for c in CHECKS): raise WorkflowError('存在 revise 核查项，不能通过')
        for kind in ('outline','script','structure'):
            key=kind+'_sha256'
            if value.get(key)!=arts[kind]['sha256']: raise WorkflowError(key+' 必须匹配已登记产物')
        if value.get('episode_id')!=ep: raise WorkflowError('审稿 episode_id 不匹配')
        value['recorded_at']=utcnow()
        # 登记原始审稿文件，同时保存绑定哈希的结构化事实。
        artifact=self.register(ep,'review',file)
        value['review_sha256']=artifact['sha256']
        with self.db: self.db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?)',(ep,json.dumps(value,ensure_ascii=False)))
        return value
    def review_errors(self,ep):
        row=self.db.execute('SELECT data FROM reviews WHERE episode_id=?',(ep,)).fetchone()
        if not row: return ['缺少独立审稿记录']
        value=json.loads(row['data']); arts=self.artifacts(ep); issues=self.outline_review_errors(ep)
        if value['verdict']!='pass': issues.append('审稿要求修改')
        for kind in ('script','outline','structure','review'):
            if value.get(kind+'_sha256')!=arts.get(kind,{}).get('sha256'): issues.append(kind+' 审稿绑定已过期')
        return issues
    def _media_review_status_errors(self,ep,value):
        """Review-only delivery can retain honest owner audition pending status."""
        errors=[]; checks=value.get('checks',{})
        pending=value.get('verdict')=='pending_owner_review' or (value.get('verdict')=='revise' and value.get('checks',{}).get('narration',{}).get('status')=='pending_owner_review')
        if value.get('verdict') not in ('pass','revise','pending_owner_review'):
            errors.append('verdict 必须是 pass、revise 或 pending_owner_review')
        if pending:
            if not self.episode(ep).get('review_only'): errors.append('pending_owner_review 仅限 review_only 交审')
            owner=value.get('owner_review',{})
            if not isinstance(owner,dict) or any(owner.get(k)!=v for k,v in {'status':'pending_owner_review','human_listening':'not_performed','prosody':'not_assessed'}.items()):
                errors.append('owner_review 必须如实保留待主人听审、未实际听审及韵律未评估')
        if not isinstance(checks,dict): return errors+['checks 必须是对象']
        for check in ('narration','subtitles','visuals','ending','promise'):
            item=checks.get(check,{})
            allowed=('pending_owner_review',) if pending and check=='narration' else ('pass','revise')
            if not isinstance(item,dict) or item.get('status') not in allowed or any(not isinstance(item.get(k),str) or not item[k].strip() for k in ('reason','evidence')):
                errors.append('审片 '+check+' 需要匹配status、非空reason和实际审查evidence')
                continue
            if pending and check=='narration':
                machine=item.get('machine_checks',{})
                for name in ('asr','forced_alignment'):
                    evidence=machine.get(name,{}) if isinstance(machine,dict) else {}
                    if not isinstance(evidence,dict) or evidence.get('status')!='pass' or any(not isinstance(evidence.get(k),str) or not evidence[k].strip() for k in ('reason','evidence')):
                        errors.append('待听审旁白仍需 '+name+' 独立技术审查通过及真实证据')
        if value.get('verdict') in ('pass','pending_owner_review') and any(isinstance(checks.get(k),dict) and checks[k].get('status')=='revise' for k in ('narration','subtitles','visuals','ending','promise')):
            errors.append('存在 revise 审片项，不能通过或交审')
        return errors
    def media_review_errors(self,ep):
        row=self.db.execute('SELECT data FROM media_reviews WHERE episode_id=?',(ep,)).fetchone()
        if not row: return ['缺少独立最终审片记录']
        value=json.loads(row['data']); arts=self.artifacts(ep); errors=[]
        errors+=self._media_review_status_errors(ep,value)
        if value.get('verdict') not in ('pass','pending_owner_review'): errors.append('最终审片要求修改')
        receipt=self.root/value.get('receipt_path','missing')
        if not receipt.is_file() or digest(receipt)!=value.get('receipt_sha256'): errors.append('最终审片回执丢失或哈希不符')
        for kind in ('script','audio','video','subtitles','cover','metadata'):
            if kind not in arts or value.get('hashes',{}).get(kind)!=arts[kind]['sha256']: errors.append(kind+' 最终审片绑定已过期')
        return errors
    def media_review(self,ep,file):
        episode=self.episode(ep); value=read_json(file); arts=self.artifacts(ep)
        if self.verify_hashes(ep): raise WorkflowError('文件已改变，请先重新登记')
        if value.get('episode_id')!=ep: raise WorkflowError('审片 episode_id 不匹配')
        reviewer=value.get('reviewer')
        if not isinstance(reviewer,str) or not reviewer.strip() or reviewer.strip().casefold()==episode['author'].strip().casefold(): raise WorkflowError('reviewer 必须明确且与 author 不同')
        status_errors=self._media_review_status_errors(ep,value)
        if status_errors: raise WorkflowError('；'.join(status_errors))
        hashes=value.get('hashes',{})
        if not isinstance(hashes,dict): raise WorkflowError('hashes 必须是对象')
        for kind in ('script','audio','video','subtitles','cover','metadata'):
            if kind not in arts or hashes.get(kind)!=arts[kind]['sha256']: raise WorkflowError('审片 hashes.'+kind+' 必须匹配已登记产物')
        sha=digest(file); receipt_dir=self.root/'episodes'/ep/'media-reviews'
        version=max([int(p.name.split('-',1)[0]) for p in receipt_dir.glob('[0-9]*-*.json')] or [0])+1
        path=receipt_dir/(str(version)+'-'+sha+'.json'); atomic_json(path,value)
        value.update(version=version,source_sha256=sha,receipt_sha256=digest(path),receipt_path=str(path.relative_to(self.root)),recorded_at=utcnow())
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO media_reviews VALUES(?,?)',(ep,json.dumps(value,ensure_ascii=False)))
            self.invalidate(ep)
        return value
    def _json_artifact(self,arts,kind): return read_json(self.root/arts[kind]['path'])
    def structural_errors(self,ep):
        arts=self.artifacts(ep); errors=[]
        if 'outline' in arts:
            try:
                v=self._json_artifact(arts,'outline')
                for key in ('premise','promise','ending','beats'):
                    if not v.get(key): errors.append('outline 缺少 '+key)
                if not isinstance(v.get('beats'),list): errors.append('outline.beats 必须是列表')
            except WorkflowError as e: errors.append(str(e))
        if 'structure' in arts:
            try:
                v=self._json_artifact(arts,'structure')
                for key in ('desire','conflict','stakes','payoff','ending','characters','world','continuity'):
                    if key not in v or v[key] in ('',None): errors.append('structure 缺少 '+key)
            except WorkflowError as e: errors.append(str(e))
        return errors
    def qa_step(self,ep,step):
        arts=self.artifacts(ep); issues=self.verify_hashes(ep)
        if step=='outline':
            if 'outline' not in arts: issues.append('缺少 outline')
            issues+=self.structural_errors(ep)
        elif step=='script':
            for kind in ('outline','script','structure'):
                if kind not in arts: issues.append('缺少 '+kind)
            issues+=self.structural_errors(ep)
        elif step=='review': issues+=self.review_errors(ep)
        elif step=='assets': issues+=self.validate(ep,persist=False)['errors']
        else: raise WorkflowError('无效 QA 步骤')
        value={'episode_id':ep,'step':step,'passed':not issues,'errors':issues,'checked_at':utcnow(),'scope':'deterministic_checks_only'}
        if step=='outline': value['independent_outline_review_errors']=self.outline_review_errors(ep)
        if not issues:
            with self.db: self.db.execute('INSERT OR REPLACE INTO qa VALUES(?,?,?)',(ep,step,json.dumps(value)))
        return value
    def _probe(self,path,expected):
        try:
            run=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],capture_output=True,text=True,timeout=30)
        except (OSError,subprocess.TimeoutExpired) as e: raise WorkflowError('ffprobe 不可用：'+str(e))
        if run.returncode: raise WorkflowError('ffprobe 无法解析 '+expected)
        try: value=json.loads(run.stdout)
        except ValueError: raise WorkflowError('ffprobe 返回无效 JSON')
        streams=value.get('streams',[])
        if not any(s.get('codec_type')==expected for s in streams): raise WorkflowError('缺少 '+expected+' 流')
        try: duration=float(value.get('format',{}).get('duration',0))
        except (ValueError,TypeError): duration=0
        if not math.isfinite(duration) or duration<=0: raise WorkflowError(expected+' 时长无效')
        return {'duration':duration,'streams':streams}
    def render_binding_errors(self,ep):
        """手工视频沿用独立审片；CLI渲染有绑定时必须完整且仍有效。"""
        binding=self.root/'episodes'/ep/'production'/'render.binding.json'
        if not binding.exists() and not binding.is_symlink():
            return ['缺少强制视觉与渲染绑定'] if self.config.get('quality',{}).get('require_visual_manifest') or self.episode(ep).get('style_resolution_required') else []
        try:
            if binding.is_symlink(): raise WorkflowError('渲染绑定不能是符号链接')
            value=read_json(binding); arts=self.artifacts(ep)
            chapter=binding.parent/'chapter-revision.binding.json'
            revision=None
            if chapter.exists():
                from .chapter_mix import verify_revision
                revision=verify_revision(self,ep)
            derived=binding.parent/'burned.binding.json'
            if derived.exists() and read_json(derived).get('output_sha256')==arts.get('video',{}).get('sha256'):
                from .burn_ops import verify_derivation
                value=verify_derivation(self,ep,read_json(derived),binding)
            elif revision:
                if revision['delivery']['video']['sha256']!=arts.get('video',{}).get('sha256'):raise WorkflowError('章节派生与当前视频不符')
            elif value.get('episode_id')!=ep or value.get('artifact',{}).get('sha256')!=arts.get('video',{}).get('sha256') or 'video' not in arts:
                raise WorkflowError('渲染绑定与当前视频不符')
            plan=value['inputs']; manifest=plan['scene_manifest']; source=manifest['source']
            if self.config.get('quality',{}).get('require_semantic_caption_units'):
                from .captions import verify_store_caption_binding
                current=verify_store_caption_binding(self,ep,arts,require_semantic=True)['contract']
                if plan.get('caption_contract')!=current: raise WorkflowError('字幕语义消费合同与渲染绑定不符')
            visual=self.visual_contract(ep)
            from .scene_cleanup import scene_identity,normalize_cleanup
            if visual and (plan.get('visual_contract')!=visual or visual['scenes']!=[scene_identity(s,self.root) for s in plan['scenes']]):
                raise WorkflowError('角色参考/视觉审查/镜头图像与渲染绑定不符')
            if value.get('scene_clips'):
                from .scene_assembly import load_manifest
                clip_manifest=load_manifest(value['scene_clips'])
                if clip_manifest['scene_manifest']!=manifest or clip_manifest.get('artifact_hashes')!=plan['artifact_hashes'] or clip_manifest.get('visual_contract')!=plan.get('visual_contract'):raise WorkflowError('whole scene clip/render contract mismatch')
            expected=(self.root/'episodes'/ep/'production'/'scenes.json') if source=='production' else (self.root/arts['structure']['path']) if source=='structure' else None
            if expected is None or Path(manifest['path'])!=expected or expected.is_symlink() or (source=='production' and expected.resolve()!=expected) or not expected.resolve().is_relative_to(self.root) or digest(expected)!=manifest['sha256']:
                raise WorkflowError('镜头清单绑定已过期或路径无效')
            if source=='structure' and (self.root/'episodes'/ep/'production'/'scenes.json').exists():
                raise WorkflowError('新增技术镜头清单后必须重新渲染')
            for kind in ('structure','script','audio','subtitles'):
                if plan['artifact_hashes'].get(kind)!=arts[kind]['sha256']: raise WorkflowError('渲染输入绑定已过期：'+kind)
            if not isinstance(plan['scenes'],list) or not plan['scenes']: raise WorkflowError('渲染镜头绑定缺失')
            for scene in plan['scenes']:
                image=Path(scene['image'])
                if not image.resolve().is_relative_to(self.root) or digest(image)!=scene['sha256']: raise WorkflowError('渲染图片绑定已过期')
                if normalize_cleanup(scene,digest(image)) and not visual: raise WorkflowError('cleanup缺少视觉交付合同')
        except (WorkflowError,OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
            return ['渲染绑定无效：'+str(exc)]
        return []
    def visual_contract(self, ep):
        path=self.root/'episodes'/ep/'production'/'visual-manifest.json'
        required=self.config.get('quality',{}).get('require_visual_manifest',False) or self.episode(ep).get('style_resolution_required',False)
        if not required and not path.exists(): return None
        if path.is_symlink() or not path.is_file() or path.resolve()!=path:
            raise WorkflowError('缺少本集实体视觉合同，禁止渲染/发布')
        from .visuals import validate_manifest
        value=read_json(path)
        from .style_binding import snapshot_for_episode,check_binding,check_plan
        snapshot=snapshot_for_episode(self,ep)
        check_binding(value,snapshot,'manifest')
        if snapshot is not None and value.get('style_snapshot')!=snapshot:raise WorkflowError('manifest snapshot differs from episode')
        result=validate_manifest(value,self.root)
        if not result['passed']: raise WorkflowError('视觉合同未通过：'+'；'.join(result['errors']))
        from .scene_cleanup import scene_identity,normalize_cleanup
        contract={'path':str(path),'sha256':digest(path),'scenes':[scene_identity(s,self.root) for s in value['scenes']]}
        if value.get('scene_timeline'):
            contract['scene_timeline']=value['scene_timeline']
        plan_path=path.parent/'visual-plan.json'
        plan=read_json(plan_path) if plan_path.exists() else {}
        if snapshot is not None:check_plan(plan,snapshot)
        if snapshot is not None:contract['style_snapshot']=snapshot
        from .visual_prompt import check_prompt
        plan_rows={s['id']:s for s in plan.get('scenes',[])}
        for row in plan.get('scenes',[]):
            check_prompt(row.get('prompt',''));check_prompt(row.get('video_prompt',''))
        for row in value['scenes']:
            if row.get('historical_source_record') and row.get('current_prompt')!=plan_rows.get(row['id'],{}).get('prompt'):
                raise WorkflowError('历史复用current_prompt与当前visual-plan不一致')
        if any('caption_band_cleanup' in s for s in value['scenes']+plan.get('scenes',[])):
            rows={s['id']:s for s in plan.get('scenes',[])}
            for scene in value['scenes']:
                expected=rows.get(scene['id'],{})
                if normalize_cleanup(scene,scene['sha256'])!=normalize_cleanup(expected,scene['sha256']):
                    raise WorkflowError('caption_band_cleanup与visual-plan声明不一致')
            if set(rows)!={s['id'] for s in value['scenes']}: raise WorkflowError('cleanup视觉镜头与plan不一致')
            contract['cleanup_plan']={'path':str(plan_path),'sha256':digest(plan_path)}
        return contract
    def validate(self,ep,persist=True):
        episode=self.episode(ep); arts=self.artifacts(ep); errors=self.verify_hashes(ep)+self.render_binding_errors(ep)
        from .style_binding import snapshot_for_episode
        try:snapshot_for_episode(self,ep)
        except WorkflowError as exc:errors.append(str(exc))
        if episode.get('editorial_status')=='withdrawn': errors.append('使用者已退稿，禁止发布')
        errors += ['缺少 '+k for k in KINDS if k not in arts]
        errors += self.review_errors(ep)+self.structural_errors(ep)
        for kind in ('audio','video','subtitles','cover','metadata'):
            if kind in arts:
                inputs=json.loads(arts[kind].get('input_hashes') or '{}')
                for dependency in (('outline','script','structure','audio') if kind in ('video','subtitles') else ('outline','script','structure')):
                    if dependency not in arts or inputs.get(dependency)!=arts[dependency]['sha256']:
                        errors.append(kind+' 对 '+dependency+' 的输入绑定缺失或过期')
        quality=self.config.get('quality',{})
        verification_only=episode.get('verification_only',False)
        if not verification_only: errors+=self.media_review_errors(ep)
        if not verification_only and 'script' in arts:
            try:
                text=(self.root/arts['script']['path']).read_text(encoding='utf-8')
                if len(''.join(text.split()))<int(quality.get('min_script_chars',2500)): errors.append('正式故事脚本文字数低于最低门槛')
            except (OSError,UnicodeError) as e: errors.append('脚本不能按UTF-8读取：'+str(e))
        probes={}
        delivery=None
        try:
            from .chapter_mix import delivery_inputs
            delivery=delivery_inputs(self,ep) if all(k in arts for k in ('audio','subtitles')) else None
        except (WorkflowError,OSError,ValueError,KeyError,TypeError) as exc:errors.append('章节交付绑定无效：'+str(exc))
        if not self.verify_hashes(ep):
            for kind,expected in (('audio','audio'),('video','video')):
                if kind in arts:
                    try: probes[kind]=self._probe(delivery['audio']['path'] if kind=='audio' and delivery else self.root/arts[kind]['path'],expected)
                    except WorkflowError as e: errors.append(str(e))
            if not verification_only:
                for kind,probe in probes.items():
                    if probe['duration']<float(quality.get('min_duration_seconds',600)): errors.append(kind+' 低于正式故事最低时长')
            if 'video' in probes and not any(s.get('codec_type')=='audio' for s in probes['video']['streams']): errors.append('成片缺少音频流')
            if 'audio' in probes and 'video' in probes and abs(probes['audio']['duration']-probes['video']['duration'])>2: errors.append('配音与成片时长相差超过 2 秒')
            if 'subtitles' in arts:
                if quality.get('require_semantic_caption_units'):
                    try:
                        from .captions import verify_store_caption_binding
                        verify_store_caption_binding(self,ep,arts,require_semantic=True)
                    except ValueError as e: errors.append('字幕消费绑定无效：'+str(e))
                try:
                    cues=parse_srt(Path(delivery['subtitles']['path']).read_text(encoding='utf-8-sig') if delivery else (self.root/arts['subtitles']['path']).read_text(encoding='utf-8-sig'))
                    if 'video' in probes and cues[-1][1]>probes['video']['duration']+0.5: errors.append('字幕超出成片时长')
                except (WorkflowError,UnicodeError,OSError) as e: errors.append(str(e))
            if 'cover' in arts:
                try: validate_cover(self.root/arts['cover']['path'])
                except WorkflowError as e: errors.append(str(e))
            if 'metadata' in arts:
                try:
                    v=self._json_artifact(arts,'metadata')
                    for key in ('title','description'):
                        if not isinstance(v.get(key),str) or not v[key].strip(): errors.append('metadata 缺少 '+key)
                    if v.get('episode_id')!=ep or v.get('channel')!=episode['channel'] or v.get('scheduled_at')!=episode['scheduled_at']: errors.append('metadata 身份或排期不符')
                except WorkflowError as e: errors.append(str(e))
        result={'episode_id':ep,'verification_only':verification_only,'passed':not errors,'errors':list(dict.fromkeys(errors)),'checked_at':utcnow(),'scope':'file_integrity_media_structure_and_review_binding; human_quality_requires_independent_review','artifact_hashes':{k:v['sha256'] for k,v in arts.items()},'media':{k:{'duration':v['duration']} for k,v in probes.items()}}
        media_review_row=self.db.execute('SELECT data FROM media_reviews WHERE episode_id=?',(ep,)).fetchone()
        if media_review_row:
            media_review_value=json.loads(media_review_row['data'])
            if media_review_value.get('verdict')=='pending_owner_review':
                result.update(review_only=bool(episode.get('review_only')),owner_review=media_review_value.get('owner_review'),delivery_scope='owner_review_only; subjective_listening_not_performed; publication_forbidden')
        if persist:
            with self.db: self.db.execute('INSERT OR REPLACE INTO validations VALUES(?,?)',(ep,json.dumps(result)))
        return result
    def next(self,ep):
        if self.episode(ep).get('editorial_status')=='withdrawn': return {'episode_id':ep,'next':'withdrawn','reason':self.episode(ep)['withdrawal_reason'],'missing':[]}
        arts=self.artifacts(ep); missing=[k for k in KINDS if k not in arts]
        pub=self.db.execute('SELECT data FROM publications WHERE episode_id=?',(ep,)).fetchone()
        if pub: return {'episode_id':ep,'next':'observe_publication','publication':json.loads(pub['data']),'missing':missing}
        if self.verify_hashes(ep): action='repair_artifact_integrity'
        elif 'outline' not in arts: action='produce_outline'
        elif 'structure' not in arts: action='produce_structure'
        elif self.structural_errors(ep): action='repair_outline_structure'
        elif self.outline_review_errors(ep): action='independent_outline_review'
        elif 'script' not in arts: action='produce_script'
        elif self.review_errors(ep): action='independent_review'
        else:
            action=None
            for kind in ('audio','subtitles','cover','metadata','video'):
                if kind not in arts: action='produce_'+kind; break
            if action is None:
                if not self.episode(ep).get('verification_only') and self.media_review_errors(ep): action='independent_media_review'
                else:
                    v=self.validate(ep,persist=False); action='ready_for_upload' if v['passed'] else 'repair_validation_errors'
        return {'episode_id':ep,'next':action,'missing':missing,'review_errors':self.review_errors(ep),'integrity_errors':self.verify_hashes(ep)}
    def acquire(self,ep,owner,ttl=3600):
        self.episode(ep)
        if not owner.strip() or not 1<=ttl<=86400: raise WorkflowError('owner 必填，ttl 在 1–86400 秒之间')
        now=dt.datetime.now(dt.timezone.utc); expires=(now+dt.timedelta(seconds=ttl)).isoformat()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT * FROM claims WHERE episode_id=?',(ep,)).fetchone()
            if row and timestamp(row['expires_at'])>now and row['owner']!=owner: raise WorkflowError('故事已被其他执行者锁定',409)
            self.db.execute('INSERT OR REPLACE INTO claims VALUES(?,?,?)',(ep,owner,expires))
        return {'episode_id':ep,'owner':owner,'expires_at':expires}
    def release(self,ep,owner):
        with self.db:
            row=self.db.execute('SELECT owner FROM claims WHERE episode_id=?',(ep,)).fetchone()
            if row and row['owner']!=owner: raise WorkflowError('只能释放自己的 claim',409)
            self.db.execute('DELETE FROM claims WHERE episode_id=?',(ep,))
        return {'released':True,'episode_id':ep}
    def publication(self,ep,file,*,verified=False):
        if not verified:
            if self.episode(ep).get('review_only'): raise WorkflowError('review_only 永久禁止公开发布',409)
            if self.episode(ep).get('verification_only'): raise WorkflowError('verification_only 测试故事永久禁止公开发布',409)
            raise WorkflowError('发布记录必须经平台回读；请使用 verify 或 publication record 核实线索',409)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            return self._publication(ep,file)
    def _publication(self,ep,file):
        episode=self.episode(ep)
        if episode.get('review_only'): raise WorkflowError('review_only 永久禁止公开发布',409)
        value=read_json(file)
        if episode.get('editorial_status')=='withdrawn': raise WorkflowError('已退稿故事禁止登记发布',409)
        if episode.get('verification_only'): raise WorkflowError('verification_only 测试故事永久禁止公开发布',409)
        for key in ('episode_id','channel','scheduled_at'):
            if value.get(key)!=episode.get('id' if key=='episode_id' else key): raise WorkflowError('发布收据 '+key+' 不符',409)
        expected_channel_id=self.config['channel'].get('id')
        if not isinstance(expected_channel_id,str) or not expected_channel_id.strip(): raise WorkflowError('未配置真实 YouTube channel.id，禁止登记正式发布',409)
        if value.get('channel_id')!=expected_channel_id: raise WorkflowError('发布收据 YouTube channel_id 不符',409)
        if value.get('status') not in ('accepted','scheduled','public'): raise WorkflowError('收据 status 仅接受 accepted/scheduled/public')
        if not isinstance(value.get('video_id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}',value['video_id']): raise WorkflowError('收据必须含有效的 11 位 YouTube video_id')
        if value.get('status')=='public':
            actual=timestamp(value.get('actual_public_at'))
            if actual<timestamp(episode['scheduled_at']) or actual>dt.datetime.now(dt.timezone.utc): raise WorkflowError('真实公开时间早于排期或晚于当前时刻',409)
        for other in self.db.execute('SELECT episode_id,data FROM publications WHERE episode_id<>?',(ep,)):
            if json.loads(other['data']).get('video_id')==value['video_id']: raise WorkflowError('video_id 已关联其他故事',409)
        old=self.db.execute('SELECT data FROM publications WHERE episode_id=?',(ep,)).fetchone()
        if old:
            existing=json.loads(old['data'])
            if existing==value: return {'idempotent':True,'receipt':existing}
            if existing.get('video_id')!=value['video_id']: raise WorkflowError('已接受上传，禁止重复上传或更换 video_id',409)
            rank={'accepted':0,'scheduled':1,'public':2}
            if rank[value['status']]<rank[existing['status']]: raise WorkflowError('发布状态不能回退',409)
            if existing.get('actual_public_at') and existing.get('actual_public_at')!=value.get('actual_public_at'): raise WorkflowError('真实公开时间冲突',409)
            if existing['status']==value['status']: raise WorkflowError('同状态收据内容冲突',409)
        else:
            validation=self.validate(ep,persist=False)
            if not validation['passed']: raise WorkflowError('验证未通过，不能记录发布收据：'+'；'.join(validation['errors']))
        self.db.execute('INSERT OR REPLACE INTO publications VALUES(?,?)',(ep,json.dumps(value,ensure_ascii=False)))
        return {'idempotent':False,'receipt':value}
    def metric(self,ep,file):
        self.episode(ep); value=read_json(file)
        actual=timestamp(value.get('actual_public_at')); captured=timestamp(value.get('captured_at'))
        if captured<actual: raise WorkflowError('采样时间早于真实公开时间')
        if value.get('episode_id')!=ep: raise WorkflowError('metrics episode_id 不符')
        metrics=value.get('metrics')
        if not isinstance(metrics,dict) or not metrics: raise WorkflowError('metrics 必须是非空对象；未知值使用 null')
        for key,v in metrics.items():
            if v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0): raise WorkflowError('指标必须是非负实数或 null：'+key)
        pub=self.db.execute('SELECT data FROM publications WHERE episode_id=?',(ep,)).fetchone()
        if not pub: raise WorkflowError('尚无正式 public 发布收据，不能记录真实指标',409)
        p=json.loads(pub['data'])
        if p.get('status')!='public' or not p.get('actual_public_at'): raise WorkflowError('发布收据尚未确认公开',409)
        if timestamp(p['actual_public_at'])!=actual: raise WorkflowError('真实公开时间与收据不符',409)
        value['hours_since_public']=(captured-actual).total_seconds()/3600
        value['eligible_for_retro']=value['hours_since_public']>=72
        with self.db: cur=self.db.execute('INSERT INTO metrics(episode_id,data) VALUES(?,?)',(ep,json.dumps(value)))
        return {'id':cur.lastrowid,**value}
    def episode_budget_cap(self,ep):
        """Explicit per-episode owner authority; defaults and monthly cap stay independent."""
        self.episode(ep)
        budget=self.config['budget']; overrides=budget.get('episode_overrides',{})
        if not isinstance(overrides,dict): raise WorkflowError('budget.episode_overrides must be an object')
        if ep not in overrides: return budget['per_episode_usd']
        value=overrides[ep]
        if not isinstance(value,dict): raise WorkflowError('episode budget override must bind owner evidence')
        cap=value.get('cap_usd'); path=value.get('owner_evidence_path'); sha=value.get('owner_evidence_sha256')
        if isinstance(cap,bool) or not isinstance(cap,(int,float)) or not math.isfinite(cap) or cap<=0: raise WorkflowError('invalid episode budget cap')
        if not isinstance(path,str) or not isinstance(sha,str): raise WorkflowError('episode budget owner evidence required')
        evidence=(self.root/path).resolve()
        if not evidence.is_relative_to(self.root) or not evidence.is_file() or digest(evidence)!=sha: raise WorkflowError('episode budget owner evidence hash/path mismatch')
        if value.get('actor')!='owner' or not isinstance(value.get('approved_at'),str): raise WorkflowError('episode budget owner approval actor/time required')
        timestamp(value['approved_at'])
        return cap

    def cost(self,ep,amount,category):
        self.episode(ep)
        if not math.isfinite(amount) or amount<=0 or not category.strip(): raise WorkflowError('费用需为正实数，category 必填')
        now=utcnow(); month=now[:7]
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            per=self.db.execute('SELECT COALESCE(SUM(amount),0) FROM costs WHERE episode_id=?',(ep,)).fetchone()[0]
            total=self.db.execute('SELECT COALESCE(SUM(amount),0) FROM costs WHERE substr(created_at,1,7)=?',(month,)).fetchone()[0]
            budget=self.config['budget']
            if per+amount>self.episode_budget_cap(ep) or total+amount>budget['monthly_usd']: raise WorkflowError('预算上限已触发',409)
            self.db.execute('INSERT INTO costs(episode_id,amount,category,created_at) VALUES(?,?,?,?)',(ep,amount,category,now))
        return {'episode_id':ep,'amount_usd':amount,'episode_total_usd':per+amount,'month_total_usd':total+amount,'category':category}
    def idea_add(self,title,body=''):
        if not title.strip(): raise WorkflowError('提案标题不能为空')
        id='idea-'+uuid.uuid4().hex[:12]; value={'id':id,'title':title,'body':body,'created_at':utcnow()}
        with self.db: self.db.execute('INSERT INTO ideas VALUES(?,?,NULL)',(id,json.dumps(value,ensure_ascii=False)))
        return value
    def idea_list(self): return [{**json.loads(r['data']),'episode_id':r['episode_id']} for r in self.db.execute('SELECT * FROM ideas')]
    def idea_use(self,id,ep):
        self.episode(ep)
        with self.db:
            row=self.db.execute('SELECT episode_id FROM ideas WHERE id=?',(id,)).fetchone()
            if not row: raise WorkflowError('提案不存在',404)
            if row['episode_id'] and row['episode_id']!=ep: raise WorkflowError('提案已用于其他故事',409)
            self.db.execute('UPDATE ideas SET episode_id=? WHERE id=?',(ep,id))
        return {'id':id,'episode_id':ep}
    def memory_add(self,kind,file):
        if kind not in ('source','character','world','plot','lesson'): raise WorkflowError('无效记忆种类')
        value=read_json(file); id=value.get('id')
        if not isinstance(id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}',id): raise WorkflowError('记忆需要稳定 id')
        if not isinstance(value.get('text'),str) or not value['text'].strip(): raise WorkflowError('记忆需要非空 text')
        payload=json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')); sha=hashlib.sha256(payload.encode()).hexdigest()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            old=self.db.execute('SELECT * FROM facts WHERE id=? ORDER BY version DESC LIMIT 1',(id,)).fetchone()
            if old and old['kind']!=kind: raise WorkflowError('记忆 ID 的种类不能改变',409)
            if old and old['sha256']==sha: return {'id':id,'version':old['version'],'sha256':sha,'idempotent':True}
            version=(old['version'] if old else 0)+1
            self.db.execute('INSERT INTO facts VALUES(?,?,?,?,?,?)',(id,version,kind,payload,sha,utcnow()))
            self.db.execute('DELETE FROM vectors WHERE id=?',(id,))
        return {'id':id,'version':version,'sha256':sha,'kind':kind}
    def memory_list(self,kind=None):
        rows=self.db.execute('SELECT f.* FROM facts f JOIN (SELECT id,MAX(version) v FROM facts GROUP BY id) l ON f.id=l.id AND f.version=l.v ORDER BY f.id')
        return [{'id':r['id'],'version':r['version'],'kind':r['kind'],'sha256':r['sha256'],'data':json.loads(r['data'])} for r in rows if kind is None or r['kind']==kind]
    def memory_show(self,id,version=None):
        if version is None: row=self.db.execute('SELECT * FROM facts WHERE id=? ORDER BY version DESC LIMIT 1',(id,)).fetchone()
        else: row=self.db.execute('SELECT * FROM facts WHERE id=? AND version=?',(id,version)).fetchone()
        if not row: raise WorkflowError('记忆或指定版本不存在',404)
        return {'id':row['id'],'version':row['version'],'kind':row['kind'],'sha256':row['sha256'],'data':json.loads(row['data'])}
    def vector_status(self):
        return {'backend':'external_embedding_cosine','embedding_generation':'not_configured; import externally generated vectors','models':[dict(r) for r in self.db.execute('SELECT model,dimensions,COUNT(*) AS documents FROM vectors GROUP BY model,dimensions')],'facts':len(self.memory_list()),'vectors':self.db.execute('SELECT COUNT(*) FROM vectors').fetchone()[0]}
    def search(self,query):
        if not query.strip(): raise WorkflowError('检索词不能为空')
        docs=[]
        for e in self.episodes():
            docs.append({'id':e['id'],'kind':'episode','text':json.dumps(e,ensure_ascii=False)})
            for kind,a in self.artifacts(e['id']).items():
                if kind=='structure':
                    try: docs.append({'id':e['id'],'kind':'structure','text':(self.root/a['path']).read_text()})
                    except (OSError,UnicodeError): pass
        for i in self.idea_list(): docs.append({'id':i['id'],'kind':'idea','text':i['title']+' '+i.get('body','')})
        for r in self.db.execute('SELECT f.* FROM facts f JOIN (SELECT id,MAX(version) v FROM facts GROUP BY id) l ON f.id=l.id AND f.version=l.v'):
            docs.append({'id':r['id'],'kind':r['kind'],'version':r['version'],'sha256':r['sha256'],'text':json.loads(r['data'])['text']})
        tokens=query.casefold().split(); results=[]
        for d in docs:
            score=sum(d['text'].casefold().count(t) for t in tokens)
            if score: results.append({**d,'score':score})
        return {'mode':'lexical','query':query,'results':sorted(results,key=lambda d:-d['score'])[:30]}
    def vector_import(self,file):
        v=read_json(file)
        for key in ('id','model','version','source_hash'):
            if not isinstance(v.get(key),str) or not v[key].strip(): raise WorkflowError('向量需要 '+key)
        values=vector_values(v.get('vector'))
        row=self.db.execute('SELECT * FROM facts WHERE id=? ORDER BY version DESC LIMIT 1',(v['id'],)).fetchone()
        if not row or row['sha256']!=v['source_hash']: raise WorkflowError('向量 source_hash 与最新事实不符',409)
        if str(row['version'])!=v['version']: raise WorkflowError('向量 version 必须匹配事实版本',409)
        with self.db: self.db.execute('INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?)',(v['id'],v['model'],v['version'],v['source_hash'],len(values),json.dumps(values)))
        return {'id':v['id'],'model':v['model'],'version':v['version'],'dimensions':len(values),'source_hash':v['source_hash']}
    def vector_search(self,file):
        q=read_json(file); values=vector_values(q.get('vector')); model=q.get('model')
        if not isinstance(model,str) or not model.strip(): raise WorkflowError('查询需要明确 embedding model')
        result=[]
        for row in self.db.execute('SELECT * FROM vectors WHERE model=? AND dimensions=?',(model,len(values))):
            fact=self.db.execute('SELECT * FROM facts WHERE id=? ORDER BY version DESC LIMIT 1',(row['id'],)).fetchone()
            if not fact or row['source_hash']!=fact['sha256'] or row['version']!=str(fact['version']): continue
            v=json.loads(row['data']); left_scale=max(map(abs,values)); right_scale=max(map(abs,v))
            left=[a/left_scale for a in values]; right=[b/right_scale for b in v]
            score=sum(a*b for a,b in zip(left,right))/(math.hypot(*left)*math.hypot(*right))
            result.append({'id':row['id'],'kind':fact['kind'],'version':fact['version'],'source_hash':fact['sha256'],'score':score,'fact':json.loads(fact['data'])})
        return {'mode':'external_embedding_cosine','model':model,'dimensions':len(values),'results':sorted(result,key=lambda i:-i['score'])[:20]}
    def status(self):
        return {'channel':self.channel(),'database':str(self.state/'story.sqlite3'),'episodes':[self.next(e['id']) for e in self.episodes()],'ideas':len(self.idea_list()),'claims':[dict(r) for r in self.db.execute('SELECT * FROM claims')],'cost_or_reserved_usd':self.db.execute('SELECT COALESCE(SUM(amount),0) FROM costs').fetchone()[0],'budget_note':'包含 reservation 类保守预留，不代表实际账单'}

def vector_values(value):
    if not isinstance(value,list) or not value or len(value)>100000: raise WorkflowError('vector 需要非空实数数组')
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in value): raise WorkflowError('vector 只能包含有限实数')
    if not any(value): raise WorkflowError('零向量无法进行余弦检索')
    return value

def parse_srt(text):
    blocks=re.split(r'\n\s*\n',text.replace('\r\n','\n').strip()); result=[]; last=0
    for n,block in enumerate(blocks,1):
        lines=block.splitlines()
        if len(lines)<3 or lines[0].strip()!=str(n): raise WorkflowError('SRT 序号或正文无效')
        m=re.fullmatch(r'(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})',lines[1].strip())
        if not m: raise WorkflowError('SRT 时间格式无效')
        nums=list(map(int,m.groups()))
        if nums[1]>59 or nums[2]>59 or nums[5]>59 or nums[6]>59: raise WorkflowError('SRT 分秒越界')
        start=nums[0]*3600+nums[1]*60+nums[2]+nums[3]/1000; end=nums[4]*3600+nums[5]*60+nums[6]+nums[7]/1000
        if start<last or end<=start or not ''.join(lines[2:]).strip(): raise WorkflowError('SRT 时间倒退、重叠或正文为空')
        result.append((start,end)); last=end
    if not result: raise WorkflowError('SRT 为空')
    return result

def validate_cover(path):
    try:
        r=subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(path)],capture_output=True,text=True,timeout=20)
        streams=json.loads(r.stdout).get('streams',[])
        if r.returncode or not streams: raise ValueError('图像无法解码')
        s=streams[0]; w=s.get('width',0); h=s.get('height',0)
        if w<640 or h<360 or abs(w/h-16/9)>0.05: raise ValueError('封面需要至少 640×360 的 16:9 图像')
        if s.get('codec_name') not in ('png','mjpeg','webp'): raise ValueError('封面仅支持 PNG/JPEG/WebP')
    except (ValueError,OSError,subprocess.TimeoutExpired,ZeroDivisionError) as e: raise WorkflowError('封面验证失败：'+str(e))
