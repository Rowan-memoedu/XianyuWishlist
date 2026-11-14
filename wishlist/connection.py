"""One in-process session maintainer; no automatic favorites mutations."""
import base64
import io
import threading
import time
from contextlib import asynccontextmanager

import qrcode
import qrcode.image.svg

from .remote import RemoteError
from .store import now


class Connection:
    def __init__(self, factory, operation_lock, interval=21600, login_factory=None):
        self.factory, self.lock, self.interval = factory, operation_lock, interval
        self.login_factory = login_factory or factory
        self.stop = threading.Event()
        self.guard = threading.Lock()
        self.thread = None
        self.login_thread = None
        self.login_active = False
        self.value = dict(state='checking', message='正在检查闲鱼连接', checked_at=None,
            last_success_at=None, next_check_at=None, automatic=True,
            check_interval_seconds=interval, browser_reuse='manual_only')
        self.next_check = 0
        self.failures = 0

    def snapshot(self):
        with self.guard:
            result = dict(self.value)
        if result.get('expires_at', 0) < time.time():
            result.pop('qr_image', None)
        return result

    def update(self, **values):
        with self.guard:
            self.value.update(values)

    def connected(self, source=None):
        self.failures = 0
        self.next_check = time.time() + self.interval
        self.update(state='connected', message='已复用 Chrome 登录态连接闲鱼，后台自动维护会话' if source == 'chrome' else '闲鱼已连接，后台自动维护登录态',
            checked_at=now(), last_success_at=now(), next_check_at=self.next_check,
            qr_image=None, expires_at=0, credential_source=source, automatic=True)

    def failed(self, exc):
        code = exc.code if isinstance(exc, RemoteError) else 'connection_error'
        needs_login = code in ('login_required', 'verification_required', 'account_mismatch')
        self.failures += 1
        delay = min(1800 * 2 ** min(self.failures - 1, 4), self.interval)
        self.next_check = None if needs_login else time.time() + delay
        self.update(state=code if needs_login else 'retrying',
            message=str(exc) if isinstance(exc, RemoteError) else '连接暂时中断，将自动重试；本地记录已保留',
            checked_at=now(), next_check_at=self.next_check, automatic=not needs_login,
            qr_image=None, expires_at=0)

    def check(self):
        if self.login_active or not self.lock.acquire(blocking=False):
            return
        remote = None
        try:
            self.update(state='checking', message='正在检查并续期闲鱼登录态')
            remote = self.factory()
            remote.check_connection()
            self.connected(getattr(remote, 'credential_source', None))
        except Exception as exc:
            self.failed(exc)
        finally:
            if remote:
                remote.close()
            self.lock.release()

    def request_check(self):
        self.update(automatic=True)
        self.next_check = 0

    def start_login(self):
        with self.guard:
            if self.login_active:
                return
            self.login_active = True
        self.login_thread = threading.Thread(target=self._login, daemon=True, name='favorites-login')
        self.login_thread.start()

    def _login(self):
        remote = None
        acquired = self.lock.acquire(blocking=False)
        try:
            if not acquired:
                raise RemoteError('收藏同步正在进行，请完成后再重新连接')
            self.update(state='connecting', message='正在检查 Chrome 与已有闲鱼登录态', qr_image=None)
            remote = self.login_factory()
            # Prefer the existing Chrome login or the maintained saved session before QR.
            try:
                remote.check_connection()
                self.connected(getattr(remote, 'credential_source', None))
                return
            except RemoteError as exc:
                if exc.code not in ('login_required', 'verification_required'):
                    raise
            self.update(message='正在获取闲鱼登录二维码')
            code = remote.begin_qr()
            stream = io.BytesIO()
            qrcode.make(code, image_factory=qrcode.image.svg.SvgPathFillImage).save(stream)
            image = 'data:image/svg+xml;base64,' + base64.b64encode(stream.getvalue()).decode('ascii')
            expires = time.time() + 180
            self.update(state='waiting_scan', message='请用闲鱼 App 扫码，并在手机确认登录',
                qr_image=image, expires_at=expires)
            while not self.stop.wait(3) and time.time() < expires:
                status = remote.poll_qr()
                if status == 'CONFIRMED':
                    self.connected(getattr(remote, 'credential_source', None))
                    return
                if status in ('EXPIRED', 'CANCELED'):
                    break
                self.update(message='已扫码，请在闲鱼 App 确认登录' if status == 'SCANNED'
                    else '请用闲鱼 App 扫码，并在手机确认登录')
            raise RemoteError('二维码已过期或已取消，请重新扫码连接', 'login_required')
        except Exception as exc:
            self.failed(exc)
        finally:
            if remote:
                remote.close()
            if acquired:
                self.lock.release()
            with self.guard:
                self.login_active = False

    def _maintain(self):
        while not self.stop.is_set():
            if self.next_check is not None and time.time() >= self.next_check:
                self.check()
            self.stop.wait(1)

    @asynccontextmanager
    async def lifespan(self, app):
        self.thread = threading.Thread(target=self._maintain, daemon=True, name='favorites-session')
        self.thread.start()
        try:
            yield
        finally:
            self.stop.set()
            self.thread.join(timeout=1)
            if self.login_thread:
                self.login_thread.join(timeout=1)
