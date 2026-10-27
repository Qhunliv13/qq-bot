"""Independent durable event outbox; no QQ bindings or network transport.

An internal sequence is not a QQ message ID. Delivery is at-least-once across a
crash after send and before acknowledgement; consumers must deduplicate event IDs.
No native fields, identities or timestamps are fabricated or rewritten.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from .store import canonical_json, copy_json, validate_account, validate_identifier


class EventOutbox:
    """Keep immutable source events and per-consumer ordered acknowledgements."""

    def __init__(self, path):
        if not isinstance(path, (str, Path)) or not str(path) or str(path) == ':memory:':
            raise ValueError('durable_event_path_required')
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connection()) as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS transport_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    account TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    UNIQUE(account, event_id)
                );
                CREATE TABLE IF NOT EXISTS event_cursors (
                    account TEXT NOT NULL,
                    consumer TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    PRIMARY KEY(account, consumer)
                );
            ''')

    def _connection(self):
        """Use one short-lived connection per operation, with a bounded lock wait."""
        return sqlite3.connect(self.path, timeout=10)

    def append(self, account, event_id, event):
        """Insert an immutable native event once; conflicting same-ID data is an error."""
        validate_account(account)
        validate_identifier(event_id)
        if not isinstance(event, dict) or not isinstance(event.get('post_type'), str) or not event['post_type']:
            raise ValueError('invalid_event')
        identity = event.get('self_id')
        if isinstance(identity, bool) or not isinstance(identity, (int, str)) or str(identity) != account:
            raise ValueError('event_account_mismatch')
        encoded = canonical_json(event)
        fingerprint = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
        connection = self._connection()
        try:
            connection.execute('BEGIN IMMEDIATE')
            prior = connection.execute(
                'SELECT sequence, fingerprint FROM transport_events WHERE account=? AND event_id=?',
                (account, event_id)).fetchone()
            if prior:
                if prior[1] != fingerprint:
                    raise ValueError('event_id_conflict')
                connection.commit()
                return {'sequence': prior[0], 'inserted': False}
            cursor = connection.execute(
                'INSERT INTO transport_events(account,event_id,fingerprint,payload) VALUES(?,?,?,?)',
                (account, event_id, fingerprint, encoded))
            connection.commit()
            return {'sequence': cursor.lastrowid, 'inserted': True}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def pending(self, account, consumer, limit=100):
        """Read unacknowledged events in source insertion order without mutating a cursor."""
        validate_account(account)
        validate_identifier(consumer)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('invalid_limit')
        connection = self._connection()
        try:
            rows = connection.execute('''
                SELECT sequence,event_id,payload FROM transport_events
                WHERE account=? AND sequence > COALESCE((
                    SELECT sequence FROM event_cursors WHERE account=? AND consumer=?
                ),0) ORDER BY sequence LIMIT ?
            ''', (account, account, consumer, limit)).fetchall()
            # Loading fresh JSON gives callers independent trees, not mutable cached events.
            return [{'sequence': row[0], 'event_id': row[1], 'event': json.loads(row[2])} for row in rows]
        finally:
            connection.close()

    def acknowledge(self, account, consumer, sequence):
        """Acknowledge only the next account event; never skip another pending event."""
        validate_account(account)
        validate_identifier(consumer)
        if type(sequence) is not int or sequence <= 0:
            raise ValueError('invalid_sequence')
        connection = self._connection()
        try:
            connection.execute('BEGIN IMMEDIATE')
            prior = connection.execute(
                'SELECT sequence FROM event_cursors WHERE account=? AND consumer=?',
                (account, consumer)).fetchone()
            current = prior[0] if prior else 0
            if sequence == current:
                connection.commit()
                return
            following = connection.execute(
                'SELECT MIN(sequence) FROM transport_events WHERE account=? AND sequence>?',
                (account, current)).fetchone()[0]
            if sequence != following:
                raise ValueError('out_of_order_acknowledgement')
            connection.execute('''
                INSERT INTO event_cursors(account,consumer,sequence) VALUES(?,?,?)
                ON CONFLICT(account,consumer) DO UPDATE SET sequence=excluded.sequence
            ''', (account, consumer, sequence))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class EventPump:
    """Deliver one queued event through an injected, explicitly acknowledging sink.

    Serialize calls for each instance; sharing a consumer across multiple pump
    instances is not a supported distributed claim mechanism. This is not a QQ
    event subscriber or a WebSocket implementation.
    """

    def __init__(self, outbox, account, consumer, send):
        import asyncio
        validate_account(account)
        validate_identifier(consumer)
        if not callable(send):
            raise ValueError('invalid_event_sink')
        self.outbox, self.account, self.consumer, self.send = outbox, account, consumer, send
        self._lock = asyncio.Lock()

    async def deliver_once(self):
        """Advance only after the sink returns literal True; failures retain the event."""
        async with self._lock:
            rows = self.outbox.pending(self.account, self.consumer, limit=1)
            if not rows:
                return {'state': 'empty', 'acknowledged': False}
            row = rows[0]
            try:
                accepted = await self.send(row['event_id'], copy_json(row['event']))
            except Exception:
                # A failed sink can have observed the event. Retain the same ID on replay.
                return {'state': 'unknown', 'acknowledged': False}
            if accepted is not True:
                return {'state': 'unacknowledged', 'acknowledged': False}
            self.outbox.acknowledge(self.account, self.consumer, row['sequence'])
            return {'state': 'acknowledged', 'acknowledged': True, 'sequence': row['sequence']}
