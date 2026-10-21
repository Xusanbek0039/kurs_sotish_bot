# Kurs sotuvini tayyorlash

## Botni brendingizga moslang

`.env`dagi `BRAND_NAME`, `WELCOME_TEXT`, `SUPPORT_USERNAME` qiymatlarini o‘zingizga moslang va botni qayta ishga tushiring. Savolga javob beradigan haqiqiy yordam akkauntini belgilang.

## Kursning taklifini aniq yozing

`/admin` → Kurslar → kurs → **Sotuv sahifasini sozlash**.

- **Kimlar uchun:** o‘quvchining boshlang‘ich darajasi va kursga kerakli bilimlar.
- **Nimalarni o‘rganasiz:** 3–5 ta aniq ko‘nikma. «Zo‘r mutaxassis bo‘lasiz» o‘rniga o‘quvchi dars oxirida bajara oladigan amallarni yozing.
- **Kurs dasturi:** modullarni ketma-ket bering; kursda yo‘q mavzularni qo‘shmang.
- **Ustoz haqida:** ism, tegishli tajriba va tekshirish mumkin bo‘lgan ma’lumotlar.
- **Davomiylik va format:** dars davomiyligi, yozib olingan/jonli ekanligi, materiallarga kirish muddati. Mentor yordami bo‘lsa uning shartlarini aniq ayting.
- **Bepul tanishuv havolasi:** ochiq, haqiqiy demo dars. Pullik kanalning taklif havolasini qo‘ymang.

Har bir maydon ixtiyoriy. `-` yuborilsa qiymat tozalanadi. Oldin yaratilgan kurslar ham ishlashda davom etadi. **Xaridor ko‘rinishi** bilan natijani tekshiring.

## Reklama postini tayyorlang

Kurs kartasida **Reklama uchun matn** tugmasini bosing. Bot kursning saqlangan ma’lumotlari va aynan shu kursga olib boradigan havolani beradi. Matnni kanalingizga nusxalang; ulashish tugmasi orqali kurs nomi, narxi va havolasini Telegramda ulashish mumkin. Bot sizning nomingizdan reklama yubormaydi.

Postning birinchi jumlasida auditoriyaning muammosini ayting. Keyin kurs qaysi ko‘nikmalarni o‘rgatishini ko‘rsating. Narx va xarid tugmasini ko‘rinadigan qoldiring. Soxta chegirma, uydirma o‘quvchi fikri yoki asossiz daromad kafolatini qo‘shmang. Haqiqiy chegirma bo‘lmasa «faqat bugun» deb yozmang.

## Xaridor yo‘lini tekshiring

Reklama havolasi → kurs sahifasi → dastur/demo → xarid → Tezcheck → tasdiqlangan to‘lov → kanalga kirish.

Xaridor `/purchases` orqali oldingi to‘lovi yoki kursini ochadi. To‘lov boshlangan bo‘lsa mavjud hisobni davom ettiradi; to‘langan bo‘lsa yana hisob ochilmaydi. `/help` savollarga javob va yordam kontaktini ko‘rsatadi.

## Natijani o‘lchang

`/admin` → **Sotuv bosqichlari**. Ko‘rish va xaridni boshlash bitta foydalanuvchi/kurs uchun bir marta hisoblanadi. «Botni ochganlar» foydalanuvchilar soni, qolgan kurs bosqichlari esa foydalanuvchi/kurs juftliklaridir. Admin harakatlari hisobga olinmaydi. Konversiya kursni ko‘rgan juftliklar ichida ayni kurs uchun haqiqiy to‘lov qilganlar ulushidir. Kuzatuv funksiya yoqilgan vaqtdan boshlanadi; kampaniya manbasi/UTM bo‘yicha ajratish hozircha yo‘q.

- Bot ochiladi, kurs ko‘rilmaydi: reklama auditoriyasi va kurs nomini tekshiring.
- Kurs ko‘riladi, xarid boshlanmaydi: dastur, o‘quv natijalari, mos auditoriya va demo mazmunini tekshiring.
- Xarid boshlanadi, to‘lov tugamaydi: `/health`, to‘lov usullari va yordam kontaktini tekshiring.

Bir vaqtda bir asosiy o‘zgarish qiling va yetarli kuzatuv yig‘ilgach baholang. Bot avtomatik reklama yoki xaridni tugatishga undovchi ommaviy xabarlar yubormaydi.
