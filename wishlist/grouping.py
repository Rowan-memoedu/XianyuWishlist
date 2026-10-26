"""Conservative automatic grouping; explicit labels can override suggestions."""
import re
import unicodedata
from difflib import SequenceMatcher


def core(title):
    title = unicodedata.normalize('NFKC', title).lower()
    title = re.sub(r'电子版|电子书|电子资料|高清|正版|全新|包邮|低价|秒发|自动发货|可打印|扫描版|pdf|word', '', title)
    return re.sub(r'[^\w\u4e00-\u9fff]', '', title)


def guards(title):
    text = unicodedata.normalize('NFKC', title).lower()
    return (
        tuple(sorted(set(re.findall(r'\d+(?:\.\d+)?\s*(?:gb|tb|g|t|寸|年|版)?', text)))),
        tuple(sorted(set(re.findall(r'上册|下册|中册|上下册|全套|第[一二三四五六七八九十\d]+[册卷版]|必修[一二三\d]?|选修[一二三\d]?', text)))),
        tuple(sorted(set(re.findall(r'[a-z]+\d+[a-z\d]*', text)))),
        tuple(sorted(set(re.findall(r'物理|数学|英语|化学|生物|语文|历史|地理|政治', text)))),
    )


def same_type(a, b):
    a_core, b_core = core(a), core(b)
    if not a_core or not b_core or guards(a) != guards(b):
        return False
    if a_core == b_core:
        return True
    score = SequenceMatcher(None, a_core, b_core).ratio()
    shortest = min(len(a_core), len(b_core))
    return shortest >= 6 and (score >= .88 or (shortest >= 8 and score >= .75 and (a_core in b_core or b_core in a_core)))
