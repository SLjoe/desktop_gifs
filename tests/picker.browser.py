"""Exercise the real content script in a local, notification-style reply modal.

Requires Playwright and its Chromium/Firefox binaries. All page content, Tenor
results and posting acknowledgements are simulated; no public comment is sent.
"""
import argparse
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = (ROOT / 'tests/fixtures/comment-modal.html').read_text(encoding='utf-8')
TEST_URL = 'https://desktop-gifs.test/fixture'


class PickerTests(unittest.TestCase):
    browser = None
    source = ROOT / 'extension/src/content.js'

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1280, 'height': 800})
        self.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=FIXTURE))
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_fixture()

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [], 'Unexpected page script error')

    def open_fixture(self, native=False):
        self.page.goto(TEST_URL + ('?native=1' if native else ''))
        self.page.add_style_tag(path=str(ROOT / 'extension/src/content.css'))
        self.page.add_script_tag(path=str(self.source))

    def open_picker(self, box='modal-box'):
        self.page.locator(f'#{box} .gcfy-btn').click()
        self.page.get_by_role('dialog', name='Choose a GIF').wait_for(state='visible')
        self.page.locator('.tile').first.wait_for(state='visible')

    def assert_modal_open(self):
        self.assertTrue(self.page.locator('#reply-modal').is_visible())
        self.assertEqual(self.page.evaluate('stats.dismissed'), 0)

    def assert_picker_events_contained(self):
        self.assertEqual(self.page.evaluate('stats.pickerBubble'), [])
        self.assertEqual(self.page.evaluate('stats.outsideFocus'), 0)

    def inject_newer_copy(self):
        """What sw.js does when the extension is updated or reloaded."""
        self.page.add_style_tag(path=str(ROOT / 'extension/src/content.css'))
        self.page.add_script_tag(path=str(self.source))

    def test_newer_copy_keeps_the_text_and_attached_gif_and_posts_once(self):
        editor = self.page.locator('#modal-box #contenteditable-root')
        editor.fill('My reply')
        self.open_picker()
        self.page.locator('.tile').nth(1).click()
        self.inject_newer_copy()
        self.page.locator('#modal-box .gcfy-attachment').wait_for(state='visible')
        for box in ('modal-box', 'inline-box'):
            self.assertEqual(self.page.locator(f'#{box} .gcfy-btn').count(), 1)
        self.assertEqual(self.page.locator('#modal-box .gcfy-attachment').count(), 1)
        self.assertEqual(self.page.locator('#modal-box .gcfy-attachment img').get_attribute('alt'), 'Second GIF')
        self.assertEqual(editor.inner_text(), 'My reply')
        self.page.locator('#modal-box #submit-button button').click()
        self.page.locator('#modal-box .gcfy-attachment').wait_for(state='detached')
        posts = self.page.evaluate('stats.posts')
        self.assertEqual(len(posts), 1)
        self.assertIn('My reply[[gif:12696714748418769445:', posts[0]['text'])
        self.assertEqual(len(self.page.evaluate('stats.arms')), 1)
        self.assert_modal_open()

    def test_newer_copy_replaces_an_open_picker_and_its_buttons_work(self):
        self.open_picker()
        self.inject_newer_copy()
        self.assertEqual(self.page.locator('[data-gcfy-picker]').count(), 0)
        self.open_picker()
        self.assertEqual(self.page.get_by_role('dialog', name='Choose a GIF').count(), 1)
        self.page.locator('.tile').first.click()
        self.assertEqual(self.page.locator('#modal-box .gcfy-attachment').count(), 1)
        # The fixture's 1x1 previews leave the button clipped, so it is clicked directly.
        self.page.locator('#modal-box .gcfy-attachment-remove').dispatch_event('click')
        self.assertEqual(self.page.locator('#modal-box .gcfy-attachment').count(), 0)
        self.assert_modal_open()

    def test_gif_without_text_stays_postable_after_a_newer_copy(self):
        self.open_picker('inline-box')
        self.page.locator('.tile').first.click()
        submit = self.page.locator('#inline-box #submit-button button')
        self.assertTrue(submit.is_enabled())
        self.inject_newer_copy()
        self.assertTrue(submit.is_enabled())
        submit.click()
        self.page.locator('#inline-box .gcfy-attachment').wait_for(state='detached')
        self.assertEqual(len(self.page.evaluate('stats.posts')), 1)

    def test_update_notice_links_to_the_release_and_can_be_hidden(self):
        self.open_picker()
        notice = self.page.locator('.update')
        self.assertFalse(notice.is_visible())
        self.page.keyboard.press('Escape')

        self.open_fixture()
        url = 'https://github.com/SLjoe/desktop_gifs/releases/tag/v9.9.9'
        self.page.evaluate(f"window.release = {{ version: '9.9.9', url: '{url}' }}")
        self.open_picker()
        notice.wait_for(state='visible')
        self.assertIn('Desktop GIFs 9.9.9 is out.', notice.inner_text())
        self.assertEqual(notice.get_by_role('link', name='Download').get_attribute('href'), url)
        notice.get_by_role('button', name='Hide until the next version').click()
        self.assertFalse(notice.is_visible())
        self.assertEqual(self.page.evaluate('storedItems.dismissedUpdate'), '9.9.9')
        self.assert_modal_open()

    def test_search_favorites_and_pointer_events_keep_reply_open(self):
        self.open_picker()
        search = self.page.get_by_role('textbox', name='Search Tenor or paste a Tenor link')
        search.click()
        search.fill('cat')
        self.page.wait_for_function("stats.requests.some((request) => request.type === 'search' && request.query === 'cat')")
        self.page.locator('.star').first.click()
        self.page.get_by_role('button', name='Favorites', exact=True).click()
        self.assertEqual(self.page.locator('.tile').count(), 1)
        self.assertEqual(self.page.locator('.star').get_attribute('aria-pressed'), 'true')
        self.page.locator('.tile').dblclick(button='right')
        self.assert_modal_open()
        self.assert_picker_events_contained()

    def test_selection_preserves_text_and_reply_can_be_submitted(self):
        editor = self.page.locator('#modal-box #contenteditable-root')
        editor.fill('My reply')
        self.open_picker()
        self.page.locator('.tile').first.click()
        self.assert_modal_open()
        self.assertEqual(editor.inner_text(), 'My reply')
        self.assertEqual(self.page.locator('#modal-box .gcfy-attachment img').get_attribute('alt'), 'First GIF')
        self.assertEqual(self.page.get_by_role('dialog', name='Choose a GIF').count(), 0)
        self.assert_picker_events_contained()
        self.page.locator('#modal-box #submit-button button').click()
        self.page.locator('#modal-box .gcfy-attachment').wait_for(state='detached')
        posts = self.page.evaluate('stats.posts')
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['box'], 'modal-box')
        self.assertIn('My reply[[gif:13723327785995541767:', posts[0]['text'])
        self.assert_modal_open()

    def test_replacing_a_gif_and_search_shortcuts_do_not_submit_existing_attachment(self):
        self.open_picker()
        self.page.locator('.tile').first.click()
        self.open_picker()
        self.page.get_by_role('textbox', name='Search Tenor or paste a Tenor link').press('Control+Enter')
        self.page.evaluate('() => new Promise(requestAnimationFrame)')
        self.assertEqual(self.page.evaluate('stats.arms'), [])
        self.assertEqual(self.page.evaluate('stats.posts'), [])
        self.page.locator('.tile').nth(1).click()
        self.assertEqual(self.page.locator('#modal-box .gcfy-attachment').count(), 1)
        self.assertEqual(self.page.locator('#modal-box .gcfy-attachment img').get_attribute('alt'), 'Second GIF')
        self.assert_modal_open()
        self.assert_picker_events_contained()

    def test_escape_and_clicks_in_reply_close_only_picker_and_outside_click_still_dismisses_modal(self):
        self.open_picker()
        self.page.get_by_role('textbox', name='Search Tenor or paste a Tenor link').press('Escape')
        self.assertEqual(self.page.get_by_role('dialog', name='Choose a GIF').count(), 0)
        self.assert_modal_open()
        self.assertEqual(self.page.evaluate('document.activeElement.className'), 'gcfy-btn')
        self.open_picker()
        self.page.locator('#modal-box #emoji-button').click()
        self.assertEqual(self.page.get_by_role('dialog', name='Choose a GIF').count(), 0)
        self.assert_modal_open()
        self.open_picker()
        self.page.locator('#outside').click()
        self.assertFalse(self.page.locator('#reply-modal').is_visible())
        self.assertEqual(self.page.evaluate('stats.dismissed'), 1)
        self.assertEqual(self.page.get_by_role('dialog', name='Choose a GIF').count(), 0)

    def test_picker_escapes_transformed_modal_clipping_and_keeps_viewport_position(self):
        self.open_picker()
        panel = self.page.get_by_role('dialog', name='Choose a GIF').bounding_box()
        anchor = self.page.locator('#modal-box .gcfy-btn').bounding_box()
        modal = self.page.locator('#reply-modal').bounding_box()
        self.assertAlmostEqual(panel['x'], anchor['x'], delta=1)
        self.assertAlmostEqual(panel['y'], anchor['y'] - panel['height'] - 8, delta=1)
        self.assertLess(panel['y'], modal['y'])
        self.assertGreaterEqual(panel['y'], 8)
        self.assertLessEqual(panel['x'] + panel['width'], 1280 - 8)
        self.page.get_by_role('textbox', name='Search Tenor or paste a Tenor link').click()
        self.assert_modal_open()

    def test_native_modal_dialog_can_focus_and_select_gifs(self):
        self.open_fixture(native=True)
        self.open_picker()
        self.page.get_by_role('textbox', name='Search Tenor or paste a Tenor link').click()
        self.page.locator('.tile').first.click()
        self.assertTrue(self.page.evaluate("document.querySelector('#reply-modal').open"))
        self.assertEqual(self.page.locator('#modal-box .gcfy-attachment').count(), 1)
        self.assert_modal_open()
        self.assert_picker_events_contained()

    def test_inline_comment_and_gif_only_submit_still_work(self):
        self.page.evaluate("document.querySelector('#reply-modal').hidden = true")
        self.open_picker('inline-box')
        self.page.locator('.tile').first.click()
        self.assertEqual(self.page.locator('#inline-box .gcfy-attachment').count(), 1)
        self.page.locator('#inline-box #submit-button button').click()
        self.page.locator('#inline-box .gcfy-attachment').wait_for(state='detached')
        posts = self.page.evaluate('stats.posts')
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['box'], 'inline-box')
        self.assertTrue(posts[0]['text'].startswith('[[gif:13723327785995541767:'))
        self.assert_picker_events_contained()

    def test_rated_gif_skips_the_check_and_is_armed_for_retries(self):
        self.open_picker()
        self.page.locator('.tile').first.click()
        self.page.locator('#modal-box #submit-button button').click()
        self.page.locator('#modal-box .gcfy-attachment').wait_for(state='detached')
        self.assertFalse(any(r['type'] == 'check' for r in self.page.evaluate('stats.requests')))
        self.assertEqual([a['ratedG'] for a in self.page.evaluate('stats.arms')], [True])

    def test_unrated_gif_rated_g_by_the_check_is_armed_for_retries(self):
        self.page.evaluate('window.unrated = true')
        self.open_picker()
        self.page.locator('.tile').first.click()
        self.page.locator('#modal-box #submit-button button').click()
        self.page.locator('#modal-box .gcfy-attachment').wait_for(state='detached')
        self.assertTrue(any(r['type'] == 'check' for r in self.page.evaluate('stats.requests')))
        self.assertEqual([a['ratedG'] for a in self.page.evaluate('stats.arms')], [True])

    def test_gif_tenor_does_not_rate_g_is_never_armed_or_posted(self):
        self.page.evaluate("window.unrated = true; window.checkRating = 'PG'")
        self.page.locator('#modal-box #contenteditable-root').fill('Keep my reply')
        self.open_picker()
        self.page.locator('.tile').first.click()
        self.page.locator('#modal-box #submit-button button').click()
        self.page.wait_for_function("document.querySelector('.gcfy-attachment-status').textContent.includes('rated PG')")
        self.assertEqual(self.page.evaluate('stats.arms'), [])
        self.assertEqual(self.page.evaluate('stats.posts'), [])
        self.assertEqual(self.page.locator('#modal-box #contenteditable-root').inner_text(), 'Keep my reply')
        self.assertEqual(self.page.locator('#modal-box .gcfy-attachment').count(), 1)
        self.assert_modal_open()

    def test_failed_rating_check_preserves_unknown_rating_for_retry_recovery(self):
        self.page.evaluate("window.unrated = true; window.checkFailure = 'Tenor returned HTTP 503.'")
        self.page.locator('#modal-box #contenteditable-root').fill('Keep my reply')
        self.open_picker()
        self.page.locator('.tile').first.click()
        self.page.locator('#modal-box #submit-button button').click()
        self.page.locator('#modal-box .gcfy-attachment').wait_for(state='detached')
        self.assertEqual([a['ratedG'] for a in self.page.evaluate('stats.arms')], [False])
        self.assertEqual([a['rating'] for a in self.page.evaluate('stats.arms')], [None])
        posts = self.page.evaluate('stats.posts')
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0]['text'].startswith('Keep my reply[[gif:'))

    def test_closing_reply_during_rating_check_cancels_submission(self):
        self.page.evaluate('window.unrated = true')
        self.open_picker()
        self.page.locator('.tile').first.click()
        self.page.evaluate('window.delayCheck = true')
        self.page.locator('#modal-box #submit-button button').click()
        self.page.wait_for_function("typeof window.finishCheck === 'function'")
        self.page.locator('#outside').click()
        self.page.evaluate('window.finishCheck()')
        self.page.wait_for_function("document.querySelector('.gcfy-attachment-status').textContent.includes('box was closed')")
        self.assertEqual(self.page.evaluate('stats.arms'), [])
        self.assertEqual(self.page.evaluate('stats.posts'), [])
        self.assertEqual(self.page.locator('#modal-box #contenteditable-root').inner_text(), '')

    def test_native_loading_button_stays_disabled_and_failed_gif_can_be_retried(self):
        self.open_picker()
        self.page.locator('.tile').first.click()
        self.page.evaluate("window.delayPost = true; window.postFailure = 'YouTube rejected the test request.'")
        submit = self.page.locator('#modal-box #submit-button button')
        submit.click()
        self.page.wait_for_function("typeof window.finishPost === 'function'")
        self.page.evaluate('() => new Promise(requestAnimationFrame)')
        self.assertFalse(submit.is_enabled())
        self.page.locator('#modal-box #contenteditable-root').press('Control+Enter')
        self.assertEqual(len(self.page.evaluate('stats.posts')), 1)
        self.page.evaluate('window.finishPost()')
        self.page.wait_for_function("document.querySelector('.gcfy-attachment-status').textContent.includes('rejected the test')")
        self.assertEqual(self.page.locator('#modal-box #contenteditable-root').inner_text().strip(), '')
        self.assertTrue(submit.is_enabled())
        self.assertEqual(self.page.locator('#modal-box .gcfy-attachment').count(), 1)

    def test_attachment_does_not_override_native_disabled_state_of_a_text_draft(self):
        editor = self.page.locator('#modal-box #contenteditable-root')
        editor.fill('Draft that YouTube is validating')
        self.open_picker()
        self.page.locator('.tile').first.click()
        submit = self.page.locator('#modal-box #submit-button button')
        self.page.evaluate("document.querySelector('#modal-box #submit-button button').disabled = true")
        self.page.evaluate('() => new Promise(requestAnimationFrame)')
        self.assertFalse(submit.is_enabled())
        self.assertEqual(self.page.evaluate('stats.posts'), [])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', choices=['chromium', 'firefox', 'both'], default='both')
    parser.add_argument('--chromium-executable')
    parser.add_argument('--firefox-executable')
    parser.add_argument('--source', type=Path, default=PickerTests.source, help='Content-script override for baseline checks')
    args = parser.parse_args()
    PickerTests.source = args.source.resolve()
    passed = True
    with sync_playwright() as playwright:
        for name in (['chromium', 'firefox'] if args.browser == 'both' else [args.browser]):
            print(f'\nBrowser: {name}', flush=True)
            options = {'headless': True}
            executable = getattr(args, f'{name}_executable')
            if executable:
                options['executable_path'] = executable
            with getattr(playwright, name).launch(**options) as browser:
                PickerTests.browser = browser
                result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PickerTests))
                passed = result.wasSuccessful() and passed
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
