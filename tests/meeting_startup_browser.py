"""Exercise initial meeting loading in Chromium with controlled API responses.

Requires Python Playwright and its Chromium browser. Start an isolated dev server:

    NEXT_PUBLIC_COGNITO_USER_POOL_ID= NEXT_PUBLIC_COGNITO_CLIENT_ID= \
      NEXT_PUBLIC_WS_ENDPOINT= \
      NEXT_PUBLIC_API_ENDPOINT=http://127.0.0.1:3100/__ui_api \
      npm run dev -- --hostname 127.0.0.1 --port 3100

Then run: python tests/meeting_startup_browser.py
All meeting/translation requests are intercepted; no account or AWS writes are used.
Set TRANSMEET_TEST_URL to use a different local dev-server URL with the same API path.
"""
import asyncio
import json
import os
from time import perf_counter
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

BASE = os.environ.get('TRANSMEET_TEST_URL', 'http://127.0.0.1:3100').rstrip('/')
SAVED = {'meetingId': 'saved', 'title': '보존된 회의 테스트', 'createdAt': '2026-10-27T12:00:00Z', 'messageCount': 2}
EMPTY = {'meetingId': 'empty', 'title': '빈 회의 테스트', 'createdAt': '2026-10-26T12:00:00Z', 'messageCount': 0}
AUDIO = {'meetingId': 'audio', 'title': '녹음 보존 테스트', 'createdAt': '2026-10-25T12:00:00Z', 'messageCount': 0, 'hasAudio': True}
UNKNOWN = {'meetingId': 'unknown', 'title': '과거 회의 테스트', 'createdAt': '2026-10-24T12:00:00Z'}
CREATED = {'meetingId': 'new', 'title': '새 회의 테스트', 'createdAt': '2026-10-28T10:00:00Z', 'messageCount': 0, 'messages': []}


async def scenario(browser, name, *, order='list', linked=None, select=None, list_status=200, create_status=200, include_created=False, send_note=False, switch_to=None, new_details=False, delete_created=False, record_before_delete=False):
    context = await browser.new_context(viewport={'width': 1280, 'height': 900}, service_workers='block')
    await context.add_init_script("localStorage.setItem('transmeet-settings', JSON.stringify({ttsAutoPlay:false,audioSource:'mic'}))")
    await context.grant_permissions(['microphone'])
    page = await context.new_page()
    list_started, create_started = asyncio.Event(), asyncio.Event()
    list_gate, create_gate = asyncio.Event(), asyncio.Event()
    deletions = []
    errors = []
    calls = []
    records = [SAVED, EMPTY, AUDIO, UNKNOWN]
    if include_created:
        records.insert(0, CREATED)
    create_count = 0
    page.on('pageerror', lambda error: errors.append(str(error)))

    async def route_api(route):
        nonlocal create_count
        method = route.request.method
        path = urlparse(route.request.url).path.removeprefix('/__ui_api')
        headers = {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': '*'}
        if method == 'OPTIONS':
            await route.fulfill(status=204, headers=headers)
            return
        calls.append({'method': method, 'path': path, 'at': round(perf_counter(), 3)})
        status = 200
        if method == 'GET' and path == '/meetings':
            list_started.set()
            await list_gate.wait()
            status = list_status
            result = records if status == 200 else {'error': 'List unavailable'}
        elif method == 'POST' and path == '/meetings':
            create_count += 1
            create_started.set()
            await create_gate.wait()
            status = create_status if create_count == 1 else 200
            result = CREATED if status == 200 else {'error': 'Create unavailable'}
        elif method == 'GET' and path.startswith('/meetings/'):
            meeting = next(m for m in records if m['meetingId'] == path.split('/')[-1])
            result = {**meeting, 'messages': [] if meeting.get('messageCount') == 0 and not (new_details and meeting['meetingId'] == 'new') else [{
                'id': 'msg1', 'speaker': 'speaker1', 'originalText': '이 기록을 보존합니다.',
                'translatedText': 'Keep this transcript.', 'timestamp': '2026-10-27T12:01:00Z',
            }]}
        elif method == 'POST' and path == '/tts':
            result = {'audioData': '', 'translatedText': 'Keep this note.', 'format': 'mp3'}
        elif method == 'DELETE':
            deletions.append(path.split('/')[-1])
            await route.fulfill(status=204, headers=headers)
            return
        else:
            raise AssertionError(f'Unexpected API: {method} {path}')
        await route.fulfill(status=status, content_type='application/json', headers=headers, body=json.dumps(result))

    await page.route('**/__ui_api/**', route_api)
    try:
        await page.goto(BASE + (f'/?meeting={linked}' if linked else '/'), wait_until='domcontentloaded')
        await asyncio.wait_for(asyncio.gather(list_started.wait(), create_started.wait()), timeout=10)
        nav = page.get_by_role('navigation', name='회의 기록')
        record_button = page.get_by_role('button', name='녹음 시작', exact=True)
        await expect(nav).to_be_visible()
        await expect(nav).to_have_attribute('aria-busy', 'true')
        await expect(record_button).to_be_disabled()
        await page.get_by_role('tablist', name='회의 도구').get_by_role('tab', name='내 메모', exact=True).click()
        await expect(page.get_by_role('textbox', name='내 메모 입력')).to_be_disabled()

        if order == 'list':
            list_gate.set()
            await expect(nav).to_have_attribute('aria-busy', 'false')
            if list_status == 200:
                await expect(nav.get_by_text(SAVED['title'], exact=True)).to_be_visible()
                assert not create_gate.is_set(), 'List must render before create finishes'
                if not linked:
                    await expect(record_button).to_be_disabled()
                if select:
                    target = next(m for m in records if m['meetingId'] == select)
                    await nav.get_by_text(target['title'], exact=True).click()
                    await expect(record_button).to_be_enabled()
                    if select == 'saved' or new_details:
                        await expect(page.get_by_text('이 기록을 보존합니다.', exact=True)).to_be_visible()
                if send_note:
                    await page.get_by_role('textbox', name='내 메모 입력').fill('새로 입력한 메모')
                    await page.get_by_role('button', name='메모 보내기', exact=True).click()
                    await expect(page.get_by_text('Keep this note.', exact=True)).to_be_visible()
                if switch_to:
                    target = next(m for m in records if m['meetingId'] == switch_to)
                    await nav.get_by_text(target['title'], exact=True).click()
                    await expect(nav.locator('button[aria-current="page"]')).to_contain_text(target['title'])
                if linked:
                    target = next(m for m in records if m['meetingId'] == linked)
                    await expect(nav.locator('button[aria-current="page"]')).to_contain_text(target['title'])
            else:
                await expect(nav.get_by_role('alert')).to_be_visible()
            create_gate.set()
        else:
            create_gate.set()
            await expect(nav.get_by_text(CREATED['title'], exact=True)).to_be_visible()
            await expect(record_button).to_be_enabled()
            await expect(nav).to_have_attribute('aria-busy', 'true')
            if record_before_delete:
                await record_button.click()
                await expect(page.get_by_role('button', name='녹음 중지', exact=True)).to_have_attribute('aria-pressed', 'true')
            if delete_created:
                await nav.get_by_role('button', name=f'{CREATED["title"]} 작업', exact=True).click()
                await nav.get_by_role('button', name='회의 삭제', exact=True).click()
                await nav.get_by_role('group', name='회의 삭제 확인').get_by_role('button', name='삭제', exact=True).click()
                await expect(nav.get_by_text(CREATED['title'], exact=True)).to_have_count(0)
                if record_before_delete:
                    stop_button = page.get_by_role('button', name='녹음 중지', exact=True)
                    await expect(stop_button).to_be_enabled()
                    await stop_button.click()
                await expect(record_button).to_be_disabled()
            list_gate.set()

        await expect(nav).to_have_attribute('aria-busy', 'false')
        await expect(page.get_by_role('button', name='회의 만드는 중…', exact=True)).to_have_count(0)
        if create_status == 200:
            await expect(nav.get_by_text(CREATED['title'], exact=True)).to_have_count(0 if delete_created else 1)
            await expect(record_button).to_be_enabled()
            expected_active = 'saved' if delete_created else linked or switch_to or select or 'new'
            target = next(m for m in records + [CREATED] if m['meetingId'] == expected_active)
            await expect(nav.locator('button[aria-current="page"]')).to_contain_text(target['title'])
            if select == 'saved' or new_details:
                await expect(page.get_by_text('이 기록을 보존합니다.', exact=True)).to_be_visible()
            if list_status == 200:
                if 'empty' in {expected_active, select, linked}:
                    await expect(nav.get_by_text(EMPTY['title'], exact=True)).to_be_visible()
                else:
                    await expect(nav.get_by_text(EMPTY['title'], exact=True)).to_have_count(0)
                await expect(nav.get_by_text(AUDIO['title'], exact=True)).to_be_visible()
                await expect(nav.get_by_text(UNKNOWN['title'], exact=True)).to_be_visible()
            protected = {'audio', 'unknown', linked, select}
            if not delete_created:
                protected.add('new')
            assert not protected.intersection(deletions), deletions
        elif list_status == 200:
            await expect(nav.locator('button[aria-current="page"]')).to_contain_text(SAVED['title'])
            await expect(nav.get_by_text(EMPTY['title'], exact=True)).to_be_visible()
            assert not deletions, deletions
            if select:
                assert len([c for c in calls if c['path'] == f'/meetings/{select}' and c['method'] == 'GET']) == 1, calls
        else:
            await expect(nav.get_by_role('alert')).to_be_visible()
            await expect(record_button).to_be_disabled()
            await expect(page.get_by_text('새 회의를 만들어 시작하세요', exact=True)).to_be_visible()
            await page.get_by_role('button', name='새 회의', exact=True).click()
            await expect(record_button).to_be_enabled()
            await expect(nav.locator('button[aria-current="page"]')).to_contain_text(CREATED['title'])
            assert not deletions, deletions

        # Mount replay in StrictMode must not issue a second initial creation.
        assert create_count == (2 if list_status != 200 and create_status != 200 else 1), calls
        assert not errors, errors
        return {'name': name, 'passed': True, 'calls': calls, 'deleted': deletions, 'runtimeErrors': errors}
    finally:
        list_gate.set()
        create_gate.set()
        await context.close()


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'])
        results = []
        cases = [
            ('list visible before creation; preserve selected transcript', {'select': 'saved'}),
            ('create visible before list; no duplicate or deletion', {'order': 'create', 'include_created': True}),
            ('new meeting returned in list before create response', {'include_created': True}),
            ('deep link opens before create and is not pruned', {'linked': 'empty'}),
            ('selected empty meeting is not pruned', {'select': 'empty'}),
            ('create failure falls back without deleting empty meetings', {'create_status': 500}),
            ('create failure does not reload an already selected meeting', {'create_status': 500, 'select': 'saved'}),
            ('list failure keeps successful new meeting and shows error', {'list_status': 500}),
            ('both failures permit a new meeting retry', {'list_status': 500, 'create_status': 500}),
            ('used meeting survives switching away before initial create', {'select': 'empty', 'send_note': True, 'switch_to': 'saved'}),
            ('late create preserves details already loaded for its ID', {'include_created': True, 'select': 'new', 'new_details': True}),
            ('late list cannot restore a successfully deleted meeting', {'order': 'create', 'include_created': True, 'delete_created': True}),
            ('recording remains stoppable when active meeting is deleted', {'order': 'create', 'include_created': True, 'delete_created': True, 'record_before_delete': True}),
        ]
        try:
            for name, options in cases:
                result = await scenario(browser, name, **options)
                results.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            await browser.close()
        print(json.dumps({'passed': len(results), 'failed': 0}, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
