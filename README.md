# Kurs sotish boti (Telegram + Tezcheck)

Foydalanuvchi kursni tanlaydi, Tezcheck orqali to‘laydi va to‘lov tasdiqlangach shu kursning yopiq
Telegram kanaliga **shaxsiy, bir martalik, muddati cheklangan** havolani avtomatik oladi.
Admin hammasini botning o‘zidan boshqaradi. Interfeys ikki tilda: 🇺🇿 o‘zbekcha (lotin) va 🇷🇺 ruscha.

Ishga tushirish: **`python main.py`**

## Imkoniyatlar

**Xaridor:** til tanlash (birinchi kirishda) · “🌐 Til / Язык” tugmasi har joyda · kurslar katalogi (nom + narx) ·
kurs kartasi · “💳 To‘lov qilish” · “🔄 To‘lovni tekshirish” · “🎓 Kurslarim” (xaridlar va kutilayotgan to‘lovlar,
havolani qayta olish) · yordam bo‘limi.

**Admin (faqat `ADMIN_IDS`dagi Telegram ID’lar):** `/admin` yoki “⚙️ Admin panel” →
kurs qo‘shish (bosqichma-bosqich, bekor qilish tugmasi bilan) · kurslar ro‘yxati va kartasi · nom/tavsif (uz/ru),
narx, kanal `chat_id`ni tahrirlash · faol/nofaol qilish · tasdiq bilan o‘chirish (tarix saqlanadi) ·
**🔗 xarid havolasi** · kanal huquqlarini tekshirish · buyurtmalar (holat va kurs bo‘yicha filtr) · Tezcheck’da
qo‘lda tekshirish · havolani qayta yuborish · qidiruv (foydalanuvchi ID, buyurtma ID, @username, ism) ·
👥 foydalanuvchilar (faollik statistikasi, filtrlar, saralash, profil, bloklash, CSV eksport) · statistika ·
yuborilmagan havolalar · admin amallari tarixi · **🎨 Bezak** — botga stiker yoki custom emoji yuborsangiz,
bot ID’ni o‘zi aniqlaydi va “qayerga qo‘yamiz?” deb so‘raydi (`.env`siz, qayta ishga tushirishsiz; `/bezak`).

## Kurs havolalari (deep link)

Har bir kurs uchun ikki xil havola bor — ikkalasi kurs kartasida va “🔗 Xarid havolasi” tugmasida ko‘rinadi:

| Havola | Foydalanuvchi “Start”ni bosganda |
|---|---|
| `https://t.me/BOT_USERNAME?start=buy_<id>` | **darhol shu kurs uchun buyurtma ochiladi va to‘lov tugmasi chiqadi** |
| `https://t.me/BOT_USERNAME?start=course_<id>` | avval kurs tavsifi va narxi, keyin “💳 To‘lov qilish” |

Yangi foydalanuvchi avval tilni tanlaydi, so‘ng havola o‘sha tilda davom etadi. Kurs nofaol yoki o‘chirilgan
bo‘lsa to‘lov ochilmaydi; kurs allaqachon sotib olingan bo‘lsa, ikkinchi buyurtma yaratilmaydi.

## O‘rnatish

Python **3.11 yoki yangiroq** kerak.

**Windows (PowerShell):**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
python main.py
```

**Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
nano .env
python main.py
```

To‘xtatish: `Ctrl+C` — fon vazifasi, Tezcheck va Telegram ulanishlari, baza tartibli yopiladi.
`data/` katalogi va SQLite bazasi avtomatik yaratiladi; qayta ishga tushganda ma’lumotlar saqlanadi.

## `.env` sozlamalari

| Kalit | Tavsif |
|---|---|
| `BOT_TOKEN` | @BotFather → `/newbot` bergan token |
| `ADMIN_IDS` | Adminlarning **raqamli** Telegram ID’lari, vergul bilan (username emas) |
| `TEZCHECK_API_KEY` | Tezcheck kabinetidagi API kalit |
| `TEZCHECK_CASH_DESK_CODE` | Kassa **kodi** (`cdk_...`), kassa ID emas |
| `TEZCHECK_BASE_URL` | `https://api.tezcheck.uz/api/merchant/v1` |
| `CARD_NUMBER` | Ixtiyoriy. 16 xonali karta raqami — to‘ldirilsa, “💳 Karta orqali to‘lash (chek)” tugmasi chiqadi: xaridor chek skrinshotini yuboradi, adminlarga ✅/❌ tugmalari bilan boradi, tasdiqlansa kanal havolasi avtomatik yuboriladi |
| `CARD_HOLDER` | Ixtiyoriy. Karta egasining ismi (xaridorga ko‘rsatiladi) |
| `DATABASE_PATH` | SQLite fayli, standart `data/bot.sqlite3` |
| `DEFAULT_LANGUAGE` | `uz` yoki `ru` |
| `SUPPORT_USERNAME` | Yordam uchun Telegram username (ixtiyoriy) |
| `CUSTOM_EMOJI_COURSE`, `CUSTOM_EMOJI_PAYMENT`, `CUSTOM_EMOJI_SUCCESS` | Ixtiyoriy haqiqiy custom emoji ID’lari (faqat raqam) |
| `STICKER_WELCOME_FILE_ID`, `STICKER_SUCCESS_FILE_ID` | Ixtiyoriy stiker `file_id` lari |
| `PAYMENT_CHECK_INTERVAL` | Ochiq to‘lovlarni tekshirish oralig‘i, soniya (15–3600, standart 60) |
| `INVITE_LINK_TTL_HOURS` | Kanal havolasi muddati, soat (1–168, standart 24) |

Eski nomlar ham ishlaydi: `TEZCHECK_API_TOKEN`, `TEZCHECK_API_BASE_URL`, `POLL_INTERVAL`, `INVITE_TTL_HOURS`.
`.env` Git’ga tushmaydi (`.gitignore`). Token va API kalit loglarda `***` bilan yashiriladi.

Ishga tushganda bot Telegram tokenini (`getMe`) va Tezcheck kalit+kassa mosligini (`GET /me`) tekshiradi —
bu so‘rovlar hisob yaratmaydi va pul harakatiga olib kelmaydi. Xato bo‘lsa, konsolda sababi yoziladi.

**Custom emoji:** ID noto‘g‘ri bo‘lsa yoki Telegram uni rad etsa, xabar avtomatik oddiy emoji bilan qayta
yuboriladi. ID olish: custom emoji’ni @RawDataBot ga yuboring yoki Bot API `getCustomEmojiStickers` orqali tekshiring.
**Stiker `file_id`:** stikerni botingizga yuboring va `message.sticker.file_id` qiymatini oling.

## Yopiq kanalni ulash

1. Yopiq kanal yarating (ochiq username bo‘lmasin).
2. Botni kanalga **admin** qiling va **“Foydalanuvchilarni taklif qilish” (Invite users)** huquqini bering.
3. Kanal `chat_id`sini oling (odatda `-100…`): kanal postini @RawDataBot / @getidsbot ga forward qiling.
4. Admin panelda kurs qo‘shayotganda `chat_id`ni kiriting — bot kanal va o‘z huquqlarini darhol tekshiradi.
   Kurs kartasidagi “📡 Kanalni tekshirish” tugmasi ham bor.

Havola `member_limit=1` (faqat bitta kishi kira oladi) va `INVITE_LINK_TTL_HOURS` muddat bilan yaratiladi va faqat
xaridorga shaxsiy xabarda yuboriladi. Admin “📨 Havolani qayta yuborish” qilsa — eski havola bekor qilinib, yangisi
yaratiladi. Xaridor o‘zi “Kurslarim”dan havolani qayta so‘rashi mumkin: kanalda bo‘lsa yangi havola berilmaydi,
muddati o‘tgan havolani yangilash 3 marta bilan cheklangan (ortig‘i — admin orqali).

## To‘lov qanday tasdiqlanadi

Bot **webhook ishlatmaydi** — ommaviy server, domen yoki HTTPS kerak emas. To‘lov faqat server tomonidan
Tezcheck API (`POST /bills/{bill}`) javobi bilan tasdiqlanadi:

1. **“🔄 To‘lovni tekshirish”** — xaridor bosganda bot Tezcheck’dan holatni so‘raydi (5 soniyalik cheklov bilan).
2. **Fon tekshiruvi** — har `PAYMENT_CHECK_INTERVAL` soniyada ochiq (`pending`) buyurtmalar avtomatik tekshiriladi;
   to‘lov o‘tgan bo‘lsa, xaridor tugmani bosmasa ham havola yuboriladi.

Brauzerdan qaytish, “To‘ladim” deyish yoki skrinshot tasdiq emas. Bot to‘xtab turgan paytdagi to‘lovlar
qayta ishga tushganda fon tekshiruvi orqali topiladi. Bot doimiy ishlashi kerak (kompyuter yoqiq yoki VPS).

Kursga kirish faqat shu holatda beriladi: `bill.paid = true`, `livemode = true`, `environment = "live"`,
`payment.state = "succeeded"`, summa, `external_reference` va hisob ID buyurtmaga mos, qaytarilgan summa 0.
Test provayderi to‘lovi (`environment: "test"`) kurs **bermaydi**, admin ogohlantiriladi. Summa yoki ma’lumot mos
kelmasa, buyurtma “🕵️ Tekshiruvda” holatiga o‘tadi va adminlarga xabar ketadi.

## Tezcheck integratsiyasi

[Rasmiy hujjat](https://tezcheck.uz/api-docs) bo‘yicha qilingan; faqat hujjatdagi endpoint va maydonlar ishlatiladi:

- Sarlavhalar: `Authorization: Bearer <TEZCHECK_API_KEY>`, `X-Cash-Desk-Code: <TEZCHECK_CASH_DESK_CODE>`.
- `GET /me` — ishga tushishda ulanish tekshiruvi.
- `POST /bills` — `amount_minor` (so‘m × 100, butun son), `title`, `external_reference`. `currency` yuborilmaydi
  (hujjat: platforma faqat UZS). Har buyurtmaning o‘zgarmas `external_reference` (`ORD-<uuid>`) va
  `Idempotency-Key`i bor — tarmoq uzilib qayta yuborilsa, ikkinchi hisob yaratilmaydi.
- `POST /bills/{bill}` — holatni o‘qish (hujjatda aynan POST).
- Xatolar: HTTP timeout, internet uzilishi, noto‘g‘ri JSON, `error.code` (401/403/404/409/422/429/5xx) ushlanadi;
  faqat qayta urinish xavfsiz bo‘lgan holatlarda (5xx, 429, tarmoq, `resource.conflict`) qayta so‘raladi.

Hujjat bo‘yicha aniqlik kiritilgan nuqtalar:

- **`payment_url` faqat bir marta qaytadi** — darhol bazaga saqlanadi. Bot faqat `https://` havolani qabul qiladi
  (hujjat namunasidagi `http://localhost…` faqat misol).
- Hujjatdagi eng kichik summa — 1 000 so‘m, shuning uchun admin panel narxni 1 000 – 1 000 000 000 so‘m oralig‘ida qabul qiladi.
- Kassa kalitida “faqat imzolangan so‘rovlar” yoqilgan bo‘lsa, Bearer so‘rovlar rad etiladi — kabinetda o‘chiq bo‘lsin.
- Pul qaytarish (refund/chargeback) holatida bot foydalanuvchini kanaldan avtomatik chiqarmaydi.
- Kurs nofaol qilinganda allaqachon yuborilgan to‘lov havolasi Tezcheck’da amal qilishda davom etadi; agar u
  to‘lansa, xaridor kursni oladi (pul to‘lagan).

## Ma’lumotlar bazasi (SQLite)

Jadvallar: `users` (ID, username, ism, til, ro‘yxatdan o‘tgan vaqt, oxirgi faollik, blok), `courses`
(`name_uz/ru`, `description_uz/ru`, `price_uzs`, `channel_chat_id`, `is_active`, `is_deleted`, vaqtlar),
`orders`, `purchases`, `course_views` (qaysi kursni ko‘rgan), `admin_logs`.

- Foreign key, unique va indekslar: bitta foydalanuvchi+kurs uchun bitta ochiq buyurtma, bitta xarid; bitta
  Tezcheck hisob/to‘lov ID bitta buyurtmaga.
- To‘lovni tasdiqlash va xarid yaratish **bitta tranzaksiyada**; takroriy tekshiruvlar ikkinchi xarid yoki havola yaratmaydi.
- Havola yuborish atomar “band qilinadi” — ikki vazifa bitta xaridni ikki marta yubormaydi.
- Kurs o‘chirilganda yozuv faqat belgilanadi (`is_deleted`), buyurtma va xaridlar tarixi saqlanadi.
- Karta ma’lumotlari bazaga hech qachon tushmaydi (to‘lov Tezcheck sahifasida bo‘ladi).

Xaridor botni bloklagan bo‘lsa, havola saqlanadi va fonda qayta urinadi (vaqt oralig‘i oshib boradi); xaridor
`/start` bosganda ham darhol yuboriladi. Kanal xatosida (bot admin emas, noto‘g‘ri `chat_id`) xaridorga yordam xabari,
adminlarga ogohlantirish ketadi; tuzatgandan so‘ng “📨 Yuborilmagan havolalar” bo‘limidan qayta yuboriladi.

## Loyiha tuzilishi

```text
main.py              ishga tushirish: polling + fon tekshiruvi, tartibli to‘xtash, loglarni maxfiylash
config.py            .env o‘qish va tekshirish
database.py          SQLite sxema, tranzaksiyalar, so‘rovlar
locales.py           barcha matnlar: uz va ru
handlers/            user.py (xaridor), admin.py (kurs/buyurtma/qidiruv), admin_users.py (foydalanuvchilar),
                     utils.py (admin filtri, middleware), __init__.py (routerlar, xatolar)
keyboards/           inline va pastki menyu tugmalari
services/            tezcheck.py (API mijozi), payments.py (to‘lov va havola oqimi), ui.py (formatlash, emoji)
tests/               unit va integratsion testlar (tarmoqsiz)
legacy_mongo/        oldingi MongoDB versiyasining zaxira nusxasi (ishlatilmaydi)
```

## Testlar

```bash
python -m unittest discover -s tests -t .
```

72 ta test, internet va haqiqiy pulsiz (Tezcheck va Telegram soxta/lokal server bilan): admin ruxsati va soxta admin
callback’lar, kurs CRUD va qo‘shish bosqichlari, buyurtma idempotentligi (parallel bosishlar), takroriy tekshiruvlarda
bitta xarid/havola, to‘lov tasdiqlanmaguncha havola berilmasligi, test va mos kelmaydigan to‘lovlar, `buy_`/`course_`
havolalari, til almashtirish, foydalanuvchilar bo‘limi va CSV, Tezcheck so‘rov formati va xatolari.

## Cheklovlar

- Faqat **bitta** bot jarayonini ishga tushiring (bir token bilan ikkita polling ishlamaydi).
- Admin kiritish bosqichlari xotirada saqlanadi — qayta ishga tushganda tugallanmagan kiritishni qaytadan boshlang.
- Webhook keyinroq kerak bo‘lsa (ommaviy HTTPS server bilan), uni alohida qo‘shish mumkin; hozir tasdiq API orqali.
