"""One background worker refreshes room seats; HTTP polling never calls Codeforces."""
import threading


class RoomAutoSync:
    def __init__(self, rooms):
        self.rooms = rooms
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name="vp-room-sync", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        # An in-flight paginated refresh has its own bounded requests. Once it
        # finishes, the worker will not start another seat on exit.
        self.thread.join(timeout=1)

    def _run(self):
        while not self.stopped.is_set():
            try:
                attempted = self.rooms.auto_sync_once()
            except Exception:
                # A temporary database failure must not permanently kill synchronization.
                attempted = False
            if self.stopped.wait(0.1 if attempted else 1):
                break
