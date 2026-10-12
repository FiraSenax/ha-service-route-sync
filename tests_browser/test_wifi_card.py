"""Real Chromium checks using synthetic data only; no HA credentials needed."""

import json
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright

SOURCE = Path(__file__).resolve().parents[1] / 'custom_components/service_route_sync/frontend/wifi-history-card.js'


class WifiCardBrowserTests(unittest.TestCase):
    def test_render_pagination_source_permission_and_text_safety(self):
        with sync_playwright() as runner:
            browser = runner.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1100, 'height': 750})
            page.set_content('<body style="font-family:sans-serif;--primary-text-color:#222;--card-background-color:white;--divider-color:#ddd"></body>')
            page.add_script_tag(content=SOURCE.read_text(), type='module')
            page.wait_for_function("customElements.get('service-route-sync-wifi-history') !== undefined")
            page.evaluate("""() => {
                window.calls = [];
                const card = document.createElement('service-route-sync-wifi-history');
                card.setConfig({entry_id: 'synthetic'});
                card.hass = {user: {is_admin: true}, callWS: async request => {
                    window.calls.push(request);
                    const columns = ['ts','mac','name','essid','ap_mac','radio','channel','width','signal','noise','tx_rate','rx_rate','nss','retry_pct','tx_retries','wifi_tx_attempts','satisfaction'];
                    const row = [1000, '02:00:00:00:00:01', '<img src=x onerror="window.injected=true">', 'Synthetic Wi-Fi', null, '5GHz', 36, 80, -53, -94, 100, 100, 2, 0, null, 0, 99];
                    return {columns, rows: [row], after: request.after + 1, ceiling: request.ceiling ?? 2, more: request.after === 0};
                }};
                document.body.append(card);
                window.card = card;
            }""")
            page.get_by_role('cell', name='Synthetic Wi-Fi', exact=True).wait_for()
            self.assertEqual(page.locator('service-route-sync-wifi-history img').count(), 0)
            self.assertFalse(page.evaluate('Boolean(window.injected)'))
            self.assertEqual(page.get_by_role('cell', name='—', exact=True).count(), 2)
            page.get_by_role('button', name='Weiter', exact=True).click()
            page.wait_for_function('window.calls.length === 2 && !window.card.loading')
            self.assertEqual(page.evaluate('window.calls[1].ceiling'), 2)
            self.assertTrue(page.get_by_role('button', name='Weiter', exact=True).is_disabled())
            page.get_by_role('button', name='Zurück', exact=True).click()
            page.wait_for_function('window.calls.length === 3 && !window.card.loading')
            self.assertEqual(page.evaluate('window.calls[2].after'), 0)
            page.get_by_label('Historienquelle').select_option('legacy')
            page.wait_for_function('window.calls.length === 4 && !window.card.loading')
            self.assertEqual(page.evaluate('window.calls[3].source'), 'legacy')
            page.screenshot(path='/tmp/service-route-sync-wifi-card-synthetic.png', full_page=True)
            page.evaluate('window.card.hass = {user: {is_admin: false}}')
            page.get_by_text('Diese Gerätehistorie ist nur für Administratoren verfügbar.').wait_for()
            self.assertEqual(page.get_by_role('table').count(), 0)
            self.assertEqual(page.evaluate('window.calls.length'), 4)
            print(json.dumps({'browser': 'Chromium', 'synthetic_only': True,
                              'paging_source_permissions_text_safety': True}))
            browser.close()


if __name__ == '__main__':
    unittest.main()
