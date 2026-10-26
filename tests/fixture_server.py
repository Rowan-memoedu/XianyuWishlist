"""Isolated browser acceptance server. It cannot connect to Xianyu."""
import tempfile

from wishlist.app import create_app
from .test_workflow import EVIDENCE, FakeRemote, item


def create_fixture():
    rows = [item(91001, '示例物理讲义 2027 上册'), item(91002, '高清示例物理讲义 2027 上册 PDF'),
            item(91003, '示例物理讲义 2027 下册')]
    remote = FakeRemote(rows)
    app = create_app(tempfile.mkdtemp(prefix='ui-', dir='D:/Quarantine/XianyuWishlistTests'), lambda: remote)
    app.state.store.import_snapshot(rows, EVIDENCE)
    return app
