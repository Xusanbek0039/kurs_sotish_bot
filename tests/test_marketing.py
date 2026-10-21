import unittest
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

from main import Shop
from marketing import MARKETING_FIELDS, course_card, course_details, share_link, validate_marketing


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


class MarketingTests(unittest.TestCase):
    def setUp(self):
        self.course = {'id': 1, 'name': 'Python & <Django>', 'price': 150000,
                       'description': 'Amaliy <darslar>'}

    def test_existing_course_without_marketing_fields(self):
        card = course_card(self.course)
        self.assertIn('150 000', card)
        self.assertIn('Python &amp; &lt;Django&gt;', card)
        self.assertNotIn('Kimlar uchun', card)

    def test_marketing_text_is_escaped(self):
        self.course['outcomes'] = '<a href="https://example.test">Havola</a>'
        self.assertIn('&lt;a', course_card(self.course))
        self.assertNotIn('<a href=', course_card(self.course))

    def test_longest_valid_course_fits_telegram_limit(self):
        self.course.update(name='x' * 120, description='x' * 2000)
        for field, (_, size) in MARKETING_FIELDS.items():
            if field != 'preview_url':
                self.course[field] = 'x' * size
        for text in (course_card(self.course), course_details(self.course)):
            parser = PlainText()
            parser.feed(text)
            self.assertLessEqual(len(''.join(parser.parts)), 4096)

    def test_private_invites_and_unsafe_preview_are_rejected(self):
        for url in ('http://example.test', 'javascript:alert(1)', 'https://t.me/+secret',
                    'https://t.me/joinchat/secret', 'https://user:pass@example.test'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_marketing('preview_url', url)
        self.assertEqual(validate_marketing('preview_url', 'https://example.test/demo'), 'https://example.test/demo')

    def test_clear_marketing_field(self):
        self.assertEqual(validate_marketing('outcomes', '-'), '')

    def test_share_url_is_encoded(self):
        query = parse_qs(urlparse(share_link('test_bot', self.course)).query)
        self.assertEqual(query['url'], ['https://t.me/test_bot?start=course_1'])
        self.assertIn('Python & <Django>', query['text'][0])


class CustomerFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        config = SimpleNamespace(admins=frozenset({1}), support_username='', emoji='',
                                 brand='Maktab', welcome='Kursni tanlang.')
        self.db = SimpleNamespace(one=AsyncMock(return_value=None), rows=AsyncMock(return_value=[]),
                                  order=AsyncMock(), track=AsyncMock(), course_change=AsyncMock())
        self.api = SimpleNamespace(create=AsyncMock())
        self.shop = Shop(config, self.db, self.api, SimpleNamespace())
        self.shop.username = 'test_bot'
        self.message = SimpleNamespace(chat=SimpleNamespace(type='private'), answer=AsyncMock())

    def call(self, data, uid=42):
        return SimpleNamespace(data=data, from_user=SimpleNamespace(id=uid),
                               message=self.message, answer=AsyncMock())

    async def test_order_lookup_is_scoped_to_customer(self):
        await self.shop.customer_callback(self.call('u:order:99'))
        self.db.one.assert_awaited_once_with('orders', {'id': 99, 'user_id': 42})
        self.api.create.assert_not_awaited()

    async def test_paid_course_never_creates_second_bill(self):
        self.db.order.return_value = {'id': 7, 'course_id': 1, 'user_id': 42, 'status': 'paid'}
        self.shop.deliver = AsyncMock()
        await self.shop.customer_callback(self.call('u:buy:1'))
        self.shop.deliver.assert_awaited_once()
        self.api.create.assert_not_awaited()

    async def test_admin_buttons_hidden_from_customer(self):
        await self.shop.catalog(self.message, 42)
        markup = self.message.answer.call_args.kwargs['reply_markup']
        self.assertFalse(any((b.callback_data or '').startswith('a:') for row in markup.inline_keyboard for b in row))

    async def test_non_admin_cannot_edit_marketing(self):
        state = SimpleNamespace(get_data=AsyncMock(), clear=AsyncMock())
        message = SimpleNamespace(chat=SimpleNamespace(type='private'), from_user=SimpleNamespace(id=42))
        await self.shop.marketing_form(message, state)
        self.db.course_change.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
