import json
import os
import secrets
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .remote import FavoritesRemote, RemoteError
from .store import Store, now
from .sync import sync_once
from .connection import Connection
from .browser_session import BrowserSession

ROOT = Path(__file__).resolve().parent.parent


class EditItem(BaseModel):
    title: str = Field(min_length=1, max_length=1200)
    notes: str = Field(default='', max_length=8000)
    group_label: str = Field(min_length=1, max_length=500)


class Action(BaseModel):
    action: str


class EditTask(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    criteria: str = Field(default='', max_length=8000)


class SyncRequest(BaseModel):
    submit: bool = False


def create_app(data_dir=None, remote_factory=None):
    data_dir = Path(data_dir or os.environ.get('WISHLIST_DATA', 'D:/AppData/XianyuWishlist/private'))
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(data_dir / 'wishlist.sqlite3')
    csrf = secrets.token_urlsafe(32)
    lock = threading.Lock()
    job = {'running': False, 'message': '尚未同步', 'error': None}
    state_path = os.environ.get('WISHLIST_LOGIN_STATE', str(data_dir / 'login-state.json'))
    browser = BrowserSession()
    def default_factory(*, allow_browser=False):
        return FavoritesRemote(state_path,
            seed_path='D:/AppData/XianyuHunterTrial/private/storage-state.json',
            expected_account=store.meta('account_hash'),
            browser_provider=browser.read if allow_browser else None)

    # Extension connections can create a visible tab. Only explicit reconnect may use one.
    factory = remote_factory or default_factory
    login_factory = remote_factory or (lambda: default_factory(allow_browser=True))
    connection = Connection(factory, lock, login_factory=login_factory)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None,
        lifespan=connection.lifespan if remote_factory is None else None)
    app.state.store = store
    app.state.job = job
    app.state.connection = connection

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        host = request.headers.get('host', '')
        if host.split(':')[0] not in ('127.0.0.1', 'localhost', 'testserver'):
            return JSONResponse({'detail': '仅允许本机访问'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != f'http://{host}':
            return JSONResponse({'detail': '来源不匹配'}, status_code=403)
        if request.method not in ('GET', 'HEAD'):
            if request.headers.get('x-wishlist-token') != csrf:
                return JSONResponse({'detail': '请刷新本地清单后重试'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' https://*.alicdn.com https://*.tbcdn.cn https://*.goofish.com; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    def mutate(fn):
        if not lock.acquire(blocking=False):
            raise HTTPException(409, '正在同步，请完成后再整理清单')
        try:
            return fn()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        finally:
            lock.release()

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'web/index.html')

    @app.get('/health')
    def health():
        return {'ok': True, 'service': 'XianyuWishlist', 'pid': os.getpid()}

    @app.get('/api/state')
    def state():
        status = connection.snapshot()
        visible_job = dict(job)
        if status['state'] == 'connected' and visible_job['error'] and not visible_job['running']:
            visible_job.update(error=None, message='连接已恢复，可重新读取最新收藏')
        return dict(store.snapshot(), csrf=csrf, job=visible_job, connection=status)

    @app.post('/api/connection/check')
    def check_connection():
        connection.request_check()
        return {'ok': True}

    @app.post('/api/connection/login')
    def login_connection():
        connection.start_login()
        return {'ok': True}

    @app.post('/api/items/{item_id}/edit')
    def edit_item(item_id: str, body: EditItem):
        mutate(lambda: store.edit(item_id, body.title, body.notes, body.group_label))
        return {'ok': True}

    @app.post('/api/items/{item_id}/action')
    def act(item_id: str, body: Action):
        mutate(lambda: store.stage(item_id, body.action))
        return {'ok': True}

    @app.post('/api/tasks/{task_id}/edit')
    def edit_task(task_id: str, body: EditTask):
        mutate(lambda: store.edit_task(task_id, body.title, body.criteria))
        return {'ok': True}

    @app.post('/api/tasks/{task_id}/purchase')
    def purchase_task(task_id: str):
        mutate(lambda: store.purchase_task(task_id))
        return {'ok': True}

    @app.get('/api/queue/export')
    def export():
        snapshot = store.snapshot()
        queue = []
        for task in snapshot['tasks']:
            if task['state'] == 'queued':
                sources = [x for x in snapshot['items'] if x['task_id'] == task['id'] and x['state'] == 'queued']
                queue.append(dict(task, sources=[{k: x[k] for k in ('id','title','display_title','url','price','notes','pending')} for x in sources]))
        return JSONResponse({'created': now(), 'tasks': queue}, headers={
            'Content-Disposition': 'attachment; filename="comparison-queue.json"'})

    @app.post('/api/sync')
    def start_sync(body: SyncRequest):
        if not lock.acquire(blocking=False):
            raise HTTPException(409, '已有同步正在进行')
        job.update(running=True, message='正在准备同步', error=None, result=None)

        def run():
            remote = None
            try:
                # A single recoverable latest pre-sync backup, not source/version copies.
                store.backup(data_dir / 'before-sync.sqlite3')
                remote = factory()
                result = sync_once(store, remote, body.submit, lambda message: job.update(message=message))
                connection.connected(getattr(remote, 'credential_source', None))
                job.update(message=result['message'], result=result)
            except Exception as exc:
                safe = str(exc) if isinstance(exc, (ValueError, RemoteError)) else '同步发生异常，本地操作已保留'
                job.update(error=safe, message='同步未完成')
                connection.failed(exc)
            finally:
                if remote:
                    remote.close()
                job['running'] = False
                lock.release()
        threading.Thread(target=run, daemon=True, name='favorites-sync').start()
        return {'ok': True}

    app.mount('/static', StaticFiles(directory=ROOT / 'web'), name='static')
    return app
