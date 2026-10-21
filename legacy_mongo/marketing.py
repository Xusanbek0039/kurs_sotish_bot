from html import escape
from urllib.parse import urlencode, urlparse


MARKETING_FIELDS = {
    'audience': ('Kimlar uchun', 350),
    'outcomes': ('Nimalarni o‘rganasiz', 700),
    'program': ('Kurs dasturi', 1500),
    'instructor': ('Ustoz haqida', 180),
    'duration': ('Davomiylik va format', 180),
    'preview_url': ('Bepul tanishuv havolasi', 500),
}


def money(value):
    return f'{value:,}'.replace(',', ' ') + ' so‘m'


def course_link(username, cid):
    return f'https://t.me/{username}?start=course_{cid}'


def share_link(username, course):
    return 'https://t.me/share/url?' + urlencode({
        'url': course_link(username, course['id']),
        'text': course['name'] + ' — ' + money(course['price'])})


def validate_marketing(field, text):
    if field not in MARKETING_FIELDS:
        raise ValueError('Maydon topilmadi.')
    if text == '-':
        return ''
    title, maximum = MARKETING_FIELDS[field]
    if not 1 <= len(text) <= maximum:
        raise ValueError(f'{title}: 1–{maximum} belgi kiriting yoki tozalash uchun - yuboring.')
    if field == 'preview_url':
        parsed = urlparse(text)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or any(c.isspace() for c in text)):
            raise ValueError('Tanishuv uchun ochiq HTTPS havola kiriting. Yopiq kurs taklif havolasini yozmang.')
        if parsed.hostname.lower() in ('t.me', 'www.t.me', 'telegram.me') and (
                parsed.path.startswith('/+') or parsed.path.startswith('/joinchat/')):
            raise ValueError('Yopiq kanal taklifini tanishuv sifatida qo‘shmang. Ochiq demo dars havolasini kiriting.')
    return text


def course_card(course):
    description = course['description']
    if len(description) > 1200:
        description = description[:1199] + '…'
    parts = [f'📚 <b>{escape(course["name"])}</b>', escape(description)]
    for field in ('audience', 'outcomes', 'duration', 'instructor'):
        if course.get(field):
            parts.append(f'<b>{MARKETING_FIELDS[field][0]}</b>\n{escape(course[field])}')
    parts.extend([f'💳 <b>Narxi: {money(course["price"])}</b>',
        '🔐 To‘lov tasdiqlangach, shu kursning yopiq kanaliga kirish havolasini olasiz.',
        'Boshlashga tayyormisiz? Quyidagi tugma orqali davom eting.'])
    return '\n\n'.join(parts)


def course_details(course):
    parts = [f'📖 <b>{escape(course["name"])}</b>', escape(course['description'])]
    if course.get('program'):
        parts.append('<b>Kurs dasturi</b>\n' + escape(course['program']))
    parts.append('Savolingiz qolgan bo‘lsa, to‘lovdan oldin yordam bo‘limiga yozing.')
    return '\n\n'.join(parts)


ORDER_LABELS = {'creating': 'Hisob tayyorlanmoqda', 'pending': 'To‘lov kutilmoqda',
                'paid': 'To‘lov tasdiqlangan', 'review': 'Admin tekshiruvi kerak'}
