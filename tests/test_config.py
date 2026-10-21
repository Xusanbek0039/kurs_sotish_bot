import unittest

from config import ConfigError, load_settings

BASE = {
    'BOT_TOKEN': '123456789:' + 'A' * 35,
    'ADMIN_IDS': '111, 222',
    'TEZCHECK_API_KEY': 'aps_key',
    'TEZCHECK_CASH_DESK_CODE': 'cdk_desk',
}


class ConfigTests(unittest.TestCase):
    def test_minimal_config_and_defaults(self):
        s = load_settings(dict(BASE))
        self.assertEqual(s.admin_ids, frozenset({111, 222}))
        self.assertEqual(s.tezcheck_base_url, 'https://api.tezcheck.uz/api/merchant/v1')
        self.assertEqual(s.default_language, 'uz')
        self.assertTrue(str(s.database_path).endswith('bot.sqlite3'))

    def test_secrets_not_in_repr(self):
        s = load_settings(dict(BASE))
        text = repr(s)
        for secret in (BASE['BOT_TOKEN'], 'aps_key', 'cdk_desk'):
            self.assertNotIn(secret, text)
        self.assertEqual(set(s.secrets()), {BASE['BOT_TOKEN'], 'aps_key'})

    def test_old_webhook_values_are_ignored(self):
        s = load_settings({**BASE, 'TEZCHECK_WEBHOOK_SECRET': 'x', 'PUBLIC_BASE_URL': 'http://old', 'WEBHOOK_PORT': 'abc'})
        self.assertFalse(hasattr(s, 'tezcheck_webhook_secret'))

    def test_required_values(self):
        for key in BASE:
            with self.subTest(key=key), self.assertRaises(ConfigError):
                load_settings({**BASE, key: ''})

    def test_bad_admin_ids(self):
        for value in ('abc', '-5', '@username'):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                load_settings({**BASE, 'ADMIN_IDS': value})

    def test_legacy_names_still_work(self):
        env = {k: v for k, v in BASE.items() if k != 'TEZCHECK_API_KEY'}
        s = load_settings({**env, 'TEZCHECK_API_TOKEN': 'old_key', 'POLL_INTERVAL': '90', 'INVITE_TTL_HOURS': '12'})
        self.assertEqual(s.tezcheck_api_key, 'old_key')
        self.assertEqual(s.payment_check_interval, 90)
        self.assertEqual(s.invite_link_ttl_hours, 12)

    def test_invalid_custom_emoji_falls_back(self):
        with self.assertLogs('config', level='WARNING'):
            s = load_settings({**BASE, 'CUSTOM_EMOJI_COURSE': 'not-an-id', 'CUSTOM_EMOJI_PAYMENT': '5368324170671202286'})
        self.assertNotIn('course', s.custom_emoji)
        self.assertEqual(s.custom_emoji['payment'], '5368324170671202286')

    def test_language_and_ranges(self):
        self.assertEqual(load_settings({**BASE, 'DEFAULT_LANGUAGE': 'RU'}).default_language, 'ru')
        for key, value in (('DEFAULT_LANGUAGE', 'en'), ('TEZCHECK_BASE_URL', 'http://api.x'), ('PAYMENT_CHECK_INTERVAL', '5'),
                           ('INVITE_LINK_TTL_HOURS', '0'), ('SUPPORT_USERNAME', 'a b')):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                load_settings({**BASE, key: value})


if __name__ == '__main__':
    unittest.main()
