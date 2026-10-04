"""Transactional histogram of message/index states; no history scans on reads."""


def rebuild(conn):
    conn.execute('DELETE FROM ai_rag_counters')
    for scope, where in (('all', '1=1'), ('initial', 'initial=1')):
        conn.execute("""INSERT INTO ai_rag_counters
            SELECT ?,chat_id,reason,eligible,indexed,count(*)
            FROM ai_rag_message_status WHERE """+where+" GROUP BY chat_id,reason,eligible,indexed", (scope,))
    conn.execute("""INSERT INTO ai_rag_counters SELECT 'chunks',0,state,0,0,count(*)
                    FROM ai_rag_chunks GROUP BY state""")


def ensure(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS ai_rag_counters(
        scope TEXT NOT NULL,chat_id INTEGER NOT NULL,reason TEXT NOT NULL,
        eligible INTEGER NOT NULL,indexed INTEGER NOT NULL,n INTEGER NOT NULL CHECK(n>=0),
        PRIMARY KEY(scope,chat_id,reason,eligible,indexed))''')
    if not conn.execute("SELECT 1 FROM ai_rag_state WHERE key='counters_v1'").fetchone():
        rebuild(conn)
        conn.execute("INSERT INTO ai_rag_state VALUES ('counters_v1','1')")
    for table, prefix in (('ai_rag_message_status','messages'),('ai_rag_chunks','chunks')):
        for event, refs in (('INSERT',[('NEW',1)]),('DELETE',[('OLD',-1)]),('UPDATE',[('OLD',-1),('NEW',1)])):
            body=[]
            for ref, sign in refs:
                if prefix=='chunks':
                    buckets=[('chunks', '0', f'{ref}.state', '0', '0', '')]
                else:
                    buckets=[(scope,f'{ref}.chat_id',f'{ref}.reason',f'{ref}.eligible',f'{ref}.indexed',
                              f'{ref}.initial=1' if scope=='initial' else '') for scope in ('all','initial')]
                for scope,chat,reason,eligible,indexed,condition in buckets:
                    where=f"scope='{scope}' AND chat_id={chat} AND reason={reason} AND eligible={eligible} AND indexed={indexed}"
                    if condition:where+=' AND '+condition
                    select=f"SELECT '{scope}',{chat},{reason},{eligible},{indexed},0 WHERE "+(condition+' AND ' if condition else '')+f'NOT EXISTS(SELECT 1 FROM ai_rag_counters WHERE {where})'
                    body.extend([f'INSERT INTO ai_rag_counters {select};',f'UPDATE ai_rag_counters SET n=n+({sign}) WHERE {where};'])
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS rag_count_{prefix}_{event.lower()} AFTER {event} ON {table} BEGIN {' '.join(body)} END")


def rows(conn, scope):
    return conn.execute('SELECT chat_id,reason,eligible,indexed,n FROM ai_rag_counters WHERE scope=? AND n>0',(scope,)).fetchall()
