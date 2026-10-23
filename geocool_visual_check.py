from pathlib import Path
from playwright.sync_api import sync_playwright
import json
import zipfile

BASE = 'http://127.0.0.1:3000'
OUT = Path('/home/ubuntu/geocool/screenshots')
OUT.mkdir(parents=True, exist_ok=True)
ROUTES = [
    ('overview', '/'),
    ('ward-detail', '/ward/DEMO-01'),
    ('alerts', '/alerts'),
    ('metrics', '/metrics'),
    ('data-pipeline', '/data'),
    ('settings', '/settings'),
    ('how-this-works', '/how'),
]
results, popup_checks, page_errors = [], [], []


def wait_app(page):
    page.wait_for_selector('#page-title', timeout=15000)
    page.wait_for_function("document.querySelector('#page-content')?.getAttribute('aria-busy') !== 'true'", timeout=30000)
    page.wait_for_timeout(300)


def run_audit(page, label):
    failures = page.evaluate('window.runContrastAudit ? window.runContrastAudit() : -1')
    result = page.evaluate('window.lastContrastAuditResult || null')
    assert isinstance(failures, int) and failures >= 0, f'{label}: contrast audit did not run.'
    assert result and result.startswith('Contrast audit:'), f'{label}: audit result sentence was not stored.'
    assert failures == 0 and '0 failure(s)' in result, f'{label}: {result}'
    page.locator('#toast-region').evaluate('(el) => el.replaceChildren()')
    return failures, result


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox', '--disable-dev-shm-usage'])
    context = browser.new_context(viewport={'width': 1600, 'height': 1050}, device_scale_factor=1)
    page = context.new_page()
    page.on('pageerror', lambda exc: page_errors.append(str(exc)))

    for theme in ('light', 'dark'):
        for slug, route in ROUTES:
            url = BASE + route + ('&' if '?' in route else '?') + 'theme=' + theme
            response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
            wait_app(page)
            title = page.locator('#page-title').inner_text()
            body_text = page.locator('#page-content').inner_text()
            map_paths = page.locator('#risk-map path.leaflet-interactive').count() if route == '/' else None
            audit_failures, audit_result = run_audit(page, f'{theme} {route}')
            screenshot = OUT / f'{theme}-{slug}.png'
            page.screenshot(path=str(screenshot), full_page=True, animations='disabled')
            result = {
                'theme': theme, 'route': route, 'http': response.status if response else None,
                'title': title, 'content_chars': len(body_text), 'map_features': map_paths,
                'audit_failures': audit_failures, 'audit_result': audit_result,
                'screenshot': str(screenshot), 'screenshot_bytes': screenshot.stat().st_size,
            }
            results.append(result)
            print(json.dumps(result))

    # Verify endpoint-backed ward explanations, signed contributions and contrast in both themes.
    for theme in ('light', 'dark'):
        page.goto(BASE + '/?theme=' + theme, wait_until='domcontentloaded', timeout=30000)
        wait_app(page)
        polygon = page.locator('#risk-map path.leaflet-interactive').first
        assert polygon.count() > 0, f'No ward geometry rendered in {theme} theme.'
        polygon.click(force=True)
        page.locator('.leaflet-popup-content [data-popup-explanation]').first.wait_for(timeout=15000)
        page.wait_for_function("document.querySelector('.leaflet-popup-content [data-popup-explanation]')?.dataset.loaded === 'true'", timeout=20000)
        popup = page.locator('.leaflet-popup-content').inner_text()
        assert 'Top 3 signed contributions' in popup and 'AHP weights' in popup and 'AHP risk' in popup, popup
        audit_failures, audit_result = run_audit(page, f'{theme} ward popup')
        screenshot = OUT / f'{theme}-overview-explanation-popup.png'
        page.screenshot(path=str(screenshot), full_page=True, animations='disabled')
        popup_checks.append({'theme': theme, 'kind': 'ward-popup', 'has_top3': True, 'has_ahp_weights': True, 'has_ml_ahp_scores': True, 'audit_failures': audit_failures, 'audit_result': audit_result, 'screenshot': str(screenshot), 'bytes': screenshot.stat().st_size})

    # Exercise the Ward/Street selector and audit street-edge explanation popups in both themes.
    for theme in ('light', 'dark'):
        page.goto(BASE + '/?theme=' + theme, wait_until='domcontentloaded', timeout=30000)
        wait_app(page)
        with page.expect_response(lambda response: response.url.endswith('/api/edges?limit=5000') and response.status == 200, timeout=20000):
            page.locator('#map-mode').select_option('street')
        page.wait_for_function("Array.from(document.querySelectorAll('#risk-map path.leaflet-interactive')).some(el => el.getAttribute('stroke-width') === '3')", timeout=20000)
        page.wait_for_timeout(300)
        page.locator('#risk-map path.leaflet-interactive').first.click(force=True)
        page.locator('.leaflet-popup-content [data-popup-explanation]').first.wait_for(timeout=15000)
        page.wait_for_function("document.querySelector('.leaflet-popup-content [data-popup-explanation]')?.dataset.loaded === 'true'", timeout=20000)
        street_popup = page.locator('.leaflet-popup-content').inner_text()
        assert 'AHP risk' in street_popup and 'Top 3 signed contributions' in street_popup and 'AHP weights' in street_popup, street_popup
        audit_failures, audit_result = run_audit(page, f'{theme} street popup')
        screenshot = OUT / f'{theme}-street-explanation-popup.png'
        page.screenshot(path=str(screenshot), full_page=True, animations='disabled')
        popup_checks.append({'theme': theme, 'kind': 'street-popup', 'has_edge_scores': True, 'has_top3': True, 'has_ahp_weights': True, 'audit_failures': audit_failures, 'audit_result': audit_result, 'screenshot': str(screenshot), 'bytes': screenshot.stat().st_size})

    # Verify ward search navigates to a real ward detail and renders its API-backed score evidence.
    page.goto(BASE + '/?theme=light', wait_until='domcontentloaded', timeout=30000)
    wait_app(page)
    page.locator('#ward-search').fill('DEMO-01')
    page.locator('[data-action="search-map"]').click()
    page.wait_for_url('**/ward/DEMO-01*', timeout=15000)
    wait_app(page)
    detail = page.locator('#page-content').inner_text()
    assert 'Top three signed contributions' in detail and 'AHP weights' in detail
    print(json.dumps({'ui_search': 'passed', 'route': page.url, 'detail_contains_explanation': True}))
    browser.close()

report = {
    'screenshots': results,
    'popup_checks': popup_checks,
    'page_errors': page_errors,
    'audit_failures_total': sum(row['audit_failures'] for row in results) + sum(row['audit_failures'] for row in popup_checks),
    'audit_result_count': sum(bool(row.get('audit_result')) for row in results) + sum(bool(row.get('audit_result')) for row in popup_checks),
}
(OUT / 'visual-audit.json').write_text(json.dumps(report, indent=2) + '\n')
with zipfile.ZipFile('/home/ubuntu/geocool/screenshots.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(OUT.iterdir()):
        if path.is_file():
            archive.write(path, arcname=path.name)
print('FINAL', json.dumps({'page_screenshots': len(results), 'popup_screenshots': len(popup_checks), 'audit_result_count': report['audit_result_count'], 'audit_failures_total': report['audit_failures_total'], 'page_errors': page_errors, 'zip': '/home/ubuntu/geocool/screenshots.zip'}))
if report['audit_result_count'] != len(results) + len(popup_checks):
    raise SystemExit('One or more route or popup contrast results were not preserved.')
if report['audit_failures_total']:
    raise SystemExit('WCAG text/focus contrast audit found failures.')
if page_errors:
    raise SystemExit('Browser runtime exceptions found.')
