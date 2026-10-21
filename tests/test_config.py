import os
import unittest
from unittest.mock import patch

from config import Config


class ConfigTests(unittest.TestCase):
    def load_config(self, values):
        base = {'BOT_TOKEN': 'test-token', 'ADMIN_IDS': '1,2',
                'TEZCHECK_CASH_DESK_CODE': 'desk', 'MONGODB_URI': 'mongodb://localhost:27017'}
        with patch.dict(os.environ, {**base, **values}, clear=True), patch('config.load_dotenv'):
            return Config.load()

    def test_new_names(self):
        cfg = self.load_config({'TEZCHECK_API_TOKEN': 'new-token',
                               'TEZCHECK_API_BASE_URL': 'https://example.test/api/'})
        self.assertEqual(cfg.api_key, 'new-token')
        self.assertEqual(cfg.base_url, 'https://example.test/api')

    def test_legacy_names_still_work(self):
        cfg = self.load_config({'TEZCHECK_API_KEY': 'old-token',
                               'TEZCHECK_BASE_URL': 'https://old.example.test/api'})
        self.assertEqual(cfg.api_key, 'old-token')
        self.assertEqual(cfg.base_url, 'https://old.example.test/api')

    def test_new_names_take_priority(self):
        cfg = self.load_config({'TEZCHECK_API_TOKEN': 'new', 'TEZCHECK_API_KEY': 'old',
            'TEZCHECK_API_BASE_URL': 'https://new.example.test', 'TEZCHECK_BASE_URL': 'https://old.example.test'})
        self.assertEqual(cfg.api_key, 'new')
        self.assertEqual(cfg.base_url, 'https://new.example.test')

    def test_blank_new_values_fall_back_to_old(self):
        cfg = self.load_config({'TEZCHECK_API_TOKEN': ' ', 'TEZCHECK_API_KEY': 'old',
            'TEZCHECK_API_BASE_URL': '', 'TEZCHECK_BASE_URL': 'https://old.example.test'})
        self.assertEqual(cfg.api_key, 'old')
        self.assertEqual(cfg.base_url, 'https://old.example.test')

    def test_missing_api_token(self):
        with self.assertRaisesRegex(ValueError, 'TEZCHECK_API_TOKEN'):
            self.load_config({})

    def test_brand_and_support(self):
        cfg = self.load_config({'TEZCHECK_API_TOKEN': 'key', 'BRAND_NAME': 'Test maktab', 'SUPPORT_USERNAME': '@test_support'})
        self.assertEqual(cfg.brand, 'Test maktab')
        self.assertEqual(cfg.support_username, 'test_support')

    def test_invalid_support_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'SUPPORT_USERNAME'):
            self.load_config({'TEZCHECK_API_TOKEN': 'key', 'SUPPORT_USERNAME': 'https://example.test'})


if __name__ == '__main__':
    unittest.main()
