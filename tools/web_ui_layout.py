"""Check management layouts using isolated browser data; no live services."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect
from web_ui_workflows import MockBackend, ORIGIN, collection, advance_poll


def main():
    reports = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        for width in (320, 390, 768, 1440):
            backend = MockBackend()
            backend.state['collections'] = [
                collection('movie-a', name='A Small Adventure', created_at=1),
                collection('movie-z', name='Zero Gravity', created_at=2, missing=[{'title':'Missing','year':2020}]),
                collection('tv', name='A Television Draft', media_type='show'),
                collection('live', name='Live Movie Shelf', status='published', home=True, rotation_enabled=True),
                collection('pinned', name='First Dibs on the Sofa', status='published', home=True, pinned_home=True),
            ]
            backend.state['jobs'] = [dict(id='new',kind='Sync',status='succeeded'),dict(id='old',kind='Sync',status='failed')]
            errors = []
            context = browser.new_context(viewport={'width':width,'height':900}, service_workers='block')
            context.route('**/*',backend.intercept)
            page = context.new_page()
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.clock.install()
            page.goto(ORIGIN+'/#overview')
            expect(page.locator('.hero')).to_have_count(0)
            expect(page.get_by_text('3 drafts ready to review')).to_be_visible()
            expect(page.get_by_text('tasks need checking', exact=False)).to_have_count(0)
            expect(page.locator('[data-overview-home] .home-shelf')).to_have_count(2)
            assert page.locator('[data-overview-home]').bounding_box()['y'] < 600
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=f'web-verification/tidy-overview-{width}.png', full_page=True)

            page.locator('a[href="#collections"]').click()
            page.locator('#collection-media').select_option('movies')
            page.locator('#collection-status').select_option('draft')
            expect(page.locator('#collection-results [data-action="open"]')).to_have_count(2)
            page.locator('[data-view="list"]').click()
            expect(page.locator('.collection-table tbody tr')).to_have_count(2)
            page.locator('#collection-sort').select_option('missing')
            expect(page.locator('.collection-table tbody tr').first).to_contain_text('Zero Gravity')
            page.locator('#collection-search').fill('Gravity')
            expect(page.locator('.collection-table tbody tr')).to_have_count(1)
            backend.state['library']['count'] = 99
            advance_poll(page,16000)
            expect(page.locator('#collection-media')).to_have_value('movies')
            expect(page.locator('#collection-sort')).to_have_value('missing')
            expect(page.locator('#collection-search')).to_have_value('Gravity')
            expect(page.locator('.collection-table tbody tr')).to_have_count(1)
            page.locator('#collection-search').fill('')
            page.locator('#collection-status').select_option('all')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=f'web-verification/tidy-collections-{width}.png', full_page=True)
            page.locator('.collection-table [data-id="live"]').click()
            expect(page.locator('#collection-dialog')).to_be_visible()
            assert page.locator('#collection-dialog').evaluate('(el)=>el.scrollWidth <= el.clientWidth')
            page.keyboard.press('Escape')
            page.reload()
            expect(page.locator('[data-view="list"]')).to_have_attribute('aria-pressed','true')

            page.locator('a[href="#rotation"]').click()
            expect(page.locator('.slot-card')).to_have_count(4)
            expect(page.locator('.slot-card').first).to_contain_text('1 / 2')
            expect(page.locator('#rotation-form')).not_to_be_visible()
            page.locator('#rotation-schedule > summary').click()
            expect(page.locator('#rotation-form')).to_be_visible()
            backend.state['library']['count'] = 100
            advance_poll(page,16000)
            expect(page.locator('#rotation-form')).to_be_visible()
            page.locator('#rotation-schedule > summary').click()
            expect(page.locator('#rotation-form')).not_to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=f'web-verification/tidy-rotation-{width}.png', full_page=True)

            backend.state['library']['count'] = 0
            page.goto(ORIGIN+'/#overview')
            page.reload()
            expect(page.locator('.hero')).to_be_visible()
            assert not errors and not backend.unexpected_requests and not backend.writes
            reports.append({'width':width,'passed':True,'writes':0,'script_errors':errors})
            context.close()
        browser.close()
    Path('web-verification/tidy-layout.json').write_text(json.dumps(reports,indent=2),encoding='utf-8')
    print(json.dumps(reports))


if __name__ == '__main__':
    main()
