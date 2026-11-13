import threading
import time

import pytest
from fastapi.testclient import TestClient

from wishlist.app import create_app
from wishlist.grouping import same_type
from wishlist.remote import RemoteError
from wishlist.store import Store
from wishlist.sync import sync_once


def item(i, title='竞赛物理讲义 2027 上册', **extra):
    return dict(id=str(i), title=title, price='12', itemDeleted=False, **extra)


EVIDENCE = {'ok': True, 'complete': True}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / 'test.sqlite3')
    s.import_snapshot([item(1), item(2, '高清竞赛物理讲义 2027 上册 PDF'), item(3, '竞赛物理讲义 2027 下册')], EVIDENCE)
    return s


class FakeRemote:
    account_hash = 'fixture-account'

    def __init__(self, rows, fail=None, retain=False):
        self.items = {str(x['id']): x for x in rows}
        self.calls = []
        self.fail_id, self.retain = fail, retain

    def read_all(self, progress=lambda _: None):
        progress('fixture read')
        return list(self.items.values()), dict(EVIDENCE)

    def unfavorite(self, item_id):
        self.calls.append(item_id)
        if self.fail_id == item_id:
            raise RemoteError('fixture network failure')
        if not self.retain:
            self.items.pop(item_id, None)

    def close(self):
        pass


def test_stage_persists_task_before_any_remote_write(store):
    store.stage('1', 'queue')
    restarted = Store(store.path)
    state = restarted.snapshot()
    row = next(x for x in state['items'] if x['id'] == '1')
    assert row['state'] == 'queued' and row['pending']
    assert row['task_id'] == state['tasks'][0]['id']


def test_same_type_merges_but_editions_volumes_and_models_stay_separate(store):
    store.stage('1', 'queue')
    store.stage('2', 'queue')
    store.stage('3', 'queue')
    tasks = [x for x in store.snapshot()['tasks'] if x['state'] == 'queued']
    assert len(tasks) == 2
    assert not same_type('物理讲义2026上册', '物理讲义2027上册')
    assert not same_type('平板 MatePad 11 128G', '平板 MatePad 11 256G')
    assert not same_type('数学讲义 上册', '物理讲义 上册')


def test_edit_survives_remote_refresh(store):
    store.edit('1', '我要的讲义', '只要最新版本', '新版讲义')
    store.import_snapshot([item(1, '卖家改名')], EVIDENCE)
    row = store.snapshot()['items'][0]
    assert row['display_title'] == '我要的讲义'
    assert row['notes'] == '只要最新版本' and row['group_label'] == '新版讲义'


def test_read_only_refresh_never_unfavorites(store):
    store.stage('1', 'queue')
    remote = FakeRemote([item(1), item(2), item(3)])
    sync_once(store, remote, False)
    assert remote.calls == [] and store.snapshot()['pending'] == 1


def test_complete_sync_keeps_queue_and_tombstones(store):
    store.stage('1', 'queue')
    store.stage('2', 'remove')
    store.stage('3', 'purchase')
    remote = FakeRemote([item(1), item(2), item(3)])
    result = sync_once(store, remote, True)
    assert result['removed'] == 3 and store.snapshot()['pending'] == 0
    assert [r['state'] for r in store.snapshot()['items']] == ['queued', 'removed', 'purchased']
    store.import_snapshot([item(1), item(2), item(3)], EVIDENCE)
    assert [r['state'] for r in store.snapshot()['items']] == ['queued', 'removed', 'purchased']
    assert store.snapshot()['pending'] == 3


def test_failure_retry_and_idempotency(store):
    store.stage('1', 'queue')
    store.stage('2', 'remove')
    remote = FakeRemote([item(1), item(2), item(3)], fail='2')
    sync_once(store, remote, True)
    assert store.snapshot()['pending'] == 1
    assert len([t for t in store.snapshot()['tasks'] if t['state'] == 'queued']) == 1
    remote.fail_id = None
    sync_once(store, remote, True)
    sync_once(store, remote, True)
    assert remote.calls.count('1') == 1 and store.snapshot()['pending'] == 0


def test_success_response_without_authoritative_removal_stays_pending(store):
    store.stage('1', 'queue')
    remote = FakeRemote([item(1), item(2), item(3)], retain=True)
    sync_once(store, remote, True)
    row = store.snapshot()['items'][0]
    assert row['pending'] == 1 and '仍显示' in row['error']


def test_incomplete_read_cannot_delete_local_state(store):
    before = store.snapshot()
    with pytest.raises(ValueError):
        store.import_snapshot([], {'ok': True, 'complete': False})
    assert store.snapshot() == before


def test_external_unfavorite_hides_inbox_but_keeps_tasks(store):
    store.stage('1', 'queue')
    store.import_snapshot([], EVIDENCE)
    rows = store.snapshot()['items']
    assert rows[0]['state'] == 'queued'
    assert rows[1]['state'] == 'remote_removed'
    store.import_snapshot([item(2)], EVIDENCE)
    assert store.snapshot()['items'][1]['state'] == 'inbox'


def test_undo_before_sync_and_purchase_type(store):
    store.stage('1', 'queue')
    store.stage('1', 'undo')
    assert store.snapshot()['items'][0]['state'] == 'inbox'
    store.stage('1', 'queue')
    store.stage('2', 'queue')
    task_id = store.snapshot()['items'][0]['task_id']
    store.purchase_task(task_id)
    assert all(r['state'] == 'purchased' for r in store.snapshot()['items'][:2])
    assert not [t for t in store.snapshot()['tasks'] if t['state'] == 'queued']


def test_manual_split_merge(store):
    store.stage('1', 'queue')
    store.stage('2', 'queue')
    store.edit('2', '另一套', '', '另一种')
    tasks = [x for x in store.snapshot()['tasks'] if x['state'] == 'queued']
    assert len(tasks) == 2
    store.edit_task(tasks[1]['id'], tasks[0]['title'], '预算20元')
    assert len([x for x in store.snapshot()['tasks'] if x['state'] == 'queued']) == 1


def test_account_change_stops_before_remote_write(store):
    store.set_meta('account_hash', 'different-account')
    remote = FakeRemote([item(1)])
    with pytest.raises(ValueError):
        sync_once(store, remote, True)
    assert remote.calls == []


def test_local_recovery_survives_refresh_without_refavoriting(store):
    store.stage('1', 'remove')
    store.import_snapshot([], EVIDENCE)
    store.stage('1', 'restore')
    store.import_snapshot([], EVIDENCE)
    row = store.snapshot()['items'][0]
    assert row['state'] == 'inbox' and not row['pending'] and not row['remote_present']


def test_ambiguous_remote_failure_is_reconciled_by_readback(store):
    store.stage('1', 'queue')
    remote = FakeRemote([item(1), item(2), item(3)])
    def remove_then_disconnect(item_id):
        remote.items.pop(item_id)
        raise RemoteError('连接中断，但远端已执行')
    remote.unfavorite = remove_then_disconnect
    result = sync_once(store, remote, True)
    assert result['removed'] == 1 and not store.snapshot()['pending']
    assert store.snapshot()['items'][0]['state'] == 'queued'


def test_api_security_persistence_and_export(tmp_path):
    app = create_app(tmp_path, lambda: FakeRemote([]))
    app.state.store.import_snapshot([item(1)], EVIDENCE)
    with TestClient(app) as client:
        state = client.get('/api/state').json()
        assert 'Cookie' not in str(state) and 'account_hash' not in state
        assert client.post('/api/items/1/action', json={'action':'queue'}).status_code == 403
        headers = {'X-Wishlist-Token': state['csrf']}
        assert client.post('/api/items/1/action', json={'action':'queue'}, headers=dict(headers,Origin='https://evil.example')).status_code == 403
        assert client.get('/api/state', headers={'Host':'evil.example'}).status_code == 403
        assert client.post('/api/items/1/action', json={'action':'queue'}, headers=headers).status_code == 200
        exported = client.get('/api/queue/export').json()
        assert len(exported['tasks']) == 1 and exported['tasks'][0]['sources'][0]['id'] == '1'
        assert client.post('/api/items/1/action', json={'action':'unknown'}, headers=headers).status_code == 400
        assert client.get('/').status_code == 200
        assert "frame-ancestors 'none'" in client.get('/').headers['content-security-policy']


def test_background_sync_locks_mutations(tmp_path):
    entered, release = threading.Event(), threading.Event()
    remote = FakeRemote([item(1)])
    original = remote.read_all
    def wait_read(progress):
        entered.set()
        assert release.wait(5)
        return original(progress)
    remote.read_all = wait_read
    app = create_app(tmp_path, lambda: remote)
    with TestClient(app) as client:
        token = {'X-Wishlist-Token': client.get('/api/state').json()['csrf']}
        assert client.post('/api/sync', json={'submit':False}, headers=token).status_code == 200
        assert entered.wait(3)
        assert client.post('/api/items/1/action', json={'action':'queue'}, headers=token).status_code == 409
        release.set()
        for _ in range(50):
            if not app.state.job['running']:
                break
            time.sleep(.02)
        assert not app.state.job['error']
        assert len(client.get('/api/state').json()['items']) == 1
