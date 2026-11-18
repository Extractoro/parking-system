# C09 «Паркінги» - спільний проєкт курсів «Обмін даними у Web-застосунках» і «Розподілені та паралельні обчислення»

Структура - за `README-template.md` обох курсів. Опис наданих каркасів - [web/KIT-README.md](web/KIT-README.md),
[dpc/KIT-README.md](dpc/KIT-README.md). Повні результати й механізм кожної ЛР - `results/<частина>-lrN/README.md`.

## 1. Курс і тема

| | |
|---|---|
| Курси | «Обмін даними у Web-застосунках» (Web) і «Розподілені та паралельні обчислення» (DPC) |
| Тема | C09 «Паркінги» (спільна тема Web + DPC) |
| Команда | Вадим, Дарина |

## 2. Проєкт

Водій бронює місце на рівні паркінгу на кілька годин; шлагбаум відкривається за бронюванням.
Головне навантаження - вечір п'ятниці біля торгового центру: багато одночасних бронювань останніх місць.

- **Ролі:** `user` - водій (бачить лише свої бронювання); `staff` - оператор (закриває рівень).
- **Головна операція:** `POST /v1/bookings` - усі години й бронювання в одній транзакції, 201 після фіксації.
- **Інваріанти:** у кожну годину бронювань на рівні не більше, ніж місць (`level_hour.free >= 0`);
  бронювання кількох годин займає всі години або жодної.
- **Гроші й час:** копійки `bigint`, неповна година - повна, тариф копіюється в бронювання;
  `timestamptz`, зона - `garage.tz` (`Europe/Kyiv`).
- **Зовнішня система** (ЛР4) - симулятор шлагбаума `POST /open`: 1-3 с, 10 % відмов 503.
- **Зміна ЛР6** - місця із зарядкою; старий клієнт без типу бронює звичайне місце.

```mermaid
stateDiagram-v2
    [*] --> confirmed: POST /v1/bookings
    confirmed --> cancelled: водій скасовує
    confirmed --> entered: в'їзд (з таблиці в'їздів DPC)
    confirmed --> expired: інтервал минув без в'їзду
```

`held` з картки не використовується: окремого підтвердження немає. `entered` у базі не зберігається -
Web обчислює його з в'їздів DPC. Схема даних - `migrations/`, рішення - [docs/decisions.md](docs/decisions.md).

**Сутності (Web).**

| Сутність | Таблиця | Що зберігає |
|---|---|---|
| Паркінг | `garage(id, name, tz)` | назва й часова зона IANA |
| Рівень | `level(id, garage_id, name, spots, closed)` | кількість місць; закритий рівень не приймає нових бронювань |
| Тариф | `tariff(garage_id, minor_per_hour, currency)` | копійок за годину; **власник - DPC** (ЛР6), Web лише читає |
| Година рівня | `level_hour(level_id, hour, free)` | вільні місця в кожну годину |
| Бронювання | `booking(id, user_id, level_id, plate, from_hour, hours, tariff_minor_per_hour, price_minor, currency, status, version, created_at, expires_at)` | головна сутність |
| Користувач | `app_user(id, login, role, password_hash)` | роль `user` / `staff`, хеш пароля argon2id (ЛР3) |
| Сесія входу | `session(token_hash, user_id, created_at, expires_at)` | sha256 токена, строк 8 годин (ЛР3) |


**Числові параметри (навчальні припущення).**

| Параметр | Значення |
|---|---|
| Паркінги | `mall` «South Mall», `center` «City Centre», зона `Europe/Kyiv` (назви англійською, як в інтерфейсі клієнта) |
| Рівні | `mall-1` 40 місць, `mall-2` 40, `mall-one` 1 (для досліду гонки), `center-1` 20 |
| Тариф | `mall` 3000 коп./год, `center` 4000 коп./год, UAH |
| Тривалість бронювання | 1-24 години |
| Номер авто | текст 2-12 символів |
| Сторінка списку | `limit` 1-50, типово 20 |
| Користувачі (вхід ЛР3) | `u-1` user1 / `user1-pass`, `u-2` user2 / `user2-pass` (user), `s-1` staff1 / `staff1-pass` (staff) |
| Сесія входу | 8 годин (`SESSION_TTL_H`) |

## 3. Архітектура

```mermaid
flowchart LR
    C["Клієнт React + TS (Vite :5173)"] -->|HTTP, Bearer| N["nginx :8080"]
    N --> A1["app-a"] & A2["app-b"]
    A1 & A2 -->|транзакції, outbox| PG[("PostgreSQL")]
    P["publisher"] -->|SKIP LOCKED| PG
    P -->|domain-events| K[["Kafka"]]
    K -->|група worker| W["worker"] -->|POST /open| S["external-sim"]
    W -->|результат, outbox| PG
    K -->|група relay| R["relay"] -->|POST /v1/entries| D["DPC api :8000"]
    C -->|Barrier, Compute| D
    D -->|задачі process_entry| RAY["Ray: ray-head, ray-worker ×2"]
    RAY -->|POST /internal/effects| D
    D -->|черга, приймач ефектів, процеси| PG
    C -->|Report| D
    D -->|POST /jobs| SR["spark-runner"] --> SW["spark-worker ×2"]
    SW & D -->|Parquet, звіт| V[("том data")]
```

### Спільний проєкт

Спільні лише PostgreSQL (одна база, спільна нумерація `migrations/`) і Kafka. Кожну таблицю змінює одна частина.

| Що | Хто змінює | Хто читає |
|---|---|---|
| каталог, `booking`, `level_hour`, користувачі, операції, outbox | Web | DPC читає `booking`, `level` у правилі в'їзду |
| зайнятість `garage_state`, `entry_request`, `entry`, `effect` (ЛР3) | DPC | Web обчислює `entered` з `entry` |
| `tariff` | DPC (ЛР6) | Web копіює в бронювання |

- **Web → DPC:** `relay` читає `entry.opened` з `domain-events` і надсилає `POST /v1/entries`
  (`event_id` = id операції, `booking_id`, `Idempotency-Key`). 202 - offset підтверджено; 429/5xx/мережа -
  повтор з паузою 1-8 с; інший 4xx остаточний.
- **Повтори:** той самий `event_id` DPC повертає тим самим запитом; другий в'їзд за бронюванням забороняє
  `UNIQUE (booking_id) WHERE direction = 'in'`.
- **Наскрізний приклад:** бронювання → «Open barrier» → операція `succeeded` → relay → в'їзд DPC `done` →
  бронювання `entered` у клієнті (`results/web-lr4/e2e.txt`). DPC зупинено на 20 с - relay дочекався,
  в'їзд записано один раз (`dpcdown.txt`).

## 4. Структура репозиторію

| Шлях | Призначення |
|---|---|
| `compose.yaml`, `.env.example` | спільний стенд обох частин |
| `migrations/` | спільні міграції PostgreSQL |
| `web/server/`, `web/openapi.yaml`, `web/asyncapi.yaml` | сервер Web і контракти |
| `web/client/` | клієнт React + TypeScript (поза Compose) |
| `dpc/api/`, `dpc/openapi.yaml` | сервіс DPC і контракт |
| `experiments/` | сценарії дослідів |
| `results/` | виводи перевірок і дослідів за ЛР |
| `docs/` | рішення, зміни плану, вимоги й стан пунктів |

## 5. Збирання й запуск

Потрібно: Docker Compose v2, ≥10 ГБ пам'яті для Docker (повний набір ≈3.9 ГіБ без навантаження),
`uv` для тестів і сценаріїв, Node.js 22 для клієнта. Усі команди - з кореня репозиторію.

```bash
cp .env.example .env
docker compose build app-a api coordinator-a spark-master external-sim   # по одному сервісу на образ
docker compose up -d
```

Для однієї ЛР досить її сервісів:

| ЛР | Команда |
|---|---|
| Web ЛР1-ЛР3 | `docker compose up -d nginx publisher` |
| Web ЛР4 | `docker compose up -d nginx publisher worker relay external-sim api` |
| Web ЛР5 | `docker compose up -d nginx publisher worker relay external-sim gateway-a gateway-b` |
| DPC ЛР1-ЛР2 | `docker compose up -d api` |
| DPC ЛР3 | `docker compose up -d api ray-head ray-worker` |
| DPC ЛР4 | `docker compose up -d api spark-master spark-worker-1 spark-worker-2 spark-runner`; дані - `docker compose run --rm generator` |

Код задач Ray виконують `ray-head` і `ray-worker` з образу `api`: після зміни `dpc/api/app` -
`docker compose up -d --build api ray-head ray-worker`.

Тести (проти запущеного стенда):

```bash
uv run --no-project --python 3.13 --with pytest --with httpx --with "psycopg[binary]" --with numpy --with pyarrow --with websockets pytest web/server/tests dpc/api/tests -v
```

Клієнт: `cd web/client && cp .env.example .env.local && npm install && npm run dev` → http://localhost:5173.

Адреси: Web API http://localhost:8080 (app-a :8001, app-b :8002), DPC api http://localhost:8000, Ray Dashboard :8265,
симулятор :8090, PostgreSQL :5432 (`app`/`app`), Kafka з хоста :9094, Kafka UI :8085 (профіль `observability`).
У Git Bash перед `docker compose exec` задайте `MSYS_NO_PATHCONV=1`.

## 6. Лабораторні роботи

Стан пунктів - [docs/requirements-web.md](docs/requirements-web.md), [docs/requirements-dpc.md](docs/requirements-dpc.md);
зміни плану - [docs/plan-changes.md](docs/plan-changes.md).

### Web ЛР1. Контракт, модель і перша операція

| Пункт | Де реалізовано | Перевірка та свідчення |
|---|---|---|
| О1.1 Модель | розділ 2, `docs/decisions.md` | діаграма станів, інваріанти |
| О1.2 Схема БД | `migrations/001`-`003`, `900`, `901` | PK/FK, CHECK `free >= 0`, застосування з нуля |
| О1.3 Контракт | `web/openapi.yaml` | lint - 0 помилок (`results/web-lr1/openapi-lint.txt`) |
| О1.4 Головна операція | `web/server/app/bookings.py` | 201 + `Location`; 409 без зміни годин (`test_lr1.py`) |
| О1.5 Список із курсором | `bookings.py`, `cursor.py` | 7 бронювань по 3 - без пропусків і повторів |
| О1.6 `/health`, журнал | каркас | `results/web-lr1/log-lines.txt` |
| О1.7 Клієнт | `web/client/` | бронювання, 409 як Problem Details |
| Д1.1 Правило за часом | `web/server/app/rules.py` | минула година → 422 (`test_rules.py`) |

### Web ЛР2. Конкуренція та повторення запитів

| Пункт | Де реалізовано | Перевірка та свідчення |
|---|---|---|
| О2.1 Інваріант | `bookings.py` (`_reserve_hours`): `FOR UPDATE` годин у порядку | «Останнє місце», 10×20 запитів: захист - 0 перевищень, без захисту - 34 години понад місця |
| О2.2 Ідемпотентність | `web/server/app/idempotency.py`, `migrations/004` | повтор → той самий `id`; інше тіло → 422; два одночасні → одне бронювання |
| О2.3 Втрачена відповідь | `fault("after_commit_before_response")` | app-a без відповіді, повтор на app-b → той самий `id`, `free` змінено один раз |
| О2.4 Повтори в клієнті | `web/client/src/retry.ts` | після мережевої помилки одне бронювання |
| Д2.4 Умовна зміна | `PATCH` + `ETag`/`If-Match` | 428, 200, 412 (`test_lr2.py::test_7`) |
| Д2.5 Повторне скасування | `POST …/cancel` | 5 одночасних → `free` +1 один раз |

Блокування рядків обрано замість SERIALIZABLE, щоб не повторювати помилки 40001 під гонкою.
Докладно: [results/web-lr2/README.md](results/web-lr2/README.md).

### Web ЛР3. Вхід і доступ

| Пункт | Де реалізовано | Перевірка та свідчення |
|---|---|---|
| О3.1 Вхід | `web/server/app/auth.py`, `migrations/006` | токен з app-a діє на app-b; після виходу 401 на обох; невідомий логін і неправильний пароль - однакові |
| О3.2 Доступ до об'єктів | власник у кожному SQL; `POST /v1/levels/{id}/close` лише `staff` | чуже бронювання → 404 як неіснуюче; user → 403 |
| О3.3 Браузерні запити | CORS рівно `CLIENT_ORIGIN`, `web/tools/evil-origin/` | сторонній сайт не читає відповідь, форма → 401 |
| О3.4 Клієнт | `LoginPage.tsx`, `AccessPage.tsx` | вхід, 404 на чуже, закриття рівня персоналом |

Непрозорий токен у `Authorization` (sha256 у таблиці `session`, 8 год) замість JWT і cookie: вихід діє
одразу, CSRF неможливий. Пароль - argon2id. Докладно: [results/web-lr3/README.md](results/web-lr3/README.md).

### Web ЛР4. Довгі операції, події й зовнішня система

Шлях операції «відкрити шлагбаум»:

```mermaid
sequenceDiagram
    participant C as Клієнт
    participant A as app-a / app-b
    participant DB as PostgreSQL
    participant P as publisher
    participant K as Kafka domain-events
    participant W as worker
    participant S as external-sim /open
    participant R as relay
    participant D as DPC api
    C->>A: POST /v1/bookings/{id}/entries (Idempotency-Key)
    A->>DB: BEGIN; booking FOR UPDATE; operation pending; outbox entry.requested; COMMIT
    A-->>C: 202, Location /v1/operations/{op}, Retry-After 2
    P->>DB: SELECT ... FOR UPDATE SKIP LOCKED
    P->>K: send (acks=all, key = booking id)
    P->>DB: published_at = now(); COMMIT
    K->>W: entry.requested (група worker)
    W->>DB: operation running
    W->>S: POST /open, Idempotency-Key = op id, тайм-аут 10 с
    S-->>W: accepted / rejected (503 - повтор)
    W->>DB: BEGIN; processed_message; operation succeeded|failed; notification; outbox entry.opened; COMMIT
    W->>K: commit offset
    P->>K: entry.opened
    K->>R: entry.opened (група relay)
    R->>D: POST /v1/entries, event_id = op id, booking_id
    D-->>R: 202 (повтор - той самий запит)
    C->>A: GET /v1/operations/{op} ... succeeded
    C->>A: GET /v1/bookings/{id} -> status entered (LEFT JOIN entry DPC)
```

| Тип | Де записується | `data` |
|---|---|---|
| `booking.created` | транзакція `POST /v1/bookings` | `user_id`, `level_id`, `from_hour`, `hours` |
| `entry.requested` | транзакція `POST …/entries` | `operation_id`, `user_id`, `garage_id`, `plate` |
| `entry.opened` | транзакція результату обробника | `operation_id`, `user_id`, `garage_id`, `plate`, `opened_at`, `effect_id` |

| Пункт | Де реалізовано | Перевірка та свідчення |
|---|---|---|
| О4.1 Асинхронна операція | `web/server/app/operations.py`, `migrations/007` | 202 + `Location` + `Retry-After`; повтор ключа - та сама операція |
| О4.2 Outbox | `events.py`, `publisher.py` | події в транзакції операції, усі опубліковані |
| О4.3 Обробник | `worker.py`, `retrying.py` | `pending → running → succeeded`, ефект 1, сповіщення 1 |
| О4.4 Відмови | `fault("publisher_before_send")`, `fault("after_external_before_commit")` | після перезапуску - один ефект, одне сповіщення |
| О4.5 Клієнт | `BookingsPage.tsx` («Open barrier») | «Opened at …», бейдж Parked |
| Д4.1 CloudEvents 1.0 | `events.py`, `web/asyncapi.yaml` | конверт у Kafka UI, AsyncAPI valid |

Доставка подій - щонайменше один раз; повтори відсікає `processed_message` і ключ симулятора = id операції.
Докладно: [results/web-lr4/README.md](results/web-lr4/README.md).

### Web ЛР5. Сповіщення через WebSocket

Схема доставки:

```mermaid
flowchart LR
    C["причина: бронювання,<br>результат worker,<br>перевірка 15 хв"] --> N["add_notification:<br>notification + outbox<br>в одній транзакції"]
    N --> P["publisher"] --> K["Kafka<br>notifications<br>key = user id"]
    K -- "група gateway-a" --> GA["gateway-a :8091"]
    K -- "група gateway-b" --> GB["gateway-b :8092"]
    GA --> W1["з'єднання користувача<br>на gateway-a"]
    GB --> W2["з'єднання користувача<br>на gateway-b"]
```

| Пункт | Де реалізовано | Перевірка та свідчення |
|---|---|---|
| О5.1 Авторизоване підключення | `POST /v1/ws-tickets` (`tickets.py`, `migrations/009`); `gateway.py`: `Origin` і погашення квитка до `accept()` | повторний, прострочений, чужий Origin, без квитка → відмова (403 на рукостискання); вихід → 1008 лише для з'єднань цієї сесії (`auth.txt`) |
| О5.2 Доставка через два шлюзи | `notifications.py` (`notification.created` в outbox), `gateway.py` (група на шлюз, `user → з'єднання`) | клієнти одного користувача на :8091 і :8092 отримали ті самі `(id, position)`, інший користувач - нічого (`two.txt`) |
| О5.3 Стрічка й відновлення | `GET /v1/me/notifications?after=`, `/head`; алгоритм клієнта | сповіщення між підпискою й `head` застосовано раз; зупинка gateway-a → дочитано 3 пропущені; 50 паралельних → позиції 1..50, клієнт із розривами отримав кожне раз |
| О5.4 Клієнт сповіщень | `web/client/src/feed.ts`, `Notifications.tsx` | дзвіночок, список, тост, Live / Reconnecting…, пауза 1-30 с (рисунки `docs/reports/img/web-lr5-*.png`) |

Квиток - 32 випадкові байти, 30 с, одноразовий (у базі sha256); з'єднання належить сесії, для якої видано
квиток. Вихід в одній транзакції видаляє сесію і пише `session.revoked`: кожен шлюз закриває з'єднання цієї
сесії кодом 1008, інші сесії користувача лишаються. Відмова до прийняття - `close(1008)` до `accept()`, що
uvicorn віддає як HTTP 403 (PC-22).

Алгоритм клієнта: квиток → WebSocket (живі кадри в буфер) → `head` → історія від `last_applied` до `head` →
буфер без уже застосованих → живі кадри; позиція з розривом → спершу історія до неї; обрив → новий квиток
з паузою, що зростає. `last_applied` - у `sessionStorage` окремо для кожного користувача. Запис, зафіксований
пізніше, не губиться: позиції видає лічильник користувача під `FOR UPDATE`, тож порядок позицій = порядок
комітів, усе до `head` уже зафіксовано, а пізніший запис отримає позицію після `head`.

Керована відмова (картка C09): `docker compose stop gateway-a` → клієнти gateway-a бачать «Reconnecting…»,
за 2.5 с через nginx підключаються до gateway-b і дочитують пропущене з історії; `docker compose start gateway-a`.
Межі: історія безстрокова, застарілого курсора немає (Д5.8 не робили); кожен шлюз читає всю тему (без реєстру
Д5.1); «Місце звільнилося» не зроблено - у системі немає підписок на рівень.
Докладно: [results/web-lr5/README.md](results/web-lr5/README.md).

### Web ЛР6

_Заповнюється в ЛР6._

### DPC ЛР1. Сервіс із чергою та модель виконання

Ресурс - зайнятість паркінгу `garage_state(spots, occupied)`, ефект - рядок `entry`.

```mermaid
flowchart LR
    B[шлагбаум / клієнт Barrier] -- "POST /v1/entries" --> A[Admission: черга повна? 429]
    A --> R[маршрут: INSERT entry_request queued, put_nowait] -- 202 + Location --> B
    R --> Q[(asyncio.Queue, QUEUE_CAPACITY)]
    Q --> W1[обробник 1] & W2[обробник ...] & W4[обробник WORKERS]
    W1 --> S[розпізнавання 100-500 мс, поза секцією]
    S --> T["транзакція: SELECT garage_state FOR UPDATE, правило, INSERT entry, UPDATE occupied, done/rejected"]
    T --> P[(PostgreSQL)]
```

| Пункт | Де реалізовано | Перевірка та свідчення |
|---|---|---|
| О1.1 Приймання | `dpc/api/app/entries.py`, `migrations/005` | 202 + `Location` до оброблення |
| О1.2 Обмежена черга | `asyncio.Queue(QUEUE_CAPACITY)`, 429 до FastAPI | 396×202, 42263×429; `/health` p95 52.6 мс |
| О1.3 Пул обробників | рівно `WORKERS` задач | `in_flight` ≤ 4 |
| О1.4 Захист ресурсу | `SELECT … FOR UPDATE` у транзакції | без захисту 27 авто на 5 місць, із захистом 5 |
| О1.5 Час і метрики | `GET /v1/entries/{id}`, `/metrics` | відповідь 28 мс проти виконання 1647 мс (p50) |
| Д1.4 Завершення | SIGTERM → 503, дренаж черги | 50 запитів дооброблено за 4.0 с, втрат 0 |

Докладно: [results/dpc-lr1/README.md](results/dpc-lr1/README.md).

### DPC ЛР2. Паралельний чисельний розрахунок

Інтеграл заповненості паркінгу `O(t)` за день (машино-години); еталон - точна первісна.

```mermaid
flowchart LR
    C[клієнт Compute / сценарій] -- "POST /v1/compute" --> R["маршрут (цикл подій): перевірка, Job, 202"]
    R -- run_in_executor --> T["потік: split [0, n) на p діапазонів, Pool(p) forkserver"]
    T -- "Task(a, b, n, lo, hi, ...)" --> P1["процес 1: [0, n/p)"] & P2["процес ..."] & Pp["процес p: [.., n)"]
    P1 & P2 & Pp -- "блоки numpy по block вузлів" --> S1[локальна сума] & S2[...] & Sp[локальна сума]
    S1 & S2 & Sp -- "p чисел" --> J["головний процес: Σ, × C·h (h/3), похибка"]
    J --> G["GET /v1/compute/{id}"]
```

| Пункт | Де реалізовано | Перевірка та свідчення |
|---|---|---|
| О2.1 Паралельне інтегрування | `dpc/api/app/integrate.py`, `compute.py` | 1, 2, 4 процеси, 3 методи, відносна похибка ≤ 1e-9 |
| О2.2 Точність | `experiments/dpc_lr2_compute.py accuracy` | E(n)/E(2n) = 16 і 4 за теорією |
| О2.3 Прискорення | `… bench`, профіль `py-spy` | S₄ 1.76; блок 10⁴ замість 10⁶ - S₄ 2.12 (обмеження - пам'ять) |
| О2.4 Спільна сума | режими `none` / `lock` / `local` | без блокування 0/5 у допуску, з ним 5/5 |

Клієнт - сторінки «Barrier» і «Compute» (`web/client/src/`). Докладно: [results/dpc-lr2/README.md](results/dpc-lr2/README.md).

### DPC ЛР3. Розподілена обробка в Ray і відновлення

Оброблення в'їзду - задача Ray; ресурс змінює лише приймач ефектів api. `EXECUTION=ray` (типово) або `local` (пул ЛР1
для порівняння); правило допуску в обох режимах - одна функція `entries.admit`.

```mermaid
flowchart LR
    Q[(черга ЛР1, 50; повна - 429)] --> W["обробник: місце диспетчера (≤ IN_FLIGHT_LIMIT 8), потім запит"]
    W -- process_entry.remote --> T["задача Ray: розпізнавання 100-500 мс"]
    T -- "POST /internal/effects, ключ event_id" --> R["приймач: effect ON CONFLICT → FOR UPDATE → правило → entry, occupied, рішення"]
    T -- "fault(after_effect_before_ack), return" --> W
```

Приймач `POST /internal/effects` (той самий Bearer-токен; лише для задач Ray, в `openapi.yaml` не описаний) приймає
`effect_key` (= `event_id`), `request_id` і поля в'їзду. Перша доставка - транзакція зі зміною ресурсу й рішенням
`accepted`/`rejected`; повтор - збережене рішення з `repeated: true` без змін. Ресурс змінює приймач, а не задача:
лише він може в одній транзакції з ключем перевірити, що ефект ще не прийнято. Повтор задачі після смерті процесу -
`max_retries=3`, ключ той самий. Обробники черги не беруть запит, поки 8 задач не завершено, тож сповільнення не
подає нових задач без меж: надлишок чекає в черзі ЛР1 і отримує 429.

| Пункт | Де реалізовано | Перевірка та свідчення |
|---|---|---|
| О3.1 Задачі Ray | `dpc/api/app/raytasks.py`, диспетчер у `entries.py` | результати за готовністю; 42 тести в режимі ray, 19 у local |
| О3.2 Обмеження задач | `IN_FLIGHT_LIMIT`, `/metrics` `in_flight` | приймач +500 мс: max `in_flight` 8, задач у Ray ≤ 8, черга 50, 99.4 % 429 |
| О3.3 Приймання ефекту | `POST /internal/effects`, `migrations/008` | повтор і 8 одночасних доставок - один ефект; відмова зберігається |
| О3.4 Відмова після ефекту | `fault("after_effect_before_ack")` у задачі | `fault.hit`, спроба 0 FAILED, 1 FINISHED; `effect` 1, `entry` 1, `invariant_ok` true |

Регрес: О1.4 у режимі ray - `invariant_ok` true; наскрізний шлях Web ЛР4 - `entered`, в'їзд 1.
Докладно: [results/dpc-lr3/README.md](results/dpc-lr3/README.md).

### DPC ЛР4. Пакетний звіт у Spark

Звіт картки: сесії, середня тривалість стоянки й виручка за паркінгами; гарячий ключ - `mall` (45 % рядків).
Стенд: `docker compose up -d api spark-master spark-worker-1 spark-worker-2 spark-runner`; дані - у томі `data`:

```bash
docker compose run --rm generator                                                 # small: 1e5 рядків, seed 909
docker compose run --rm generator --out /data/c09/big --rows 10000000 --jobs 4    # big: 1e7 рядків
```

```mermaid
flowchart LR
    A["api: POST /v1/reports/main/refresh"] -->|POST /jobs| R["spark-runner (драйвер)"]
    R --> W["spark-worker-1, -2: Parquet → join garages → groupBy garage_id"]
    R -->|Parquet + rows.json| N["/data/reports/runs/{run}"] -->|"tmp + os.replace"| P["/data/reports/current"]
    G["api: GET /v1/reports/main"] --> P
    F["api: GET .../reference (pyarrow, без Spark)"] --> P
```

| Набір | Рядків | Розмір фактів | seed | Частка `mall` |
|---|---|---|---|---|
| small | 10⁵ | 2.7 МБ | 909 | 0.4471 |
| big | 10⁷ | 252 МБ, 49 файлів | 909 | 0.4502 |

Задача `dpc/spark/jobs/report.py`: факти `history` з'єднуються з довідником `garages` і групуються за
`(garage_id, name)`; лічильники й суми - `bigint` (гроші - копійки), `avg_minutes = total_minutes / sessions`.
Еталон `dpc/api/app/reference.py` читає ті самі файли `pyarrow` порціями й додає рядки в словник цілих Python;
порівняння - за `garage_id`, цілі точно, середнє в межах 1e-9. Запуск пише новий каталог і лише потім одним
`os.replace` замінює вказівник, тому невдалий запуск не змінює опублікованого звіту. Типово довідник розсилається
(broadcast join, факти не перетасовуються); `--join shuffle` вимикає розсилання для досліду перекосу.

| Пункт | Де реалізовано | Перевірка та свідчення |
|---|---|---|
| О4.1 Дані | `dpc/tools/generator/spec.c09.json`, `columns.c09.py`, сервіс `generator` | таблиця вище; повторна генерація small - ті самі SHA-256 |
| О4.2 Звіт через API | `report.py`, `dpc/api/app/reports.py`, сторінка Report клієнта | refresh 202, повторний - 409 `report-running`; 10 рядків за 28 с |
| О4.3 Еталон | `reference.py`, `GET /v1/reports/main/reference` | small і big: 0 розбіжностей (`test_lr4.py`, `dpc_lr4_compare.py`) |
| О4.4 План і перекіс | `--join shuffle`, `--aqe`, пороги skew join, `plan-*.txt` | частина `mall` - 23 МБ із 54; AQE: стадія з'єднання 17.9 → 8.3 с, найдовша задача 13.5 → 5.0 с, звіт = еталон |
| О4.5 Втрата виконавця | `dpc_lr4.py executor` | kill `spark-worker-1` на 41-й с: 2 задачі `ExecutorLostFailure` повторено на worker-2, `done`, звіт = еталон |
| Д4.3 Атомарна публікація | `report.py` (`publish`) | запуск з неіснуючим шляхом - `failed`, `current` побайтно той самий |

Досліди О4.4-О4.5 виконувались лише з сервісами ЛР4 (PC-28). Докладно: [results/dpc-lr4/README.md](results/dpc-lr4/README.md).

### DPC ЛР5-ЛР6

_Заповнюється в ЛР5-ЛР6._

## 7. Досліди

Сценарії - з кореня: `uv run --no-project --python 3.13 --with httpx --with "psycopg[binary]" python experiments/<файл>.py`.

| Дослід | Команда | Результат |
|---|---|---|
| Останнє місце (Web О2.1) | `web_last_spot.py`; без захисту - `INVARIANT_MODE=unprotected` | `results/web-lr2/last-spot-*` |
| Втрачена відповідь (Web О2.3) | `bash experiments/web_lost_response.sh` | `results/web-lr2/lost-response.txt` |
| Ідемпотентність, скасування (Web О2.2, Д2.5) | `web_idempotency.py`, `web_cancel_twice.py` | `results/web-lr2/` |
| Вхід, доступ, CORS (Web ЛР3) | `web_lr3_checks.py` | `results/web-lr3/checks.txt` |
| Довга операція й відмови (Web ЛР4) | `web_lr4.py e2e\|publisher\|worker\|rejected\|simdown\|dpcdown` | `results/web-lr4/` |
| Квиток, два шлюзи, відновлення (Web ЛР5) | `web_lr5.py auth\|two\|window\|gateway\|parallel\|e2e\|ending` (`--with websockets`) | `results/web-lr5/` |
| Черга, гонка, час, зупинка (DPC ЛР1) | `dpc_lr1_overload.py`, `_race`, `_timing`, `_drain` | `results/dpc-lr1/` |
| Точність, режими, прискорення (DPC ЛР2) | `dpc_lr2_compute.py accuracy\|modes\|bench\|health` | `results/dpc-lr2/` |
| Відмова після ефекту, local проти ray, обмеження задач (DPC ЛР3) | `dpc_lr3.py fault\|compare\|inflight`; режими `EXECUTION=local\|ray`, `RECEIVER_DELAY_MS` | `results/dpc-lr3/` |
| Звіт = еталон, набори, перекіс, втрата виконавця, вказівник (DPC ЛР4) | `dpc_lr4_compare.py`, `dpc_lr4.py datasets\|skew\|executor\|atomic` (`--python 3.14 --with httpx --with pyarrow`); режими `--join shuffle`, `--aqe off\|on` | `results/dpc-lr4/` |

Умови, прогноз, вимірювання й висновки кожного досліду - у `results/<ЛР>/README.md`.

## 8. Межі гарантій і керовані відмови

Відмови вмикаються файлом-прапорцем (одноразово, `FAULTS=on`); Docker перезапускає процес.

| Відмова | Увімкнення |
|---|---|
| `after_commit_before_response` (Web ЛР2) | `docker compose exec app-a touch /faults/after_commit_before_response` |
| `publisher_before_send` (Web ЛР4) | `docker compose exec publisher touch /faults/publisher_before_send` |
| `after_external_before_commit` (Web ЛР4) | `docker compose exec worker touch /faults/after_external_before_commit` |
| Зупинка шлюзу (Web ЛР5) | `docker compose stop gateway-a`, потім `docker compose start gateway-a` |
| SIGTERM з повною чергою (DPC Д1.4) | `docker compose stop api`, потім `docker compose start api` |
| Втрата виконавця Spark (DPC О4.5) | `docker compose kill spark-worker-1` під час refresh, потім `docker compose up -d spark-worker-1`; журнал - таблиця виконавців Spark UI (:4040 або сервер історії :18080, профіль `extras`) |
| `after_effect_before_ack` (DPC ЛР3; у задачі Ray після відповіді приймача) | `docker compose exec api touch /faults/after_effect_before_ack`; журнал - `docker compose logs ray-head ray-worker`; Ray сам повторює задачу |

Перевірка - `fault.hit` у `docker compose logs <сервіс>`; повне скидання - `docker compose down -v`.

Головні межі:
- Ключ ідемпотентності захищає лише повтор з тим самим ключем; живе 24 год.
- Перехоплений токен діє до виходу або 8 год; кількість спроб входу не обмежена.
- Поки симулятор недоступний, обробник чекає на одній події без межі спроб.
- Сповіщення наживо може прийти двічі або не прийти (розрив, зупинка шлюзу); рівно один раз - за позицією в клієнті, пропущене дочитується зі стрічки.
- Черга DPC у пам'яті: аварія процесу (не SIGTERM) втрачає `queued`-запити (`lost-on-restart`).
- DPC ЛР3: один ефект на ключ перевірено для відмови процесу-виконавця Ray; відмову самого api і втрату вузла Ray не досліджували.
- DPC ЛР4: звіт = еталон на двох наборах; втрату виконавця перевірено на стадії читання; відмову драйвера (`spark-runner`) не досліджували - запуск треба повторити, `current` лишається попереднім.
- Прискорення DPC виміряно у ВМ з 4 логічними CPU (2 ядра × 2 потоки).

Повний перелік - у `results/<ЛР>/README.md`.

## 9. Внесок учасників

| Учасник | Реалізовані пункти й досліди |
|---|---|
| Дарина | Web ЛР1, ЛР3; DPC ЛР1, ЛР2, ЛР4; екрани клієнта |
| Вадим | Web ЛР2, ЛР4, ЛР5; DPC ЛР3; інфраструктура, зв'язок Web↔DPC |

### Використані бібліотеки

| Бібліотека | Для чого | Де |
|---|---|---|
| FastAPI, psycopg | HTTP і PostgreSQL | `web/server/`, `dpc/api/` |
| aiokafka | публікація й читання подій | `publisher.py`, `worker.py`, `relay.py`, `gateway.py` |
| websockets | клієнт-скрипт дослідів і тестів ЛР5 | `experiments/feed_client.py`, `test_lr5.py` |
| argon2-cffi | хеш пароля | `web/server/app/auth.py` |
| numpy, multiprocessing | обчислення блоками, пул процесів | `dpc/api/app/integrate.py` |
| PySpark, pyarrow | пакетний звіт; еталон і генератор Parquet | `dpc/spark/jobs/report.py`, `dpc/api/app/reference.py`, `dpc/tools/generator/` |
| React, Vite | клієнт | `web/client/` |
| py-spy, matplotlib | профіль і рисунки дослідів | `experiments/` |

Каркаси курсів: `a-kovt/web-interactions-course`, `a-kovt/distributed-parallel-computing-course` (MIT).
