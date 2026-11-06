from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from zipfile import ZipFile
import time

from adapters import CodeforcesAdapter
from training_archive import TrainingArchive
from recommendation_pool import SmartRecommendationPool, PoolError
from training_service import TrainingService, DEFAULTS, day_key


def write_archive(path, count=130, extra_alias=False, changed_body=False):
    problems, statements = [], {}
    for i in range(count):
        idx = f'A{i+1}'
        problems.append({'problemKey':f'cf:1:{idx}', 'contestId':1, 'index':idx,
                         'name':f'Fixture {idx}', 'rating':1800, 'tags':['hidden-algorithm']})
        statements[idx] = {'name':f'Fixture {idx}',
            'body':('Describe an independent public problem with sufficiently detailed conditions. '*2)+idx,
            'input_format':'Read an integer.', 'output_format':'Print an integer.',
            'examples':[{'input':str(i), 'output':str(i)}], 'body_source':'openr1', 'example_source':'openr1',
            'time_limit':'2 seconds', 'memory_limit':'256 megabytes'}
    if changed_body:
        statements['A1']['body'] += ' Corrected public condition.'
    contests = [{'contestId':1, 'name':'Fixture contest', 'durationSeconds':100,
        'startTimeSeconds':100, 'phase':'FINISHED', 'problemSetComplete':True,
        'problemKeys':[p['problemKey'] for p in problems]}]
    if extra_alias:
        problems.append({'problemKey':'cf:2:A1','contestId':2,'index':'A1',
                         'name':'A shared problem under another title','rating':1800,'tags':[]})
        contests.append({**contests[0], 'contestId':2, 'problemKeys':['cf:2:A1']})
    with ZipFile(path, 'w') as z:
        z.writestr('catalog.json', json.dumps({'schemaVersion':'1.0','mode':'real_snapshot',
                     'snapshot':'fixture','problems':problems,'contests':contests}))
        z.writestr('statements/1.json', json.dumps(statements))
        if extra_alias:
            z.writestr('statements/2.json', json.dumps({'A1':statements['A1']}))


def seed_catalog(db, pack):
    """Offline browser fixture: populate the same cache used by ordinary daily practice."""
    with ZipFile(pack) as z:
        doc=json.loads(z.read('catalog.json'))
    problems=[{'platform':'codeforces','contestId':p['contestId'],'index':p['index'],
        'problemKey':f"{p['contestId']}{p['index']}",'externalId':f"{p['contestId']}{p['index']}",
        'title':p['name'],'rating':p['rating'],'tags':p['tags'],
        'url':f"https://codeforces.com/contest/{p['contestId']}/problem/{p['index']}"} for p in doc['problems']]
    c=sqlite3.connect(db)
    try:
        with c:
            c.execute('CREATE TABLE IF NOT EXISTS training_catalog(platform TEXT PRIMARY KEY,payload TEXT,updated_at INTEGER)')
            c.execute("INSERT OR REPLACE INTO training_catalog VALUES('codeforces',?,?)",(json.dumps(problems),int(time.time())))
            SmartRecommendationPool.init_tables(c)
            seed_editorials(c,[p['problemKey'] for p in doc['problems']])
    finally:c.close()


def seed_editorials(c, keys):
    payload={'text':'Public fixture editorial with a precise algorithm and explanation. '*4,
             'codeBlocks':[{'text':'int main() { return 0; }'}],
             'sourceUrl':'https://codeforces.com/blog/entry/1','method':'fixture','warnings':[]}
    for key in keys:
        c.execute('INSERT OR REPLACE INTO training_public_materials VALUES(?,?,?,?,?)',
                  (key,'editorial','fixture',json.dumps(payload),1000))


class PoolPreparationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root/'test.sqlite3'
        self.pack = self.root/'pool.zip'
        write_archive(self.pack)
        self.now = 1000
        with self.connect() as c:
            c.executescript('''CREATE TABLE users(id INTEGER PRIMARY KEY,settings TEXT);
              CREATE TABLE submissions(user_id INTEGER,platform TEXT,external_id TEXT,payload TEXT,
                PRIMARY KEY(user_id,platform,external_id));
              CREATE TABLE training_runs(id INTEGER PRIMARY KEY,user_id INTEGER,status TEXT,problem TEXT);''')
            for uid, handle in ((1,'alice'),(2,'bob')):
                c.execute('INSERT INTO users VALUES(?,?)', (uid,json.dumps({'handle':handle,'profile':{'rating':1800}})))
            TrainingService.init_tables(c)
            SmartRecommendationPool.init_tables(c)
            if getattr(self,'seed_materials',True):seed_editorials(c,[f'cf:1:A{i}' for i in range(1,131)])
        self.pool = SmartRecommendationPool(self.connect, threading.RLock(), TrainingArchive(self.pack), lambda:self.now)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def record(self, uid=1, sid='1', **fields):
        row = {'platform':'codeforces','handle':'alice' if uid==1 else 'bob', 'externalId':sid,
            'contestId':1,'index':'A1','verdict':'WRONG_ANSWER','submittedAt':150,
            'participantType':'CONTESTANT','participantMembers':['alice' if uid==1 else 'bob'],
            'partyStartTime':None,'teamId':None,'submissionContestId':1,'source':'private-code-never-in-public-cache'}
        row.update(fields)
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO submissions VALUES(?,?,?,?)',(uid,'codeforces',sid,json.dumps(row)))

    def material_rows(self):
        with self.connect() as c:
            return {r['problem_id']:dict(r) for r in c.execute('SELECT * FROM training_public_materials')}

    def test_capacity_repeat_cache_and_no_spoilers(self):
        a = self.pool.refresh(1)
        self.assertEqual((a['candidateCount'],a['reserveCount'],a['statementReady']),(100,30,100))
        self.assertNotIn('hidden-algorithm', json.dumps(a))
        self.assertNotIn('"tags"', json.dumps(a))
        self.assertTrue(a['selectionReady'])
        before = self.material_rows()
        self.now += 50
        b = self.pool.refresh(1)
        self.assertEqual(a['version'], b['version'])
        self.assertEqual(a['candidates'], b['candidates'])
        self.assertEqual(before, self.material_rows())
        self.assertNotIn('private-code',json.dumps(before))

    def test_ac_refills_without_deleting_material(self):
        a = self.pool.refresh(1); first=a['candidates'][0]
        self.record(sid='2',index=first['problem']['index'],verdict='OK',submittedAt=300,participantType='PRACTICE')
        self.assertTrue(self.pool.state(1)['needsRefresh'])
        b = self.pool.refresh(1)
        self.assertEqual((b['candidateCount'],b['reserveCount']),(100,29))
        self.assertNotIn(first['id'],[p['id'] for p in b['candidates']])
        self.assertTrue(self.pool.material(1,first['id'])['material']['statement']['body'])

    def test_rating_changes_recheck_pool_but_keep_materials(self):
        self.pool.refresh(1); before=self.material_rows()
        with self.connect() as c:
            c.execute("UPDATE users SET settings=? WHERE id=1",(json.dumps({'handle':'alice','profile':{'rating':3000}}),))
        state=self.pool.refresh(1)
        self.assertEqual((state['referenceRating'],state['candidateCount']),(3000,0))
        self.assertEqual(before,self.material_rows())

    def test_missing_rating_is_not_replaced_with_user_default(self):
        cat=deepcopy(self.pool.archive.load())
        cat['problems']['cf:1:A1']['rating']=None
        with patch.object(self.pool.archive,'load',return_value=cat):
            result=self.pool.refresh(1)
        self.assertEqual(result['excluded']['unrated'],1)
        self.assertNotIn('cf:1:A1',[p['id'] for p in result['candidates']])

    def test_other_user_cannot_read_prepared_material(self):
        key=self.pool.refresh(1)['candidates'][0]['id']
        with self.assertRaises(PoolError) as failure:self.pool.material(2,key)
        self.assertEqual(failure.exception.status,404)
        self.assertEqual(self.pool.refresh(2)['candidateCount'],100)

    def test_no_contest_participation_is_required(self):
        result=self.pool.refresh(1)
        self.assertEqual(result['candidateCount'],100)
        self.assertNotIn('sessionCount',result)

    def test_online_catalog_adds_problem_without_archived_statement(self):
        with self.connect() as c:seed_editorials(c,['cf:9999:A'])
        self.pool.catalog_loader=lambda:[{'platform':'codeforces','contestId':9999,'index':'A',
            'problemKey':'9999A','title':'New problem','rating':1800,
            'url':'https://codeforces.com/contest/9999/problem/A','tags':['hidden-algorithm']}]
        result=self.pool.refresh(1)
        self.assertEqual(result['catalogScope'],'cf_problemset')
        self.assertEqual((result['candidateCount'],result['statementReady']),(1,0))
        self.assertEqual(result['candidates'][0]['id'],'cf:9999:A')
        self.assertNotIn('hidden-algorithm',json.dumps(result))

    def test_old_public_cache_is_preserved_without_reusing_old_pool(self):
        with self.connect() as c:
            c.execute('DELETE FROM training_public_materials')
            c.execute('CREATE TABLE upsolving_public_materials AS SELECT * FROM training_public_materials')
            c.execute('INSERT INTO upsolving_public_materials VALUES(?,?,?,?,?)',
                      ('cf:9999:A','statement','old-hash','{"body":"saved statement"}',123))
            SmartRecommendationPool.init_tables(c)
            SmartRecommendationPool.init_tables(c)
            self.assertEqual(c.execute('SELECT count(*) FROM training_public_materials').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT payload FROM training_public_materials').fetchone()[0],
                             '{"body":"saved statement"}')
        self.assertFalse(self.pool.state(1)['prepared'])

    def test_attempted_toggle_applies_to_practice_and_contest_records(self):
        self.record(participantType='PRACTICE',participantMembers=None)
        result=self.pool.refresh(1)
        self.assertEqual(result['excluded']['attempted'],1)
        self.assertNotIn('cf:1:A1',[p['id'] for p in result['candidates']])
        self.preferences(includeAttempted=True)
        self.assertEqual(self.pool.refresh(1)['reserveCount'],30)

    def preferences(self, **values):
        self.pool.settings(1,values)

    def test_smart_difficulty_is_independent_of_daily_reference(self):
        self.preferences(difficultyMode='manual',minRating=2000,maxRating=2400)
        result=self.pool.refresh(1)
        self.assertEqual(result['candidateCount'],0)
        self.assertEqual(result['range'],[2000,2400])
        self.preferences(difficultyMode="default")
        with self.connect() as c:c.execute('INSERT OR REPLACE INTO training_profiles(user_id,preferences,reference,cf_rating) VALUES(?,?,?,?)',(1,json.dumps(DEFAULTS),1900,1800))
        result=self.pool.refresh(1)
        self.assertEqual(result['referenceRating'],1800)
        self.assertEqual(result['range'],[1600,2000])

    def test_daily_cooldown_and_day_changes_do_not_affect_smart_pool(self):
        self.preferences(includeAttempted=True)
        with self.connect() as c:c.execute('INSERT INTO training_cooldowns VALUES(?,?,?,?,?)',
            (1,'codeforces','1A1',day_key(self.now),'not_now'))
        result=self.pool.refresh(1)
        self.assertNotIn('cooldown',result['excluded'])
        self.assertIn('cf:1:A1',[p['id'] for p in result['candidates']])
        self.now+=86400
        self.assertFalse(self.pool.state(1)['needsRefresh'])
        self.assertEqual(self.pool.refresh(1)['reserveCount'],30)

    def test_daily_active_is_independent(self):
        with self.connect() as c:
            c.execute('INSERT INTO training_runs(user_id,status,problem) VALUES(?,?,?)',(1,'active',json.dumps(
                {'platform':'codeforces','contestId':1,'index':'A1'})))
        result=self.pool.refresh(1)
        self.assertNotIn('in_daily_training',result['excluded'])
        self.assertIn('cf:1:A1',[p['id'] for p in result['candidates']])

    def test_bad_archive_does_not_replace_previous_pool(self):
        before=self.pool.refresh(1)
        self.pack.write_bytes(b'broken')
        with self.assertRaises(ValueError):self.pool.refresh(1)
        self.assertEqual(before,self.pool.state(1))

    def test_content_change_refreshes_only_changed_public_material(self):
        self.pool.refresh(1);before=self.material_rows();self.now+=100
        write_archive(self.pack,changed_body=True)
        self.pool.refresh(1);after=self.material_rows()
        changed=[k for k in before if before[k]!=after[k]]
        self.assertEqual(changed,['cf:1:A1'])

    def test_exact_alias_ac_excludes_same_material_in_other_contest(self):
        write_archive(self.pack,extra_alias=True)
        self.record(sid='2',contestId=2,submissionContestId=2,verdict='OK',participantType='PRACTICE')
        result=self.pool.refresh(1)
        self.assertEqual(result['excluded']['accepted'],1)
        self.assertNotIn('cf:1:A1',[p['id'] for p in result['candidates']])

    def test_changed_inputs_during_preparation_do_not_publish_stale_pool(self):
        before=self.pool.refresh(1)
        prepare=self.pool._prepare
        def update(*args):
            result=prepare(*args)
            self.record(sid='2',verdict='OK',participantType='PRACTICE')
            return result
        with patch.object(self.pool,'_prepare',side_effect=update):
            with self.assertRaises(PoolError) as failure:self.pool.refresh(1)
        self.assertEqual(failure.exception.status,409)
        after=self.pool.state(1)
        self.assertEqual(before['version'],after['version'])
        self.assertTrue(after['needsRefresh'])

    def test_small_pool_does_not_invent_candidates(self):
        write_archive(self.pack,count=3)
        result=self.pool.refresh(1)
        self.assertEqual((result['candidateCount'],result['reserveCount']),(3,0))


class ParticipationNormalizationTests(unittest.TestCase):
    def test_preserve_virtual_identity_and_start_without_fabricating_missing_fields(self):
        raw={'id':123,'contestId':1,'creationTimeSeconds':950,'problem':{'contestId':1,'index':'A'},
             'author':{'participantType':'VIRTUAL','members':[{'handle':'alice'}],'startTimeSeconds':900}}
        adapter=CodeforcesAdapter()
        row=adapter.normalize(raw)
        self.assertEqual((row['participantMembers'],row['partyStartTime'],row['submissionContestId']),(['alice'],900,1))
        raw['author']={'members':[{'handle':'alice'}]}
        self.assertIsNone(adapter.normalize(raw)['partyStartTime'])


import server
import test_demo


class PoolHttpTests(unittest.TestCase):
    client = test_demo.HttpTests.client
    request = test_demo.HttpTests.request

    @classmethod
    def setUpClass(cls):
        test_demo.HttpTests.setUpClass.__func__(cls)
        pack=Path(cls.tmp.name)/'pool.zip'
        write_archive(pack,count=3)
        cls.archive_patch=patch.object(server.INTELLIGENT,'archive',TrainingArchive(pack))
        cls.archive_patch.start()
        cls.loader_patch=patch.object(server.INTELLIGENT,'catalog_loader',None)
        cls.loader_patch.start()
        with server.connect() as c:seed_editorials(c,[f'cf:1:A{i}' for i in range(1,4)])

    @classmethod
    def tearDownClass(cls):
        cls.loader_patch.stop()
        cls.archive_patch.stop()
        test_demo.HttpTests.tearDownClass.__func__(cls)

    def test_routes_authorization_and_parameter_boundaries(self):
        import urllib.request
        anonymous=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.assertEqual(self.request(anonymous,'/api/intelligent/pool')[0],401)
        owner,other=self.client('pool_owner'),self.client('pool_other')
        raw=test_demo.record(500,'WRONG_ANSWER','A1')
        s=raw['submission'];s['contestId']=1;s['problem']['contestId']=1;s['creationTimeSeconds']=150
        s['author']['participantType']='CONTESTANT'
        self.assertEqual(self.request(owner,'/api/import',{'text':json.dumps([raw])})[0],200)
        self.assertEqual(self.request(owner,'/api/intelligent/pool')[1]['prepared'],False)
        self.assertEqual(self.request(owner,'/api/intelligent/prepare',{'rating':5000})[0],400)
        self.request(owner,'/api/intelligent/settings',{'includeAttempted':True})
        status,result=self.request(owner,'/api/intelligent/prepare',{})
        self.assertEqual(status,200)
        self.assertEqual(result['candidateCount'],3)
        self.assertNotIn('"tags"',json.dumps(result))
        self.assertEqual(self.request(other,'/api/intelligent/material?id=cf:1:A1')[0],404)
        self.assertEqual(self.request(owner,'/api/intelligent/material?id=cf:1:A1')[0],200)
        with patch.object(server.ADAPTERS['codeforces'],'sync',side_effect=ValueError('CF unavailable')):
            self.assertEqual(self.request(owner,'/api/intelligent/sync',{})[0],400)
        self.assertEqual(self.request(owner,'/api/intelligent/pool')[1]['candidates'],result['candidates'])
        self.assertEqual(self.request(owner,'/api/intelligent/complete',{'done':True})[0],404)

    def test_sync_uses_bound_handle_and_reconciles_ac(self):
        owner=self.client('pool_sync')
        raw=test_demo.record(501,'WRONG_ANSWER','A1')
        s=raw['submission'];s['contestId']=1;s['problem']['contestId']=1;s['creationTimeSeconds']=150
        s['author']['participantType']='CONTESTANT'
        self.request(owner,'/api/import',{'text':json.dumps([raw])})
        normalized=server.ADAPTERS['codeforces'].normalize(raw)
        ac={**normalized,'externalId':'502','verdict':'OK','submittedAt':250,'participantType':'PRACTICE'}
        result={'records':[normalized,ac],'scope':'Fixture bounded sync','profile':{'handle':'tester','rating':1800}}
        with patch.object(server.ADAPTERS['codeforces'],'sync',return_value=result) as sync:
            status,prepared=self.request(owner,'/api/intelligent/sync',{})
        self.assertEqual(status,200);sync.assert_called_once_with('tester')
        self.assertEqual(prepared['candidateCount'],2)
        self.assertNotIn('cf:1:A1',[p['id'] for p in prepared['candidates']])
