"""Advance only the browser harness's disposable training room. CF responses are test fixtures."""
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch


def settle(test_db, room_id):
    if test_db.name != "test.sqlite3" or not test_db.parent.name.startswith("vplab-duo-") or not test_db.is_file():
        raise ValueError("Expected the temporary database created by duo_browser.cjs")
    os.environ["VPLAB_DB"] = str(test_db)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import server

    with server.connect() as conn:
        room = conn.execute("SELECT mode,phase,start_at,end_at FROM vp_rooms WHERE id=?", (room_id,)).fetchone()
        members = list(conn.execute("SELECT uid,handle FROM vp_members WHERE room_id=? ORDER BY slot", (room_id,)))
        if (not room or room["mode"] != "training" or room["phase"] != "RUNNING"
                or [m["handle"] for m in members] != ["vp_fixture_a","vp_fixture_b"]):
            raise ValueError("Expected the two live-mode fixtures from the browser walkthrough")
        duration = room["end_at"] - room["start_at"]
        end = int(time.time()) - 1
        conn.execute("UPDATE vp_rooms SET start_at=?,end_at=? WHERE id=?", (end-duration,end,room_id))
    with patch.object(server.ROOMS.adapter, "call", return_value=[]) as cf:
        phases = [server.ROOMS.handle("POST", "/api/rooms/"+room_id+"/sync", m["uid"])["phase"] for m in members]
    if phases != ["SETTLING","FINISHED"] or cf.call_count != 2:
        raise ValueError("Expected settlement only after syncing both members")
    return {"method":"仅测试库内提前截止，两名成员的 CF 响应均用空列表替代", "phases":phases}


if __name__ == "__main__":
    print(json.dumps(settle(Path(sys.argv[1]).resolve(), sys.argv[2])))
