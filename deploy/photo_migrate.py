"""Run from project root with bot/web stopped, before enabling photo topics."""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
source=root/'stats.db'
backup=root/'backups'/('stats_before_photos_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.db')
backup.parent.mkdir(exist_ok=True)
with sqlite3.connect(f'file:{source.as_posix()}?mode=ro',uri=True) as live:
    result=live.execute('PRAGMA integrity_check').fetchall()
    if result!=[('ok',)]:
        raise RuntimeError(f'Integrity check failed: {result}')
    with sqlite3.connect(backup) as copy:
        live.backup(copy)
        if copy.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
            raise RuntimeError('Backup integrity failed')
print('Verified backup:',backup)
import photo_albums
photo_albums.ensure_schema()
with photo_albums.connection() as c:
    print('Migration complete; configured topics:',c.execute('SELECT COUNT(*) FROM photo_topics').fetchone()[0])
