# Вимоги Web — «Обмін даними у Web-застосунках»

Джерело: `web-project-guide-2026.pdf` (методичка 2026). Тема C09 «Паркінги».
«Ядро» перевіряється завжди; «О» — бажані пункти, «Д» — додаткові. Вибрані пункти поза ядром перелічуються в `checks.yaml` (`web.items`).
Статуси: `not_started` → `in_progress` → `done` (лише з фактичною перевіркою в Evidence) / `skipped`.
Опис — скорочена назва пункту; повний текст — у методичці.

## ЛР1

| Lab | ID | Ядро/О/Д | Описание | Компонент | Файлы | Тесты | Evidence | Status |
|---|---|---|---|---|---|---|---|---|
| ЛР1 | О1.1 | Ядро | Модель області. | README §2, docs/decisions.md | README.md | — | діаграма станів, інваріанти, параметри | done |
| ЛР1 | О1.2 | Ядро | Схема бази даних. | PostgreSQL | migrations/001–003, 900 | test_lr1.py (усі) | міграції з нуля: 4 версії applied | done |
| ЛР1 | О1.3 | Ядро | Контракт API. | Web API | web/openapi.yaml | redocly lint | valid, 0 errors | done |
| ЛР1 | О1.4 | Ядро | Головна операція. | Web API | web/server/app/bookings.py | test_1–4, test_7 | 8 passed; 201+Location, 409 без змін | done |
| ЛР1 | О1.5 | О | Список із курсором. | Web API | web/server/app/bookings.py, cursor.py | test_5 | 3 сторінки, однаковий created_at, 400 | done |
| ЛР1 | О1.6 | О | Система, перевірка живучості й журнал. | каркас | service.py, middleware.py, jsonlog.py | test_health_and_request_id | JSON-журнал без X-User-Id | done |
| ЛР1 | О1.7 | Ядро | Клієнт. | Клієнт | web/client/ | вручну в браузері | знімки у звіті ЛР1 | done |
| ЛР1 | Д1.1 | Д | Правило за часом — чиста функція, що отримує поточний час параметром. | Web API | web/server/app/rules.py | test_rules.py, test_lr1.py::test_8 | 16 passed; PC-1 | done |
| ЛР1 | Д1.2 | Д | Клієнт написано іншою мовою, ніж сервер. | Клієнт | web/client/ (TypeScript; сервер — Python) | — | — | done |
| ЛР1 | Д1.3 | Д | Одне правило RFC 9110 або RFC 9457 перетворене на тест. | — | — | — | — | not_started |
| ЛР1 | Д1.4 | Д | Перевірений traceparent записується в журнал як trace_id. | — | — | — | — | not_started |
| ЛР1 | Д1.5 | Д | Гроші, точні величини й час. | — | — | — | — | not_started |

## ЛР2

| Lab | ID | Ядро/О/Д | Описание | Компонент | Файлы | Тесты | Evidence | Status |
|---|---|---|---|---|---|---|---|---|
| ЛР2 | О2.1 | Ядро | Інваріант за одночасних запитів. | Web API, PostgreSQL | web/server/app/bookings.py (`_reserve_hours`), migrations/902 | test_lr1.py::test_7; experiments/web_last_spot.py, web_invariant_check.sql | 10×20 через nginx: protected 0 годин понад місця, unprotected 34 (results/web-lr2/last-spot-*) | done |
| ЛР2 | О2.2 | Ядро | Механізм ідемпотентності. | Web API, publisher | web/server/app/idempotency.py, publisher.py, migrations/004 | test_lr2.py::test_1–6, test_11; experiments/web_idempotency.py | повтор → той самий id; інше тіло 422; без ключа 400; одночасні з одним ключем → 1 бронювання (results/web-lr2/idempotency.txt) | done |
| ЛР2 | О2.3 | О | Повтор після втраченої відповіді. | Web API | web/server/app/idempotency.py (`fault("after_commit_before_response")`) | experiments/web_lost_response.sh | app-a: Empty reply + fault.hit; app-b: 201 той самий id, 1 бронювання, free −1 (results/web-lr2/lost-response.txt) | done |
| ЛР2 | О2.4 | О | Повторні спроби в клієнті. | Клієнт | web/client/src/retry.ts, BookingPage.tsx | вручну в браузері з відмовою на app-a; знімок «We couldn't confirm your booking» | мережева помилка → повтор із тим самим ключем → 201, 1 бронювання (results/web-lr2/client-retry.txt) | done |
| ЛР2 | Д2.1 | Д | Ліміт частоти змінювальних запитів, спільний для екземплярів; у разі перевищення — 429. | — | — | — | — | not_started |
| ЛР2 | Д2.2 | Д | Порівняння двох способів забезпечення інваріанта під однаковим навантаженням. | — | — | — | — | not_started |
| ЛР2 | Д2.3 | Д | Другий інваріант області з картки. | — | — | — | — | not_started |
| ЛР2 | Д2.4 | Д | Умовна зміна. | Web API, клієнт | bookings.py (`update_booking`), BookingsPage.tsx | test_lr2.py::test_7 | 428, 412, 422, 200 + ETag; 412 у клієнті | done |
| ЛР2 | Д2.5 | Д | Повторне скасування. | Web API, клієнт | bookings.py (`cancel_booking`), BookingsPage.tsx | test_lr2.py::test_8–10; experiments/web_cancel_twice.py | послідовно й одночасно: free +1 рівно раз (results/web-lr2/cancel-twice.txt) | done |
| ЛР2 | Д2.6 | Д | Межі ресурсів. | — | — | — | — | not_started |
| ЛР2 | Д2.7 | Д | План запиту. | — | — | — | — | not_started |

## ЛР3

| Lab | ID | Ядро/О/Д | Описание | Компонент | Файлы | Тесты | Evidence | Status |
|---|---|---|---|---|---|---|---|---|
| ЛР3 | О3.1 | Ядро | Вхід. | Web API, PostgreSQL, publisher | web/server/app/auth.py, users.py, publisher.py; migrations/006, 904; scripts/hash_password.py | test_lr3.py::test_1–5, test_12; experiments/web_lr3_checks.py | вхід app-a → токен на app-b; вихід → 401 на обох; прострочена 401; однакова 401 для пароля й логіна; X-User-Id → 401 (results/web-lr3/checks.txt) | done |
| ЛР3 | О3.2 | Ядро | Доступ до об’єктів і функції персоналу. | Web API | bookings.py (власник у SQL), catalog.py (`close`/`open`, `StaffUser`) | test_lr3.py::test_7–10, test_lr2.py::test_7 | чуже GET/PATCH/cancel → 404 як відсутнє; user close 403, staff 200, нове бронювання 409, підтверджене лишається (results/web-lr3/checks.txt) | done |
| ЛР3 | О3.3 | О | Захист браузерних запитів. | Web API, клієнт | main.py (CORS), web/tools/evil-origin/index.html, web/client/src/api.ts | test_lr3.py::test_11; браузер :8082 і :5173 | preflight 5173 → ACAO, 8082 → 400 без ACAO; у браузері заблоковано POST і GET, форма → 401 (results/web-lr3/cors-preflight.txt, знімки) | done |
| ЛР3 | О3.4 | О | Сценарії входу й доступу в клієнті. | Клієнт | LoginPage.tsx, AccessPage.tsx, App.tsx, components.tsx | вручну в браузері, scripts/screenshots/web-lr3.py | вхід/вихід, логін і роль у шапці; чуже → 404, close у user → 403, у staff → закрито; 401 → вхід | done |
| ЛР3 | Д3.1 | Д | JWT і дослід відкликання. | — | — | — | — | skipped |
| ЛР3 | Д3.2 | Д | Вхід через OpenID Connect із готовим провайдером (Keycloak). | — | — | — | — | skipped |
| ЛР3 | Д3.3 | Д | Обмеження спроб входу. | — | — | — | — | skipped |

## ЛР4

| Lab | ID | Ядро/О/Д | Описание | Компонент | Файлы | Тесты | Evidence | Status |
|---|---|---|---|---|---|---|---|---|
| ЛР4 | О4.1 | Ядро | Асинхронна операція. | — | — | — | — | not_started |
| ЛР4 | О4.2 | Ядро | Таблиця вихідних подій і публікатор. | — | — | — | — | not_started |
| ЛР4 | О4.3 | Ядро | Обробник події. | — | — | — | — | not_started |
| ЛР4 | О4.4 | О | Відновлення після керованих відмов. | — | — | — | — | not_started |
| ЛР4 | О4.5 | О | Клієнт асинхронної операції. | — | — | — | — | not_started |
| ЛР4 | Д4.1 | Д | Формат подій CloudEvents 1.0. | — | — | — | — | not_started |
| ЛР4 | Д4.2 | Д | Опис каналів і повідомлень в AsyncAPI 3.0. | — | — | — | — | not_started |
| ЛР4 | Д4.3 | Д | Наскрізна траса однієї операції через HTTP, брокер і обробник. | — | — | — | — | not_started |
| ЛР4 | Д4.4 | Д | Проблемні повідомлення. | — | — | — | — | not_started |

## ЛР5

| Lab | ID | Ядро/О/Д | Описание | Компонент | Файлы | Тесты | Evidence | Status |
|---|---|---|---|---|---|---|---|---|
| ЛР5 | О5.1 | Ядро | Авторизоване підключення. | — | — | — | — | not_started |
| ЛР5 | О5.2 | Ядро | Доставка через два шлюзи. | — | — | — | — | not_started |
| ЛР5 | О5.3 | О | Стрічка сповіщень і відновлення. | — | — | — | — | not_started |
| ЛР5 | О5.4 | О | Клієнт сповіщень. | — | — | — | — | not_started |
| ЛР5 | Д5.1 | Д | Адресна маршрутизація з реєстром «користувач — шлюз» і орендою; дослід видалення маршруту із затримкою. | — | — | — | — | not_started |
| ЛР5 | Д5.2 | Д | Відображення присутності користувачів у мережі. | — | — | — | — | not_started |
| ЛР5 | Д5.3 | Д | Server-Sent Events як другий канал. | — | — | — | — | not_started |
| ЛР5 | Д5.4 | Д | Розподіл 100 з’єднань і використання пам’яті шлюзом за такого навантаження. | — | — | — | — | not_started |
| ЛР5 | Д5.5 | Д | Контроль з’єднання. | — | — | — | — | not_started |
| ЛР5 | Д5.6 | Д | Повільний клієнт. | — | — | — | — | not_started |
| ЛР5 | Д5.7 | Д | Балансування з’єднань. | — | — | — | — | not_started |
| ЛР5 | Д5.8 | Д | Застарілий курсор. | — | — | — | — | not_started |

## ЛР6

| Lab | ID | Ядро/О/Д | Описание | Компонент | Файлы | Тесты | Evidence | Status |
|---|---|---|---|---|---|---|---|---|
| ЛР6 | О6.1 | Ядро | Запланована зміна та повернення попередньої версії. | — | — | — | — | not_started |
| ЛР6 | О6.2 | О | Сумісність. | — | — | — | — | not_started |
| ЛР6 | О6.3 | Ядро | Готовність і коректне завершення. | — | — | — | — | not_started |
| ЛР6 | О6.4 | О | Поступове оновлення під навантаженням. | — | — | — | — | not_started |
| ЛР6 | Д6.1 | Д | Запис архітектурного рішення у відповідь на зміну вимоги. | — | — | — | — | not_started |
| ЛР6 | Д6.2 | Д | Канаркове оновлення з частиною трафіку на новій версії. | — | — | — | — | not_started |
| ЛР6 | Д6.3 | Д | Прапорці функцій як окремий сервіс конфігурації. | — | — | — | — | not_started |
| ЛР6 | Д6.4 | Д | Розгортання системи на кількох вузлах (Docker Swarm або Kubernetes) і поступове оновлення засобами оркестра… | — | — | — | — | not_started |
