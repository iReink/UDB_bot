"""Shared SQL predicates for Telegram and web daily discovery."""
from datetime import datetime
import photo_albums as photos

PHOTO_EXISTS = """EXISTS(SELECT 1 FROM photo_batches b JOIN daily_photos p ON p.batch_id=b.id
 WHERE b.daily_id=d.id AND b.chat_id=d.chat_id AND b.decision='attached' AND p.status='ready')"""


def filters(uid, chat, q='', date='', only_mine=False, end_date='', only_photos=False):
    clauses, args = ['d.chat_id=?'], [chat]
    if q:
        clauses.append('instr(CASEFOLD(d.name),CASEFOLD(?))>0')
        args.append(q[:200])
    if date:
        datetime.strptime(date, '%Y-%m-%d')
        if end_date:
            datetime.strptime(end_date, '%Y-%m-%d')
            clauses.append('d.date BETWEEN ? AND ?'); args.extend([date, end_date])
        else:
            clauses.append('d.date=?'); args.append(date)
    if only_mine:
        clauses.append('EXISTS(SELECT 1 FROM daily_participants dp WHERE dp.daily_id=d.id AND dp.user_id=?)')
        args.append(uid)
    if only_photos:
        clauses.append(PHOTO_EXISTS)
    return clauses, args


def page(chat, uid, q='', start='', end='', only_photos=False, page_number=0):
    clauses,args=filters(uid,chat,q,start,end_date=end,only_photos=only_photos)
    where=' AND '.join(clauses)
    with photos.connection() as c:
        c.execute('BEGIN')
        count=c.execute('SELECT COUNT(*) FROM daily_events d WHERE '+where,args).fetchone()[0]
        pages=max(1,(count+5)//6)
        page_number=max(0,min(page_number,pages-1))
        rows=c.execute('SELECT d.*,'+PHOTO_EXISTS+' AS has_photos FROM daily_events d WHERE '+where+
                       ' ORDER BY d.date DESC,d.time DESC,d.id DESC LIMIT 6 OFFSET ?',(*args,page_number*6)).fetchall()
    return [dict(r) for r in rows],count,pages,page_number


def event(chat, daily_id):
    with photos.connection() as c:
        row=c.execute('SELECT * FROM daily_events WHERE id=? AND chat_id=?',(daily_id,chat)).fetchone()
    return dict(row) if row else None
