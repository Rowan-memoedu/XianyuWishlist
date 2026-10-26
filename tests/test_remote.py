import pytest
from wishlist.remote import FavoritesRemote, RemoteError


def adapter(pages):
    r = object.__new__(FavoritesRemote)
    r.delay = 0
    calls = iter(pages)
    r._call = lambda *args: next(calls)
    return r


def test_paging_aggregates_more_than_thirty():
    pages = [{'items':[{'id':str(i)} for i in range(1,21)],'nextPage':True,'totalCount':39},
             {'items':[{'id':str(i)} for i in range(21,40)],'nextPage':False,'totalCount':39}]
    rows, evidence = adapter(pages).read_all()
    assert len(rows) == 39 and evidence['complete']


@pytest.mark.parametrize('pages', [
    [{'items':[{'id':'1'}], 'nextPage':False, 'totalCount':2}],
    [{'items':[{'id':'1'}], 'nextPage':True}, {'items':[{'id':'1'}], 'nextPage':True}],
    [{'nextPage':False, 'totalCount':0}],
    [{'items':[{'title':'missing id'}], 'nextPage':False,'totalCount':1}],
])
def test_incomplete_or_duplicate_pages_fail_closed(pages):
    with pytest.raises(RemoteError):
        adapter(pages).read_all()


def test_empty_favorites_is_valid_complete_snapshot():
    rows, evidence = adapter([{'items':[], 'nextPage':False,'totalCount':0}]).read_all()
    assert rows == [] and evidence['complete']
