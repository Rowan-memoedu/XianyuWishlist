"""Bounded adapter around pinned pyxianyu. No messaging/order/payment APIs."""
import hashlib
import json
import time
from pathlib import Path

from loguru import logger
from pyxianyu.core import XianyuClient

logger.remove()
LIST = 'mtop.taobao.idle.web.favor.item.list'
REMOVE = 'com.taobao.idle.unfavor.item'
BASE = 'https://h5api.m.goofish.com/h5/'


class RemoteError(Exception):
    pass


class FavoritesRemote:
    def __init__(self, state_path, delay=2):
        self.delay = delay
        self.state_path = Path(state_path)
        try:
            state = json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise RemoteError('本机登录态不可读，请恢复已授权的闲鱼登录态') from None
        self.client = XianyuClient({}, 'wishlist-local')
        for c in state.get('cookies', []):
            domain = c.get('domain', '').lstrip('.')
            if domain == 'goofish.com' or domain.endswith('.goofish.com'):
                self.client.session.cookies.set(c['name'], c['value'], domain=c['domain'], path=c.get('path', '/'))
        identity = self.client.get_cookie_value('unb')
        if not identity:
            raise RemoteError('登录态缺少账号标识，请重新连接闲鱼登录态')
        self.account_hash = hashlib.sha256(identity.encode()).hexdigest()
        request = self.client.session.request

        def guarded(method, url, **kwargs):
            if method.upper() != 'POST' or url not in {BASE + LIST + '/1.0/', BASE + REMOVE + '/1.0/'}:
                raise RemoteError('该接口不属于收藏同步范围')
            kwargs.update(timeout=(10, 25), allow_redirects=False)
            return request(method, url, **kwargs)
        self.client.session.request = guarded

    def _call(self, api, data):
        try:
            params = self.client.build_mtop_params(api, 'a21ybx.personal.0.0', 'a21ybx.personal.0.0', 'wishlist')
            response = self.client.post_json(BASE + api + '/1.0/', params=params,
                data_val=json.dumps(data, ensure_ascii=False, separators=(',', ':')))
            payload = self.client.parse_json_response(response, api_name=api)
        except Exception:
            # Never expose signed URLs, cookies, response bodies, or credential values.
            raise RemoteError('闲鱼连接失败；本地操作已保留，可稍后重试') from None
        codes = payload.get('ret') or []
        if not any(str(x).startswith('SUCCESS::') for x in codes):
            code = str(codes[0]).split('::')[0] if codes else 'INVALID_RESPONSE'
            safe = code if code.replace('_', '').isalnum() and len(code) < 80 else 'PLATFORM_REJECTED'
            raise RemoteError('闲鱼未接受请求（' + safe + '）；请检查登录态或稍后重试')
        return payload.get('data', {})

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
