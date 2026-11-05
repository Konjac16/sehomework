"""Two independent sessions, team acceptance examples; no external service or model key."""
import itertools
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import server
import test_demo
from duo_rules import read_catalog, check_replay, RoomError
from duo_rooms import fetch_cf_events


class DuoHttpTests(unittest.TestCase):
    client = test_demo.HttpTests.client
    request = test_demo.HttpTests.request
    ids = itertools.count(1)

    @classmethod
    def setUpClass(cls):
        test_demo.HttpTests.setUpClass.__func__(cls)
        fixture = server.DuoRooms(server.connect, server.DB_LOCK, server.ADAPTERS["codeforces"], server.ROOT/"data/contests.json")
        cls.catalog_patch = patch.object(server, "ROOMS", fixture)
        cls.catalog_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.catalog_patch.stop()
        test_demo.HttpTests.tearDownClass.__func__(cls)

    def setUp(self):
        n = next(self.ids)
        self.a, self.b, self.c = [self.client(f"duo_{n}_{slot}") for slot in ("host","guest","third")]
        self.now = self.start = 1800000000
        clock = patch.object(server.ROOMS, 'clock', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def call(self, client, path, body=None, status=200):
        code, value = self.request(client, path, body)
        self.assertEqual(code, status, (path, value))
        return value

    def create(self, mode="replay"):
        r = self.call(self.a,"/api/rooms",{"mode":mode,"handle":"alpha"})
        self.room_path="/api/rooms/"+r["id"]
        return r

    def pair(self, mode="replay"):
        r=self.create(mode)
        return self.call(self.b,"/api/rooms/join",{"code":r["inviteCode"].lower(),"handle":"beta"})

    def select(self, r, cid=1):
        return self.call(self.a,self.room_path+"/select",{"version":r["version"],"contestId":cid})

    def test_select_confirm_and_budget_change_are_one_guarded_action(self):
        room = self.pair()
        room = self.call(self.a, self.room_path+"/preferences",
                         {"version":room["version"], "maxMinutes":60, "allowSeen":False})
        choice = {"version":room["version"], "contestId":4, "extendToMinutes":120, "confirmOwner":True}
        self.call(self.b, self.room_path+"/select", choice, 403)
        self.call(self.a, self.room_path+"/select", {**choice, "extendToMinutes":90}, 409)
        self.call(self.a, self.room_path+"/select", {**choice, "version":room["version"]-1}, 409)
        past = test_demo.record(901, index="A")
        past["handle"]="beta"
        past["submission"]["problem"]["contestId"]=4
        self.call(self.b, "/api/import", {"text":json.dumps([past])})
        self.call(self.a, self.room_path+"/select", choice, 409)
        after = self.call(self.a, self.room_path)
        self.assertEqual((after["version"], after["maxMinutes"], after["selected"]),
                         (room["version"], 60, None))
        options = {c["contestId"]:c for c in after["candidates"]}
        self.assertIsNone(options[4]["extendToMinutes"])
        self.assertEqual(options[1]["extendToMinutes"],120)
        selected = self.call(self.a, self.room_path+"/select", {**choice, "contestId":1})
        self.assertEqual(selected["version"], room["version"]+1)
        self.assertEqual(selected["maxMinutes"], 120)
        self.assertEqual([m["ready"] for m in selected["members"]], [True,False])
        self.call(self.a, self.room_path+"/start", {"version":selected["version"]}, 409)

    def test_invite_preview_and_replay_account_name(self):
        room = self.create()
        invite = "/api/rooms/invite?code="+room["inviteCode"].lower()
        preview = self.call(self.b, invite)
        self.assertEqual(preview["memberCount"], 1)
        self.assertIsNone(preview["memberRoomId"])
        self.assertNotIn("alpha", json.dumps(preview))
        self.call(self.b, self.room_path, status=404)
        other = self.client("队友小李")
        joined = self.call(other, "/api/rooms/join", {"code":room["inviteCode"]})
        self.assertEqual(joined["members"][1]["handle"], "队友小李")
        self.assertEqual(self.call(other, invite)["memberRoomId"], room["id"])
        room = self.select(joined)
        self.start_room(room, self.a, other)
        self.assertTrue(self.call(self.b, invite)["issue"])
        self.call(self.b, "/api/rooms/join", {"code":room["inviteCode"]}, 409)
        self.call(self.b, "/api/rooms/invite?code=invalid", status=404)

    def start_room(self,r,*members):
        for client in members or (self.a,self.b):
            self.call(client,self.room_path+"/ready",{"version":r["version"]})
        return self.call(self.a,self.room_path+"/start",{"version":r["version"]})

    def test_membership_selection_privacy_and_stale_confirmation(self):
        fixture=test_demo.record(8521,index="A",source="private source sentinel")
        fixture["handle"]="beta"
        fixture["submission"]["problem"]["contestId"]=4
        self.call(self.b,"/api/import",{"text":json.dumps([fixture])})
        r=self.create()
        # Adding a member later invalidates this valid solo selection and confirmation.
        r=self.select(r)
        self.call(self.a,self.room_path+"/ready",{"version":r["version"]})
        # An existing bound account must retain its own identity.
        self.call(self.b,"/api/rooms/join",{"code":r["inviteCode"],"handle":"ALPHA"},400)
        self.call(self.c,"/api/rooms/join",{"code":r["inviteCode"],"handle":"ALPHA"},409)
        r=self.call(self.b,"/api/rooms/join",{"code":r["inviteCode"],"handle":"beta"})
        self.assertIsNone(r["selected"]); self.assertFalse(any(m["ready"] for m in r["members"]))
        options={c["contestId"]:c for c in r["candidates"]}
        self.assertTrue(options[1]["eligible"]); self.assertFalse(options[4]["eligible"])
        self.assertNotIn("private source sentinel",json.dumps(r)); self.assertNotIn('"tags"',json.dumps(r))
        self.assertNotIn('Spreadsheet',json.dumps(r)); self.assertNotIn('"rating"',json.dumps(r))
        self.call(self.c,self.room_path,status=404)
        self.call(self.b,self.room_path+"/select",{"version":r["version"],"contestId":1},403)
        r=self.select(r)
        self.call(self.a,self.room_path+"/ready",{"version":r["version"]})
        self.call(self.a,self.room_path+"/start",{"version":r["version"]},409)
        new=self.call(self.a,self.room_path+"/preferences",{"version":r["version"],"maxMinutes":60,"allowSeen":False})
        self.assertFalse(any(c["eligible"] for c in new["candidates"]))
        self.assertFalse(any(m["ready"] for m in new["members"]))
        self.call(self.b,self.room_path+"/ready",{"version":r["version"]},409)

    def test_solo_replay_needs_no_other_seat(self):
        r=self.create()
        self.assertTrue(any(x["eligible"] for x in r["candidates"]))
        r=self.select(r)
        self.call(self.a,self.room_path+"/start",{"version":r["version"]},409)
        self.start_room(r,self.a)
        self.call(self.b,"/api/rooms/join",{"code":r["inviteCode"],"handle":"beta"},409)
        done=self.call(self.a,self.room_path+"/replay",{"elapsedSeconds":7200})
        self.assertEqual(done["phase"],"FINISHED")
        self.assertEqual([(x["handle"],x["rank"],x["penalty"]) for x in done["standings"]],[("alpha",1,40)])
        self.assertEqual([t["index"] for t in done["upsolves"]],["B","C"])

    def test_third_member_invalidates_votes_and_gets_own_score_and_queue(self):
        r=self.select(self.pair())
        for user in (self.a,self.b):
            self.call(user,self.room_path+"/ready",{"version":r["version"]})
        old=r["version"]
        # Only the third member has touched contest 4.
        record=test_demo.record(311); record["handle"]="gamma";record["submission"]["problem"]["contestId"]=4
        self.call(self.c,"/api/import",{"text":json.dumps([record])})
        r=self.call(self.c,"/api/rooms/join",{"code":r["inviteCode"],"handle":"gamma"})
        self.assertEqual([m["slot"] for m in r["members"]],[1,2,3])
        self.assertIsNone(r["selected"]); self.assertFalse(any(m["ready"] for m in r["members"]))
        self.assertFalse(next(c for c in r["candidates"] if c["contestId"]==4)["eligible"])
        self.call(self.a,self.room_path+"/start",{"version":old},409)
        fourth=self.client("fourth_capacity")
        self.call(fourth,"/api/rooms/join",{"code":r["inviteCode"],"handle":"delta"},409)
        self.call(fourth,self.room_path,status=404)
        r=self.select(r)
        for user in (self.a,self.b):
            self.call(user,self.room_path+"/ready",{"version":r["version"]})
        self.call(self.a,self.room_path+"/start",{"version":r["version"]},409)
        self.call(self.c,self.room_path+"/ready",{"version":r["version"]})
        self.call(self.a,self.room_path+"/start",{"version":r["version"]})
        r=self.call(self.a,self.room_path+"/replay",{"elapsedSeconds":7200})
        self.assertEqual([(x["slot"],x["rank"],x["penalty"]) for x in r["standings"]],[(2,1,30),(3,1,30),(1,3,40)])
        self.assertEqual([t["index"] for t in self.call(self.c,self.room_path)["upsolves"]],["A","C"])
        self.call(self.c,self.room_path+"/demo-task",{"index":"C","done":True})
        self.assertFalse(next(t for t in self.call(self.b,self.room_path)["upsolves"] if t["index"]=="C")["done"])

    def test_two_accounts_racing_for_last_seat_cannot_overfill(self):
        r=self.pair()
        fourth=self.client("fourth_race")
        def join(entry):
            user,handle=entry
            return self.request(user,"/api/rooms/join",{"code":r["inviteCode"],"handle":handle})[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(join,[(self.c,"gamma"),(fourth,"delta")]))
        self.assertEqual(sorted(statuses),[200,409])
        self.assertEqual([m["slot"] for m in self.call(self.a,self.room_path)["members"]],[1,2,3])

    def test_live_settlement_waits_for_exactly_the_enrolled_members(self):
        for size in (1,3):
            with self.subTest(size=size):
                r=self.create("training")
                enrolled=[self.a]
                if size==3:
                    for who,handle in ((self.b,"beta"),(self.c,"gamma")):
                        r=self.call(who,"/api/rooms/join",{"code":r["inviteCode"],"handle":handle})
                        enrolled.append(who)
                r=self.start_room(self.select(r),*enrolled)
                self.now=r["endAt"]
                with patch.object(server.ROOMS.adapter,"call",return_value=[]):
                    for i,who in enumerate(enrolled,1):
                        r=self.call(who,self.room_path+"/sync",{})
                        self.assertEqual(r["phase"],"FINISHED" if i==size else "SETTLING")
                all_tasks=[self.call(who,self.room_path)["upsolves"] for who in enrolled]
                self.assertTrue(all(len(q)==3 for q in all_tasks))
                self.assertEqual(len({t["taskId"] for q in all_tasks for t in q}),3*size)

    def test_replay_leaderboard_personal_queue_and_repeated_reads(self):
        r=self.start_room(self.select(self.pair()))
        self.assertEqual(r["phase"],"RUNNING")
        self.call(self.a,self.room_path+"/start",{"version":r["version"]},409)
        self.call(self.b,self.room_path+"/replay",{"elapsedSeconds":7200},403)
        r=self.call(self.a,self.room_path+"/replay",{"elapsedSeconds":1800})
        self.assertEqual([(x["slot"],x["rank"],x["solved"],x["penalty"]) for x in r["standings"]],
                         [(2,1,1,30),(1,2,1,40)])
        self.call(self.a,self.room_path+"/replay",{"elapsedSeconds":30},400)
        r=self.call(self.a,self.room_path+"/replay",{"elapsedSeconds":7200})
        self.assertEqual(r["phase"],"FINISHED")
        self.assertEqual([t["index"] for t in r["upsolves"]],["B","C"])
        self.assertTrue(all(t["taskId"] is None for t in r["upsolves"]))
        self.call(self.b,self.room_path+"/demo-task",{"index":"B","done":True})
        a=self.call(self.a,self.room_path); b=self.call(self.b,self.room_path)
        self.assertFalse(a["upsolves"][0]["done"]); self.assertTrue(b["upsolves"][0]["done"])
        self.assertEqual(a["standings"],b["standings"])
        self.assertEqual(self.call(self.a,"/api/dashboard")["tasks"],[])
        self.assertEqual(self.call(self.b,"/api/dashboard")["stats"]["submissions"],0)

    def test_changed_input_changes_both_ranking_and_upsolve(self):
        r=self.select(self.pair())
        self.call(self.b,self.room_path+"/ready",{"version":r["version"]})
        data=json.loads((server.ROOT/"data/replays/02-tie.json").read_text("utf-8"))["events"]
        data.append(dict(data[0]))
        r=self.call(self.a,self.room_path+"/replay-file",{"version":r["version"],"events":data})
        self.assertFalse(any(m["ready"] for m in r["members"]))
        self.start_room(r)
        r=self.call(self.a,self.room_path+"/replay",{"elapsedSeconds":7200})
        self.assertEqual([(x["rank"],x["penalty"]) for x in r["standings"]],[(1,40),(1,40)])
        self.assertEqual([t["index"] for t in self.call(self.b,self.room_path)["upsolves"]],["A","C"])

    def test_live_official_sync_window_settlement_and_reuse_of_existing_tasks(self):
        self.call(self.a,"/api/tasks/create",{"title":"My B","problemKey":"1B"})
        existing=self.call(self.a,"/api/dashboard")["tasks"][0]["id"]
        self.call(self.a,"/api/tasks/update",{"id":existing,"note":"keep this note"})
        r=self.start_room(self.select(self.pair("training")))
        self.call(self.a,self.room_path+"/replay",{"elapsedSeconds":7200},409)
        self.call(self.c,self.room_path+"/sync",{},404)
        pending=True
        def fixture(method,**kw):
            self.assertEqual(method,"contest.status"); self.assertEqual(kw["contestId"],1)
            handle=kw["handle"]
            tuples=([(21,-1,"B","OK"),(22,0,"A","WRONG_ANSWER"),(23,600,"A","COMPILATION_ERROR"),
                     (24,1200,"A","OK"),(25,1300,"B","WRONG_ANSWER"),(26,7200,"B","OK")]
                    if handle=="alpha" else [(41,1800,"A","TESTING" if pending else "OK")])
            rows=[]
            for eid,sec,idx,v in tuples:
                if self.start+sec>self.now: continue
                row=test_demo.record(eid,v,idx)["submission"]
                row.update(contestId=1,creationTimeSeconds=self.start+sec)
                row["problem"]["contestId"]=1; row["author"]["members"]=[{"handle":handle}]
                rows.append(row)
            if handle=="alpha":
                noisy=dict(rows[0], id=9001, creationTimeSeconds=self.start+9,verdict="OK")
                noisy["author"]={"members":[{"handle":"beta"}],"participantType":"PRACTICE"}
                rows.extend([noisy,dict(noisy,id=9002,author={"members":[{"handle":"alpha"}],"participantType":"CONTESTANT"}),rows[-1]])
            return rows[::-1]
        self.now+=1900
        with patch.object(server.ROOMS.adapter,"call",side_effect=fixture):
            self.call(self.a,self.room_path+"/sync",{})
            r=self.call(self.b,self.room_path+"/sync",{})
            self.assertEqual(r["standings"][0]["penalty"],40)
            self.assertEqual(r["standings"][1]["cells"]["A"]["pending"],1)
            with patch.object(server.ROOMS.adapter,"call",side_effect=ValueError("testing offline")):
                self.call(self.a,self.room_path+"/sync",{},503)
            self.assertEqual(self.call(self.a,self.room_path)["standings"],r["standings"])
            self.now=self.start+7200
            self.assertEqual(self.call(self.a,self.room_path)["phase"],"SETTLING")
            self.assertEqual(self.call(self.a,self.room_path+"/sync",{})["phase"],"SETTLING")
            self.assertEqual(self.call(self.b,self.room_path+"/sync",{})["phase"],"SETTLING")
            pending=False
            r=self.call(self.b,self.room_path+"/sync",{})
        self.assertEqual(r["phase"],"FINISHED")
        self.assertEqual([(x["slot"],x["penalty"]) for x in r["standings"]],[(2,30),(1,40)])
        a=self.call(self.a,self.room_path); b=self.call(self.b,self.room_path)
        at=a["upsolves"][0]["taskId"]; bt=b["upsolves"][0]["taskId"]
        self.assertNotEqual(at,bt)
        self.call(self.a,"/api/tasks/update",{"id":bt,"done":True,"completionMode":"independent"},404)
        self.call(self.b,"/api/tasks/update",{"id":bt,"done":True,"completionMode":"independent"})
        self.assertTrue(self.call(self.b,self.room_path)["upsolves"][0]["done"])
        tasks=self.call(self.a,"/api/dashboard")["tasks"]
        self.assertEqual(len(tasks),2); self.assertEqual(next(t["note"] for t in tasks if t["id"]==at),"keep this note")
        self.assertEqual(self.call(self.a,self.room_path)["startAt"],self.start)


class RulesBoundaryTests(unittest.TestCase):
    def test_catalog_and_replay_validation(self):
        catalog=read_catalog(Path(server.ROOT/"data/contests.json"))
        self.assertEqual([x["index"] for x in catalog[4]["problems"]],["A","B","C","D"])
        bad={"eventId":1,"slot":1,"elapsedSeconds":10,"index":"A","verdict":"OK"}
        for change in ({"index":"Z"},{"slot":3},{"slot":4},{"elapsedSeconds":7201},{"verdict":"TESTING"}):
            with self.subTest(change=change), self.assertRaises(RoomError):
                check_replay([dict(bad,**change)],catalog[4],{1,2})
        with self.assertRaises(RoomError):
            check_replay([bad,dict(bad,slot=2)],catalog[4],{1,2})
        self.assertEqual(len(check_replay([bad,bad],catalog[4],{1})),1)
        self.assertEqual(check_replay([dict(bad,slot=3)],catalog[4],{1,2,3})[0]["slot"],3)
