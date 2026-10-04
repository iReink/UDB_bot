"""Best-effort queue wakeups. SQLite leases remain authoritative."""
import asyncio
import os
import socket
import sqlite3
from pathlib import Path

QUEUES={'tasks','type-checks','search-plans','rag'}
SOCKET=os.getenv('AI_NOTIFY_SOCKET','/run/udb-ai-notify.sock')


def notify(queues):
    if not hasattr(socket,'AF_UNIX') or os.name=='nt':return
    for queue in set(queues)&QUEUES:
        try:
            with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock:
                sock.settimeout(.05)
                sock.sendto(queue.encode(),SOCKET)
        except OSError:pass  # A lost wakeup is recovered by the coordinator.


class Connection(sqlite3.Connection):
    """Publish only after a successful commit, including context-manager commits."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.wakes={}
        self.set_trace_callback(self._observe)

    def _observe(self,sql):
        words=sql.lstrip().lower().split()
        if not words or words[0] not in ('insert','update','delete','replace'):return
        for table,queue in (('ai_tasks','tasks'),('ai_type_checks','type-checks'),('ai_search_plans','search-plans'),('ai_rag_queries','rag')):
            if table in words[:5]:self.wakes.setdefault(queue,self.total_changes)

    def _publish(self):
        queues={q for q,start in self.wakes.items() if self.total_changes>start};self.wakes.clear()
        if queues:notify(queues)

    def commit(self):
        super().commit();self._publish()

    def rollback(self):
        super().rollback();self.wakes.clear()

    def __exit__(self,*args):
        result=super().__exit__(*args)
        if args[0] is None:self._publish()
        else:self.wakes.clear()
        return result


class Coordinator:
    def __init__(self):
        self.versions={q:0 for q in QUEUES}
        self.condition=asyncio.Condition()
        self.transport=None
        self.fallback=None

    async def wake(self,queue):
        if queue not in QUEUES:return
        if queue=='rag' and os.name!='nt':
            try:
                with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock:
                    sock.settimeout(.05);sock.sendto(b'rag',SOCKET+'.rag')
            except OSError:pass
        async with self.condition:
            self.versions[queue]+=1
            self.condition.notify_all()

    async def wait(self,queues,versions,seconds):
        async with self.condition:
            try:
                await asyncio.wait_for(self.condition.wait_for(lambda:any(self.versions[q]!=versions[q] for q in queues)),seconds)
            except asyncio.TimeoutError:pass

    async def start(self):
        owner=self
        class Protocol(asyncio.DatagramProtocol):
            def datagram_received(self,data,address):
                queue=data.decode('ascii',errors='ignore')
                if queue in QUEUES:asyncio.create_task(owner.wake(queue))
        if os.name!='nt':
            Path(SOCKET).unlink(missing_ok=True)
            sock=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM)
            sock.bind(SOCKET);sock.setblocking(False)
            protocol=Protocol()
            def receive():
                try:protocol.datagram_received(sock.recv(64),None)
                except BlockingIOError:pass
            asyncio.get_running_loop().add_reader(sock.fileno(),receive)
            self.transport=sock
            os.chmod(SOCKET,0o600)
        self.fallback=asyncio.create_task(self._fallback())

    async def _fallback(self):
        import ai_runtime
        while True:
            try:
                for queue in await asyncio.to_thread(ai_runtime.ready_queues):await self.wake(queue)
            except sqlite3.OperationalError:
                pass
            await asyncio.sleep(1)

    async def close(self):
        if self.fallback:
            self.fallback.cancel()
            try:await self.fallback
            except asyncio.CancelledError:pass
        if self.transport:
            asyncio.get_running_loop().remove_reader(self.transport.fileno())
            self.transport.close();Path(SOCKET).unlink(missing_ok=True)


coordinator=Coordinator()


def rag_listener(stop,event):
    if os.name=='nt':return
    path=SOCKET+'.rag'
    Path(path).unlink(missing_ok=True)
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock:
            sock.bind(path);os.chmod(path,0o600);sock.settimeout(1)
            while not stop.is_set():
                try:
                    if sock.recv(64)==b'rag':event.set()
                except socket.timeout:continue
    finally:Path(path).unlink(missing_ok=True)
