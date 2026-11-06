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
    with patch.object(server.ROOMS.adapter, "call", return_value=[]):
        phases = []
        for member in members:
            try:
                phases.append(server.ROOMS.handle("POST", "/api/rooms/"+room_id+"/sync", member["uid"])["phase"])
            except server.RoomError as exc:
                with server.connect() as conn:
                    phase = conn.execute("SELECT phase FROM vp_rooms WHERE id=?", (room_id,)).fetchone()[0]
                if exc.status != 409 or phase != "FINISHED":
                    raise
                phases.append(phase)
    # The browser service now also syncs automatically; it may have refreshed a
    # member while this helper was running. The dedicated concurrency tests
    # verify the all-member cutoff guard; this walkthrough needs a settled room.
    if phases[-1] != "FINISHED" or any(p not in ("SETTLING", "FINISHED") for p in phases):
        raise ValueError("Expected the controlled room to finish settlement")
    return {"method":"仅测试库内提前截止，两名成员的 CF 响应均用空列表替代", "phases":phases}


if __name__ == "__main__":
    print(json.dumps(settle(Path(sys.argv[1]).resolve(), sys.argv[2])))
