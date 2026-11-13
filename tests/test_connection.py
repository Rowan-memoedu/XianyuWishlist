import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from wishlist.app import create_app
from wishlist.connection import Connection
from wishlist.remote import FavoritesRemote, RemoteError, LIST, REMOVE


@pytest.fixture
def remote(tmp_path):
    seed = tmp_path / 'seed.json'
    seed.write_text(json.dumps({'cookies': [
        dict(name='unb', value='synthetic-account', domain='.goofish.com'),
        dict(name='_m_h5_tk', value='old_0', domain='.goofish.com'),
        dict(name='cookie2', value='synthetic-session', domain='.goofish.com')]}))
    r = FavoritesRemote(tmp_path / 'session.json', seed_path=seed, delay=0)
    yield r
    r.close()


def test_rotated_cookie_persists_and_reloads_without_touching_seed(remote):
    original = remote.state_path.parent.joinpath('seed.json').read_bytes()
    calls = []
    def request(api, data):
        calls.append(api)
        if len(calls) == 1:
            remote.client.session.cookies.set('_m_h5_tk', 'new_123', domain='.goofish.com')
            return {'ret': ['FAIL_SYS_TOKEN_EXOIRED::expired']}
        assert remote.client.get_cookie_value('_m_h5_tk') == 'new_123'
        return {'ret': ['SUCCESS::ok'], 'data': {'items': []}}
    remote._request_once = request
    remote.check_connection()
    with_session = FavoritesRemote(remote.state_path)
    assert with_session.client.get_cookie_value('_m_h5_tk') == 'new_123'
    with_session.close()
    assert remote.state_path.parent.joinpath('seed.json').read_bytes() == original
    assert len(calls) == 2


def test_iframe_redirect_is_not_successful_renewal(remote):
    remote._request_once = lambda *args: {'ret': ['FAIL_SYS_SESSION_EXPIRED::expired']}
    remote._passport = lambda *args: SimpleNamespace(json=lambda: {
        'content': {'success': True, 'data': {'iframeRedirect': True}}})
    with pytest.raises(RemoteError) as exc:
        remote.check_connection()
    assert exc.value.code == 'login_required'
    assert not remote.state_path.exists()


def test_session_then_token_refresh_is_bounded(remote):
    replies = iter([{'ret': ['FAIL_SYS_SESSION_EXPIRED']},
        {'ret': ['FAIL_SYS_TOKEN_EMPTY']}, {'ret': ['SUCCESS::ok'], 'data': {'items': []}}])
    remote._request_once = lambda *args: next(replies)
    renewals = []
    remote._renew = lambda: renewals.append(True)
    assert remote.check_connection()
    assert renewals == [True]


def test_token_refresh_uses_new_signature_and_stops_after_four_rejections(remote):
    from pyxianyu.utils.xianyu_utils import generate_sign
    forms = []
    def post(url, params, data_val):
        remote.client.build_signed_form(params, data_val)
        forms.append((dict(params), data_val))
        if len(forms) == 1:
            remote.client.session.cookies.set('_m_h5_tk', 'rotated_123', domain='.goofish.com')
        return SimpleNamespace(json=lambda: {'ret': ['FAIL_SYS_TOKEN_EXPIRED']})
    remote.client.post_json = post
    with pytest.raises(RemoteError):
        remote.check_connection()
    assert len(forms) == 4
    assert forms[0][0]['sign'] == generate_sign(forms[0][0]['t'], 'old', forms[0][1])
    assert forms[1][0]['sign'] == generate_sign(forms[1][0]['t'], 'rotated', forms[1][1])
    assert not remote.state_path.exists()


def test_qr_cannot_persist_a_different_account(remote):
    def passport(*args, **kwargs):
        remote.client.session.cookies.set('unb', 'wrong-account', domain='.goofish.com')
        return SimpleNamespace(json=lambda: {'content': {'success': True,
            'data': {'qrCodeStatus': 'CONFIRMED'}}})
    remote._qr = {'t': 'synthetic', 'ck': 'private-synthetic'}
    remote._passport = passport
    with pytest.raises(RemoteError) as exc:
        remote.poll_qr()
    assert exc.value.code == 'account_mismatch'
    assert not remote.state_path.exists()


def test_network_error_never_replays_unfavorite(remote):
    calls = []
    def broken(*args):
        calls.append(True)
        raise RemoteError('network failure')
    remote._request_once = broken
    with pytest.raises(RemoteError):
        remote.unfavorite('123')
    assert len(calls) == 1


@pytest.mark.parametrize('code', ['FAIL_SYS_USER_VALIDATE', 'RGV587_ERROR', 'FAIL_SYS_ILLEGAL_ACCESS'])
def test_risk_control_stops_without_retry(remote, code):
    remote._request_once = lambda *args: {'ret': [code]}
    with pytest.raises(RemoteError) as exc:
        remote.check_connection()
    assert exc.value.code == 'verification_required'


def test_changed_account_and_concurrent_state_are_not_persisted(remote):
    remote.client.session.cookies.set('unb', 'another-account', domain='.goofish.com')
    with pytest.raises(RemoteError, match='账号'):
        remote._persist()
    assert not remote.state_path.exists()
    remote.client.session.cookies.set('unb', 'synthetic-account', domain='.goofish.com')
    remote.state_path.write_text('{"externally_updated":true}')
    with pytest.raises(RemoteError) as exc:
        remote._persist()
    assert exc.value.code == 'state_changed'
    assert json.loads(remote.state_path.read_text()) == {'externally_updated': True}


def test_endpoint_allowlist_remains_closed(remote):
    for method, url in [('GET', 'https://evil.example/'),
        ('POST', 'https://h5api.m.goofish.com/h5/mtop.trade.create/1.0/'),
        ('GET', 'https://passport.goofish.com/iv/normal_validate.htm')]:
        with pytest.raises(RemoteError):
            remote.client.session.request(method, url)


def test_heartbeat_only_checks_read_connection_and_backs_off():
    calls = []
    class R:
        def check_connection(self):
            calls.append('read')
        def close(self):
            calls.append('close')
    lock = threading.Lock()
    c = Connection(R, lock)
    c.check()
    assert calls == ['read', 'close']
    assert c.snapshot()['state'] == 'connected'
    assert c.next_check > time.time() + 590
    with lock:
        c.check()
    assert len(calls) == 2
    c.failed(RemoteError('temporary'))
    first = c.next_check
    c.failed(RemoteError('temporary'))
    assert c.next_check > first + 20
    c.failed(RemoteError('scan required', 'login_required'))
    assert c.next_check > time.time() + 3500


def test_qr_confirmation_must_pass_remote_readback():
    class R:
        def begin_qr(self):
            return 'https://example.invalid/synthetic-qr'
        def poll_qr(self):
            raise RemoteError('wrong account', 'account_mismatch')
        def close(self):
            pass
    c = Connection(R, threading.Lock())
    c.stop = SimpleNamespace(wait=lambda _: False)
    c._login()
    assert c.snapshot()['state'] == 'account_mismatch'
    assert not c.snapshot().get('qr_image')
    assert not c.snapshot()['last_success_at']
    assert not c.lock.locked()


def test_connection_routes_require_csrf_and_state_has_no_secrets(tmp_path):
    app = create_app(tmp_path, lambda: None)
    c = app.state.connection
    called = []
    c.start_login = lambda: called.append('login')
    with TestClient(app) as client:
        assert client.post('/api/connection/login').status_code == 403
        state = client.get('/api/state').json()
        assert client.post('/api/connection/login', headers={'x-wishlist-token': state['csrf']}).status_code == 200
        assert called == ['login']
        assert not any(x in json.dumps(state) for x in ['synthetic-session', 'account_hash', 'cookie2'])


def test_missing_credentials_can_begin_login_but_cannot_read(tmp_path):
    r = FavoritesRemote(tmp_path / 'missing.json')
    with pytest.raises(RemoteError) as exc:
        r.check_connection()
    assert exc.value.code == 'login_required'
    r.close()
