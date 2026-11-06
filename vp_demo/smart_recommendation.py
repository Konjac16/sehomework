"""Independent smart recommendation jobs, public cards and per-user snapshots."""
import json
import threading
import time

from ai_provider import DeepSeekProvider, ProviderError
from recommendation_pool import PoolError
from smart_retrieval import Retriever
from smart_discovery import Discovery, admission_order
from statement_search import StatementSearch, with_statement_hits
from cf_editorials import AccessRestricted, Cancelled as FetchCancelled
from smart_workflow import (PROMPTS, VERSION, REASONS, bundle, validate_intent,
                            validate_card, validate_audit, validate_rank, unavailable_card, canonicalize, fail)
from training_archive import signature


class Cancelled(Exception):
    pass


class SmartRecommendationService:
    def __init__(self, connect, lock, pool, key_loader, provider=None, retriever=None, clock=time.time, editorials=None):
        self.connect,self.db_lock,self.pool,self.key_loader=connect,lock,pool,key_loader
        self.provider=provider or DeepSeekProvider()
        self.retriever=retriever or Retriever()
        self.clock=clock
        self.lock=threading.RLock()
        self.active={}
        self.discovery=Discovery(pool)
        self.statements=StatementSearch(pool,self.retriever)
        self.editorials=editorials

    @staticmethod
    def init_tables(c):
        c.executescript('''
        CREATE TABLE IF NOT EXISTS smart_public_cards(
          cache_key TEXT PRIMARY KEY, problem_id TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS smart_jobs(
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          status TEXT NOT NULL, preference TEXT NOT NULL, fingerprint TEXT NOT NULL,
          progress TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS smart_jobs_user ON smart_jobs(user_id,id);
        CREATE TABLE IF NOT EXISTS smart_recommendations(
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          fingerprint TEXT NOT NULL, payload TEXT NOT NULL, diagnostics TEXT NOT NULL, created_at INTEGER NOT NULL,
          UNIQUE(user_id,fingerprint));
        ''')

    def recover(self):
        with self.db_lock,self.connect() as c:
            c.execute("UPDATE smart_jobs SET status='interrupted',progress=? WHERE status IN ('running','cancelling')",
                      (json.dumps({'message':'服务重启，请重新点击推荐；已完成的公共学习卡会复用。'}),))

    def _inputs(self, uid):
        rows=self.pool.recommendation_candidates(uid)
        bundles={row['id']:bundle(self.pool,uid,row) for row in rows}
        with self.connect() as c:
            _,input_hash=self.pool._inputs(c,uid)
        return bundles,input_hash

    def _fingerprint(self, preference, bundles, input_hash):
        return signature({'preference':preference,'bundles':{k:b['hash'] for k,b in bundles.items()},
                          'inputs':input_hash,'model':self.provider.model,'workflow':VERSION})

    def _snapshot_valid(self, uid, row, bundles, input_hash):
        payload=json.loads(row['payload'])
        if self._fingerprint(payload['preference'],bundles,input_hash)!=row['fingerprint']:return False
        diagnostics=json.loads(row['diagnostics'])
        if diagnostics.get('statementCorpusHash') and diagnostics['statementCorpusHash']!=self.statements.revision():return False
        with self.connect() as c:
            for pid,expected in diagnostics.get('materialHashes',{}).items():
                entry=c.execute('SELECT payload FROM smart_pool_entries WHERE user_id=? AND problem_id=?',(uid,pid)).fetchone()
                if entry is None or bundle(self.pool,uid,json.loads(entry[0]))['hash']!=expected:return False
        return True

    def state(self, uid):
        with self.connect() as c:
            row=c.execute('SELECT * FROM smart_jobs WHERE user_id=? ORDER BY id DESC LIMIT 1',(uid,)).fetchone()
            result=c.execute('SELECT * FROM smart_recommendations WHERE user_id=? ORDER BY id DESC LIMIT 1',(uid,)).fetchone()
            completed=c.execute("SELECT progress FROM smart_jobs WHERE user_id=? AND status='completed' ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
            if completed:
                snapshot_id=json.loads(completed[0]).get('snapshotId')
                if snapshot_id:
                    result=c.execute('SELECT * FROM smart_recommendations WHERE id=? AND user_id=?',(snapshot_id,uid)).fetchone()
        job={'status':'idle'} if row is None else json.loads(row['progress']) | {
            'id':row['id'],'status':row['status'],'preference':row['preference'],'updatedAt':row['updated_at']}
        snapshot=None
        if result:
            snapshot=json.loads(result['payload']) | {'id':result['id'],'createdAt':result['created_at']}
            # A reload never makes a model call, even if the saved snapshot is now stale.
            try:
                bundles,input_hash=self._inputs(uid)
                stale=not self._snapshot_valid(uid,result,bundles,input_hash)
            except PoolError:
                stale=True
            snapshot['stale']=stale
        return {'job':job,'recommendation':snapshot,'settings':self.pool.settings(uid),
                'configured':bool(self.key_loader(uid)),'model':self.provider.model}

    def start(self, uid, data):
        if not isinstance(data,dict) or set(data)!={'preference'} or not isinstance(data['preference'],str):
            raise PoolError('请填写本次训练偏好。')
        preference=data['preference'].strip()
        if not 2<=len(preference)<=800:raise PoolError('训练偏好需为 2～800 个字符。')
        with self.lock:
            if uid in self.active:return self.state(uid)
            if self.active:raise PoolError('另一个推荐任务正在运行，请稍后再试。',409)
            state=self.pool.state(uid)
            if not state['prepared'] or state.get('needsRefresh'):self.pool.refresh(uid)
            bundles,input_hash=self._inputs(uid)
            fingerprint=self._fingerprint(preference,bundles,input_hash)
            with self.connect() as c:
                old=c.execute('SELECT * FROM smart_recommendations WHERE user_id=? AND fingerprint=?',(uid,fingerprint)).fetchone()
            if old and self._snapshot_valid(uid,old,bundles,input_hash):
                # Re-select the existing snapshot as current, preserving its stable ID and content.
                with self.db_lock,self.connect() as c:
                    c.execute('INSERT INTO smart_jobs(user_id,status,preference,fingerprint,progress,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                        (uid,'completed',preference,fingerprint,json.dumps({'stage':'completed','message':'已复用相同条件的推荐，无新增模型调用。','calls':0,'snapshotId':old['id']}),int(self.clock()),int(self.clock())))
                return self.state(uid)
            key=self.key_loader(uid)
            if not key:raise PoolError('请先在“数据与账号”配置 DeepSeek API Key。',409)
            self.retriever.ready()
            now=int(self.clock())
            with self.db_lock,self.connect() as c:
                jid=c.execute('INSERT INTO smart_jobs(user_id,status,preference,fingerprint,progress,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                    (uid,'running',preference,fingerprint,json.dumps({'stage':'queued','calls':0,'message':'正在理解偏好并检查已有资料。'}),now,now)).lastrowid
            cancel=threading.Event();self.active[uid]=cancel
            threading.Thread(target=self._run,args=(uid,jid,preference,bundles,input_hash,fingerprint,key,cancel),daemon=True).start()
            return self.state(uid)

    def cancel(self, uid):
        with self.lock:
            if uid in self.active:
                with self.db_lock,self.connect() as c:
                    changed=c.execute("UPDATE smart_jobs SET status='cancelling' WHERE user_id=? AND status='running'",(uid,)).rowcount
                if changed:self.active[uid].set()
            return self.state(uid)

    def _update(self, jid, progress, status=None):
        with self.db_lock,self.connect() as c:
            c.execute('UPDATE smart_jobs SET progress=?,updated_at=? WHERE id=?',
                      (json.dumps(progress,ensure_ascii=False),int(self.clock()),jid))
            if status:c.execute('UPDATE smart_jobs SET status=? WHERE id=?',(status,jid))

    def _run(self, uid, jid, preference, bundles, input_hash, fingerprint, key, cancel):
        progress={'stage':'intent','calls':0,'cardsReady':0,'cardsTotal':0,'cardWarnings':0,'totalTokens':0,'usageComplete':True}
        diagnostics={'workflow':VERSION,'model':self.provider.model,'materialHashes':{p:b['hash'] for p,b in bundles.items()},'usage':[]}
        def check():
            if cancel.is_set():raise Cancelled()
            with self.connect() as c:
                if self.pool._inputs(c,uid)[1]!=input_hash:
                    raise PoolError('账号、提交或智能推荐设置有更新，请重新准备后再推荐；已缓存的学习卡会保留。',409)
        def update(stage, message):
            check();progress.update(stage=stage,message=message);self._update(jid,progress)
        def call(stage, inputs, tokens):
            check()
            if progress['calls']>=9:raise PoolError('本轮已达到 9 次调用上限，已缓存的资料会保留。')
            progress['calls']+=1;self._update(jid,progress)
            content,usage,model=self.provider._request(PROMPTS[stage],inputs,key,
                {'thinking':{'type':'disabled'},'temperature':.2,'max_tokens':tokens},True,model=self.provider.model)
            progress['totalTokens']+=(usage or {}).get('total_tokens',0)
            progress['usageComplete'] &= bool(usage and 'total_tokens' in usage)
            diagnostics['usage'].append({'stage':stage,'usage':usage,'model':model})
            check()
            try:
                raw=json.loads(content)
                if not isinstance(raw,dict):fail()
                return raw
            except (ValueError,TypeError):fail()
        try:
            update('intent','正在理解本次偏好，并检查可用标签与公开资料。')
            statement_revision=self.statements.revision()
            scope=self.discovery.prepare(uid,check)
            intent=validate_intent(call('intent',{'preference':preference,'validTags':scope['validTags']},2200),scope['validTags'])
            diagnostics['intent']=intent
            cached_ids=set(bundles)
            original_bundles=dict(bundles)
            bundles={p:bundles[p] for p in self.discovery.filter(scope,intent,bundles)}
            cards={};real_cards=set();failed_cards=set();reviewed=set();all_audit={};eligible=[];top=[]
            fetch_count=0;fetch_deadline=time.monotonic()+180
            statement_mode=intent['retrievalTarget']=='statement'
            statement_result=None

            def audit_bundle(pid):
                b=self.discovery.with_metadata(scope,bundles[pid])
                if statement_mode:
                    b=with_statement_hits(b,statement_result['hits'].get(pid,[]))
                    # Content requests must be justified by actual statements, not editorial mentions.
                    b=b|{'evidence':[e for e in b['evidence'] if e['kind']=='statement']}
                return b|{'card':unavailable_card() if statement_mode else cards[pid]}

            def card_key(b):
                return signature({'bundle':b['hash'],'prompt':PROMPTS['card'],'schema':1,'model':self.provider.model})

            def load_cards(items):
                for pid,b in items.items():
                    with self.connect() as c:
                        row=c.execute('SELECT payload FROM smart_public_cards WHERE cache_key=?',(card_key(b),)).fetchone()
                    cards[pid]=unavailable_card()
                    if row:
                        cached=json.loads(row[0])
                        try:
                            cards[pid]=validate_card(cached.get('raw') if cached.get('unavailable') else cached,self.discovery.with_metadata(scope,b))
                            real_cards.add(pid)
                        except ProviderError:
                            if pid not in failed_cards:progress['cardWarnings']+=1
                            failed_cards.add(pid)

            def expand():
                update('discovery','已有资料覆盖不足，正在按方向和正文检索池外题目。')
                added,trace=self.discovery.search(uid,scope,intent,cached_ids,check)
                bundles.update(added);diagnostics['discovery']=trace
                load_cards(added)
                progress['discoveredCount']=len(added)

            def enrich(ids):
                nonlocal fetch_count
                if self.editorials is None:return
                # A bounded event adapter interrupts the provider between HTTP requests.
                class Budget:
                    def is_set(self):return cancel.is_set() or time.monotonic()>=fetch_deadline
                    def wait(self, seconds):
                        cancel.wait(min(seconds,max(0,fetch_deadline-time.monotonic())))
                        return self.is_set()
                event=Budget()
                for pid in ids:
                    check()
                    if event.is_set() or fetch_count>=8:break
                    if pid in admission_order({pid:bundles[pid]}):continue
                    fetch_count+=1
                    update('materials',f'正在补充候选公开资料（{fetch_count}/8），无法取得的题仍保留已有依据。')
                    try:
                        result=self.editorials.prepare_one(uid,pid,event,allow_reserve=True)
                        diagnostics.setdefault('materialAttempts',[]).append({'problemId':pid,'status':result['status']})
                    except FetchCancelled:
                        check();break
                    except AccessRestricted as exc:
                        diagnostics.setdefault('materialAttempts',[]).append({'problemId':pid,'status':'restricted','code':exc.code})
                        break
                    finally:
                        bundles[pid]=bundle(self.pool,uid,scope['entries'][pid])
                load_cards({p:bundles[p] for p in ids})

            load_cards(bundles)
            # Few cached candidates cannot establish adequate coverage of a new direction.
            expanded=len(bundles)<8
            if statement_mode:
                diagnostics['statementCorpusHash']=statement_revision
                ids=self.discovery.filter(scope,intent,scope['entries'])
                corpus=self.statements.corpus(scope,ids)
                statement_result=self.statements.search(corpus,intent,check,update)
                diagnostics['statementSearch']=statement_result
                selected=statement_result['ranked']
                self.pool.register_discovered(uid,scope,selected)
                bundles={p:bundle(self.pool,uid,scope['entries'][p]) for p in selected}
                load_cards(bundles);expanded=True
            elif expanded:expand()
            for round_index in range(2):
                check()
                available={p:audit_bundle(p) for p in bundles if p not in reviewed}
                if not available:break
                update('retrieval',f'正在对 {len(available)} 道题进行语义与关键词检索。')
                retrieval=({'top8':[p for p in statement_result['ranked'] if p in available][:8],
                            'method':statement_result['method'],'searchedProblemCount':statement_result['problemCount']}
                           if statement_mode else self.retriever.retrieve(available,{p:cards[p] for p in available},intent,check))
                top=retrieval['top8']
                if len(top)>8 or len(set(top))!=len(top) or not set(top)<=set(available):
                    raise PoolError('未检索到可审查的题目，请调整偏好或候选范围。')
                retrieval['cachedCandidateCount']=sum(p in cached_ids for p in available)
                retrieval['outsideCandidateCount']=sum(p not in cached_ids for p in available)
                retrieval['reviewedOutsideCount']=sum(p not in cached_ids for p in top)
                diagnostics.setdefault('retrievalRounds',[]).append(retrieval)
                if not top:
                    if not expanded:
                        expand();expanded=True;continue
                    break
                if not statement_mode:enrich(top)
                candidates={p:audit_bundle(p) for p in top}
                update('audit',f'正在结合已有资料审查 {len(top)} 道候选。')
                raw=call('audit',{'preference':preference,'task_definition':{'criteria':intent['criteria'],'soft':intent['soft']},
                    'candidates':list(candidates.values())},7200)
                audit,qualified=validate_audit(canonicalize(raw,'assessments',candidates),candidates,intent)
                all_audit.update(audit);eligible.extend(qualified);reviewed.update(top)
                if eligible or any(a['match']=='moderate' for a in audit.values()):break
                if not statement_mode and not expanded:expand();expanded=True
            diagnostics['audit']=all_audit
            chosen=None
            rank_fallback=False
            if reviewed:
                update('rank',f'正在比较 {len(reviewed)} 道候选，选择本次最值得尝试的一题。')
                qualified={p:audit_bundle(p)|{'assessment':all_audit[p]} for p in sorted(reviewed)}
                rank=call('rank',{'preference':preference,'soft':intent['soft'],'candidates':list(qualified.values())},3200)
                rank=canonicalize(rank,'assessments',qualified)
                first=validate_rank(rank,qualified);diagnostics['rank']=rank
                bounds=self.pool.settings(uid)['range'];mid=sum(bounds)/2
                if not first:
                    # A valid but empty ranking must not turn available candidates into no recommendation.
                    rank_fallback=True
                    order={'strong':0,'moderate':1,'weak':2,'unknown':3}
                    level=min(order[a['match']] for a in all_audit.values())
                    first=[p for p in sorted(reviewed) if order[all_audit[p]['match']]==level]
                chosen=min(first,key=lambda p:(p not in real_cards,abs(bundles[p]['problem']['rating']-mid),p))
            diagnostics['rankFallback']=rank_fallback

            # Cache after selection. Only this new batch funds its own incomplete quota.
            fresh={p:bundles[p] for p in sorted(reviewed) if p not in real_cards and p not in failed_cards}
            to_cache=admission_order(fresh,20)
            clean={};invalid={};progress['cardsTotal']=len(to_cache);progress['cardsReady']=0
            for offset in range(0,len(to_cache),5):
                group=to_cache[offset:offset+5]
                update('cards',f'正在保存可复用资料卡（{offset}/{len(to_cache)}），不改变本轮选题。')
                raw=canonicalize(call('card',{'problems':[self.discovery.with_metadata(scope,bundles[p]) for p in group]},5000),
                    'cards',{p:bundles[p] for p in group})
                for pid in group:
                    try:clean[pid]=validate_card(raw['cards'][pid],self.discovery.with_metadata(scope,bundles[pid]))
                    except ProviderError:
                        progress['cardWarnings']+=1;invalid[pid]=raw['cards'][pid]
                progress['cardsReady']+=len(group)
            admitted=admission_order({p:bundles[p] for p in clean},20)
            check()
            if statement_mode and statement_revision!=self.statements.revision():
                raise PoolError('题面检索资料刚刚更新，请重新推荐；已生成的本地向量会复用。',409)
            # Acquisition is allowed to update materials; all audited versions must still match.
            for pid in reviewed:
                if bundle(self.pool,uid,scope['entries'][pid])['hash']!=bundles[pid]['hash']:
                    raise PoolError('审查过的资料刚刚更新，未发布过期推荐，请重试。',409)
            current,current_hash=self._inputs(uid)
            for pid,b in original_bundles.items():
                expected=bundles.get(pid,b)
                if pid not in current or current[pid]['hash']!=expected['hash']:
                    raise PoolError('已有候选资料刚刚更新，请重新推荐。',409)
            approximate=bool(chosen and (chosen not in eligible or rank_fallback))
            result={'preference':preference,'problem':bundles[chosen]['problem'] if chosen else None,
                'problemId':chosen,'reasons':(['作为本轮候选中较接近偏好的一题，建议尝试；不保证满足全部要求。'] if approximate else
                    [REASONS[c] for c in intent['reasonCodes']] if chosen else []),
                'matchLevel':all_audit[chosen]['match'] if chosen else None,'approximate':approximate,
                'message':('AI 未给出明确首选，已按本轮匹配评估和难度选出一题供尝试。' if rank_fallback else
                    '这是当前候选中推荐尝试的一题，可能只满足部分偏好，已有资料也可能不完整。' if approximate else
                    'AI 根据已有资料比较选出；软偏好用于排序，不要求每项都完全满足。') if chosen else
                    '当前难度、做题记录和方向范围内没有可送审的候选，请调整范围后重试。',
                'candidateCount':len(bundles),'reviewedCount':len(reviewed),'qualifiedCount':len(eligible),
                'searchedProblemCount':statement_result['problemCount'] if statement_mode else None,
                'referenceCodeAvailable':bool(chosen and bundles[chosen]['reference_code_available']),
                'outsideCache':bool(chosen and chosen not in cached_ids),'expanded':expanded,'admittedCards':len(admitted),
                'materialKinds':sorted({e['kind'] for e in bundles[chosen]['evidence']}) if chosen else [],
                'cardWarnings':progress['cardWarnings'],'calls':progress['calls'],'totalTokens':progress['totalTokens'],
                'usageComplete':progress['usageComplete'],'model':self.provider.model,'range':self.pool.settings(uid)['range']}
            diagnostics['admitted']=admitted
            diagnostics['materialHashes']={p:bundles[p]['hash'] for p in reviewed}
            diagnostics['retrieval']=diagnostics.get('retrievalRounds',[{}])[-1]
            # Cache rows and admission are atomic with publication; failed/cancelled jobs retain raw materials.
            with self.lock,self.db_lock,self.connect() as c:
                check()
                now=int(self.clock())
                for pid,raw in invalid.items():
                    c.execute('INSERT OR REPLACE INTO smart_public_cards VALUES(?,?,?,?)',
                        (card_key(bundles[pid]),pid,json.dumps({'unavailable':True,'raw':raw},ensure_ascii=False),now))
                for pid in admitted:
                    c.execute('INSERT OR REPLACE INTO smart_public_cards VALUES(?,?,?,?)',
                        (card_key(bundles[pid]),pid,json.dumps(clean[pid],ensure_ascii=False),now))
                    c.execute('INSERT OR IGNORE INTO smart_card_memberships VALUES(?,?,?)',(uid,pid,now))
                # Include newly admitted members in the stable snapshot key without a second model call.
                final_bundles=current|{p:bundles[p] for p in admitted}
                fingerprint=self._fingerprint(preference,final_bundles,current_hash)
                rid=c.execute('INSERT OR REPLACE INTO smart_recommendations(user_id,fingerprint,payload,diagnostics,created_at) VALUES(?,?,?,?,?)',
                    (uid,fingerprint,json.dumps(result,ensure_ascii=False),json.dumps(diagnostics,ensure_ascii=False),now)).lastrowid
                progress.update(stage='completed',snapshotId=rid,admittedCards=len(admitted),message='推荐已保存；新增资料卡满足 80% 题解与代码完整率。刷新不会重新调用。')
                c.execute("UPDATE smart_jobs SET fingerprint=?,progress=?,status='completed',updated_at=? WHERE id=?",
                    (fingerprint,json.dumps(progress,ensure_ascii=False),now,jid))
        except Cancelled:
            progress.update(message='已取消。已发出的请求可能计费；没有发出下一轮请求，已完成的学习卡保留。')
            self._update(jid,progress,'cancelled')
        except (PoolError,ProviderError) as exc:
            progress.update(message=str(exc));self._update(jid,progress,'failed')
        except Exception as exc:
            # Never expose provider responses, credentials or problem solutions through job errors.
            progress.update(message=f'推荐未完成（{type(exc).__name__}），已有资料和结果已保留。请手动重试。')
            self._update(jid,progress,'failed')
        finally:
            with self.lock:self.active.pop(uid,None)
