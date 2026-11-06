"""Preparation pool for later AI selection; no model calls or VP-room mutations."""
from collections import Counter
import json
import time

from training_archive import problem_id, signature
from material_quality import complete, coverage, has_editorial, has_code, required_count

CAPACITY = 100
POLICY_VERSION = 5
DEFAULTS = {'difficultyMode':'default', 'minRating':1600, 'maxRating':2000,
            'includeAttempted':False}
INPUT_FIELDS = ('platform','handle','externalId','contestId','index','verdict','submittedAt',
                'participantType','participantMembers','partyStartTime','teamId','submissionContestId')


class PoolError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class SmartRecommendationPool:
    def __init__(self, connect, lock, archive, clock=time.time, catalog_loader=None):
        self.connect, self.lock, self.archive, self.clock = connect, lock, archive, clock
        self.catalog_loader = catalog_loader

    @staticmethod
    def init_tables(c):
        c.executescript('''
        CREATE TABLE IF NOT EXISTS smart_settings(
          user_id INTEGER PRIMARY KEY REFERENCES users(id), payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS smart_preparations(
          user_id INTEGER PRIMARY KEY REFERENCES users(id), version INTEGER NOT NULL,
          input_hash TEXT NOT NULL, snapshot_hash TEXT NOT NULL, prepared_at INTEGER NOT NULL,
          summary TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS smart_pool_entries(
          user_id INTEGER NOT NULL REFERENCES users(id), problem_id TEXT NOT NULL,
          status TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY(user_id,problem_id));
        CREATE TABLE IF NOT EXISTS training_public_materials(
          problem_id TEXT NOT NULL, kind TEXT NOT NULL, content_hash TEXT NOT NULL,
          payload TEXT NOT NULL, updated_at INTEGER NOT NULL,
          PRIMARY KEY(problem_id,kind));
        CREATE TABLE IF NOT EXISTS training_editorial_attempts(
          problem_id TEXT PRIMARY KEY,status TEXT NOT NULL,payload TEXT NOT NULL,updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS smart_card_memberships(
          user_id INTEGER NOT NULL REFERENCES users(id), problem_id TEXT NOT NULL,
          admitted_at INTEGER NOT NULL, PRIMARY KEY(user_id,problem_id));
        ''')
        # Preserve public materials from the discontinued upsolving prototype, not its candidate scope.
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='upsolving_public_materials'").fetchone():
            c.execute('''INSERT OR IGNORE INTO training_public_materials
                SELECT * FROM upsolving_public_materials''')

    def _inputs(self, c, uid):
        row = c.execute('SELECT settings FROM users WHERE id=?', (uid,)).fetchone()
        if not row:
            raise PoolError('请先登录', 401)
        settings = json.loads(row[0])
        records = []
        for item in c.execute("SELECT json_remove(payload,'$.source') FROM submissions WHERE user_id=? ORDER BY platform,external_id", (uid,)):
            raw = json.loads(item[0])
            records.append({key:raw.get(key) for key in INPUT_FIELDS})
        rating = (settings.get('profile') or {}).get('rating')
        reference = rating if type(rating) is int and rating >= 0 else 1800
        profile = c.execute('SELECT payload FROM smart_settings WHERE user_id=?',(uid,)).fetchone()
        prefs = dict(DEFAULTS)
        if profile:
            prefs.update(json.loads(profile[0]))
        bounds = [max(0,reference-800),reference+800] if prefs['difficultyMode']=='default' else [prefs['minRating'],prefs['maxRating']]
        data = {'handle':settings.get('handle') or '', 'reference':reference,
                'ratingKnown':type(rating) is int and rating >= 0,
                'records':records, 'preferences':prefs, 'range':bounds,
                'historyCoverage':settings.get('historyCoverage'),
                'lastSync':settings.get('lastSync'), 'lastScope':settings.get('lastScope') or '尚未同步'}
        return data, signature(data)

    def settings(self, uid, values=None):
        with self.lock, self.connect() as c:
            data,_ = self._inputs(c,uid)
            if values is not None:
                if not isinstance(values,dict) or set(values)-set(DEFAULTS):
                    raise PoolError('智能推荐设置包含未知字段。')
                prefs = data['preferences'] | values
                if prefs['difficultyMode'] not in ('default','manual') or type(prefs['includeAttempted']) is not bool:
                    raise PoolError('智能推荐设置格式不正确。')
                if any(type(prefs[k]) is not int or not 0 <= prefs[k] <= 5000 for k in ('minRating','maxRating')) or prefs['minRating'] > prefs['maxRating']:
                    raise PoolError('难度范围应为 0～5000 内的整数，下限不能高于上限。')
                c.execute('INSERT OR REPLACE INTO smart_settings VALUES(?,?)',(uid,json.dumps(prefs)))
                data,_ = self._inputs(c,uid)
            return {k:data[k] for k in ('preferences','reference','ratingKnown','range')}

    @staticmethod
    def _key(row):
        try:
            return problem_id(row.get('contestId'), row.get('index'))
        except ValueError:
            return None

    def _catalog(self):
        catalog = self.archive.load()
        if self.catalog_loader is None:
            return catalog | {'scope':'offline_snapshot'}
        try:
            rows = self.catalog_loader()
        except ValueError:
            return catalog | {'scope':'offline_snapshot'}
        # Keep archive aliases/materials but let problemset.problems determine the current candidate universe.
        problems, tags = {}, {}
        for row in rows:
            key = self._key(row)
            if row.get('platform')!='codeforces' or not key:
                continue
            problems[key] = {k:row[k] for k in ('platform','contestId','index','problemKey','title','rating','url')}
            problems[key]['id'] = key
            tags[key] = [t for t in row.get('tags',[]) if isinstance(t,str) and not t.startswith('*')]
        return catalog | {'problems':problems, 'tags':tags, 'scope':'cf_problemset',
                          'revision':signature({'archive':catalog['revision'],'catalog':problems})}

    def _prepare(self, data, catalog, previous):
        reference = data['reference']
        lo, hi = data['range']
        canonical = catalog['canonical']
        canon = lambda key: canonical.get(key, key)
        records = [r for r in data['records'] if r['platform'] == 'codeforces'
                   and str(r['handle']).casefold() == data['handle'].casefold()]
        solved = {canon(self._key(r)) for r in records if r['verdict'] == 'OK'}
        attempted = {canon(self._key(r)) for r in records}
        pending = {canon(self._key(r)) for r in records if r['verdict'] in ('PENDING','TESTING')}
        eligible, excluded, seen = [], Counter(), set()
        training_eligible=0
        for key, p in catalog['problems'].items():
            cid = canon(key)
            if cid in seen:
                continue
            seen.add(cid)
            reason = None
            if cid in solved: reason = 'accepted'
            elif cid in pending: reason = 'judging'
            elif not data['preferences']['includeAttempted'] and cid in attempted: reason = 'attempted'
            elif type(p['rating']) is not int: reason = 'unrated'
            elif not lo <= p['rating'] <= hi: reason = 'outside_difficulty'
            if reason:
                excluded[reason] += 1
                continue
            training_eligible+=1
            material = catalog['materials'].get(key)
            contest = catalog['contests'].get(p['contestId'],{})
            eligible.append({'id':cid, 'problem':p,
                'sources':[{'contestId':p['contestId'], 'name':contest.get('name',f"CF #{p['contestId']}")}],
                'level':'normal' if data['preferences']['difficultyMode']=='default' else 'manual',
                'materials':{'statement':'ready' if material else 'missing',
                             'editorial':'missing','referenceCode':'missing','analysis':'not_started'},
                'materialKey':key,'materialHash':material['contentHash'] if material else None})
        target = (lo+hi)/2
        eligible.sort(key=lambda e:(abs(e['problem']['rating']-target),-e['problem']['contestId'],e['id']))
        by_id = {e['id']:e for e in eligible}
        complete_keys = catalog.get('completeMaterialKeys',set())
        checked_keys = catalog.get('checkedMaterialKeys',set())
        target_count=min(CAPACITY,training_eligible)
        incomplete_limit=target_count-required_count(target_count)
        order=list(dict.fromkeys([key for key in previous if key in by_id]+[e['id'] for e in eligible]))
        good=[key for key in order if by_id[key]['materialKey'] in complete_keys]
        partial=[key for key in order if by_id[key]['materialKey'] in checked_keys and by_id[key]['materialKey'] not in complete_keys]
        # Admission, rather than filling the pool with unchecked problems and rejecting them later.
        chosen=good[:target_count]
        chosen+=partial[:min(incomplete_limit,target_count-len(chosen))]
        chosen_set=set(chosen)
        entries = [by_id[key] for key in chosen] + [e for e in eligible if e['id'] not in chosen_set]
        for i, entry in enumerate(entries):
            entry['status'] = 'candidate' if i < len(chosen) else 'reserve'
        summary = {'referenceRating':reference,'ratingKnown':data['ratingKnown'],'capacity':CAPACITY,
            'targetCount':target_count,'trainingEligibleCount':training_eligible,'incompleteLimit':incomplete_limit,
            'range':data['range'],'preferences':data['preferences'],
            'candidateCount':len(chosen),'reserveCount':len(entries)-len(chosen),
            'catalogCount':len(catalog['problems']),'eligibleCount':len(entries),'excluded':dict(excluded),
            'statementReady':sum(e['materials']['statement']=='ready' for e in entries[:len(chosen)]),
            'editorialReady':0,'analysisReady':0,'catalogSnapshot':catalog['snapshot'],
            'catalogScope':catalog.get('scope','offline_snapshot'),
            'lastSync':data['lastSync'],'historyScope':data['lastScope'],
            'selectionReady':False,'policyVersion':POLICY_VERSION}
        return entries, summary

    def refresh(self, uid):
        # Read materials outside the write lock; compare user inputs again before publishing.
        catalog = self._catalog()
        with self.connect() as c:
            data, input_hash = self._inputs(c, uid)
            previous = [r[0] for r in c.execute("SELECT problem_id FROM smart_pool_entries WHERE user_id=? AND status='candidate' ORDER BY position", (uid,))]
            docs={r[0]:(json.loads(r[1]),r[2]) for r in c.execute("SELECT problem_id,payload,updated_at FROM training_public_materials WHERE kind='editorial'")}
            complete_keys={k for k,(m,at) in docs.items() if complete(m)}
            checked_keys={k for k,(m,at) in docs.items() if m.get('codeDiscoveryVersion') or complete(m)}
            checked_keys.update(r[0] for r in c.execute('SELECT problem_id FROM training_editorial_attempts'))
        catalog = catalog | {'completeMaterialKeys':complete_keys,'checkedMaterialKeys':checked_keys}
        entries, summary = self._prepare(data, catalog, previous)
        snapshot_hash = signature({'summary':summary, 'entries':entries, 'catalog':catalog['revision']})
        now = int(self.clock())
        with self.lock, self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if self._inputs(c, uid)[1] != input_hash:
                raise PoolError('提交或账号信息刚刚更新，请重新整理候选；原候选已保留。', 409)
            old = c.execute('SELECT version,snapshot_hash FROM smart_preparations WHERE user_id=?', (uid,)).fetchone()
            version = 1 if old is None else old[0] + (old[1] != snapshot_hash)
            c.execute("UPDATE smart_pool_entries SET status='archived' WHERE user_id=?", (uid,))
            for position, entry in enumerate(entries):
                c.execute('INSERT OR REPLACE INTO smart_pool_entries VALUES(?,?,?,?,?)',
                          (uid, entry['id'], entry['status'], position, json.dumps(entry, ensure_ascii=False)))
                if entry['status'] == 'candidate' and entry['materialHash']:
                    material = catalog['materials'][entry['materialKey']]
                    c.execute('''INSERT INTO training_public_materials VALUES(?,?,?,?,?)
                      ON CONFLICT(problem_id,kind) DO UPDATE SET content_hash=excluded.content_hash,
                      payload=excluded.payload,updated_at=excluded.updated_at
                      WHERE content_hash<>excluded.content_hash''',
                      (entry['materialKey'], 'statement', material['contentHash'],
                       json.dumps(material['payload'], ensure_ascii=False), now))
            c.execute('INSERT OR REPLACE INTO smart_preparations VALUES(?,?,?,?,?,?)',
                      (uid, version, input_hash, snapshot_hash, now, json.dumps(summary, ensure_ascii=False)))
        return self.state(uid)

    def state(self, uid):
        with self.connect() as c:
            data, input_hash = self._inputs(c, uid)
            row = c.execute('SELECT * FROM smart_preparations WHERE user_id=?', (uid,)).fetchone()
            if row is None:
                return {'prepared':False, 'handle':data['handle'], 'capacity':CAPACITY,
                        'referenceRating':data['reference'], 'candidates':[], 'selectionReady':False,
                        'materialCoverage':coverage(0,0),'retrievalReady':False}
            summary = json.loads(row['summary'])
            stale = row['input_hash'] != input_hash or summary.get('policyVersion')!=POLICY_VERSION
            editorials = {r[0]:json.loads(r[1]) for r in c.execute("SELECT problem_id,payload FROM training_public_materials WHERE kind='editorial'")}
            attempts = {r[0]:json.loads(r[1]) for r in c.execute('SELECT problem_id,payload FROM training_editorial_attempts')}
            entries=[(r['status'],json.loads(r['payload'])) for r in c.execute(
                "SELECT status,payload FROM smart_pool_entries WHERE user_id=? AND status IN ('candidate','reserve') ORDER BY position",(uid,))]
            statements={r[0]:json.loads(r[1]) for r in c.execute(
                "SELECT problem_id,payload FROM training_public_materials WHERE kind='statement'")}
            archive_unavailable=False
            try:archived=self.archive.load()['materials']
            except ValueError:
                archived={};archive_unavailable=True
            def has_statement(entry):
                key=entry['materialKey']
                material=statements.get(key) or archived.get(key,{}).get('payload',{})
                statement=material.get('statement') or {}
                return any(str(statement.get(k) or '').strip() for k in
                           ('body','input_format','output_format','interaction_format','note'))
            statement_ids={e['id'] for _,e in entries if has_statement(e)}
            card_count=c.execute('''SELECT count(*) FROM smart_card_memberships m
                JOIN smart_pool_entries e ON e.user_id=m.user_id AND e.problem_id=m.problem_id
                WHERE m.user_id=? AND e.status IN ('candidate','reserve')''',(uid,)).fetchone()[0]
            overview={'availableCount':len(entries),'statementCount':len(statement_ids),'cardCount':card_count}
            if archive_unavailable:overview['archiveUnavailable']=True
            items = []
            for status,entry in entries:
                if status!='candidate':continue
                entry['materials']['statement']='ready' if entry['id'] in statement_ids else 'missing'
                editorial = editorials.get(entry['materialKey'])
                attempt = attempts.get(entry['materialKey'])
                if editorial:
                    entry['materials'].update(editorial='ready' if has_editorial(editorial) else 'partial',
                        referenceCode='ready' if has_code(editorial) else 'links_only' if editorial.get('codeLinks') else 'missing')
                elif attempt:
                    entry['materials'].update(editorial='failed',editorialReason=attempt.get('message'),editorialCode=attempt.get('code'))
                items.append({k:entry[k] for k in ('id','problem','sources','level','materials')})
            summary['editorialReady'] = sum(e['materials']['editorial']=='ready' for e in items)
            summary['statementReady'] = sum(e['materials']['statement']=='ready' for e in items)
            summary['referenceCodeReady'] = sum(e['materials']['referenceCode']=='ready' for e in items)
            summary['referenceCodeLinksOnly'] = sum(e['materials']['referenceCode']=='links_only' for e in items)
            quality=coverage(len(items),sum(e['materials']['editorial']=='ready' and e['materials']['referenceCode']=='ready' for e in items),stale,summary.get('targetCount',len(items)))
            return summary | {'prepared':True, 'handle':data['handle'], 'version':row['version'],
                'preparedAt':row['prepared_at'], 'needsRefresh':stale,
                'candidates':items,'overview':overview,'materialCoverage':quality,'retrievalReady':quality['ready'],
                'selectionReady':quality['ready']}

    def retrieval_candidates(self, uid):
        """Future RAG entry point: never consume an insufficient or stale pool."""
        state=self.state(uid)
        if not state['retrievalReady']:
            raise PoolError('候选池尚未满足80%的题解与代码覆盖要求，或训练条件已变化。',409)
        return state['candidates']

    def recommendation_candidates(self, uid):
        """The reusable corpus is not an eligibility gate for recommending a problem."""
        state=self.state(uid)
        if state.get('needsRefresh'):
            raise PoolError('智能推荐范围已变化，请重新整理。',409)
        rows={e['id']:e for e in state['candidates']}
        with self.connect() as c:
            for row in c.execute('''SELECT e.payload FROM smart_card_memberships m
                JOIN smart_pool_entries e ON e.user_id=m.user_id AND e.problem_id=m.problem_id
                WHERE m.user_id=? AND e.status IN ('candidate','reserve')''',(uid,)):
                e=json.loads(row[0]);rows[e['id']]=e
        return list(rows.values())

    def discovery_scope(self, uid):
        catalog=self._catalog()
        with self.connect() as c:data,input_hash=self._inputs(c,uid)
        entries,_=self._prepare(data,catalog,[])
        return {'entries':{e['id']:e for e in entries},'catalog':catalog,'inputHash':input_hash}

    def register_discovered(self, uid, scope, ids):
        """Record ownership and reuse public text; discovering a problem does not admit a card."""
        with self.lock,self.connect() as c:
            if self._inputs(c,uid)[1]!=scope['inputHash']:
                raise PoolError('账号或训练范围已变化，请重新推荐。',409)
            for pid in ids:
                e=scope['entries'][pid]
                c.execute('INSERT OR IGNORE INTO smart_pool_entries VALUES(?,?,?,?,?)',
                    (uid,pid,'reserve',100000,json.dumps(e|{'status':'reserve'},ensure_ascii=False)))
                m=scope['catalog']['materials'].get(e['materialKey'])
                if m:
                    c.execute('INSERT OR IGNORE INTO training_public_materials VALUES(?,?,?,?,?)',
                        (e['materialKey'],'statement',m['contentHash'],json.dumps(m['payload'],ensure_ascii=False),int(self.clock())))

    def preparation_targets(self, uid, code_version):
        """Candidates first, then reserves. Skip complete and recently examined materials."""
        with self.connect() as c:
            docs={r[0]:(json.loads(r[1]),r[2]) for r in c.execute("SELECT problem_id,payload,updated_at FROM training_public_materials WHERE kind='editorial'")}
            failed={r[0]:r[1] for r in c.execute('SELECT problem_id,updated_at FROM training_editorial_attempts')}
            result=[]
            for r in c.execute("SELECT payload FROM smart_pool_entries WHERE user_id=? AND status IN ('candidate','reserve') ORDER BY CASE status WHEN 'candidate' THEN 0 ELSE 1 END,position",(uid,)):
                entry=json.loads(r[0]);key=entry['materialKey']
                doc,at=docs.get(key,({},0))
                if complete(doc) or (key in failed and self.clock()-failed[key]<900):continue
                if doc.get('codeDiscoveryVersion')==code_version:
                    if self.clock()-at<900:continue
                    if self.clock()-at<86400 and has_editorial(doc) and not doc.get('codeLinks') and not doc.get('codeDiscoveryErrors') and not has_code(doc):continue
                result.append(entry)
            return result

    def material(self, uid, key):
        with self.connect() as c:
            row = c.execute('SELECT payload FROM smart_pool_entries WHERE user_id=? AND problem_id=?', (uid,key)).fetchone()
            if row is None:
                raise PoolError('题目不属于你的候选记录', 404)
            entry = json.loads(row[0])
            row = c.execute("SELECT payload,content_hash,updated_at FROM training_public_materials WHERE problem_id=? AND kind='statement'", (entry['materialKey'],)).fetchone()
            editorial = c.execute("SELECT payload,content_hash,updated_at FROM training_public_materials WHERE problem_id=? AND kind='editorial'", (entry['materialKey'],)).fetchone()
            attempt = c.execute('SELECT payload FROM training_editorial_attempts WHERE problem_id=?',(entry['materialKey'],)).fetchone()
            archived=None
            if row is None:
                archived=self.archive.load()['materials'].get(entry['materialKey'])
            return {'problem':entry['problem'], 'material':json.loads(row[0]) if row else None,
                    'contentHash':row[1] if row else None, 'cachedAt':row[2] if row else None,
                    'editorial':json.loads(editorial[0]) if editorial else None,
                    'editorialHash':editorial[1] if editorial else None,
                    'editorialAttempt':json.loads(attempt[0]) if attempt else None} | (
                    {'material':archived['payload'],'contentHash':archived['contentHash']} if archived else {})
