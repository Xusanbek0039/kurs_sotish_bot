"""Barcha matnlar shu yerda: o‘zbekcha (lotin) va ruscha.

Foydalanish: t(lang, 'key', **qiymatlar). Kalitlar ikkala tilda bir xil bo‘lishi testda tekshiriladi.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

LANGUAGE_BUTTON = '🌐 Til / Язык'
LANGUAGE_PROMPT = '🌐 Tilni tanlang\n🌐 Выберите язык'

TEXTS: dict[str, dict[str, str]] = {
    'uz': {
        # --- umumiy
        'currency': 'so‘m',
        'lang_name': '🇺🇿 O‘zbekcha',
        'language_saved': '✅ Til saqlandi: O‘zbekcha',
        'welcome': '{emoji} <b>Assalomu alaykum, {name}!</b>\n\n'
                   'Bu yerda kurslarni tanlab, onlayn to‘lov qilishingiz va yopiq kanalga darhol kirishingiz mumkin.\n\n'
                   '👇 Boshlash uchun kurslar bo‘limini oching.',
        'main_menu': '🏠 <b>Bosh menyu</b>\n\nKerakli bo‘limni tanlang 👇',
        'btn_courses': '📚 Kurslar',
        'btn_my_courses': '🎓 Kurslarim',
        'btn_help': '💬 Yordam',
        'btn_admin': '⚙️ Admin panel',
        'btn_back': '⬅️ Orqaga',
        'btn_main_menu': '🏠 Bosh menyu',
        'btn_cancel': '✖️ Bekor qilish',
        'btn_skip': '⏭ O‘tkazib yuborish',
        'btn_prev': '◀️',
        'btn_next': '▶️',
        'help': '💬 <b>Yordam</b>\n\n'
                '1️⃣ «📚 Kurslar» bo‘limida kursni tanlang.\n'
                '2️⃣ «💳 To‘lov qilish» tugmasini bosing va Tezcheck sahifasida to‘lang.\n'
                '3️⃣ To‘lov tasdiqlangach, bot sizga yopiq kanalga shaxsiy havola yuboradi.\n\n'
                '❓ <b>To‘ladim, havola kelmadi?</b>\n'
                '«🎓 Kurslarim» → buyurtma → «🔄 To‘lovni tekshirish». Qayta pul to‘lamang.\n\n'
                '🆔 Sizning ID: <code>{user_id}</code>\n{support}',
        'support_contact': '👤 Qo‘llab-quvvatlash: @{username}',
        'support_contact_admin': '👤 Savollar bo‘lsa, administratorga murojaat qiling.',
        'btn_support': '👤 Yordamchiga yozish',
        'blocked': '⛔ Botdan foydalanish cheklangan. Savollar bo‘lsa, administratorga murojaat qiling.',
        'error_generic': '⚠️ Kutilmagan xatolik yuz berdi. Birozdan so‘ng qayta urinib ko‘ring.',
        'outdated_button': 'Bu tugma eskirgan. /start ni bosing.',
        'no_permission': '⛔ Ruxsat yo‘q.',
        'unknown_message': 'Kerakli bo‘limni menyudan tanlang 👇',
        'page': 'Sahifa {page}/{pages}',

        # --- katalog
        'catalog_title': '{emoji} <b>Kurslar</b>\n\nO‘zingizga mos kursni tanlang 👇',
        'catalog_empty': '📭 Hozircha sotuvda kurslar yo‘q. Tez orada qo‘shiladi!',
        'course_button': '{name} · {price}',
        'course_card': '{emoji} <b>{name}</b>\n\n{description}\n\n💰 Narxi: <b>{price}</b>\n'
                       '🔐 To‘lov tasdiqlangach, yopiq kanalga shaxsiy havola olasiz.',
        'course_owned': '\n\n✅ <b>Bu kurs sizda bor.</b>',
        'course_not_found': '🔍 Kurs topilmadi. Havola eskirgan bo‘lishi mumkin.',
        'course_unavailable': '⛔ Bu kurs hozir sotuvda emas.',
        'btn_pay': '💳 To‘lov qilish',
        'btn_get_link': '🔗 Kanal havolasini olish',
        'btn_all_courses': '📚 Barcha kurslar',

        # --- to‘lov
        'payment_preparing': '⏳ To‘lov hisobi tayyorlanmoqda…',
        'payment_ready': '{emoji} <b>To‘lov</b>\n\n📚 {course}\n🧾 Buyurtma: <b>#{order_id}</b>\n'
                         '💰 Summa: <b>{amount}</b>\n\n'
                         '1️⃣ «💳 To‘lovni amalga oshirish» tugmasini bosing.\n'
                         '2️⃣ Ochilgan Tezcheck sahifasida to‘lovni yakunlang.\n'
                         '3️⃣ Bot to‘lovni avtomatik tasdiqlaydi. Xohlasangiz, «🔄 To‘lovni tekshirish»ni bosing.\n\n'
                         '🔒 Karta ma’lumotlaringiz botga ko‘rinmaydi va saqlanmaydi.',
        'btn_pay_now': '💳 To‘lovni amalga oshirish',
        'btn_check_payment': '🔄 To‘lovni tekshirish',
        'payment_error': '⚠️ To‘lov hisobini hozir yaratib bo‘lmadi. Birozdan so‘ng qayta urinib ko‘ring.\n\n{support}',
        'status_check_pending': '⏳ To‘lov hali tasdiqlanmadi.\n\nTo‘lovni yakunlagan bo‘lsangiz, bir-ikki daqiqadan so‘ng qayta tekshiring. Qayta pul to‘lamang.',
        'status_check_paid': '✅ To‘lov tasdiqlandi! Kanal havolasi alohida xabarda yuboriladi.',
        'status_check_expired': '⌛ To‘lov havolasining muddati tugadi. Kurs sahifasidan yangi to‘lov yarating.',
        'status_check_test': '⚠️ Bu test to‘lovi — haqiqiy pul emas. Kursga kirish berilmaydi.',
        'status_check_review': '🕵️ To‘lov ma’lumotlari tekshiruvga yuborildi. Administrator siz bilan bog‘lanadi.\n\n{support}',
        'status_check_error': '⚠️ To‘lov tizimi bilan aloqa vaqtincha yo‘q. Birozdan so‘ng qayta tekshiring. Qayta pul to‘lamang.',
        'status_check_no_bill': '⚠️ Bu buyurtma uchun to‘lov hisobi hali yaratilmagan. Kurs sahifasidan «💳 To‘lov qilish»ni bosing.',
        'check_too_often': '⏳ Biroz kuting va qayta tekshiring.',
        'payment_expired_notice': '⌛ #{order_id} buyurtma bo‘yicha to‘lov havolasining muddati tugadi. '
                                  'Kerak bo‘lsa, kurs sahifasidan yangi to‘lov yarating.',
        'already_purchased': '✅ Siz bu kursni allaqachon sotib olgansiz. Havolani «🎓 Kurslarim» bo‘limidan olishingiz mumkin.',
        'access_granted': '{emoji} <b>To‘lov tasdiqlandi!</b>\n\n📚 {course}\n🧾 Buyurtma: #{order_id}\n\n'
                          'Quyidagi tugma orqali yopiq kanalga qo‘shiling. Havola faqat siz uchun va bir martalik.\n'
                          '⏰ Amal qilish muddati: {expires} (Toshkent vaqti)',
        'btn_join_channel': '🔐 Kanalga qo‘shilish',
        'invite_failed_user': '✅ To‘lovingiz tasdiqlangan (buyurtma #{order_id}).\n\n'
                              'Kanal havolasini yaratishda texnik muammo bo‘ldi. Administratorlar xabardor qilindi '
                              'va havolani tez orada yuborishadi. Qayta to‘lov qilmang.\n\n{support}',
        'link_member': '✅ Siz allaqachon kurs kanalidasiz. Telegram’da kanalni qidirib oching.',
        'link_limit': '⚠️ Havola so‘rash limiti tugadi. Yangi havola uchun administratorga yozing.\n\n{support}',
        'link_sent': '📨 Havola yuborildi.',
        'link_busy': '⏳ Havola tayyorlanmoqda, biroz kuting.',
        'link_failed': '⚠️ Havolani hozir yuborib bo‘lmadi. Administratorlar xabardor qilindi.\n\n{support}',

        # --- kurslarim
        'my_title': '🎓 <b>Kurslarim</b>',
        'my_empty': '🎓 <b>Kurslarim</b>\n\nHali kurs sotib olmagansiz. «📚 Kurslar» bo‘limidan boshlang.',
        'my_purchases': '<b>Sotib olingan:</b>',
        'my_orders': '<b>Kutilayotgan to‘lovlar:</b>',
        'pstatus_sent': 'havola yuborilgan',
        'pstatus_waiting': 'havola tayyorlanmoqda',
        'pstatus_failed': 'havola hali yuborilmagan',

        # --- holatlar
        'status_new': '🆕 Yangi',
        'status_pending': '⏳ To‘lov kutilmoqda',
        'status_paid': '✅ To‘langan',
        'status_expired': '⌛ Muddati o‘tgan',
        'status_cancelled': '✖️ Bekor qilingan',
        'status_review': '🕵️ Tekshiruvda',
        'status_all': '📋 Barchasi',

        # --- admin panel
        'admin_title': '⚙️ <b>Admin panel</b>\n\n📚 Kurslar: <b>{courses}</b> (faol: {active})\n'
                       '⏳ Kutilayotgan to‘lovlar: <b>{pending}</b>\n📨 Yuborilmagan havolalar: <b>{undelivered}</b>',
        'btn_add_course': '➕ Kurs qo‘shish',
        'btn_admin_courses': '📚 Kurslar',
        'btn_admin_orders': '🧾 Buyurtmalar',
        'btn_admin_search': '🔎 Qidirish',
        'btn_admin_users': '👥 Foydalanuvchilar',
        'btn_admin_stats': '📊 Statistika',
        'btn_admin_undelivered': '📨 Yuborilmagan havolalar',
        'btn_admin_logs': '📜 Amallar tarixi',
        'btn_admin_panel': '⚙️ Admin panel',

        # kurs qo‘shish
        'add_step': '➕ <b>Yangi kurs</b> · {step}/6\n\n',
        'add_name_uz': '🇺🇿 Kurs nomini <b>o‘zbekcha</b> yuboring (2–120 belgi).',
        'add_desc_uz': '🇺🇿 Kurs tavsifini <b>o‘zbekcha</b> yuboring (10–3000 belgi).',
        'add_name_ru': '🇷🇺 Kurs nomini <b>ruscha</b> yuboring (2–120 belgi).',
        'add_desc_ru': '🇷🇺 Kurs tavsifini <b>ruscha</b> yuboring (10–3000 belgi).',
        'add_price': '💰 Narxni so‘mda, butun son bilan yuboring. Masalan: <code>150000</code>',
        'add_channel': '📡 Yopiq kanalning <code>chat_id</code> qiymatini yuboring (odatda <code>-100</code> bilan boshlanadi).\n\n'
                       'Avval botni kanalga admin qiling va «Foydalanuvchilarni taklif qilish» huquqini bering.',
        'add_confirm': '📋 <b>Tekshirib chiqing</b>\n\n🇺🇿 <b>{name_uz}</b>\n{description_uz}\n\n🇷🇺 <b>{name_ru}</b>\n{description_ru}\n\n'
                       '💰 Narx: <b>{price}</b>\n📡 Kanal: <code>{channel}</code>\n\nSaqlaymizmi?',
        'btn_save': '✅ Saqlash',
        'course_saved': '✅ Kurs saqlandi (#{course_id}).\n\n💳 Xarid havolasi (bosganda to‘g‘ri to‘lovga o‘tadi):\n'
                        '<code>{buy_link}</code>\n\n📚 Kurs sahifasi:\n<code>{link}</code>',
        'input_cancelled': '✖️ Bekor qilindi.',
        'not_set': '— kiritilmagan —',
        'invalid_length': '⚠️ Matn {min}–{max} belgi bo‘lishi kerak. Qayta yuboring.',
        'invalid_price': '⚠️ Narx musbat butun son bo‘lishi kerak (1000 – 1 000 000 000). Masalan: 150000',
        'invalid_channel': '⚠️ chat_id butun son bo‘lishi kerak, masalan: <code>-1001234567890</code>',
        'channel_problem': '⚠️ Kanal tekshiruvi: {reason}\n\nTuzatib qayta yuboring yoki baribir davom eting.',
        'btn_continue_anyway': '➡️ Baribir davom etish',
        'chan_not_found': 'kanal topilmadi yoki bot unga qo‘shilmagan',
        'chan_not_admin': 'bot kanalda admin emas',
        'chan_no_invite': 'botda «Foydalanuvchilarni taklif qilish» huquqi yo‘q',
        'chan_ok': '✅ Kanal to‘g‘ri sozlangan: bot admin va havola yarata oladi.',
        'need_one_name': '⚠️ Kamida bitta tilda nom kiritilishi kerak.',
        'session_expired': '⌛ Kiritish sessiyasi tugagan. Qaytadan boshlang.',

        # kurs boshqaruvi
        'admin_courses_title': '📚 <b>Kurslar</b> ({total})\n\n🟢 faol · ⚪️ nofaol · ⚠️ tarjima yetishmaydi',
        'admin_courses_empty': '📭 Hali kurslar yo‘q. «➕ Kurs qo‘shish» tugmasini bosing.',
        'admin_course_card': '{status} <b>#{id} · {name}</b>\n\n🇺🇿 <b>{name_uz}</b>\n{description_uz}\n\n'
                             '🇷🇺 <b>{name_ru}</b>\n{description_ru}\n\n💰 Narx: <b>{price}</b>\n'
                             '📡 Kanal: <code>{channel}</code>\n🧾 Buyurtmalar: {orders} · ✅ to‘langan: {paid}\n'
                             '🕒 Yaratilgan: {created}\n\n💳 Xarid havolasi: <code>{buy_link}</code>\n'
                             '📚 Kurs sahifasi: <code>{link}</code>',
        'course_active': '🟢 Faol',
        'course_inactive': '⚪️ Nofaol',
        'missing_translation': '\n\n⚠️ <b>Tarjima yetishmaydi:</b> {fields}. Xaridorga boshqa tildagi matn ko‘rsatiladi.',
        'field_name_uz': 'Nom 🇺🇿',
        'field_name_ru': 'Nom 🇷🇺',
        'field_description_uz': 'Tavsif 🇺🇿',
        'field_description_ru': 'Tavsif 🇷🇺',
        'field_price_uzs': 'Narx',
        'field_channel_chat_id': 'Kanal chat_id',
        'btn_edit_field': '✏️ {field}',
        'btn_deactivate': '⏸ Nofaol qilish',
        'btn_activate': '▶️ Faollashtirish',
        'btn_delete': '🗑 O‘chirish',
        'btn_course_orders': '🧾 Buyurtmalar',
        'btn_check_channel': '📡 Kanalni tekshirish',
        'btn_preview': '👁 Xaridor ko‘rinishi',
        'btn_course_links': '🔗 Xarid havolasi',
        'btn_share_link': '📤 Havolani ulashish',
        'course_links': '🔗 <b>{name}</b> — havolalar\n\n'
                        '💳 <b>Xarid havolasi</b> — foydalanuvchi «Start»ni bosishi bilan shu kursni sotib olishga o‘tadi:\n'
                        '<code>{buy_link}</code>\n\n'
                        '📚 <b>Kurs sahifasi havolasi</b> — avval kurs tavsifi ko‘rsatiladi:\n<code>{link}</code>\n\n'
                        'Havolani bosib nusxalang va kanal, reklama yoki ijtimoiy tarmoqlarga joylang.',
        'edit_prompt': '✏️ <b>{field}</b> uchun yangi qiymat yuboring.\n\nHozirgi qiymat:\n{current}',
        'edit_saved': '✅ Saqlandi.',
        'course_toggled_on': '▶️ Kurs faollashtirildi.',
        'course_toggled_off': '⏸ Kurs nofaol qilindi. Yangi to‘lovlar qabul qilinmaydi.',
        'confirm_delete': '🗑 <b>#{id} kursni o‘chirasizmi?</b>\n\nKurs ro‘yxatdan yo‘qoladi va sotuv to‘xtaydi. '
                          'Eski buyurtma va xaridlar tarixi saqlanadi.',
        'btn_confirm_delete': '🗑 Ha, o‘chirish',
        'course_deleted': '🗑 Kurs o‘chirildi. Buyurtmalar tarixi saqlandi.',

        # buyurtmalar
        'admin_orders_title': '🧾 <b>Buyurtmalar</b> · {filter} ({total})',
        'admin_orders_empty': '📭 Buyurtmalar yo‘q.',
        'admin_order_line': '#{id} · {status} · {amount} · 👤 {user_id}',
        'admin_order_card': '🧾 <b>Buyurtma #{id}</b>\n\n👤 Foydalanuvchi: <code>{user_id}</code> {user}\n📚 Kurs: #{course_id} {course}\n'
                            '💰 Summa: <b>{amount}</b>\n📌 Holat: {status}\n🔖 external_reference: <code>{reference}</code>\n'
                            '🧾 Tezcheck bill: <code>{bill}</code>\n💳 To‘lov ID: <code>{payment}</code>\n'
                            '🕒 Yaratilgan: {created}\n✅ To‘langan: {paid_at}\n🔄 Oxirgi tekshiruv: {checked}\n'
                            '⚠️ Oxirgi xato: {error}\n\n📨 Kanal havolasi: {delivery}',
        'delivery_none': '—',
        'delivery_sent': '✅ yuborilgan ({at})',
        'delivery_pending': '⏳ {status}',
        'btn_order_check': '🔄 Tezcheck’da tekshirish',
        'btn_resend_link': '📨 Havolani qayta yuborish',
        'btn_open_user': '👤 Foydalanuvchi',
        'order_check_result': '🔄 Tekshiruv natijasi: <b>{result}</b>',
        'resend_result': '📨 Natija: <b>{result}</b>',
        'undelivered_title': '📨 <b>Havola yuborilmagan xaridlar</b> ({total})',
        'undelivered_empty': '✅ Barcha xaridorlarga havola yuborilgan.',
        'undelivered_line': '#{order_id} · 👤 {user_id} · {course} · {status}',

        # qidiruv
        'search_prompt': '🔎 Foydalanuvchi ID, buyurtma ID, @username yoki ism yuboring.',
        'search_empty': '🔍 Hech narsa topilmadi.',
        'search_results': '🔎 <b>Natijalar</b>',
        'search_order_found': '🧾 Buyurtma #{id}',
        'search_user_found': '👤 {label} · {id}',

        # foydalanuvchilar
        'users_title': '👥 <b>Foydalanuvchilar</b>\n\nJami: <b>{total}</b> · bloklangan: {blocked}\n'
                       'Faol: bugun <b>{active_today}</b> · 7 kun <b>{active_7}</b> · 30 kun <b>{active_30}</b>\n'
                       'Yangi: bugun {new_today} · 7 kun {new_7}\nXaridorlar: <b>{buyers}</b>\n\n'
                       'Filtr: {filter} · Saralash: {sort} · {page_info}',
        'ufilter_all': 'Barchasi',
        'ufilter_buyers': '💳 Xaridorlar',
        'ufilter_non_buyers': '🙅 Xarid qilmaganlar',
        'ufilter_pending': '⏳ To‘lovi kutilayotgan',
        'ufilter_inactive': '💤 30 kun nofaol',
        'ufilter_blocked': '⛔ Bloklangan',
        'usort_new': '🆕 Yangilar',
        'usort_active': '🕒 Oxirgi faollik',
        'btn_export_csv': '📥 CSV eksport',
        'btn_users_search': '🔎 Qidirish',
        'user_card': '👤 <b>{label}</b>\n\n🆔 ID: <code>{id}</code>\n🌐 Til: {language}\n📅 Ro‘yxatdan o‘tgan: {created}\n'
                     '🕒 Oxirgi faollik: {last_active}\n{blocked}\n\n👁 <b>Ko‘rgan kurslari:</b>\n{views}\n\n'
                     '🎓 <b>Xaridlar:</b>\n{purchases}\n\n🧾 <b>Buyurtmalar:</b>\n{orders}',
        'user_blocked_mark': '⛔ <b>Bloklangan</b>',
        'user_active_mark': '✅ Faol',
        'none': '—',
        'btn_block': '⛔ Bloklash',
        'btn_unblock': '✅ Blokdan chiqarish',
        'confirm_block': '⛔ <b>{label}</b> ({id}) bloklansinmi?\n\nU botdan foydalana olmaydi. Xaridlar tarixi saqlanadi.',
        'btn_confirm_block': '⛔ Ha, bloklash',
        'user_blocked': '⛔ Foydalanuvchi bloklandi.',
        'user_unblocked': '✅ Foydalanuvchi blokdan chiqarildi.',
        'cannot_block_admin': '⚠️ Adminni bloklab bo‘lmaydi.',
        'user_not_found': '🔍 Foydalanuvchi topilmadi.',
        'export_caption': '📥 Foydalanuvchilar ro‘yxati ({count} ta).',

        # statistika va tarix
        'stats': '📊 <b>Statistika</b>\n\n📚 Kurslar: <b>{courses}</b> (faol: {active_courses})\n'
                 '🧾 Jami buyurtmalar: <b>{orders}</b>\n✅ Muvaffaqiyatli to‘lovlar: <b>{paid}</b>\n'
                 '⏳ Kutilayotgan to‘lovlar: <b>{pending}</b>\n💰 Tushum: <b>{revenue}</b>\n'
                 '👥 Foydalanuvchilar: <b>{users}</b>\n📨 Yuborilmagan havolalar: <b>{undelivered}</b>',
        'logs_title': '📜 <b>Admin amallari tarixi</b>',
        'logs_empty': '📭 Hali amallar yo‘q.',

        # admin ogohlantirishlari
        'alert_test_payment': '⚠️ Buyurtma #{order_id} (user {user_id}) TEST provayderi orqali to‘langan. Kurs berilmadi.',
        'alert_mismatch': '🚨 Buyurtma #{order_id} (user {user_id}): Tezcheck ma’lumotlari buyurtmaga mos emas. '
                          'Holat «Tekshiruvda». Kabinetda solishtiring.',
        'alert_duplicate_payment': '🚨 Buyurtma #{order_id} (user {user_id}): bu kurs allaqachon boshqa buyurtma orqali sotib olingan. '
                                   'Ikkinchi to‘lovni qaytarishni ko‘rib chiqing.',
        'alert_invite_failed': '🚨 Buyurtma #{order_id} (user {user_id}): kurs #{course_id} kanaliga havola yaratilmadi. '
                               'Bot kanalda admin va taklif huquqiga egaligini, chat_id to‘g‘riligini tekshiring, '
                               'so‘ng «📨 Yuborilmagan havolalar» bo‘limidan qayta yuboring.',
        'alert_refund': '⚠️ Buyurtma #{order_id} (user {user_id}): {event}. Kanal a’zoligini qo‘lda ko‘rib chiqing.',

        # tekshiruv natijalari (admin)
        'result_paid': '✅ to‘langan',
        'result_pending': '⏳ hali to‘lanmagan',
        'result_expired': '⌛ muddati o‘tgan',
        'result_test': '⚠️ test to‘lovi',
        'result_review': '🕵️ mos kelmadi — tekshiruvda',
        'result_no_bill': '— hisob yo‘q',
        'result_error': '⚠️ API xatosi',
        'result_not_found': '— topilmadi',
        'result_sent': '✅ yuborildi',
        'result_busy': '⏳ jarayonda',
        'result_not_paid': '⛔ to‘lov tasdiqlanmagan',
        'result_member': '✅ allaqachon kanalda',
        'result_limit': '⚠️ limit',
        'result_invite_failed': '🚨 havola yaratilmadi (kanal huquqlari?)',
        'result_send_failed': '⚠️ foydalanuvchiga yuborilmadi (botni bloklagan?)',
    },

    'ru': {
        'currency': 'сум',
        'lang_name': '🇷🇺 Русский',
        'language_saved': '✅ Язык сохранён: Русский',
        'welcome': '{emoji} <b>Здравствуйте, {name}!</b>\n\n'
                   'Здесь можно выбрать курс, оплатить его онлайн и сразу получить доступ в закрытый канал.\n\n'
                   '👇 Чтобы начать, откройте раздел курсов.',
        'main_menu': '🏠 <b>Главное меню</b>\n\nВыберите нужный раздел 👇',
        'btn_courses': '📚 Курсы',
        'btn_my_courses': '🎓 Мои курсы',
        'btn_help': '💬 Помощь',
        'btn_admin': '⚙️ Админ-панель',
        'btn_back': '⬅️ Назад',
        'btn_main_menu': '🏠 Главное меню',
        'btn_cancel': '✖️ Отмена',
        'btn_skip': '⏭ Пропустить',
        'btn_prev': '◀️',
        'btn_next': '▶️',
        'help': '💬 <b>Помощь</b>\n\n'
                '1️⃣ В разделе «📚 Курсы» выберите курс.\n'
                '2️⃣ Нажмите «💳 Оплатить» и завершите оплату на странице Tezcheck.\n'
                '3️⃣ После подтверждения оплаты бот пришлёт личную ссылку в закрытый канал.\n\n'
                '❓ <b>Оплатил, а ссылки нет?</b>\n'
                '«🎓 Мои курсы» → заказ → «🔄 Проверить оплату». Не платите повторно.\n\n'
                '🆔 Ваш ID: <code>{user_id}</code>\n{support}',
        'support_contact': '👤 Поддержка: @{username}',
        'support_contact_admin': '👤 Если есть вопросы, обратитесь к администратору.',
        'btn_support': '👤 Написать в поддержку',
        'blocked': '⛔ Доступ к боту ограничен. Если есть вопросы, обратитесь к администратору.',
        'error_generic': '⚠️ Произошла непредвиденная ошибка. Попробуйте ещё раз чуть позже.',
        'outdated_button': 'Кнопка устарела. Нажмите /start.',
        'no_permission': '⛔ Нет доступа.',
        'unknown_message': 'Выберите нужный раздел в меню 👇',
        'page': 'Страница {page}/{pages}',

        'catalog_title': '{emoji} <b>Курсы</b>\n\nВыберите подходящий курс 👇',
        'catalog_empty': '📭 Сейчас курсов в продаже нет. Скоро появятся!',
        'course_button': '{name} · {price}',
        'course_card': '{emoji} <b>{name}</b>\n\n{description}\n\n💰 Стоимость: <b>{price}</b>\n'
                       '🔐 После подтверждения оплаты вы получите личную ссылку в закрытый канал.',
        'course_owned': '\n\n✅ <b>Этот курс уже у вас.</b>',
        'course_not_found': '🔍 Курс не найден. Возможно, ссылка устарела.',
        'course_unavailable': '⛔ Этот курс сейчас не продаётся.',
        'btn_pay': '💳 Оплатить',
        'btn_get_link': '🔗 Получить ссылку на канал',
        'btn_all_courses': '📚 Все курсы',

        'payment_preparing': '⏳ Готовим счёт на оплату…',
        'payment_ready': '{emoji} <b>Оплата</b>\n\n📚 {course}\n🧾 Заказ: <b>#{order_id}</b>\n'
                         '💰 Сумма: <b>{amount}</b>\n\n'
                         '1️⃣ Нажмите «💳 Перейти к оплате».\n'
                         '2️⃣ Завершите оплату на открывшейся странице Tezcheck.\n'
                         '3️⃣ Бот подтвердит оплату автоматически. При желании нажмите «🔄 Проверить оплату».\n\n'
                         '🔒 Данные вашей карты не видны боту и не сохраняются.',
        'btn_pay_now': '💳 Перейти к оплате',
        'btn_check_payment': '🔄 Проверить оплату',
        'payment_error': '⚠️ Не удалось создать счёт. Попробуйте ещё раз чуть позже.\n\n{support}',
        'status_check_pending': '⏳ Оплата пока не подтверждена.\n\nЕсли вы уже оплатили, проверьте ещё раз через минуту-две. Не платите повторно.',
        'status_check_paid': '✅ Оплата подтверждена! Ссылка на канал придёт отдельным сообщением.',
        'status_check_expired': '⌛ Срок действия ссылки на оплату истёк. Создайте новую оплату на странице курса.',
        'status_check_test': '⚠️ Это тестовый платёж — не реальные деньги. Доступ к курсу не выдаётся.',
        'status_check_review': '🕵️ Данные оплаты отправлены на проверку. Администратор свяжется с вами.\n\n{support}',
        'status_check_error': '⚠️ Нет связи с платёжной системой. Проверьте чуть позже. Не платите повторно.',
        'status_check_no_bill': '⚠️ Счёт для этого заказа ещё не создан. Нажмите «💳 Оплатить» на странице курса.',
        'check_too_often': '⏳ Подождите немного и проверьте снова.',
        'payment_expired_notice': '⌛ Срок действия ссылки на оплату заказа #{order_id} истёк. '
                                  'При необходимости создайте новую оплату на странице курса.',
        'already_purchased': '✅ Вы уже купили этот курс. Ссылку можно получить в разделе «🎓 Мои курсы».',
        'access_granted': '{emoji} <b>Оплата подтверждена!</b>\n\n📚 {course}\n🧾 Заказ: #{order_id}\n\n'
                          'Вступите в закрытый канал по кнопке ниже. Ссылка личная и одноразовая.\n'
                          '⏰ Действует до: {expires} (время Ташкента)',
        'btn_join_channel': '🔐 Вступить в канал',
        'invite_failed_user': '✅ Ваша оплата подтверждена (заказ #{order_id}).\n\n'
                              'При создании ссылки на канал возникла техническая проблема. Администраторы уже уведомлены '
                              'и скоро пришлют ссылку. Не оплачивайте повторно.\n\n{support}',
        'link_member': '✅ Вы уже состоите в канале курса. Найдите его в списке чатов Telegram.',
        'link_limit': '⚠️ Лимит запросов ссылки исчерпан. Напишите администратору за новой ссылкой.\n\n{support}',
        'link_sent': '📨 Ссылка отправлена.',
        'link_busy': '⏳ Ссылка готовится, подождите немного.',
        'link_failed': '⚠️ Сейчас не удалось отправить ссылку. Администраторы уведомлены.\n\n{support}',

        'my_title': '🎓 <b>Мои курсы</b>',
        'my_empty': '🎓 <b>Мои курсы</b>\n\nУ вас пока нет купленных курсов. Начните с раздела «📚 Курсы».',
        'my_purchases': '<b>Купленные:</b>',
        'my_orders': '<b>Ожидают оплаты:</b>',
        'pstatus_sent': 'ссылка отправлена',
        'pstatus_waiting': 'ссылка готовится',
        'pstatus_failed': 'ссылка ещё не отправлена',

        'status_new': '🆕 Новый',
        'status_pending': '⏳ Ожидает оплаты',
        'status_paid': '✅ Оплачен',
        'status_expired': '⌛ Истёк',
        'status_cancelled': '✖️ Отменён',
        'status_review': '🕵️ На проверке',
        'status_all': '📋 Все',

        'admin_title': '⚙️ <b>Админ-панель</b>\n\n📚 Курсы: <b>{courses}</b> (активных: {active})\n'
                       '⏳ Ожидают оплаты: <b>{pending}</b>\n📨 Неотправленные ссылки: <b>{undelivered}</b>',
        'btn_add_course': '➕ Добавить курс',
        'btn_admin_courses': '📚 Курсы',
        'btn_admin_orders': '🧾 Заказы',
        'btn_admin_search': '🔎 Поиск',
        'btn_admin_users': '👥 Пользователи',
        'btn_admin_stats': '📊 Статистика',
        'btn_admin_undelivered': '📨 Неотправленные ссылки',
        'btn_admin_logs': '📜 История действий',
        'btn_admin_panel': '⚙️ Админ-панель',

        'add_step': '➕ <b>Новый курс</b> · {step}/6\n\n',
        'add_name_uz': '🇺🇿 Отправьте название курса <b>на узбекском</b> (2–120 символов).',
        'add_desc_uz': '🇺🇿 Отправьте описание курса <b>на узбекском</b> (10–3000 символов).',
        'add_name_ru': '🇷🇺 Отправьте название курса <b>на русском</b> (2–120 символов).',
        'add_desc_ru': '🇷🇺 Отправьте описание курса <b>на русском</b> (10–3000 символов).',
        'add_price': '💰 Отправьте цену в сумах целым числом. Например: <code>150000</code>',
        'add_channel': '📡 Отправьте <code>chat_id</code> закрытого канала (обычно начинается с <code>-100</code>).\n\n'
                       'Сначала сделайте бота администратором канала с правом «Приглашать пользователей».',
        'add_confirm': '📋 <b>Проверьте данные</b>\n\n🇺🇿 <b>{name_uz}</b>\n{description_uz}\n\n🇷🇺 <b>{name_ru}</b>\n{description_ru}\n\n'
                       '💰 Цена: <b>{price}</b>\n📡 Канал: <code>{channel}</code>\n\nСохранить?',
        'btn_save': '✅ Сохранить',
        'course_saved': '✅ Курс сохранён (#{course_id}).\n\n💳 Ссылка на покупку (сразу ведёт к оплате):\n'
                        '<code>{buy_link}</code>\n\n📚 Страница курса:\n<code>{link}</code>',
        'input_cancelled': '✖️ Отменено.',
        'not_set': '— не указано —',
        'invalid_length': '⚠️ Текст должен содержать {min}–{max} символов. Отправьте ещё раз.',
        'invalid_price': '⚠️ Цена должна быть положительным целым числом (1000 – 1 000 000 000). Например: 150000',
        'invalid_channel': '⚠️ chat_id должен быть целым числом, например: <code>-1001234567890</code>',
        'channel_problem': '⚠️ Проверка канала: {reason}\n\nИсправьте и отправьте снова или продолжите всё равно.',
        'btn_continue_anyway': '➡️ Продолжить всё равно',
        'chan_not_found': 'канал не найден или бот не добавлен в него',
        'chan_not_admin': 'бот не является администратором канала',
        'chan_no_invite': 'у бота нет права «Приглашать пользователей»',
        'chan_ok': '✅ Канал настроен верно: бот — администратор и может создавать ссылки.',
        'need_one_name': '⚠️ Нужно указать название хотя бы на одном языке.',
        'session_expired': '⌛ Сеанс ввода истёк. Начните заново.',

        'admin_courses_title': '📚 <b>Курсы</b> ({total})\n\n🟢 активен · ⚪️ неактивен · ⚠️ нет перевода',
        'admin_courses_empty': '📭 Курсов пока нет. Нажмите «➕ Добавить курс».',
        'admin_course_card': '{status} <b>#{id} · {name}</b>\n\n🇺🇿 <b>{name_uz}</b>\n{description_uz}\n\n'
                             '🇷🇺 <b>{name_ru}</b>\n{description_ru}\n\n💰 Цена: <b>{price}</b>\n'
                             '📡 Канал: <code>{channel}</code>\n🧾 Заказы: {orders} · ✅ оплачено: {paid}\n'
                             '🕒 Создан: {created}\n\n💳 Ссылка на покупку: <code>{buy_link}</code>\n'
                             '📚 Страница курса: <code>{link}</code>',
        'course_active': '🟢 Активен',
        'course_inactive': '⚪️ Неактивен',
        'missing_translation': '\n\n⚠️ <b>Нет перевода:</b> {fields}. Покупателю будет показан текст на другом языке.',
        'field_name_uz': 'Название 🇺🇿',
        'field_name_ru': 'Название 🇷🇺',
        'field_description_uz': 'Описание 🇺🇿',
        'field_description_ru': 'Описание 🇷🇺',
        'field_price_uzs': 'Цена',
        'field_channel_chat_id': 'chat_id канала',
        'btn_edit_field': '✏️ {field}',
        'btn_deactivate': '⏸ Деактивировать',
        'btn_activate': '▶️ Активировать',
        'btn_delete': '🗑 Удалить',
        'btn_course_orders': '🧾 Заказы',
        'btn_check_channel': '📡 Проверить канал',
        'btn_preview': '👁 Вид для покупателя',
        'btn_course_links': '🔗 Ссылка на покупку',
        'btn_share_link': '📤 Поделиться ссылкой',
        'course_links': '🔗 <b>{name}</b> — ссылки\n\n'
                        '💳 <b>Ссылка на покупку</b> — после нажатия «Start» пользователь сразу переходит к оплате этого курса:\n'
                        '<code>{buy_link}</code>\n\n'
                        '📚 <b>Ссылка на страницу курса</b> — сначала показывается описание:\n<code>{link}</code>\n\n'
                        'Нажмите на ссылку, чтобы скопировать, и разместите её в канале, рекламе или соцсетях.',
        'edit_prompt': '✏️ Отправьте новое значение для поля <b>{field}</b>.\n\nТекущее значение:\n{current}',
        'edit_saved': '✅ Сохранено.',
        'course_toggled_on': '▶️ Курс активирован.',
        'course_toggled_off': '⏸ Курс деактивирован. Новые оплаты не принимаются.',
        'confirm_delete': '🗑 <b>Удалить курс #{id}?</b>\n\nКурс исчезнет из списка, продажи остановятся. '
                          'История заказов и покупок сохранится.',
        'btn_confirm_delete': '🗑 Да, удалить',
        'course_deleted': '🗑 Курс удалён. История заказов сохранена.',

        'admin_orders_title': '🧾 <b>Заказы</b> · {filter} ({total})',
        'admin_orders_empty': '📭 Заказов нет.',
        'admin_order_line': '#{id} · {status} · {amount} · 👤 {user_id}',
        'admin_order_card': '🧾 <b>Заказ #{id}</b>\n\n👤 Пользователь: <code>{user_id}</code> {user}\n📚 Курс: #{course_id} {course}\n'
                            '💰 Сумма: <b>{amount}</b>\n📌 Статус: {status}\n🔖 external_reference: <code>{reference}</code>\n'
                            '🧾 Tezcheck bill: <code>{bill}</code>\n💳 ID платежа: <code>{payment}</code>\n'
                            '🕒 Создан: {created}\n✅ Оплачен: {paid_at}\n🔄 Последняя проверка: {checked}\n'
                            '⚠️ Последняя ошибка: {error}\n\n📨 Ссылка на канал: {delivery}',
        'delivery_none': '—',
        'delivery_sent': '✅ отправлена ({at})',
        'delivery_pending': '⏳ {status}',
        'btn_order_check': '🔄 Проверить в Tezcheck',
        'btn_resend_link': '📨 Отправить ссылку заново',
        'btn_open_user': '👤 Пользователь',
        'order_check_result': '🔄 Результат проверки: <b>{result}</b>',
        'resend_result': '📨 Результат: <b>{result}</b>',
        'undelivered_title': '📨 <b>Покупки без отправленной ссылки</b> ({total})',
        'undelivered_empty': '✅ Всем покупателям ссылки отправлены.',
        'undelivered_line': '#{order_id} · 👤 {user_id} · {course} · {status}',

        'search_prompt': '🔎 Отправьте ID пользователя, ID заказа, @username или имя.',
        'search_empty': '🔍 Ничего не найдено.',
        'search_results': '🔎 <b>Результаты</b>',
        'search_order_found': '🧾 Заказ #{id}',
        'search_user_found': '👤 {label} · {id}',

        'users_title': '👥 <b>Пользователи</b>\n\nВсего: <b>{total}</b> · заблокировано: {blocked}\n'
                       'Активны: сегодня <b>{active_today}</b> · 7 дней <b>{active_7}</b> · 30 дней <b>{active_30}</b>\n'
                       'Новые: сегодня {new_today} · 7 дней {new_7}\nПокупатели: <b>{buyers}</b>\n\n'
                       'Фильтр: {filter} · Сортировка: {sort} · {page_info}',
        'ufilter_all': 'Все',
        'ufilter_buyers': '💳 Покупатели',
        'ufilter_non_buyers': '🙅 Без покупок',
        'ufilter_pending': '⏳ Ожидают оплаты',
        'ufilter_inactive': '💤 Неактивны 30 дней',
        'ufilter_blocked': '⛔ Заблокированные',
        'usort_new': '🆕 Новые',
        'usort_active': '🕒 Последняя активность',
        'btn_export_csv': '📥 Экспорт CSV',
        'btn_users_search': '🔎 Поиск',
        'user_card': '👤 <b>{label}</b>\n\n🆔 ID: <code>{id}</code>\n🌐 Язык: {language}\n📅 Регистрация: {created}\n'
                     '🕒 Последняя активность: {last_active}\n{blocked}\n\n👁 <b>Просмотренные курсы:</b>\n{views}\n\n'
                     '🎓 <b>Покупки:</b>\n{purchases}\n\n🧾 <b>Заказы:</b>\n{orders}',
        'user_blocked_mark': '⛔ <b>Заблокирован</b>',
        'user_active_mark': '✅ Активен',
        'none': '—',
        'btn_block': '⛔ Заблокировать',
        'btn_unblock': '✅ Разблокировать',
        'confirm_block': '⛔ Заблокировать <b>{label}</b> ({id})?\n\nОн не сможет пользоваться ботом. История покупок сохранится.',
        'btn_confirm_block': '⛔ Да, заблокировать',
        'user_blocked': '⛔ Пользователь заблокирован.',
        'user_unblocked': '✅ Пользователь разблокирован.',
        'cannot_block_admin': '⚠️ Нельзя заблокировать администратора.',
        'user_not_found': '🔍 Пользователь не найден.',
        'export_caption': '📥 Список пользователей ({count}).',

        'stats': '📊 <b>Статистика</b>\n\n📚 Курсы: <b>{courses}</b> (активных: {active_courses})\n'
                 '🧾 Всего заказов: <b>{orders}</b>\n✅ Успешные оплаты: <b>{paid}</b>\n'
                 '⏳ Ожидают оплаты: <b>{pending}</b>\n💰 Выручка: <b>{revenue}</b>\n'
                 '👥 Пользователи: <b>{users}</b>\n📨 Неотправленные ссылки: <b>{undelivered}</b>',
        'logs_title': '📜 <b>История действий администраторов</b>',
        'logs_empty': '📭 Действий пока нет.',

        'alert_test_payment': '⚠️ Заказ #{order_id} (user {user_id}) оплачен через ТЕСТОВЫЙ провайдер. Доступ не выдан.',
        'alert_mismatch': '🚨 Заказ #{order_id} (user {user_id}): данные Tezcheck не совпадают с заказом. '
                          'Статус «На проверке». Сверьте в кабинете.',
        'alert_duplicate_payment': '🚨 Заказ #{order_id} (user {user_id}): этот курс уже куплен другим заказом. '
                                   'Рассмотрите возврат второго платежа.',
        'alert_invite_failed': '🚨 Заказ #{order_id} (user {user_id}): не удалось создать ссылку в канал курса #{course_id}. '
                               'Проверьте, что бот — администратор с правом приглашения и chat_id верный, '
                               'затем отправьте заново из раздела «📨 Неотправленные ссылки».',
        'alert_refund': '⚠️ Заказ #{order_id} (user {user_id}): {event}. Проверьте участие в канале вручную.',

        'result_paid': '✅ оплачен',
        'result_pending': '⏳ ещё не оплачен',
        'result_expired': '⌛ истёк',
        'result_test': '⚠️ тестовый платёж',
        'result_review': '🕵️ не совпадает — на проверке',
        'result_no_bill': '— счёта нет',
        'result_error': '⚠️ ошибка API',
        'result_not_found': '— не найден',
        'result_sent': '✅ отправлена',
        'result_busy': '⏳ в процессе',
        'result_not_paid': '⛔ оплата не подтверждена',
        'result_member': '✅ уже в канале',
        'result_limit': '⚠️ лимит',
        'result_invite_failed': '🚨 ссылка не создана (права в канале?)',
        'result_send_failed': '⚠️ не доставлено пользователю (бот заблокирован?)',
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    """Tarjima. Kalit topilmasa, boshqa tildagi matn, so‘ng kalitning o‘zi qaytariladi."""
    table = TEXTS.get(lang) or TEXTS['uz']
    text = table.get(key)
    if text is None:
        log.warning('Tarjima topilmadi: %s/%s', lang, key)
        text = TEXTS['uz'].get(key) or TEXTS['ru'].get(key) or key
    try:
        return text.format(**kwargs) if kwargs else text
    except (KeyError, IndexError):
        log.warning('Tarjima parametrlari mos emas: %s/%s', lang, key)
        return text


def all_variants(key: str) -> set[str]:
    """Reply-tugmalarni ikkala tilda tanish uchun."""
    return {TEXTS[lang][key] for lang in TEXTS}
