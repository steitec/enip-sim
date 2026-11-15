"""Temporary CI diagnostic: how long do short waits really take here?"""
import select, socket, statistics, threading, time

def measure(name, fn, n=20):
    xs = []
    for _ in range(n):
        t = time.monotonic(); fn(); xs.append((time.monotonic() - t) * 1000)
    print(f"{name:32} median {statistics.median(xs):7.1f} ms  max {max(xs):7.1f} ms")

ev = threading.Event()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("127.0.0.1", 0))
measure("time.sleep(0.02)", lambda: time.sleep(0.02))
measure("Event.wait(0.02)", lambda: ev.wait(0.02))
measure("Event.wait(0.05)", lambda: ev.wait(0.05))
measure("select(0.02)", lambda: select.select([s], [], [], 0.02))

def in_thread(fn):
    def run():
        measure("thread: " + fn.__name__, fn)
    t = threading.Thread(target=run); t.start(); t.join()

def sleep20(): time.sleep(0.02)
def wait20(): ev.wait(0.02)
in_thread(sleep20); in_thread(wait20)

# a busy receive thread alongside (like the engine's recv loop)
stop = threading.Event()
s.settimeout(0.05)
def rx():
    while not stop.is_set():
        try: s.recvfrom(100)
        except socket.timeout: pass
t = threading.Thread(target=rx); t.start()
measure("Event.wait(0.02) + recv thread", lambda: ev.wait(0.02))
stop.set(); t.join()
