"""Shared checkpoint chunks and bounded history at coordinator exit."""
import json
import sqlite3
from contextlib import closing, contextmanager
import threading
import zlib
import re

import ormsgpack
from langgraph.checkpoint.sqlite import SqliteSaver as BaseSqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


CHUNK_TYPE = 'edge-chunks-v1'
SMALL_PREFIX = 'edge-zlib-v1:'
CHUNK_SIZE = 16384
HISTORY_LIMIT = 32
ANCHORS = re.compile(b'\xa7profile|\xa8snapshot|\xa7current|\xadmapping_cache|\xa8proposal|\xa7results|\xadchannel_values|\xaclast_receipt')


class ChunkSerializer:
    def __init__(self, conn, lock, base=None):
        self.conn, self.lock = conn, lock
        self.base = base or JsonPlusSerializer()
        conn.execute('CREATE TABLE IF NOT EXISTS state_chunks '
                     '(id INTEGER PRIMARY KEY, payload BLOB NOT NULL, raw_size INTEGER NOT NULL, packed_size INTEGER NOT NULL)')
        conn.execute('CREATE INDEX IF NOT EXISTS chunk_sizes ON state_chunks (raw_size,packed_size)')
        conn.commit()

    def encode_raw(self, kind, payload):
        """Keep exact serialized bytes, including typed interrupt/child state."""
        if len(payload) < 4096:
            packed = zlib.compress(payload, 1)
            return (SMALL_PREFIX + kind, packed) if len(packed) < len(payload) else (kind, payload)
        refs = []
        # Literal key boundaries align repeated state objects without changing bytes.
        boundaries = [0] + [m.start() for m in ANCHORS.finditer(payload) if m.start()] + [len(payload)]
        with self.lock:
            for start, end in zip(boundaries, boundaries[1:]):
                for offset in range(start, end, CHUNK_SIZE):
                    raw = payload[offset:min(offset + CHUNK_SIZE,end)]
                    packed = zlib.compress(raw, 3)
                    row = self.conn.execute('SELECT id FROM state_chunks WHERE raw_size=? AND packed_size=? AND payload=?',
                                            (len(raw),len(packed),packed)).fetchone()
                    if row is None:
                        ref = self.conn.execute('INSERT INTO state_chunks (payload,raw_size,packed_size) VALUES (?,?,?)',
                                                (packed,len(raw),len(packed))).lastrowid
                    else:
                        ref = row[0]
                    refs.append(ref)
        return CHUNK_TYPE, ormsgpack.packb([kind, len(payload), refs])

    def decode_raw(self, typed):
        kind, payload = typed
        if kind == CHUNK_TYPE:
            original, size, refs = ormsgpack.unpackb(payload)
            parts = []
            with self.lock:
                for ref in refs:
                    row = self.conn.execute('SELECT payload,raw_size FROM state_chunks WHERE id=?', (ref,)).fetchone()
                    if row is None:
                        raise ValueError('checkpoint_chunk_missing')
                    raw = zlib.decompress(row[0])
                    if len(raw) != row[1]:
                        raise ValueError('checkpoint_chunk_size_mismatch')
                    parts.append(raw)
            restored = b''.join(parts)
            if len(restored) != size:
                raise ValueError('checkpoint_size_mismatch')
            return original, restored
        if kind and kind.startswith(SMALL_PREFIX):
            return kind[len(SMALL_PREFIX):], zlib.decompress(payload)
        return kind, payload

    def dumps_typed(self, obj):
        return self.encode_raw(*self.base.dumps_typed(obj))

    def loads_typed(self, typed):
        return self.base.loads_typed(self.decode_raw(typed))


class SqliteSaver(BaseSqliteSaver):
    """Keep recent recovery state, compact command links, and live child graphs.

    Maintenance runs after the coordinator has finished using the connection,
    never while graph tasks or checkpoint writers are still running. Writer
    journals, final outcomes and current channel values are not truncated.
    """
    def __init__(self, conn, *, serde=None):
        super().__init__(conn, serde=serde)
        self.lock = threading.RLock()
        self.setup()
        self.serde = ChunkSerializer(conn, self.lock, self.serde)
        self.dirty = False
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS command_links (
                thread_id TEXT NOT NULL, command_id TEXT NOT NULL,
                kind TEXT, original_command_id TEXT, module_selector TEXT,
                checkpoint_id TEXT NOT NULL,
                PRIMARY KEY (thread_id, command_id)
            );
            CREATE TABLE IF NOT EXISTS recovery_anchors (
                thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL,
                checkpoint_id TEXT NOT NULL,
                PRIMARY KEY (thread_id, checkpoint_ns)
            );
            CREATE TABLE IF NOT EXISTS storage_meta (key TEXT PRIMARY KEY, value TEXT);
            PRAGMA journal_size_limit=4194304;
        ''')

    @classmethod
    @contextmanager
    def from_conn_string(cls, conn_string):
        with closing(sqlite3.connect(conn_string, check_same_thread=False)) as conn:
            saver = cls(conn)
            try:
                yield saver
            finally:
                if saver.dirty:
                    saver.compact()

    def _index_checkpoint(self, thread_id, namespace, checkpoint):
        values = checkpoint.get('channel_values', {})
        command = values.get('command') or {}
        if isinstance(command, dict) and command.get('command_id'):
            self.conn.execute('''INSERT INTO command_links VALUES (?,?,?,?,?,?)
                ON CONFLICT(thread_id,command_id) DO UPDATE SET
                    kind=excluded.kind, original_command_id=excluded.original_command_id,
                    module_selector=excluded.module_selector, checkpoint_id=excluded.checkpoint_id
                WHERE excluded.checkpoint_id >= command_links.checkpoint_id''',
                (thread_id, command['command_id'], command.get('kind'),
                 command.get('original_command_id'), command.get('module_selector'), checkpoint['id']))
        if values.get('status') == 'needs_reconciliation':
            self.conn.execute('''INSERT INTO recovery_anchors VALUES (?,?,?)
                ON CONFLICT(thread_id,checkpoint_ns) DO UPDATE SET checkpoint_id=excluded.checkpoint_id
                WHERE excluded.checkpoint_id >= recovery_anchors.checkpoint_id''',
                (thread_id, namespace, checkpoint['id']))

    def _backfill_indexes(self):
        if self.conn.execute("SELECT 1 FROM storage_meta WHERE key='indexed-v1'").fetchone():
            return
        for thread_id, namespace, kind, payload in self.conn.execute(
                'SELECT thread_id,checkpoint_ns,type,checkpoint FROM checkpoints ORDER BY checkpoint_id'):
            self._index_checkpoint(thread_id, namespace, self.serde.loads_typed((kind, payload)))
        self.conn.execute("INSERT INTO storage_meta VALUES ('indexed-v1','1')")

    def command_link(self, thread_id, command_id):
        with self.lock, self.conn:
            self._backfill_indexes()
            row = self.conn.execute('SELECT kind,original_command_id,module_selector FROM command_links '
                                    'WHERE thread_id=? AND command_id=?', (thread_id, command_id)).fetchone()
            return dict(zip(('kind', 'original_command_id', 'module_selector'), row)) if row else None

    def reconciliation_values(self, thread_id, namespace=''):
        with self.lock, self.conn:
            self._backfill_indexes()
            row = self.conn.execute('SELECT checkpoint_id FROM recovery_anchors '
                                    'WHERE thread_id=? AND checkpoint_ns=?', (thread_id, namespace)).fetchone()
            if row:
                saved = self.get_tuple({'configurable': {'thread_id': thread_id,
                    'checkpoint_ns': namespace, 'checkpoint_id': row[0]}})
                return saved.checkpoint.get('channel_values', {}) if saved else None
            return None

    def put(self, config, checkpoint, metadata, new_versions):
        # Include chunks and their referring row in the same serialized transaction.
        with self.lock:
            self._index_checkpoint(str(config['configurable']['thread_id']),
                                   config['configurable'].get('checkpoint_ns', ''), checkpoint)
            result = super().put(config, checkpoint, metadata, new_versions)
            self.dirty = True
            return result

    def put_writes(self, config, writes, task_id, task_path=''):
        with self.lock:
            super().put_writes(config, writes, task_id, task_path)
            self.dirty = True

    def _collect_chunks(self):
        live = set()
        for table, column in [('checkpoints', 'checkpoint'), ('writes', 'value')]:
            for row in self.conn.execute(f'SELECT {column} FROM {table} WHERE type=?', (CHUNK_TYPE,)):
                live.update(ormsgpack.unpackb(row[0])[2])
        self.conn.execute('CREATE TEMP TABLE IF NOT EXISTS live_chunks (id INTEGER PRIMARY KEY)')
        self.conn.execute('DELETE FROM live_chunks')
        self.conn.executemany('INSERT INTO live_chunks VALUES (?)', ((ref,) for ref in live))
        self.conn.execute('DELETE FROM state_chunks WHERE id NOT IN (SELECT id FROM live_chunks)')

    def compact(self, *, keep=HISTORY_LIMIT, vacuum=False):
        """Prune obsolete steps at a quiescent boundary; preserve live dependencies.

        Standalone roots keep 32 steps. Child namespaces survive while their
        originating parent checkpoint is retained. Explicit cross-namespace
        parent references and the latest save-reconciliation anchor are pinned.
        Unknown states are never treated as completed runs or deleted wholesale.
        """
        if keep < 2:
            raise ValueError('history_limit_must_be_at_least_two')
        with self.lock, self.conn:
            self._backfill_indexes()
            rows = self.conn.execute('SELECT thread_id,checkpoint_ns,checkpoint_id,metadata '
                                     'FROM checkpoints ORDER BY checkpoint_id DESC').fetchall()
            groups = {}
            for thread_id, namespace, checkpoint_id, metadata in rows:
                groups.setdefault((thread_id, namespace), []).append(
                    (checkpoint_id, json.loads(metadata) if metadata else {}))
            retained = set()
            active = set()
            # Unknown namespace layouts are kept conservatively as standalone roots.
            for key, items in groups.items():
                if not key[1] or not items[0][1].get('parents'):
                    active.add(key)
                    retained.update((*key, cid) for cid, _ in items[:keep])
            anchors = self.conn.execute('SELECT thread_id,checkpoint_ns,checkpoint_id FROM recovery_anchors').fetchall()
            # Root save recovery must remain available after arbitrary later steps.
            retained.update(tuple(row) for row in anchors if not row[1])
            changed = True
            while changed:
                before = len(retained), len(active)
                for key, items in groups.items():
                    if key in active:
                        continue
                    parents = items[0][1].get('parents', {})
                    if parents and all((key[0], ns, cid) in retained for ns, cid in parents.items()):
                        active.add(key)
                        retained.update((*key, cid) for cid, _ in items[:keep])
                retained.update(tuple(row) for row in anchors if tuple(row[:2]) in active)
                # Retained child steps can refer to an older exact ancestor.
                for key in list(active):
                    for cid, metadata in groups[key]:
                        if (*key, cid) in retained:
                            retained.update((key[0], ns, parent) for ns, parent in metadata.get('parents', {}).items())
                changed = before != (len(retained), len(active))
            self.conn.execute('CREATE TEMP TABLE IF NOT EXISTS retained_checkpoints '
                '(thread_id TEXT,checkpoint_ns TEXT,checkpoint_id TEXT,PRIMARY KEY(thread_id,checkpoint_ns,checkpoint_id))')
            self.conn.execute('DELETE FROM retained_checkpoints')
            self.conn.executemany('INSERT INTO retained_checkpoints VALUES (?,?,?)', sorted(retained))
            match = ('r.thread_id={0}.thread_id AND r.checkpoint_ns={0}.checkpoint_ns '
                     'AND r.checkpoint_id={0}.checkpoint_id')
            for table in ('writes', 'checkpoints', 'recovery_anchors'):
                self.conn.execute(f'DELETE FROM {table} WHERE NOT EXISTS '
                                  f'(SELECT 1 FROM retained_checkpoints r WHERE {match.format(table)})')
            self.conn.execute('UPDATE checkpoints SET parent_checkpoint_id=NULL WHERE parent_checkpoint_id IS NOT NULL '
                'AND NOT EXISTS (SELECT 1 FROM checkpoints p WHERE p.thread_id=checkpoints.thread_id '
                'AND p.checkpoint_ns=checkpoints.checkpoint_ns AND p.checkpoint_id=checkpoints.parent_checkpoint_id)')
            self._collect_chunks()
        with self.lock:
            pages = self.conn.execute('PRAGMA page_count').fetchone()[0]
            free = self.conn.execute('PRAGMA freelist_count').fetchone()[0]
            page_size = self.conn.execute('PRAGMA page_size').fetchone()[0]
            if vacuum or (free * page_size >= 1048576 and free >= pages // 4):
                self.conn.execute('VACUUM')
            self.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.dirty = False
            return {'checkpoints': self.conn.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0],
                    'writes': self.conn.execute('SELECT COUNT(*) FROM writes').fetchone()[0],
                    'chunks': self.conn.execute('SELECT COUNT(*) FROM state_chunks').fetchone()[0]}

    def delete_thread(self, thread_id):
        with self.lock:
            super().delete_thread(thread_id)
            self.conn.execute('DELETE FROM command_links WHERE thread_id=?', (str(thread_id),))
            self.conn.execute('DELETE FROM recovery_anchors WHERE thread_id=?', (str(thread_id),))
            self._collect_chunks()
            self.conn.commit()
