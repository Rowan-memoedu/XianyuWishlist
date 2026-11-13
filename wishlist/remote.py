"""Bounded adapter around pinned pyxianyu. No messaging/order/payment APIs."""
import hashlib
import json
import os
import time
import tempfile
from pathlib import Path

from loguru import logger
from pyxianyu.core import XianyuClient
from pyxianyu.utils.xianyu_utils import generate_sign

logger.remove()
LIST = 'mtop.taobao.idle.web.favor.item.list'
REMOVE = 'com.taobao.idle.unfavor.item'
BASE = 'https://h5api.m.goofish.com/h5/'
PASSPORT = 'https://passport.goofish.com'
TOKEN_ERRORS = {'FAIL_SYS_TOKEN_EXOIRED', 'FAIL_SYS_TOKEN_EXPIRED', 'FAIL_SYS_TOKEN_EMPTY'}


class RemoteError(Exception):
    def __init__(self, message, code='connection_error'):
        super().__init__(message)
        self.code = code


class FavoritesRemote:
    def __init__(self, state_path, delay=2, seed_path=None, expected_account=None, browser_provider=None):
        self.delay = delay
        self.state_path = Path(state_path)
        self.browser_provider = browser_provider
        try:
            self._revision = self.state_path.read_bytes() if self.state_path.exists() else None
            state = json.loads(self._revision or Path(seed_path or state_path).read_bytes())
        except FileNotFoundError:
            state = {'cookies': []}
        except (OSError, ValueError):
            raise RemoteError('本机登录态不可读，请恢复登录态文件后重试', 'login_required') from None
        self.client = XianyuClient({}, 'wishlist-local')
        self.credential_source = state.get('credential_source', 'saved')
        for c in state.get('cookies', []):
            domain = c.get('domain', '').lstrip('.')
            if domain == 'goofish.com' or domain.endswith('.goofish.com'):
                self.client.session.cookies.set(c['name'], c['value'], domain=c['domain'], path=c.get('path', '/'),
                    secure=c.get('secure', True), expires=c['expires'] if c.get('expires', -1) > 0 else None,
                    rest={'HttpOnly': None} if c.get('httpOnly') else {})
        identity = self.client.get_cookie_value('unb')
        self.account_hash = hashlib.sha256(identity.encode()).hexdigest() if identity else None
        self.expected_account = expected_account or self.account_hash
        if identity:
            self._check_account()
        request = self.client.session.request

        def guarded(method, url, **kwargs):
            allowed = {('POST', BASE + LIST + '/1.0/'), ('POST', BASE + REMOVE + '/1.0/'),
                ('POST', PASSPORT + '/newlogin/hasLogin.do'),
                ('GET', PASSPORT + '/mini_login.htm'),
                ('GET', PASSPORT + '/newlogin/qrcode/generate.do'),
                ('POST', PASSPORT + '/newlogin/qrcode/query.do'),
                ('POST', PASSPORT + '/login_token/login.do')}
            if (method.upper(), url) not in allowed:
                raise RemoteError('该接口不属于收藏同步范围')
            kwargs.update(timeout=(10, 25), allow_redirects=False)
            return request(method, url, **kwargs)
        self.client.session.request = guarded

    def _check_account(self):
        identity = self.client.get_cookie_value('unb')
        actual = hashlib.sha256(identity.encode()).hexdigest() if identity else None
        if not actual or (self.expected_account and actual != self.expected_account):
            raise RemoteError('当前登录账号与此清单不一致，请使用原账号扫码', 'account_mismatch')
        self.account_hash = actual
        self.expected_account = actual

    def _persist(self):
        self._check_account()
        current = self.state_path.read_bytes() if self.state_path.exists() else None
        if current != self._revision:
            raise RemoteError('登录态已由其他连接更新，请重新检查连接', 'state_changed')
        cookies = []
        for c in self.client.session.cookies:
            domain = c.domain.lstrip('.')
            if domain != 'goofish.com' and not domain.endswith('.goofish.com'):
                continue
            cookies.append(dict(name=c.name, value=c.value, domain=c.domain, path=c.path,
                expires=c.expires if c.expires is not None else -1, secure=c.secure,
                httpOnly=bool(c.has_nonstandard_attr('HttpOnly')), sameSite='Lax'))
        raw = json.dumps({'cookies': cookies, 'origins': [], 'credential_source': self.credential_source}, ensure_ascii=False).encode('utf-8')
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.state_path.parent, suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.state_path)
            self._revision = raw
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def _passport_params(self):
        return dict(appName='xianyu', fromSite='77', appEntrance='web',
            _csrf_token=self.client.get_cookie_value('XSRF-TOKEN'),
            hsiz=self.client.get_cookie_value('cookie2'), ltl='true',
            hid=self.client.get_cookie_value('unb'), umidToken='',
            bizParams='taobaoBizLoginFrom=web', mainPage='false', isMobile='false',
            lang='zh_CN', returnUrl='', isIframe='true', umidTag='SERVER',
            documentReferer='https://www.goofish.com/', defaultView='hasLogin',
            deviceId=self.client.get_cookie_value('cna'))

    def _passport(self, method, path, data=None, params=None):
        try:
            args = {'params' if method == 'GET' else 'data': data or self._passport_params()}
            if method == 'POST':
                args['params'] = {'appName': 'xianyu', 'fromSite': '77'}
            if params is not None:
                args['params'] = params
            response = self.client.session.request(method, PASSPORT + path,
                headers={**self.client.build_json_headers(), 'origin': PASSPORT,
                    'referer': PASSPORT + '/mini_login.htm'}, **args)
            response.raise_for_status()
            return response
        except Exception:
            raise RemoteError('闲鱼连接暂时不可用，将自动重试；本地记录已保留') from None

    def _renew(self):
        body = self._passport('POST', '/newlogin/hasLogin.do').json()
        content = body.get('content', {})
        # success only means the check ran; an iframe redirect requires human verification.
        if not content.get('success') or content.get('data', {}).get('iframeRedirect'):
            raise RemoteError('闲鱼登录已过期或需要验证，请扫码重新连接', 'login_required')
        self._check_account()

    def _reuse_browser(self):
        if not self.browser_provider:
            return False
        try:
            cookies = self.browser_provider()
        except RemoteError as exc:
            if exc.code == 'browser_unavailable':
                return False
            raise
        # Replace the jar as one candidate, never mix accounts or persist before API success.
        from requests.cookies import RequestsCookieJar
        jar = RequestsCookieJar()
        for c in cookies:
            domain = c.get('domain', '').lstrip('.')
            if domain != 'goofish.com' and not domain.endswith('.goofish.com'):
                continue
            jar.set(c['name'], c['value'], domain=c['domain'], path=c.get('path', '/'),
                expires=c['expires'] if c.get('expires', -1) > 0 else None,
                secure=c.get('secure', True), rest={'HttpOnly': None} if c.get('httpOnly') else {})
        self.client.session.cookies = jar
        self._check_account()
        self.credential_source = 'chrome'
        return True

    def begin_qr(self):
        self._passport('GET', '/mini_login.htm')
        try:
            content = self._passport('GET', '/newlogin/qrcode/generate.do').json()['content']
            data = content['data']
            if not content.get('success') or not all(data.get(k) for k in ('codeContent', 't', 'ck')):
                raise ValueError()
            self._qr = {k: data[k] for k in ('t', 'ck')}
            return data['codeContent']
        except (KeyError, TypeError, ValueError):
            raise RemoteError('闲鱼暂未提供登录二维码，请稍后重新连接', 'login_required') from None

    def poll_qr(self):
        try:
            content = self._passport('POST', '/newlogin/qrcode/query.do',
                {**self._passport_params(), **self._qr}).json()['content']
            data = content['data']
            status = data.get('qrCodeStatus')
            # The platform uses SCANED (one N); accept the standard spelling too.
            if status == 'SCANED':
                status = 'SCANNED'
            if status == 'CANCELLED':
                status = 'CANCELED'
            if status == 'CONFIRMED':
                if data.get('iframeRedirect'):
                    raise RemoteError('闲鱼要求安全验证，请在已登录的 Chrome 中完成验证后检查连接', 'verification_required')
                token = data.get('token') or data.get('lgToken')
                if token:
                    # Fixed endpoint; login tokens never enter the page or logs.
                    self._passport('POST', '/login_token/login.do',
                        {'deviceId': self.client.get_cookie_value('cna')}, params=dict(token=token,
                        subFlow='DIALOG_CHECK_LOGIN_RPC', nextCode='0018', bizScene='qrcode',
                        confirm='true'))
                self._check_account()
                self.check_connection()
            elif status not in ('NEW', 'SCANNED', 'EXPIRED', 'CANCELED'):
                raise RemoteError('扫码接口返回了未识别的状态，请检查浏览器连接后重试', 'login_protocol_error')
            return status
        except (KeyError, TypeError, ValueError):
            raise RemoteError('扫码需要在闲鱼中完成验证，请重新连接', 'login_required') from None

    def check_connection(self):
        data = self._call(LIST, {'pageNumber': 1, 'rowsPerPage': 1, 'type': 'DEFAULT'})
        if not isinstance(data.get('items'), list):
            raise RemoteError('闲鱼未返回有效收藏列表，将稍后重新检查')
        return True

    def _call(self, api, data):
        browser_tried = False
        if not self.account_hash:
            browser_tried = True
            if not self._reuse_browser():
                raise RemoteError('请在 Chrome 登录闲鱼后检查连接，或扫码连接原账号', 'login_required')
        renewed = False
        for attempt in range(4):
            payload = self._request_once(api, data)
            codes = payload.get('ret') or []
            if any(str(x).startswith('SUCCESS::') for x in codes):
                self._persist()
                return payload.get('data', {})
            code = str(codes[0]).split('::')[0] if codes else 'INVALID_RESPONSE'
            if (code == 'FAIL_SYS_SESSION_EXPIRED' or code.startswith(('FAIL_SYS_USER_VALIDATE', 'RGV587', 'FAIL_SYS_ILLEGAL_ACCESS'))) and not browser_tried and attempt < 3:
                browser_tried = True
                if self._reuse_browser():
                    continue
            if code in TOKEN_ERRORS and attempt < 3:
                # requests has already merged Set-Cookie; regenerate the signature.
                continue
            if code == 'FAIL_SYS_SESSION_EXPIRED' and not renewed and attempt < 3:
                self._renew()
                renewed = True
                continue
            if code == 'FAIL_SYS_SESSION_EXPIRED':
                raise RemoteError('闲鱼登录已过期，请扫码重新连接', 'login_required')
            if code.startswith(('FAIL_SYS_USER_VALIDATE', 'RGV587', 'FAIL_SYS_ILLEGAL_ACCESS')):
                raise RemoteError('闲鱼要求安全验证，请在闲鱼完成验证后重新连接', 'verification_required')
            safe = code if code.replace('_', '').isalnum() and len(code) < 80 else 'PLATFORM_REJECTED'
            raise RemoteError('闲鱼未接受请求（' + safe + '）；本地记录已保留')

    def _request_once(self, api, data):
        try:
            params = self.client.build_mtop_params(api, 'a21ybx.personal.0.0', 'a21ybx.personal.0.0', 'wishlist')
            data_val = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
            if not self.client.get_cookie_value('_m_h5_tk'):
                # The platform bootstraps Set-Cookie for an empty-token signature.
                params['sign'] = generate_sign(params['t'], '', data_val)
                response = self.client.session.post(BASE + api + '/1.0/', params=params,
                    data={'data': data_val}, headers=self.client.build_json_headers())
                response.raise_for_status()
            else:
                response = self.client.post_json(BASE + api + '/1.0/', params=params, data_val=data_val)
            payload = self.client.parse_json_response(response, api_name=api)
        except Exception:
            # Never expose signed URLs, cookies, response bodies, or credential values.
            raise RemoteError('闲鱼连接失败；本地操作已保留，可稍后重试') from None
        if not isinstance(payload, dict):
            raise RemoteError('闲鱼返回格式异常，将稍后重新检查')
        return payload

    def read_all(self, progress=lambda _: None):
        items, seen, pages = [], set(), []
        for page in range(1, 101):
            progress(f'正在读取收藏第 {page} 页')
            data = self._call(LIST, {'pageNumber': page, 'rowsPerPage': 20, 'type': 'DEFAULT'})
            rows = data.get('items')
            if not isinstance(rows, list):
                raise RemoteError('收藏返回不完整，本次不更新本地清单')
            new = []
            for row in rows:
                item_id = str(row.get('id', ''))
                if not item_id.isdigit():
                    raise RemoteError('收藏编号缺失，本次不更新本地清单')
                if item_id not in seen:
                    seen.add(item_id)
                    new.append({k: row.get(k) for k in ('id','title','price','picUrl','userNick','favorTime','itemStatus','itemDeleted','favored','reducePrice')})
            items.extend(new)
            pages.append({'page': page, 'count': len(rows), 'new': len(new)})
            if data.get('nextPage') is False:
                try:
                    total = int(data['totalCount'])
                except (KeyError, TypeError, ValueError):
                    raise RemoteError('收藏总数缺失，本次不更新本地清单') from None
                if len(items) != total:
                    raise RemoteError('分页期间收藏发生变化或条目不全，请重试同步')
                return items, {'ok': True, 'complete': True, 'unique_count': total, 'pages': pages}
            if not new:
                raise RemoteError('收藏分页重复或提前结束，本次不更新本地清单')
            time.sleep(self.delay)
        raise RemoteError('收藏超过本轮分页上限，本次不更新本地清单')

    def unfavorite(self, item_id):
        if not str(item_id).isdigit():
            raise RemoteError('商品编号格式不正确')
        # The modern web unfavorite route, supported independently by two primary implementations.
        # No alternate/add endpoint is attempted. Authoritative list readback confirms removal.
        self._call(REMOVE, {'itemId': str(item_id)})
        time.sleep(self.delay)

    def close(self):
        self.client.session.close()
