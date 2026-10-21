import re
import string
import unittest
from pathlib import Path

from locales import TEXTS, t

ROOT = Path(__file__).resolve().parent.parent


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class LocaleTests(unittest.TestCase):
    def test_same_keys_in_both_languages(self):
        self.assertEqual(set(TEXTS['uz']), set(TEXTS['ru']))

    def test_same_placeholders(self):
        for key in TEXTS['uz']:
            with self.subTest(key=key):
                self.assertEqual(placeholders(TEXTS['uz'][key]), placeholders(TEXTS['ru'][key]))

    def test_every_key_used_in_code_exists(self):
        """Koddagi t(lang, 'kalit') chaqiruvlaridagi har bir kalit tarjimada bo‘lishi kerak."""
        used = set()
        for path in list(ROOT.glob('*.py')) + list(ROOT.glob('handlers/*.py')) + list(ROOT.glob('services/*.py')) \
                + list(ROOT.glob('keyboards/*.py')):
            used |= set(re.findall(r"\bt\([^,()]+,\s*'([a-z0-9_]+)'", path.read_text(encoding='utf-8')))
        self.assertTrue(used)
        self.assertEqual(sorted(used - set(TEXTS['uz'])), [])

    def test_uzbek_uses_proper_apostrophe(self):
        # O‘zbek lotin yozuvida o‘ va g‘ uchun ‘ (U+2018) ishlatiladi, oddiy ' emas.
        for key, text in TEXTS['uz'].items():
            with self.subTest(key=key):
                self.assertNotRegex(text, r"[OoGg]'")

    def test_format_and_fallback(self):
        self.assertIn('150', t('ru', 'course_button', name='X', price='150 сум'))
        self.assertEqual(t('xx', 'currency'), 'so‘m')        # noma’lum til → o‘zbekcha
        self.assertEqual(t('uz', 'missing_key_zzz'), 'missing_key_zzz')


if __name__ == '__main__':
    unittest.main()
