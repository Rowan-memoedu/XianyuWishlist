"""Read-only render checks against the local app; never clicks or submits."""
import json
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

out = Path('D:/AppData/XianyuWishlist/private/render-check')
out.mkdir(parents=True, exist_ok=True)
results = []
with urlopen('http://127.0.0.1:8766/api/state') as response:
    expected = sum(x['state'] == 'inbox' for x in json.load(response)['items'])
with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    for width in (320, 375, 414, 768, 1280):
        page = browser.new_page(viewport={'width':width,'height':800},device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8766/', wait_until='domcontentloaded')
        page.locator('.item').first.wait_for(timeout=20000)
        page.screenshot(path=str(out / f'inbox-{width}.png'))
        measurement = page.evaluate('''() => ({
          width:innerWidth, scrollWidth:document.documentElement.scrollWidth,
          rows:document.querySelectorAll('.item').length,
          checkboxCount:document.querySelectorAll('input[type=checkbox]').length,
          wrappedButtons:[...document.querySelectorAll('button')].filter(x=>x.getBoundingClientRect().width>0&&x.scrollWidth>x.clientWidth).map(x=>x.textContent),
          heading:document.querySelector('h1').textContent
        })''')
        measurement.update(errors=errors)
        assert measurement['scrollWidth'] <= width, measurement
        assert measurement['rows'] == measurement['checkboxCount'] == expected, measurement
        assert not errors and not measurement['wrappedButtons'], measurement
        results.append(measurement)
        page.close()
    browser.close()
(out / 'report.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'render_checks':len(results),'widths':[x['width'] for x in results], 'rows':expected,'writes':0}))
