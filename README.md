# Telegram orqali kurs sotish

Python 3.11+ va MongoDB Atlas asosidagi aiogram 3 bot. Boshlash buyrug‘i: `python main.py`.

## Tuzilishi

```text
kurs_sotish_bot/
  main.py                  Telegram menyulari, admin suhbatlari va fon vazifasi
  config.py                .env konfiguratsiyasi
  database.py              MongoDB kolleksiyalari, indekslar va tranzaksiyalar
  tezcheck.py               Merchant API mijozi
  requirements.txt
  .env.example
  .gitignore
  README.md
  tests/test_payments.py
  FULL_CODE.md             barcha fayllar kodining birlashtirilgan nusxasi
```

## Windows (PowerShell)

Python 3.11 yoki yangiroq versiyani python.org saytidan o‘rnating va PATH belgisini yoqing.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python main.py
```

Agar PowerShell aktivlashtirishni cheklasa, virtual muhitdagi Pythonni to‘g‘ridan-to‘g‘ri chaqiring:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

## Linux

Python 3.11+ va distributivingizdagi Python venv paketini o‘rnating.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
nano .env
python main.py
```

## Sozlash

- `BOT_TOKEN`: Telegramdagi rasmiy @BotFather orqali `/newbot` bilan yarating.
- `ADMIN_IDS`: adminlarning raqamli Telegram IDlari, vergul bilan. Botdagi `/id` o‘z IDingizni ko‘rsatadi. Username yozmang.
- `TEZCHECK_API_KEY`: Tezcheck savdogar kabinetida API kalit yarating.
- `TEZCHECK_CASH_DESK_CODE`: kabinetdagi kassa kodi; bu kassa IDsi emas.
- `TEZCHECK_BASE_URL`: namunadagi rasmiy HTTPS manzil.
- `MONGODB_URI`: Atlas kabinetidagi Connect → Drivers bo‘limidan olingan maxfiy ulanish manzili.
- `MONGODB_DATABASE`: baza nomi, odatda `kurs_sotish_bot`.
- `POLL_INTERVAL`: tekshirishlar orasidagi soniyalar, kamida 30.
- `INVITE_TTL_HOURS`: kirish havolasi muddati, 1–168 soat.
- `CUSTOM_EMOJI_ID`: ixtiyoriy haqiqiy Telegram custom emoji IDsi. Bo‘sh bo‘lsa oddiy emoji ishlaydi. Telegram hisob/bot cheklovlariga qarab custom emoji oddiy emoji sifatida ko‘rinishi mumkin.

`.env` va MongoDB ulanish manzilini oshkor qilmang. Loglarda token, MongoDB URI, API javobi va to‘lov/taklif URLlari chiqarilmaydi.

## MongoDB Atlas sozlash

1. [MongoDB Atlas](https://www.mongodb.com/atlas) hisobingizda loyiha va klaster yarating.
2. Database Access orqali baza foydalanuvchisini yarating. Bot uchun `kurs_sotish_bot` bazasida `readWrite` huquqini bering.
3. Network Access bo‘limida bot ishlaydigan kompyuter/serverning tashqi IP manzilini ruxsat ro‘yxatiga qo‘shing. [Atlas IP access list hujjati](https://www.mongodb.com/docs/atlas/security/add-ip-address-to-list/).
4. Connect → Drivers → Python orqali connection stringni oling. Username va parolni o‘zingizniki bilan almashtiring. Paroldagi maxsus belgilar URI uchun percent-encode qilinishi kerak.
5. `.env`ga yozing. Quyidagilar faqat format namunasi, haqiqiy kalitlar emas:

```dotenv
MONGODB_URI="mongodb+srv://BOT_USER:ENCODED_PASSWORD@YOUR_CLUSTER.mongodb.net/?retryWrites=true&w=majority"
MONGODB_DATABASE=kurs_sotish_bot
```

Bot bazani, `courses`, `orders`, `purchases`, `admin_logs` va raqamli IDlar uchun `counters` kolleksiyasini avtomatik yaratadi. Ulanish uchun internet zarur. PyMongo Async ishlatiladi; [rasmiy tranzaksiya hujjati](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/crud/transactions/). Tranzaksiyalar uchun replica set kerak; Atlas klasteridan foydalaning, standalone lokal MongoDB mos emas. Eski SQLite fayli bo‘lsa, uning ma’lumotlari avtomatik import qilinmaydi.

Yopiq kanal yarating va botni **Invite users** huquqi bilan admin qiling. Kanalda ochiq username bo‘lmasin. Kanal postini botga yuborib chat ID aniqlash mumkin; ishonchli Telegram Bot API `getUpdates` javobida `channel_post.chat.id` qiymati ham ko‘rinadi. Polling ishlayotgan paytda ikkinchi `getUpdates` chaqiruvini ishlatmang. Kanal IDsi odatda `-100` bilan boshlanadi. Uni kurs qo‘shish suhbatida kiriting; bot kanal turi va o‘z huquqlarini tekshiradi.

Admin avval botni `/start` bilan ochsin. `/admin` menyusida kurs qo‘shish, barcha maydonlarni tahrirlash, faol/nofaol qilish, tasdiqlab o‘chirish, sahifalangan buyurtmalar va statistika bor. Tahrirlashda nom, tavsif, narx, kanal ketma-ket qayta kiritiladi. `/cancel` suhbatni bekor qiladi. Kurs kartasida alohida `https://t.me/BOT_USERNAME?start=course_ID` havolasi chiqadi. Oddiy xaridor admin menyusini ko‘rmaydi; admin handlerlari ham IDni tekshiradi.

## Tezcheck bilan shartnoma

Kod yozishdan oldin [rasmiy Merchant API hujjati](https://tezcheck.uz/api-docs) tekshirildi. Aiogram versiyasi [rasmiy 3.29 hujjatlariga](https://docs.aiogram.dev/en/v3.29.0/changelog.html) asoslangan.

API mijozi autentifikatsiya sarlavhalarini yuboradi. Hisob `POST /bills` bilan yaratiladi; `data.bill.id` va `data.payment_url` darhol saqlanadi. Narx butun so‘mda, API summasi esa 100 ga ko‘paytirilib yuboriladi. Har bir order o‘zgarmas UUID reference va idempotency kalitiga ega.

Holat uchun **POST** `/bills/{bill}` ishlatiladi. `bill.paid`, `bill.livemode`, haqiqiy muhit, payment holati, summa, valyuta va buyurtmaga moslik tekshiriladi. Test to‘lovi kanalni ochmaydi. Brauzer qaytishi va tugma bosilishi to‘lov isboti emas. Webhook kerak emas: foydalanuvchi tugmasi va fon tekshiruvi mavjud. `/health` admin buyrug‘i bazani va autentifikatsiyalangan `GET /me` ulanishini tekshiradi; hisob yaratmaydi.

Kassa kalitida faqat imzolangan so‘rovlar talabi yoqilgan bo‘lsa, bu Bearer mijozi rad etiladi. Ushbu loyiha uchun kabinetda Bearer so‘rovlariga ruxsat bering.

## Saqlash va xatolardan tiklash

Bir foydalanuvchi/kurs uchun bitta ochiq buyurtma va bitta xarid mavjud. To‘lovni qayta ishlash tranzaksiyali. Mavjud xarid uchun yangi hisob ochilmaydi; saqlangan havola qayta ko‘rsatilishi mumkin. Kurs o‘chirilishi arxivlash orqali bajariladi, buyurtma va audit tarixi saqlanadi. Buyurtma narxi, nomi va kanal IDsi xarid boshlangan paytdagi nusxasi bilan saqlanadi.

Noaniq hisob yaratish javobi o‘sha order va idempotency kaliti bilan qayta so‘raladi. Idempotency saqlanishi cheklanganligi sabab 23 soatdan eski, bill IDsi olinmagan order `review` holatiga o‘tadi. Admin kabinetda orderning `external_reference` raqami bo‘yicha solishtirishi kerak; avtomatik yangi hisob ochilmaydi.

Kirish havolasi muddati cheklangan **join request** havolasi: bot faqat to‘lagan foydalanuvchining IDsi uchun so‘rovni tasdiqlaydi, keyin havolani bekor qiladi. Telegramda join request va `member_limit=1` birga ishlatilmaydi. Ushbu usul ulashilgan havoladan begona shaxs kirishini cheklaydi. Boshqa adminlar begona so‘rovlarni qo‘lda tasdiqlamasin.

Telegramga taklif yaratish paytida jarayon uzilsa, yangi havola avtomatik yaratilmaydi: admin yordam beradi. Kanal huquqi xatosi haqida adminlarga xabar yuboriladi. Havola muddati o‘tgan yoki `error` holatidagi haqiqiy xaridorga admin kabinetdagi tekshiruvdan so‘ng qo‘lda yordam beradi. To‘langan foydalanuvchini qayta to‘lashga majburlamang. Bot bloklangan bo‘lsa xabar yetkazish keyingi fon aylanishida qayta uriniladi.

Faqat bitta bot jarayonini ishga tushiring. Buyurtmalar, xaridlar va audit Atlasda doimiy saqlanadi; tugallanmagan admin suhbatlari xotirada, restartdan keyin qayta boshlanadi. Bir user/kurs uchun bitta order saqlanadi va qayta ishlatiladi. To‘lov va xarid yozuvlari, shuningdek kurs o‘zgarishi va uning auditi bir tranzaksiyada yoziladi. Noyob indekslar bill, payment, xarid va havola takrorlanishini cheklaydi. Taklif yaratish huquqi atomar yangilash bilan olinadi. Bu bot refund/chargebackdan keyin kanal a’zosini avtomatik chiqarishni bajarmaydi; keyingi qaytarishlarni admin nazorat qiladi.

## Test

```bash
python -m unittest discover -s tests -v
```

API validatsiya testlari tarmoqsiz ishlaydi. Baza testlari `MONGODB_TEST_URI` berilmasa o‘tkazib yuboriladi. Atlas integratsiya testlari bir vaqtdagi xarid, restart, takroriy to‘lov, tranzaksiya rollback, kurs nusxasi, audit va taklif uchun atomar himoyani tekshiradi. Telegram/Tezcheck tarmog‘iga murojaat qilinmaydi.

Integratsiya testlari uchun alohida test klasterida `kurs_bot_test_` prefiksli vaqtinchalik bazalarni yaratish/o‘chirish huquqi kerak. Har bir test noyob vaqtinchalik baza yaratadi va tugagach o‘chiradi. Test URI oddiy `.env`dan avtomatik o‘qilmaydi; muhit o‘zgaruvchisi qilib bering:

```powershell
$env:MONGODB_TEST_URI = 'ATLAS_TEST_CONNECTION_STRING'
python -m unittest discover -s tests -v
```

```bash
export MONGODB_TEST_URI='ATLAS_TEST_CONNECTION_STRING'
python -m unittest discover -s tests -v
```

Qo‘lda yakuniy ssenariy:

1. `/health` bilan kassa ulanishini tekshiring.
2. `/admin` → **Kurs qo‘shish** → nom → tavsif → narx → kanal ID → tasdiqlash.
3. Kurs kartasidagi deep linkni boshqa foydalanuvchi bilan oching.
4. **To‘lov qilish** → hisob yaratilishini tekshiring. Qayta bosilganda o‘sha hisob chiqsin.
5. To‘lamasdan **To‘lovni tekshirish** bosing: kanal havolasi berilmasin.
6. Avval Test provider bilan to‘lang: test to‘lovi sifatida rad etilsin. Bu haqiqiy kursni ochmaydi; jonli sinov uchun boshqa foydalanuvchi/kursdan foydalaning.
7. Ruxsat etilgan haqiqiy provayder bilan to‘lovni yakunlang. **To‘lovni tekshirish** yoki fon vazifasini kuting.
8. Kanal havolasini oching, kirish so‘rovi aynan xaridor uchun tasdiqlansin. Havolani boshqa ID bilan ochganda rad etilsin.
9. Botni qayta ishga tushiring va xaridni takrorlang: ikkinchi hisob/havola yaratilmasin. Admin statistikasida bitta to‘lov ko‘rinsin.

Ushbu tayyorlash muhitida Python runtime topilmadi; avtomatik testlar va real to‘lov yakunigacha ishga tushirish bajarilmagan. Ishga tushirish uchun haqiqiy tokenlar, kassa va kanal huquqlari kerak.
