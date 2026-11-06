"""Public editorial preparation used by the web UI and offline evaluations alike."""
import json
import threading
import time

from cf_editorials import CFEditorialProvider, EditorialError, AccessRestricted, Cancelled
from recommendation_pool import PoolError
from training_archive import signature
from material_quality import has_editorial, has_code



class EditorialService:
    def __init__(self, connect, lock, provider=None, clock=time.time, pool=None):
        self.connect, self.db_lock, self.clock = connect, lock, clock
        self.provider = provider or CFEditorialProvider()
        self.lock = threading.RLock()
        self.jobs, self.cancels = {}, {}
        self.pool = pool

    @staticmethod
    def init_tables(c):
        c.execute('''CREATE TABLE IF NOT EXISTS training_editorial_attempts(
            problem_id TEXT PRIMARY KEY,status TEXT NOT NULL,payload TEXT NOT NULL,updated_at INTEGER NOT NULL)''')

    def status(self, uid):
        with self.lock:
            return dict(self.jobs.get(uid, {'status':'idle'}))

    def cancel(self, uid):
        with self.lock:
            event = self.cancels.get(uid)
            if event:
                event.set()
            return self.status(uid)

    def start(self, uid):
        with self.lock:
            if self.jobs.get(uid,{}).get('status') == 'running':
                return self.status(uid)
            if any(j['status']=='running' for j in self.jobs.values()):
                raise PoolError('正在准备其他题解，请稍后重试。',409)
            with self.connect() as c:
                rows = c.execute("SELECT payload FROM smart_pool_entries WHERE user_id=? AND status='candidate' ORDER BY position",(uid,)).fetchall()
            entries = [json.loads(r[0]) for r in rows]
            if not entries and (self.pool is None or not self.pool.state(uid)['prepared']):
                raise PoolError('请先准备候选池。')
            self.cancels[uid] = threading.Event()
            self.jobs[uid] = {'status':'running','total':len(entries),'processed':0,'acquired':0,
                              'cached':0,'failed':0,'deferred':0,'partial':0,'codeReady':0,'linksOnly':0,'startedAt':int(self.clock())}
            threading.Thread(target=self._run,args=(uid,entries,self.cancels[uid]),daemon=True).start()
            return self.status(uid)

    def _update(self, uid, **fields):
        with self.lock:
            self.jobs[uid].update(fields)

    def prepare_one(self, uid, key, cancel=None, allow_reserve=False):
        """Resolve only a user's candidate; failed attempts never overwrite valid text."""
        cancel = cancel or threading.Event()
        with self.connect() as c:
            row = c.execute("SELECT payload,status FROM smart_pool_entries WHERE user_id=? AND problem_id=?",(uid,key)).fetchone()
            if row is None or row[1] not in (('candidate','reserve') if allow_reserve else ('candidate',)):
                raise PoolError('题目不属于当前候选池。',404)
            entry = json.loads(row[0])
            material_key = entry['materialKey']
            old = c.execute("SELECT payload,updated_at FROM training_public_materials WHERE problem_id=? AND kind='editorial'",(material_key,)).fetchone()
            attempt = c.execute('SELECT payload,updated_at FROM training_editorial_attempts WHERE problem_id=?',(material_key,)).fetchone()
        previous=json.loads(old[0]) if old else None
        if previous:
            current=previous.get('codeDiscoveryVersion')==CFEditorialProvider.CODE_VERSION
            incomplete=not has_editorial(previous) or bool(previous.get('codeDiscoveryErrors')) or (previous.get('codeLinks') and not has_code(previous))
            if (has_code(previous) and has_editorial(previous)) or (current and self.clock()-old[1] < (900 if incomplete else 86400)):
                return {'status':'cached','problemId':key,'material':previous}
        if attempt and self.clock()-attempt[1] < 900:
            return {'status':'deferred','problemId':key,'attempt':json.loads(attempt[0])}
        try:
            material = self.provider.fetch(entry['problem'], cancel, existing=previous) if previous else self.provider.fetch(entry['problem'], cancel)
            if cancel.is_set():
                raise Cancelled('已停止准备。','cancelled')
            material = material | {'problemId':material_key,'codeDiscoveryVersion':CFEditorialProvider.CODE_VERSION}
            result = {'status':'ready' if material.get('text') else 'partial','problemId':key,'method':material['method'],
                      'sourceUrl':material['sourceUrl'],'codeCount':len(material.get('codeBlocks',[]))}
            with self.db_lock, self.connect() as c:
                c.execute('INSERT OR REPLACE INTO training_public_materials VALUES(?,?,?,?,?)',
                    (material_key,'editorial',signature(material),json.dumps(material,ensure_ascii=False),int(self.clock())))
                c.execute('DELETE FROM training_editorial_attempts WHERE problem_id=?',(material_key,))
            if material.get('stopReason'):
                raise AccessRestricted(material['stopReason']['message'],material['stopReason']['code'])
            return result | {'material':material}
        except Cancelled:
            raise
        except EditorialError as exc:
            result = {'status':'failed','problemId':key,'code':exc.code,
                      'message':str(exc),'sources':exc.sources}
            with self.db_lock, self.connect() as c:
                c.execute('INSERT OR REPLACE INTO training_editorial_attempts VALUES(?,?,?,?)',
                    (material_key,'failed',json.dumps(result,ensure_ascii=False),int(self.clock())))
            if isinstance(exc,AccessRestricted):
                raise
            return result

    def _run(self, uid, entries, cancel):
        if self.pool is not None:
            return self._run_quality(uid,cancel)
        try:
            for entry in entries:
                if cancel.is_set():
                    raise Cancelled('已停止准备。','cancelled')
                self._update(uid,current=entry['problem']['problemKey'])
                try:
                    result = self.prepare_one(uid,entry['id'],cancel)
                except PoolError:
                    result = {'status':'deferred'}  # Pool refreshed during a job.
                field = {'ready':'acquired','partial':'partial','cached':'cached','failed':'failed','deferred':'deferred'}[result['status']]
                job = self.status(uid)
                material=result.get('material',{})
                self._update(uid,processed=job['processed']+1,
                    codeReady=job['codeReady']+bool(material.get('codeBlocks')),
                    linksOnly=job['linksOnly']+bool(material.get('codeLinks') and not material.get('codeBlocks')),
                    **{field:job[field]+1})
            self._update(uid,status='completed',finishedAt=int(self.clock()),current=None)
        except Cancelled:
            self._update(uid,status='cancelled',message='已停止，已取得的题解已保留。')
        except AccessRestricted as exc:
            self._update(uid,status='blocked',message=str(exc),code=exc.code)
        except Exception:
            self._update(uid,status='failed',message='准备中断，已取得的题解已保留。')

    def _run_quality(self, uid, cancel):
        """Admit checked problems sequentially, allowing only the incomplete quota."""
        try:
            state=self.pool.refresh(uid)
            entries=self.pool.preparation_targets(uid,CFEditorialProvider.CODE_VERSION)
            self._update(uid,total=len(entries),materialCoverage=state['materialCoverage'])
            for entry in entries:
                if cancel.is_set():raise Cancelled('已停止准备。','cancelled')
                if state['retrievalReady']:break
                self._update(uid,current=entry['problem']['problemKey'])
                try:
                    result=self.prepare_one(uid,entry['id'],cancel,allow_reserve=True)
                except PoolError:
                    result={'status':'deferred'}
                field={'ready':'acquired','partial':'partial','cached':'cached','failed':'failed','deferred':'deferred'}[result['status']]
                job=self.status(uid);material=result.get('material',{})
                self._update(uid,processed=job['processed']+1,
                    codeReady=job['codeReady']+has_code(material),
                    linksOnly=job['linksOnly']+bool(material.get('codeLinks') and not has_code(material)),
                    **{field:job[field]+1})
                # Recheck live eligibility and the incomplete quota after each problem.
                state=self.pool.refresh(uid)
                self._update(uid,materialCoverage=state['materialCoverage'])
            state=self.pool.refresh(uid)
            quality=state['materialCoverage']
            ready=state['retrievalReady']
            message='已满足80%的题解与代码覆盖要求。' if ready else f"资料仍不足：还需 {quality['shortfall']} 道题同时具备题解和代码，候选缺位 {quality['candidateShortfall']} 道；未开启检索。"
            self._update(uid,status='completed' if ready else 'insufficient',finishedAt=int(self.clock()),
                         current=None,message=message,materialCoverage=quality)
        except Cancelled:
            self._update(uid,status='cancelled',message='已停止，已取得的资料已保留。')
        except AccessRestricted as exc:
            self._update(uid,status='blocked',message=str(exc),code=exc.code)
        except PoolError as exc:
            self._update(uid,status='failed',message=str(exc))
        except Exception:
            self._update(uid,status='failed',message='准备中断，已取得的资料已保留。')
        finally:
            if self.status(uid)['status'] in ('cancelled','blocked','failed'):
                try:
                    state=self.pool.refresh(uid)
                    self._update(uid,materialCoverage=state['materialCoverage'])
                except Exception:
                    pass  # A later refresh still derives membership from saved material results.
