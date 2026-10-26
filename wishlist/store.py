import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .grouping import same_type


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS items (
              id TEXT PRIMARY KEY, raw TEXT NOT NULL, title TEXT NOT NULL,
              local_title TEXT, notes TEXT NOT NULL DEFAULT '',
              group_label TEXT NOT NULL DEFAULT '', group_manual INTEGER NOT NULL DEFAULT 0,
              task_id TEXT, state TEXT NOT NULL DEFAULT 'inbox',
              remote_present INTEGER NOT NULL DEFAULT 1, pending INTEGER NOT NULL DEFAULT 0,
              error TEXT, updated TEXT NOT NULL, local_keep INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, criteria TEXT NOT NULL DEFAULT '',
              state TEXT NOT NULL DEFAULT 'queued', created TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT,
              action TEXT NOT NULL, created TEXT NOT NULL);
            ''')
            columns = {r['name'] for r in c.execute('PRAGMA table_info(items)')}
            if 'local_keep' not in columns:
                c.execute('ALTER TABLE items ADD COLUMN local_keep INTEGER NOT NULL DEFAULT 0')

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=20)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def meta(self, key, default=None):
        with self.db() as c:
            row = c.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.db() as c:
            c.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def import_snapshot(self, items, evidence):
        if evidence.get('complete') is not True or evidence.get('ok') is not True:
            raise ValueError('收藏未完整读取，本地清单保持原样')
        ids = [str(x.get('id', '')) for x in items]
        if any(not x.isdigit() for x in ids) or len(set(ids)) != len(ids):
            raise ValueError('收藏编号缺失或重复，本地清单保持原样')
        with self.db() as c:
            c.execute('UPDATE items SET remote_present=0')
            # Refresh only remote-owned fields. Local edits and tombstones survive.
            for item in items:
                item_id, title = str(item['id']), str(item.get('title') or '无标题商品')
                c.execute('''INSERT INTO items(id,raw,title,updated) VALUES (?,?,?,?)
                  ON CONFLICT(id) DO UPDATE SET raw=excluded.raw,title=excluded.title,
                  remote_present=1,local_keep=0,updated=excluded.updated''',
                  (item_id, json.dumps(item, ensure_ascii=False), title, now()))
                row = c.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
                if row['state'] == 'remote_removed':
                    c.execute("UPDATE items SET state='inbox' WHERE id=?", (item_id,))
                elif row['state'] in ('queued', 'removed', 'purchased'):
                    # Tombstones stay hidden even if reimported; sync enforces removal.
                    c.execute('UPDATE items SET pending=1 WHERE id=?', (item_id,))
                if not row['group_label']:
                    candidates = c.execute('SELECT title,group_label FROM items WHERE id!=? AND group_label!=?', (item_id, '')).fetchall()
                    label = next((r['group_label'] for r in candidates if same_type(title, r['title'])), title[:180])
                    c.execute('UPDATE items SET group_label=? WHERE id=?', (label, item_id))
            c.execute("UPDATE items SET state='remote_removed' WHERE remote_present=0 AND state='inbox' AND local_keep=0")
            c.execute('UPDATE items SET pending=0,error=NULL WHERE remote_present=0')
            c.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('last_read', json.dumps(evidence, ensure_ascii=False)))

    def snapshot(self):
        with self.db() as c:
            rows = c.execute('SELECT * FROM items ORDER BY rowid').fetchall()
            tasks = [dict(r) for r in c.execute('SELECT * FROM tasks ORDER BY created DESC')]
        result = []
        for row in rows:
            item = dict(row)
            raw = json.loads(item.pop('raw'))
            item.update(price=raw.get('price'), image=raw.get('picUrl'), seller=raw.get('userNick'),
                        invalid=bool(raw.get('itemDeleted')), url='https://www.goofish.com/item?id=' + item['id'])
            item['display_title'] = item['local_title'] if item['local_title'] is not None else item['title']
            result.append(item)
        return {'items': result, 'tasks': tasks, 'last_read': self.meta('last_read'),
                'last_sync': self.meta('last_sync'), 'pending': sum(bool(x['pending']) for x in result)}

    def edit(self, item_id, title, notes, group_label):
        title, group_label = title.strip(), group_label.strip()
        if not title or not group_label:
            raise ValueError('名称和比价类型不能为空')
        with self.db() as c:
            row = self._item(c, item_id)
            if row['state'] == 'queued' and row['task_id'] and group_label != row['group_label']:
                # Reassign atomically, leaving other task members alone.
                task_id = self._task(c, group_label)
                c.execute('UPDATE items SET task_id=? WHERE id=?', (task_id, item_id))
                self._retire_empty(c, row['task_id'])
            c.execute('UPDATE items SET local_title=?,notes=?,group_label=?,group_manual=1,updated=? WHERE id=?',
                      (title, notes, group_label, now(), item_id))

    def stage(self, item_id, action):
        if action not in ('queue', 'remove', 'purchase', 'undo', 'restore'):
            raise ValueError('不支持的操作')
        with self.db() as c:
            row = self._item(c, item_id)
            old_task = row['task_id']
            if action == 'undo':
                if not row['pending']:
                    raise ValueError('远端已同步，无法撤销取消收藏；可恢复到本地待整理')
                state, task_id, pending = 'inbox', None, 0
            elif action == 'restore':
                # Local recovery never implicitly adds a remote favorite.
                state, task_id, pending = 'inbox', None, 0
            elif action == 'queue':
                task_id = self._task(c, row['group_label'] or row['local_title'] or row['title'])
                state, pending = 'queued', int(row['remote_present'])
            else:
                state, task_id, pending = ('removed' if action == 'remove' else 'purchased'), None, int(row['remote_present'])
            local_keep = int(action == 'restore' and not row['remote_present'])
            c.execute('UPDATE items SET state=?,task_id=?,pending=?,local_keep=?,error=NULL,updated=? WHERE id=?',
                      (state, task_id, pending, local_keep, now(), item_id))
            if old_task and old_task != task_id:
                self._retire_empty(c, old_task)
            c.execute('INSERT INTO events(item_id,action,created) VALUES (?,?,?)', (item_id, action, now()))

    @staticmethod
    def _item(c, item_id):
        row = c.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
        if row is None:
            raise ValueError('商品不存在')
        return row

    @staticmethod
    def _task(c, label):
        row = c.execute("SELECT id FROM tasks WHERE title=? AND state='queued'", (label,)).fetchone()
        if row:
            return row['id']
        task_id = str(uuid.uuid4())
        c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)', (task_id, label, '', 'queued', now(), now()))
        return task_id

    @staticmethod
    def _retire_empty(c, task_id):
        n = c.execute('SELECT count(*) FROM items WHERE task_id=?', (task_id,)).fetchone()[0]
        if not n:
            c.execute("UPDATE tasks SET state='archived',updated=? WHERE id=?", (now(), task_id))

    def edit_task(self, task_id, title, criteria):
        if not title.strip():
            raise ValueError('比价类型不能为空')
        with self.db() as c:
            if not c.execute('SELECT id FROM tasks WHERE id=?', (task_id,)).fetchone():
                raise ValueError('任务不存在')
            other = c.execute("SELECT * FROM tasks WHERE title=? AND state='queued' AND id!=?", (title.strip(), task_id)).fetchone()
            if other:
                combined = '\n'.join(x for x in (other['criteria'], criteria) if x)
                c.execute('UPDATE items SET task_id=?,group_label=?,group_manual=1 WHERE task_id=?', (other['id'], title.strip(), task_id))
                c.execute('UPDATE tasks SET criteria=?,updated=? WHERE id=?', (combined, now(), other['id']))
                c.execute("UPDATE tasks SET state='archived',updated=? WHERE id=?", (now(), task_id))
            else:
                c.execute('UPDATE tasks SET title=?,criteria=?,updated=? WHERE id=?', (title.strip(), criteria, now(), task_id))
                c.execute('UPDATE items SET group_label=?,group_manual=1 WHERE task_id=?', (title.strip(), task_id))

    def purchase_task(self, task_id):
        with self.db() as c:
            if not c.execute('SELECT id FROM tasks WHERE id=?', (task_id,)).fetchone():
                raise ValueError('任务不存在')
            c.execute("UPDATE tasks SET state='purchased',updated=? WHERE id=?", (now(), task_id))
            c.execute("UPDATE items SET state='purchased',pending=remote_present,updated=? WHERE task_id=?", (now(), task_id))
            c.execute('INSERT INTO events(item_id,action,created) VALUES (?,?,?)', (task_id, 'purchase_type', now()))

    def pending_items(self):
        with self.db() as c:
            return [dict(r) for r in c.execute('SELECT id,state,task_id FROM items WHERE pending=1')]

    def fail(self, item_id, message):
        with self.db() as c:
            c.execute('UPDATE items SET error=? WHERE id=?', (message, item_id))

    def backup(self, path):
        with self.db() as c, sqlite3.connect(path) as target:
            c.backup(target)
