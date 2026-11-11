# Рішення проєкту C09 «Паркінги»

Прийняті рішення; переглядаються лише окремим записом із датою та причиною.
Джерело: первинний аналіз методичок і комплектів, узгоджено 2026-11-03.

## Архітектура й стек

| № | Рішення | Причина |
|---|---|---|
| A1 | Web-сервер — Python/FastAPI з каркаса Web (`web/server`) | готові gateway, publisher, worker, router, faults, JSON-журнал, lifecycle; Д1.2 з клієнтом на TS |
| A2 | DPC api — Python/FastAPI з каркаса DPC (`dpc/api`) | api є клієнтом Ray (один образ із вузлами Ray), черга + пул, процеси для ЛР2 |
| A3 | Клієнт — React + TypeScript + Vite у `web/client/`, поза Compose | клієнт в обох курсах поза Compose; звертається до Web лише через nginx |
| A4 | Дві частини з окремими API: Web через nginx `:8080`, DPC api `:8000`; спільні лише PostgreSQL і Kafka | `joint/checks-template.yaml` перевіряє дві бази й два `openapi.yaml` |
| A5 | Одна база PostgreSQL і одна папка `migrations/` зі спільною нумерацією | `migrate.sh` у каркасах ідентичний і веде одну `schema_migrations` |
| A6 | Один брокер Kafka: сервіс `broker` з мережевим псевдонімом `kafka` | Web звертається до `broker:9092`, DPC — до `kafka:9092`; змінні каркасів не змінюються |
| A7 | Один `compose.yaml` у корені без `include` | у `include` кожен файл перевіряється окремо, а сервіси DPC залежать від `postgres`/`broker-init` іншого файлу |
| A8 | Профілі Compose: типовий (ядро), `observability`, `extras` | базовий запуск має вміщатися в пам'ять |
| A9 | Тема подій DPC — `barrier-events`, 1 партиція | тема з картки C09; О5.3 потребує контрольованого порядку |

## Межа станів Web ↔ DPC

| № | Рішення |
|---|---|
| B1 | Web володіє: каталог `garage`, `level`, `level_hour`, `booking`, `users`, `operations`, `outbox`, `notifications`. DPC володіє: зайнятість `garage` (spots, occupied), `entry` (въїзди/виїзди), `tariff`, покоління/історія координатора, таблиці panel/report |
| B2 | Жодна частина не пише в таблиці іншої; читання зафіксованого результату дозволене |
| B3 | Статус бронювання `entered` Web обчислює під час читання з таблиці въїздів DPC (`LEFT JOIN entry ON entry.booking_id = booking.id`); у БД бронювання лишається `confirmed` |
| B4 | Въїзд за бронюванням **не перевіряє** вільну квоту: місце вже зарезервоване в `level_hour` |
| B5 | У таблиці въїздів `UNIQUE(event_id)` (повтор ефекту) і `UNIQUE(booking_id)` для в'їзду (другий в'їзд за тим самим бронюванням) |
| B6 | Въїзд без бронювання допускається, якщо `occupied + невикористані бронювання поточної години < spots`; перевірка й зміна атомарні |
| B7 | `garage.spots = Σ level.spots`; въїзд без бронювання враховує невикористані бронювання всіх рівнів поточної години |
| B8 | Закриття рівня забороняє лише нові бронювання; підтверджені лишаються чинними |
| B9 | Тариф встановлює лише менеджер DPC (координатор → ресурс із fencing); Web лише показує його |

## Подання даних

- Гроші — цілі копійки (`bigint`); час — `timestamptz`, ім'я часової зони — у `garage.tz`; ідентифікатори в JSON — рядки.
- Неповна година = повна; інтервал бронювання `[start, start+N)` у цілих годинах.

## Інфраструктура (етап 0)

| № | Рішення | Причина |
|---|---|---|
| I1 | `CLIENT_ORIGIN` розведено на `WEB_CLIENT_ORIGIN` і `DPC_CLIENT_ORIGIN` (типово `http://localhost:5173`, Vite); у контейнерах ім'я лишається `CLIENT_ORIGIN` | у каркасах різні значення |
| I2 | `app-canary` і `flags` — у типовому наборі | `nginx.conf` посилається на `app-canary` в upstream; `flags` потрібен у ЛР6 |
| I3 | Приклади міграцій каркасів не застосовуються; лежать у `web/migrations-examples/`, `dpc/migrations-examples/` | чужа предметна область |
| I4 | Prometheus і Grafana — спільні, у корені `configs/`; дашборди `web.json` і `dpc.json` в одній теці | один екземпляр на обидві частини |

## Модель Web ЛР1 (етап 1, 2026-11-04)

| № | Рішення | Причина |
|---|---|---|
| M1 | `POST /v1/bookings` одразу резервує години й створює бронювання в стані `confirmed`; стан `held` не використовується | у картці немає маршруту підтвердження; методичка дозволяє адаптувати структуру, інваріанти зберігаються |
| M2 | Переходи: `confirmed → cancelled` (водій, ЛР2/ЛР3), `confirmed → expired` (кінець інтервалу без в'їзду, ЛР4), `confirmed → entered` — обчислюється з в'їздів DPC, у БД не пишеться | рішення B3 |
| M3 | Година — `timestamptz` початку години; `from_hour` — ISO 8601 зі зміщенням, рівно початок години, інакше 422; інтервал `[from_hour, from_hour + hours)`, `hours` 1..24 | картка: «години [початок, початок + N)» |
| M4 | `day` у `GET /v1/levels/{id}/hours` — календарна дата в зоні `garage.tz`; у дні переходу на літній/зимовий час 23 або 25 годин | момент і назва зони зберігаються окремо (О1.2) |
| M5 | Гроші — `bigint` копійок + `currency char(3)`; ціна = тариф × `hours`; тариф на момент бронювання копіюється в бронювання | неповна година = повна; тариф змінює DPC (ЛР6) |
| M6 | Рядки `level_hour` створюються ліниво в транзакції бронювання (`free = level.spots`, `ON CONFLICT DO NOTHING`); година без рядка має `free = spots` | не треба заповнювати календар наперед |
| M7 | Блокування годин `SELECT … ORDER BY hour FOR UPDATE` в одному порядку, потім `UPDATE … free = free - 1 … AND free > 0` з перевіркою кількості змінених рядків | інваріанти 1 і 2; немає взаємоблокувань перетинних багатогодинних бронювань |
| M8 | Закритий рівень (`level.closed`) — 409 `level-closed` для нових бронювань; маршрут закриття — ЛР3 | рішення B8 |
| M9 | До ЛР3 користувач — заголовок `X-User-Id` заздалегідь створеного користувача; власник бронювання береться лише з нього | методичка ЛР1 |
| M10 | Каталог (`GET /v1/garages`, `GET /v1/levels/{id}/hours`) доступний без входу; бронювання — лише для користувача | каталог — загальнодоступна інформація |
| M12 | `from_hour` не раніше за початок поточної години (поточна година дозволена), інакше 422; правило — чиста функція `rules.booking_start_error(from_hour, now)` | PC-1 (`docs/plan-changes.md`): в'їзд можливий лише в межах інтервалу, минула година марна |
| M11 | `INVARIANT_MODE=unprotected` (лише для досліду на тестових даних): читання без блокування й запис значення, обчисленого в застосунку (втрачене оновлення) | показати порушення інваріанта 1 для порівняння в ЛР2 |

## Модель Web ЛР2 (етап 2, 2026-11-05)

| № | Рішення | Причина |
|---|---|---|
| M13 | Ідемпотентність `POST /v1/bookings`: таблиця `idempotency_key` (PK `user_id, operation, key`), `request_hash` = sha256(метод + шаблон шляху + канонічний JSON тіла, як надіслано); стан «в обробці» — `pg_try_advisory_xact_lock` транзакції (не взято → 409 `idempotency-in-progress`, `Retry-After: 1`), рядок лише `completed`; зберігаються 201, 404, 409, не зберігаються 400/422; повтор — збережена відповідь + `Idempotent-Replayed: true`; строк 24 год | ключ і бронювання фіксуються одним COMMIT; блокування звільняється саме при аварії, тож «завислого» стану немає |
| M14 | `INVARIANT_MODE=unprotected` діє лише на рівень `UNPROTECTED_LEVEL` (типово `race-test`, міграція 902); перемикання — `INVARIANT_MODE=unprotected docker compose up -d app-a app-b`, повернення — `docker compose up -d app-a app-b` | небезпечний режим не може зіпсувати години демо-рівнів; змінна вже є в `compose.yaml` і `.env` |
| M15 | Правило часу (PC-1) перевіряється всередині транзакції ключа | повтор після того, як година минула, отримує збережений 201, а не 422 |
| M16 | `PATCH /v1/bookings/{id}` змінює лише `plate` (інші поля → 422, `extra="forbid"`); `ETag` = `"<version>"`; одна інструкція `UPDATE … AND version = If-Match AND status = 'confirmed'`; 0 рядків → 404 / 412 / 409 за поточним станом; без `If-Match` → 428; ключ ідемпотентності не потрібен | повтор захищає версія; 412 повертає поточний `ETag` |
| M17 | `POST /v1/bookings/{id}/cancel`: рядок бронювання `FOR UPDATE`, потім години `ORDER BY hour FOR UPDATE`; `confirmed` і `from_hour > now()` → `cancelled`, `version + 1`, `free + 1`; уже `cancelled` → 200 з поточним станом; почалося → 409 `booking-started`; ключ не потрібен | повтор безпечний за станом; той самий порядок блокувань годин, що й у бронюванні |
| M18 | Клієнт: ключ — `crypto.randomUUID()` на натискання «Book», той самий у повторах і «Try again», новий при зміні години/рівня/номера; повтор лише без відповіді (тайм-аут 10 с), 502/503/504, 409 `idempotency-in-progress`; повний jitter base 0.5 с, cap 8 с, не менше `Retry-After`, ≤ 5 спроб, ≤ 20 с очікування; далі «We couldn't confirm your booking» (результат невідомий) + «Check my bookings» | О2.4; інші 4xx — остаточна відповідь |
| M19 | Очищення прострочених ключів — фоновий цикл у процесі `publisher` (раз на `IDEMPOTENCY_CLEANUP_S` = 60 с, порції `IDEMPOTENCY_CLEANUP_BATCH` = 1000, `DELETE … WHERE ctid IN (SELECT … LIMIT n)`); outbox-частина `publisher` лишається заглушкою до ЛР4 | короткі транзакції без довгих блокувань; окремий процес, не API |

## Модель DPC ЛР1 (етап 3, 2026-11-05)

| № | Рішення | Причина |
|---|---|---|
| P1 | `garage_state.spots` = сума `level.spots` паркінгу **без** тестового рівня `race-test` (Web ЛР2): `mall` = 81, `center` = 20; в'їзд за бронюванням рівня `race-test` → `booking-invalid`. Окремий тестовий паркінг `dpc-race` (5 місць, без рівнів і тарифу, тож каталог Web його не показує) у міграції 903 | `race-test` існує лише для досліду Web і містить навмисні перевищення; невраховані місця не можна займати |
| P2 | Ресурс `garage_state(garage_id, spots, occupied)`, ефект `entry` (PK `event_id`, `UNIQUE (booking_id) WHERE direction = 'in'`), запит `entry_request` (`event_id UNIQUE`, стани `queued → processing → done / rejected / failed`). Інваріант: `occupied` = в'їзди мінус виїзди з `entry` і `0 ≤ occupied ≤ spots`; `GET /v1/garage/{id}` повертає `invariant_ok` | картка C09, розділи 1-2 |
| P3 | Без бронювання: `occupied + reserved < spots`, де `reserved` - `confirmed`-бронювання рівнів паркінгу (крім P1), інтервал яких містить `event_time`, без прийнятого в'їзду. За бронюванням: бронювання існує, `confirmed`, номер збігається (без пробілів і дефісів, великими літерами), `event_time` в інтервалі, рівень у цьому паркінгу, в'їзду за ним ще не було (`already-entered`); квоту не перевіряє (B4), але `occupied < spots` (PC-4). Номер уже всередині - `already-inside`; виїзд лише якщо номер усередині (`not-inside`) | B4, B6, B7; таблицю `booking` DPC лише читає (B2) |
| P4 | Критична секція - транзакція PostgreSQL: `SELECT … FROM garage_state … FOR UPDATE` → правило P3 → `INSERT entry` → `UPDATE garage_state SET occupied = occupied ± 1` → `entry_request` у кінцевий стан → COMMIT. Розпізнавання номера (`asyncio.sleep` 100-500 мс, генератор із `SEED`) виконується до транзакції | блокування рядка працює для будь-якої кількості процесів (ЛР3 - Ray); повільний виклик поза секцією |
| P5 | Черга `asyncio.Queue(maxsize=QUEUE_CAPACITY)`, рівно `WORKERS` задач-обробників у `lifespan`; місце в черзі резервується до `INSERT` (`qsize + reserving < capacity`), тому `put_nowait` не падає; повна черга → 429 `Retry-After: 1`, запит не зберігається | обробник у `await queue.get()` чекає на future циклу подій без CPU |
| P6 | Повтор того самого `event_id` з тими самими полями → 202 з тим самим `Location` і `Repeated-Request: true`, без другого запиту в черзі; з іншими полями → 409 `event-id-reused` | повторна передача від Web (ЛР4) і генератора; методичка 1.10 |
| P7 | Невалідне тіло → **400** `invalid-request` (обробник каркаса `problems.py`, приклад контракту), а не 422 з плану етапу | слідуємо коду каркаса |
| P8 | `event_time` - ISO 8601 UTC з `Z` і рівно трьома знаками мілісекунд; зберігається як `timestamptz` | методичка DPC 1.5 |
| P9 | `/metrics`: `queue_length`, `in_flight` (запити в обробниках), `rejected` (429), `rejected_rule` (стан `rejected`), `rejected_draining` (503); лічильник HTTP-запитів каркаса перейменовано на `http_in_flight`, щоб ряд `in_flight` у Prometheus не дублювався | О1.5, `checks.yaml` |
| P10 | Позначки часу `time.monotonic_ns()` у пам'яті процесу (останні 100 000 запитів): надходження, початок і кінець оброблення, перший GET кінцевого стану; `GET /v1/entries/{id}` повертає `queue_wait_ms`, `processing_ms`, `total_ms`, `delivery_wait_ms`, `marks_ms` | О1.5 |
| P11 | SIGTERM (Д1.4): власний обробник ставить `draining` (нові `POST /v1/entries` → 503 `Retry-After: 5`), чекає `queue.join()`, пише `api.drained count=N`, потім викликає обробник uvicorn; далі `lifespan` зупиняє обробники й закриває пул | uvicorn після свого сигналу перестає приймати з'єднання, тож 503 можливий лише поки HTTP ще обслуговується |
| P12 | Під час старту запити `queued`/`processing` попереднього процесу позначаються `failed` / `lost-on-restart` | черга в пам'яті; кожен збережений запит доходить до кінцевого стану |
| P13 | `RESOURCE_MODE=unsafe` (синонім `unprotected` каркаса) діє лише на `UNSAFE_GARAGE` (`dpc-race`): читання без блокування, пауза 50 мс, запис обчисленого значення | небезпечний режим лише на тестових даних |
| P14 | Відмови 429/503 на `POST /v1/entries` дає зовнішній шар ASGI `entries.Admission` до маршрутизації FastAPI (PC-6); вони рахуються в `/metrics`, але не мають рядка журналу | `/health` p95 ≤ 100 мс при потоці відмов |
| P15 | Сторінка клієнта «Barrier» звертається до DPC api `:8000` напряму з токеном `VITE_DPC_TOKEN`; повтор після 429 - з тим самим `event_id` | CORS `DPC_CLIENT_ORIGIN` у каркасі |

## Модель DPC ЛР2 (етап 4, 2026-11-07)

| № | Рішення | Причина |
|---|---|---|
| P16 | Функція картки `O(t) = 16·Omax/T⁴ · t²(T−t)²`, `T = 14`; `Omax = 81` - навчальне припущення (місткість `mall` у демоданих), параметр `omax` тіла необов'язковий. Інтеграл - машино-години, × тариф - виручка. Еталон `F(b) − F(a)` у `fractions.Fraction`, на `[0; 14]` = 604.8. Проміжок обмежено `0 ≤ a < b ≤ 14` | модель визначена лише на робочому дні; точний еталон не додає власної похибки округлення |
| P17 | `[0, n)` → `p` неперервних діапазонів `[lo, hi)` (розміри різняться ≤ 1); вузол `i` належить діапазону з `i`, вузол `n` - лише останній частині (`Task.last`), поправки ваг кінців - у частинах із вузлами `0` і `n`. Частини рахують блоками numpy без множника `C·h`, головний процес множить один раз | ваги кінців рівно один раз і при `n < p` (порожні діапазони); вада «порожня частина теж додає вузол n» знайдена тестом на `[2.5; 9]` і виправлена до коміту |
| P18 | Одночасно один розрахунок: перевірка й встановлення `state.running` без `await` між ними; інший `POST` → 429 `compute-busy`, `Retry-After: 2` (`COMPUTE_RETRY_AFTER_S`), не стає в чергу. Розрахунки - у пам'яті (останні `COMPUTE_KEEP` = 100), `GET /v1/compute?limit=` для клієнта | пул на всі 4 CPU; другий пул лише сповільнив би обидва, десятки пулів вичерпали б пам'ять |
| P19 | Пул `multiprocessing` контексту `forkserver` (типовий у Python 3.14), новий на кожен розрахунок, preload `app.integrate` і numpy; створення й очікування - у потоці `run_in_executor`; `os.nice(COMPUTE_NICE = 10)` у процесах пулу. Типовий блок 10⁶ (`local`) і 1000 (`none`, `lock`), `block` у тілі 1000-10⁷; `n ≤ 10⁹` | цикл подій і черга ЛР1 не блокуються (`/health` p95 8.0 мс під p = 4); fork з процесу з потоками небезпечний; блок 10⁴ швидший (дослід О2.3), але типовий лишено з плану |
| P20 | `mode`: `local` (типово), `lock` (`Value('d')` + `get_lock()`), `none` (`Value('d', lock=False)`, `total.value += s` після кожного блоку). Спільне значення передається через `initializer` пулу. Допуск - відносна похибка `1e-9` (`COMPUTE_TOLERANCE`, `checks.yaml`), результат має `within_tolerance`; невалідне тіло → 400 (як P7) | О2.4; обґрунтування допуску - О2.2 (README розділ 7) |

## Модель Web ЛР3 (етап 5, 2026-11-10)

| № | Рішення | Причина |
|---|---|---|
| M20 | Вхід - непрозорий токен у `Authorization: Bearer`: `secrets.token_urlsafe(32)` (43 символи), у таблиці `session` лише sha256 (hex), `expires_at = now() + SESSION_TTL_H` (8 год); перевірка `expires_at > now()` у кожному запиті; вихід видаляє рядок (повтор 204, без заголовка 401); `X-User-Id` не читається (M9 скасовано) | вхід на обох екземплярах без спільної пам'яті; вихід знищує запис на сервері; браузер не додає заголовок сам, тож CSRF неможливий |
| M21 | Пароль - argon2id (`argon2-cffi`, типові параметри m = 64 МіБ, t = 3, p = 4) у `app_user.password_hash`; типове значення `'!'` (не хеш) - вхід неможливий; невідомий логін перевіряється з фіктивним хешем тих самих параметрів; однакова 401 `invalid-credentials`; перевірка в потоці, не більше `LOGIN_CONCURRENCY` = 4 на процес | однакова відповідь за тілом і часом; цикл подій не блокується; сплеск входів не вичерпує пам'ять |
| M22 | Відповідь входу - як у прикладі комплекту: `access_token`, `token_type: Bearer`, `expires_at` (UTC, `Z`) і `user {id, login, name, role}`; `GET /v1/me` - `{id, login, name, role}`, `name` = `login` | службові маршрути однакові для всіх тем (PC-11) |
| M23 | Власник - частина кожного SQL бронювання (`WHERE id = %s AND user_id = %s`, список `WHERE user_id = %s`); чуже й відсутнє - той самий 404 `booking-not-found` для GET, PATCH, cancel | немає окремого кроку перевірки, який можна пропустити; 403 підтвердив би існування id |
| M24 | `POST /v1/levels/{id}/close` (`x-course: staff-action`) і `/open` - залежність `StaffUser`: роль перевіряється до будь-якої дії (user → 403 і для неіснуючого рівня), потім один `UPDATE level SET closed`; повтор 200; неіснуючий для staff 404. Закриття діє лише на нові бронювання (B8) | функція персоналу картки; відповідь user не розкриває каталог |
| M25 | CORS: рівно `CLIENT_ORIGIN`, без `*` і `allow_credentials`; методи GET, POST, PATCH, OPTIONS; заголовки `Authorization, Content-Type, Idempotency-Key, If-Match, X-Request-ID`; expose `Location, ETag, Retry-After, X-Request-ID, Idempotent-Replayed, X-Instance`; preflight обробляє `CORSMiddleware` (200; стороннє походження 400 без `Access-Control-Allow-Origin`). Перевірки `Sec-Fetch-Site`/`Origin` немає | cookie немає, тож запит стороннього сайту приходить без особи й отримує 401 |
| M26 | Клієнт тримає сесію в пам'яті модуля `api.ts` і в `sessionStorage` (ключ `session`); 401 на запит із токеном стирає сесію й показує сторінку входу; сторінки DPC працюють без входу Web. Прострочені сесії видаляє той самий цикл `publisher`, що й ключі ідемпотентності (`IDEMPOTENCY_CLEANUP_S`, порції `IDEMPOTENCY_CLEANUP_BATCH`) | переживає перезавантаження вкладки, не інші вкладки; ризик XSS описано в README |

## Модель Web ЛР4 (етап 6, 2026-11-11)

| № | Рішення | Причина |
|---|---|---|
| M27 | `POST /v1/bookings/{id}/entries` без тіла, `Idempotency-Key` обов'язковий (`operation = 'entry'`, хеш - метод, шаблон шляху, id бронювання); одна транзакція: рядок бронювання `FOR UPDATE`, перевірки (404 чуже; 409 `booking-not-active`, `already-entered`, `entry-in-progress`, `outside-interval` за чистою функцією `rules.can_enter(from_hour, hours, now)`), `operation` `pending` і `outbox` `entry.requested`; 202 + `Location` + `Retry-After: 2`; 202 зберігається в ключі (повтор - та сама операція). Відповідь операції має `state` і `status` | О4.1; PC-13, PC-14 |
| M28 | Події - конверт CloudEvents 1.0 (`source: parking-web`, `subject` = id бронювання, `time` UTC мс Z, `data.entity_id`/`entity_version`), повністю в `outbox.payload`; тема з `outbox.topic` (`domain-events`), ключ = id бронювання; типи `booking.created` (транзакція бронювання), `entry.requested`, `entry.opened` | О4.2, Д4.1; повторна публікація зберігає id |
| M29 | Публікатор: порція `ORDER BY seq LIMIT 100 FOR UPDATE SKIP LOCKED` → `fault("publisher_before_send")` → `send_and_wait` кожного (acks=all, ідемпотентний продюсер) → `published_at = now()` → COMMIT; пауза 200 мс при порожній порції; збій БД або брокера - нічого не позначено, пауза 1 с | О4.2; PC-16 |
| M30 | Обробник (група `worker`): `processed_message` → `running` → симулятор (`Idempotency-Key` = id операції, тайм-аут 10 с) → `fault("after_external_before_commit")` → одна транзакція: `processed_message` (`ON CONFLICT DO NOTHING`, інакше нічого), операція `succeeded` (`result {opened_at, effect_id}`, `opened_at` = `done_at` симулятора) або `failed` (`error` = причина), сповіщення, `entry.opened`; offset - після COMMIT; 503/тайм-аут/мережа - повтор без межі | О4.3; PC-15 |
| M31 | Сповіщення лише через `notifications.add_notification` у транзакції причини: рядок `notification_seq` `FOR UPDATE`, перевірка `(user_id, source_event_id)`, позиція = лічильник + 1; повтор події не займає позицію | порядок позицій = порядок фіксацій без пропусків (О5.3) |
| M32 | Relay (`python -m app.relay`, група `relay`): `entry.opened` → `POST {DPC_URL}/v1/entries` (`event_id` = id операції, `direction: in`, `event_time` = `opened_at`, `booking_id`), токен `DPC_TOKEN` = `API_TOKEN`; 202 → offset; 429/5xx/мережа → повтор із `Retry-After`; інший 4xx → `relay.refused`, offset | Web не пише в таблиці DPC (B2); повтор безпечний за P6 |
| M33 | Стан `entered` - `LEFT JOIN entry e ON e.booking_id = b.id AND e.direction = 'in'` у кожному читанні бронювання (об'єкт і список); у базі `confirmed` | B3; унікальний індекс DPC не множить рядки, курсор той самий |
| M34 | Клієнт: «Open barrier» на картці бронювання, коли зараз у його інтервалі; ключ `crypto.randomUUID()` на натискання (повтори POST - `withRetries` із тим самим ключем); опитування `Location` з паузою `Retry-After`; `entry-in-progress` → стежити за наявною операцією; після `succeeded` - читання бронювання раз на 2 с до `entered` (до 15 разів) | О4.5; як M18 |
