"""Scoped local credential transport. Browser secrets never go to logs or the workbench."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from .remote import RemoteError


class BrowserSession:
    def __init__(self, reader=None):
        self.reader = reader or Path(__file__).resolve().parent.parent / 'scripts/browser-session.cjs'

    def read(self):
        env = dict(os.environ)
        if not env.get('PLAYWRIGHT_MCP_EXTENSION_TOKEN') and os.name == 'nt':
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                    env['PLAYWRIGHT_MCP_EXTENSION_TOKEN'] = winreg.QueryValueEx(key, 'PLAYWRIGHT_MCP_EXTENSION_TOKEN')[0]
            except OSError:
                pass
        if not env.get('PLAYWRIGHT_MCP_EXTENSION_TOKEN'):
            raise RemoteError('本机 Chrome 扩展连接凭据不可用', 'browser_unavailable')
        try:
            result = subprocess.run([shutil.which('node') or 'node', str(self.reader)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=50, env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if len(result.stdout) > 1_000_000:
                raise ValueError()
            value = json.loads(result.stdout)
            if not value.get('ok'):
                code = value.get('code')
                if code == 'browser_not_logged_in':
                    raise RemoteError('当前 Chrome 中的闲鱼未登录，请先在 Chrome 登录原账号', 'login_required')
                raise RemoteError('暂未连接到 Chrome，可打开已登录闲鱼的 Chrome 后检查连接', 'browser_unavailable')
            cookies = value['cookies']
            if not isinstance(cookies, list) or not cookies:
                raise ValueError()
            return cookies
        except RemoteError:
            raise
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            raise RemoteError('Chrome 会话读取暂未完成，已保留原有登录态', 'browser_unavailable') from None
