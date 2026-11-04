import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app_settings import AppSettingsManager, MODE_IDS
from test_app_settings import FakeProtector


class FlashMigrationTest(unittest.TestCase):
    def manager(self, state):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name, 'settings.enc')
        path.write_text(json.dumps(state), encoding='utf-8')
        return AppSettingsManager(config_path=str(path), protector=FakeProtector()), path

    def test_schema_two_moves_both_legacy_model_families_to_flash(self):
        state = {'schema_version': 2, 'provider': 'deepseek',
                 'models': {'fast-go': 'deepseek-v4-flash', 'fast-plus': 'deepseek-v4-flash-vision-exp', 'deep': 'deepseek-v4-pro'},
                 'primary_api_key_ciphertext': 'enc::primary-example', 'thinking_api_key_ciphertext': 'enc::thinking-example'}
        manager, path = self.manager(state)
        payload = manager.get_frontend_payload()
        self.assertEqual(payload['models'], dict.fromkeys(MODE_IDS, 'deepseek-flash'))
        self.assertEqual(manager.resolve_llm_config(required=True)['api_key'], 'primary-example')
        self.assertEqual(manager.resolve_llm_config(required=True, slot='thinking')['api_key'], 'thinking-example')
        # Loading must not rewrite the user's file. Normal saves persist schema 3.
        self.assertEqual(json.loads(path.read_text()), state)
        manager.save_api_settings('deepseek', models={'fast-go': 'deepseek-flash', 'fast-plus': 'deepseek-flash', 'deep': 'a-custom-model'})
        saved = json.loads(path.read_text())
        self.assertEqual(saved['schema_version'], 3)
        self.assertEqual(saved['thinking_api_key_ciphertext'], state['thinking_api_key_ciphertext'])

    def test_new_schema_preserves_explicit_per_mode_customization(self):
        manager, _ = self.manager({'schema_version': 3, 'provider': 'deepseek',
                                  'models': {'fast-go': 'my-fast', 'fast-plus': 'my-plus', 'deep': 'deepseek-v4-pro'}})
        self.assertEqual(manager.get_frontend_payload()['models']['deep'], 'deepseek-v4-pro')

    def test_openai_compatible_models_are_never_renamed(self):
        manager, _ = self.manager({'schema_version': 2, 'provider': 'openai_compatible',
                                  'custom_base_url': 'http://127.0.0.1:8000/v1',
                                  'models': dict.fromkeys(MODE_IDS, 'deepseek-v4-pro')})
        self.assertEqual(set(manager.get_frontend_payload()['models'].values()), {'deepseek-v4-pro'})

    def test_empty_legacy_state_gets_flash_for_every_mode(self):
        manager, _ = self.manager({'schema_version': 1, 'provider': 'deepseek'})
        self.assertEqual(manager.get_frontend_payload()['models'], dict.fromkeys(MODE_IDS, 'deepseek-flash'))

    def test_host_station_bridge_still_supplies_query_results(self):
        from tools.rail import railgo_client
        with patch.object(railgo_client, 'bridge_enabled', return_value=True), \
             patch.object(railgo_client, 'query_railgo_host', return_value=[{'Name': '南京南', 'Telecode': 'NKH'}]), \
             patch.object(railgo_client, '_get_json') as http:
            result = railgo_client.fetch_station_preselect_v1('南京')
        self.assertEqual(result['data'][0]['telecode'], 'NKH')
        http.assert_not_called()

    def test_embedded_page_and_host_version_fields_survive_upgrade(self):
        import web_app
        client = web_app.app.test_client()
        page = client.get('/?embedded=1').get_data(as_text=True)
        self.assertIn('class="embedded"', page)
        self.assertIn('settings-save-models', page)
        status = client.get('/api/status').get_json()
        self.assertEqual(status['version'], '3.0.0')
        self.assertTrue(status['embedded_supported'])
        self.assertIn('/api/shutdown', {rule.rule for rule in web_app.app.url_map.iter_rules()})


if __name__ == '__main__':
    unittest.main()
