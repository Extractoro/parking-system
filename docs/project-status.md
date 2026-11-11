# Стан проєкту C09 «Паркінги»

## Етап 6 - Web ЛР4: **виконано** (2026-11-11, відповідальний Вадим)

Пункти: О4.1, О4.2, О4.3 (ядро), О4.4, О4.5, Д4.1. `checks.yaml` → `web.items += О4.4, О4.5, Д4.1`; `web/asyncapi.yaml`
валідний, Д4.2 не заявлено. Стенд: `docker compose up -d nginx publisher worker relay external-sim api`
(+ `--profile observability up -d kafka-ui`), клієнт Vite :5173. Новий сервіс `relay` у `compose.yaml`.

| Перевірка | Результат |
|---|---|
| Міграція 007 (`operation`, `outbox`, `processed_message`, `notification_seq`, `notification`) на наявній базі | applied; образ `project-web` перезібрано |
| `pytest web/server/tests` | 50 passed, 1 skipped (`test_lr2::test_10`: поточна година `center-1` заповнена дослідами ЛР4, охоронна умова тесту) - 10 ЛР1 + 11 ЛР2 + 13 ЛР3 + 8 ЛР4 + 9 правило часу; до дослідів 51 passed |
| `pytest dpc/api/tests` (регрес) | 36 passed, 2 skipped |
| `npx @redocly/cli lint web/openapi.yaml` / `dpc/openapi.yaml` | valid, 0 помилок (13 / 5 давніх попереджень) |
| `npx @asyncapi/cli validate web/asyncapi.yaml` | valid (1 info: версія 3.1.0) |
| `tsc -b`, `eslint`, `npm run build` клієнта; `docker compose config --quiet` | без помилок; CONFIG_OK |
| Наскрізний шлях (`experiments/web_lr4.py e2e`) | 202 → succeeded за 8 с → relay → DPC `done` → `entered`; effects 1, сповіщень 1, в'їздів 1; повтор `event_id` у DPC → той самий запит (`Repeated-Request`) |
| `publisher_before_send` | подія не опублікована до перезапуску (1.15 с), потім опублікована з тим самим id, операція succeeded |
| `after_external_before_commit` | після перезапуску симулятор `repeat`, effects 1, сповіщень 1 |
| Відхилення (`SIM_REJECT_SHARE=1`) | failed «шлагбаум несправний», effects 0, без `entry.opened`, запитів у DPC 0 |
| Симулятор недоступний 20 с | 6 повторів, succeeded через 37.3 с |
| DPC зупинено 20 с | relay 4 повтори, `entered` через 33.5 с, в'їзд 1 |
| Клієнт (Edge, Playwright) | Open barrier → Opened at … → Parked; знімки `docs/reports/img/web-lr4-*.png` |

Plan Change: PC-13 (`status` = `state` у відповіді операції; відкрите питання про засіб перевірки довгої операції),
PC-14 (одночасні запити відкриття, `entry-in-progress`), PC-15 (повтори без межі), PC-16 (`outbox.seq`),
PC-17 (DPC відхилив в'їзд `full` після відкриття; тестові авто випущено). Нові рішення M27-M34.
Не зроблено: Д4.2 (не заявлено), Д4.3, Д4.4, стан `expired` бронювання.

## PC-12 - DPC ЛР2, процес на частину: **виконано** (2026-11-11, тег `dpc-lr2.1`)

Бар'єр `multiprocessing.Barrier(workers)` на старті частини; `test_1_2_4_workers_within_tolerance` 10/10 при стенді Web;
`pytest dpc/api/tests` 36 passed, 2 skipped; контроль О2.3: S₄ 1.73 (було 1.76), таблиця не змінена (деталі - PC-12).

## Етап 5 - Web ЛР3: **виконано** (2026-11-10, відповідальна Дарина)

Пункти: О3.1, О3.2 (ядро), О3.3, О3.4. `checks.yaml` → `web.items += О3.3, О3.4`; `role_names` типові (`user`, `staff`).
Стенд: `docker compose up -d nginx publisher` (+ `api` для регресу DPC), клієнт Vite :5173, сторонній сайт
`python -m http.server 8082 --directory web/tools/evil-origin`. Демо-вхід: user1/user1-pass, user2/user2-pass, staff1/staff1-pass.

| Перевірка | Результат |
|---|---|
| Міграції 006 (`app_user.password_hash`, `session`), 904 (хеші argon2id) на наявній базі | applied; образ `project-web` перезібрано з `argon2-cffi` 25.1.0 |
| `pytest web/server/tests` | 41 passed (10 ЛР1 + 11 ЛР2 + 13 ЛР3 + 7 правило часу) |
| `pytest dpc/api/tests` (регрес) | 34 passed, 2 failed; повтор 35 passed, 1 failed - `test_1_2_4_workers_within_tolerance` `processes 3 == 4` (PC-12, код DPC не змінювався); виправлено 2026-11-11 (бар'єр частин, тег `dpc-lr2.1`): 36 passed, 2 skipped |
| `npx @redocly/cli lint web/openapi.yaml` | valid, 0 помилок, 14 попереджень (службові маршрути й невикористані компоненти) |
| `tsc -b`, `eslint`, `npm run build` клієнта | без помилок |
| `docker compose config --quiet` | CONFIG_OK |
| Вхід/вихід/строк (`experiments/web_lr3_checks.py`) | вхід app-a → `/v1/me` app-b 200; вихід 204 → старий токен 401 на обох; повтор 204; прострочена 401; у `session` лише sha256 |
| Неправильний пароль / невідомий логін | однакове тіло 401; медіана 75.4 / 73.6 мс (правильний 81.2), 15 спроб |
| Лише `X-User-Id` | 401 для GET/POST бронювань і `/v1/me` |
| Чуже бронювання | GET, PATCH, cancel → 404 з тими самими `type`/`title`/`detail`, що й відсутнє |
| Закриття рівня | user 403 (і для неіснуючого), staff 200 двічі, нове бронювання 409 `level-closed`, підтверджене `confirmed`, open → 201 |
| CORS | preflight 5173 → `access-control-allow-origin`; 8082 → 400 без нього; у Edge сторінка :8082 - POST і GET заблоковано, форма → 401; на :5173 обидві відповіді прочитано |
| Клієнт у браузері | «Wrong login or password»; 404 і 403 як Problem Details на «Access check»; staff закриває й відкриває рівень; вихід і 401 → сторінка входу; знімки `docs/reports/img/web-lr3-*.png` |
| Дослід О2.3 повторено | `Empty reply` + `fault.hit` на app-a; app-b 201 з тим самим id і `Idempotent-Replayed`; 1 бронювання; `free` 40 → 39 - як у ЛР2 |
| Досліди ЛР2 після переходу на токен | `web_idempotency.py`, `web_cancel_twice.py`, `web_last_spot.py --rounds 2` - ті самі результати (вивід не зберігався, `results/web-lr2` не перезаписано) |

Plan Change: PC-11 (формат відповіді входу з прикладу комплекту, preflight 200, `LOGIN_CONCURRENCY`, типовий хеш `'!'`,
приклад `race-test`), PC-12 (нестабільний тест DPC ЛР2). Нові рішення M20-M26. Не зроблено: Д3.1-Д3.3 (не вибрані).

## Етап 4 - DPC ЛР2: **виконано** (2026-11-07, відповідальна Дарина)

Пункти: О2.1, О2.3, О2.4 (ядро), О2.2. `checks.yaml` → `dpc`: `items += О2.2`, `compute {tolerance 1e-9,
bench_n 600000000, modes none/lock/local}`. Стенд: `docker compose up -d api` (+ `nginx publisher` для регресу
Web). Клієнт - Vite :5173, сторінка «Compute».

| Перевірка | Результат |
|---|---|
| `pytest dpc/api/tests` (з `--with numpy`) | 36 passed, 2 skipped (13 ЛР1 + 23 ЛР2; пропущено Сімпсон з непарним n) |
| `pytest web/server/tests` (регрес) | 28 passed |
| `npx @redocly/cli lint dpc/openapi.yaml` | valid, 0 помилок, 5 попереджень (ті самі службові маршрути) |
| `tsc -b`, `eslint`, `npm run build` клієнта | без помилок |
| `docker compose config --quiet` | OK |
| О2.1: p = 1, 2, 4, 3 методи, n = 2·10⁷ | rel ≤ 1e-9 (фактично ≤ 3.8e-16); малі n (1-1002) паралельно = послідовно з rel 1e-12 |
| О2.2: n і 2n, n = 100…12800 | [0; 14]: 16.00/16.00/16.00 (O' = 0 на кінцях, PC-8); [0; 10]: 4.00/4.00/16.00; Сімпсон = оцінці |
| О2.4: n = 2·10⁷, block 1000, p = 4 × 5 | none 0/5 (555.2-586.9 замість 604.8), lock 5/5, local 5/5; none p = 2: 0/3; p = 1: 2/2 |
| О2.3: n = 6·10⁸, 5 замірів | блок 10⁶: T 3.776/2.707/2.150 с, S 1.39/1.76, E 0.70/0.44; блок 10⁴: T 1.145/0.702/0.541, S 1.63/2.12, E 0.82/0.53 |
| Профіль py-spy p = 4 | `np.arange` + `np.subtract` (нові масиви 8 МБ) 49 % вибірок проти 5.7-9.3 % у рядків на місці |
| `/health` під розрахунками p = 4 | p95 8.0 мс (спокій 4.9), 217 замірів |
| CPU | хост 8 ядер / 12 потоків; ВМ WSL2 4 CPU (2 ядра × 2 потоки за `lscpu`), cgroup без обмеження |
| Клієнт «Compute» у браузері | Compare 1/2/4 на n = 2·10⁸: три результати в допуску, таблиця S_p, E_p; знімок `docs/reports/img/dpc-lr2-compute.png` |

Plan Change: PC-8 (порядок збіжності на [0; 14] - h⁴ для всіх методів, дослід доповнено [0; 10]),
PC-9 (Omax 81, bench_n 6·10⁸), PC-10 (Pool forkserver, список розрахунків, додаткові поля, nice). Нові
рішення P16-P20. Не зроблено: Д2.1-Д2.6 (не вибрані).

## Етап 3 - DPC ЛР1: **виконано** (2026-11-05, відповідальна Дарина)

Пункти: О1.1-О1.4 (ядро), О1.5, Д1.4. `checks.yaml` → `dpc`: `items [О1.5, Д1.4]`, `resource /v1/garage/center
invariant_ok`, `metrics [queue_length, in_flight, rejected]`. Стенд: `docker compose up -d nginx api`
(+ `--profile observability prometheus grafana` для рисунка). Генератори - хост Windows, Python 3.13.

| Перевірка | Результат |
|---|---|
| Міграції 005 (`garage_state`, `entry_request`, `entry`), 903 (`mall` 81, `center` 20, `dpc-race` 5) на наявній базі | applied |
| `pytest dpc/api/tests` | 13 passed |
| `pytest web/server/tests` (регрес) | 28 passed |
| `npx @redocly/cli lint dpc/openapi.yaml` | valid, 0 помилок, 5 попереджень (4xx службових маршрутів, ліцензія, localhost) |
| `tsc -b`, `eslint`, `npm run build` клієнта | без помилок |
| `docker compose config --quiet` | OK |
| О1.2: QUEUE_CAPACITY=50, WORKERS=4, 60 клієнтів aiohttp × 30 с, `/health` 10/с окремим процесом | 396×202, 42263×429; черга ≥ 48 у 51/60 замірів; `/health` p50 6.4, p95 52.6, p99 78.0 мс (перший прогін до PC-6: p95 121.6) |
| О1.2 засобом комплекту `load.py` (Python 3.14) | 6000 запитів, 60 потоків: 159×202, 5841×429 |
| О1.4: `dpc-race`, 40 авто, 200 запитів, WORKERS=8 | unsafe: occupied 5, inside 27, `invariant_ok=false`, 10 виїздів `failed` (CHECK); locked: 5/5, `true` |
| О1.5: 300 запитів, 20 клієнтів | POST HTTP p50/p95 28.0/132.9 мс; повне виконання 1647.1/2022.7 мс; черга 1305.0/1596.5; оброблення 343.5/529.7; видача 48.7/107.6; порядок позначок 300/300 |
| Д1.4: 80 POST, `docker compose stop api` | 35×503 `Retry-After=5`; `api.draining queued=46 processing=4` → `api.drained count=50 seconds=4.0` → `api.stopped`; 61 прийнятий запит у кінцевому стані до й після `start` |
| Правила на демоданих (бронювання через Web API) | в'їзд за бронюванням done; другий - `already-entered`; виїзд без в'їзду - `not-inside`; повтор `event_id` - той самий `Location`, 1 запит; інший номер - `booking-invalid` |
| Клієнт «Barrier» у браузері | відмова `not-inside` з причиною, список запитів і картка стану оновлюються щосекунди; CORS DPC працює |
| Звіт `docs/reports/dpc-lr1.md` | лінтер ok; `Звіт_DPC_ЛР1.docx` і `.pdf`, 14 сторінок; переглянуто титулку, сторінку з таблицею й рисунком, лістинг |

Plan Change: PC-4 (в'їзд за бронюванням обмежений фізичними місцями), PC-5 (генератори aiohttp і власні
сценарії; `httpx.AsyncClient` був вузьким місцем), PC-6 (відмова 429 до маршрутизації FastAPI), PC-7
(дрібні відхилення: 400 замість 422, `already-inside`, `lost-on-restart`, список запитів, `http_in_flight`).
Нові рішення P1-P15. Рисунок Grafana знято під час повторного 30-секундного навантаження тієї самої форми
(60 клієнтів, без перестворення api), а не під час прогону з таблиці. Не зроблено: Д1.1-Д1.3 (не вибрані).

## Етап 2 — Web ЛР2: **виконано** (2026-11-05, відповідальний Вадим)

Пункти: О2.1, О2.2 (ядро), О2.3, О2.4, Д2.4, Д2.5. `checks.yaml` → `web.items` += О2.3, О2.4, Д2.4, Д2.5.

| Перевірка | Результат |
|---|---|
| Міграції 004 (`idempotency_key`), 902 (рівень `race-test`) на наявній базі | applied |
| `pytest web/server/tests` (стенд `docker compose up -d nginx publisher`, Python 3.13) | 28 passed (10 ЛР1 + 11 ЛР2 + 7 правило часу) |
| `npx @redocly/cli lint web/openapi.yaml` | valid, 0 помилок, 16 попереджень (службові маршрути каркаса; 2 — навмисний приклад `forbidden` для PATCH) |
| `npx tsc -b`, `npx eslint .` клієнта | без помилок |
| О2.1 «Останнє місце», 10×20 через nginx, `race-test` | protected: 17×201, 183×409, 0 годин понад місця; unprotected: 115×201, 34 години понад місця в 10/10 раундах |
| SQL інваріанта по всій базі | понад місця лише `race-test` (34, з досліду unprotected), демо-рівні — 0 |
| О2.2 дослід | повтор → той самий id; інше тіло 422; без ключа 400; 10 раундів одночасних запитів з одним ключем → 1 бронювання в кожному |
| О2.3 втрачена відповідь | app-a: `Empty reply`, `fault.hit`; app-b: 201, той самий id, `Idempotent-Replayed`; 1 бронювання, free 40 → 39 |
| О2.4 у браузері з відмовою на app-a | мережева помилка → автоматичний повтор із тим самим ключем → 201 від app-b, 1 бронювання |
| Д2.4, Д2.5 у браузері | 412 «The booking has changed» з кнопкою «Refresh the list»; скасування → повідомлення й перехід у «Past» |
| Д2.5 дослід | 1 послідовне + 5 одночасних подвійних скасувань: free +1 рівно один раз |

Plan Change: PC-2 (дослід без захисту на нових годинах — ліниве створення рядків саме серіалізує
запити), PC-3 (самостійні скрипти дослідів замість `web/tools/workload`). Нові рішення M13–M19.
Не зроблено: Д2.1–Д2.3, Д2.6, Д2.7 (не вибрані).

## Етап R — збирач звітів .docx: **виконано** (2026-11-04)

| Перевірка | Результат |
|---|---|
| `uv run scripts/build_report.py docs/reports/web-lr1.md --pdf` | `docs/reports/out/Звіт_Web_ЛР1.docx`, 13 сторінок PDF (через Word) |
| Перегляд сторінок | титулка (опечатку «робіти» виправлено), розділи 1.1–1.3 і «Висновки» на місцях, таблиці, рисунки 1–4, лістинги 1–5 з git |
| `pytest scripts/tests` | 9 passed (розділи, рисунок, лістинг, помилки джерела) |

Відхилення від плану етапу R: знімки клієнта зроблено вбудованим браузером, а не скриптом Playwright
(`scripts/screenshots/` не створено); PDF — через Word (LibreOffice не встановлено), сторінки PNG — через PyMuPDF.

## Етап 1 — Web ЛР1: **виконано** (2026-11-04, відповідальна Дарина)

| Перевірка | Результат |
|---|---|
| Міграції з нуля (порожній том pgdata) | 001, 002, 003 (notx), 900 — applied; 901 (англійські назви) — applied пізніше |
| `npx @redocly/cli lint web/openapi.yaml` | valid, 0 помилок, 16 попереджень (службові маршрути й компоненти каркаса) |
| `pytest web/server/tests` (стенд `docker compose up -d nginx`) | 17 passed (після PC-1 і регресу) |
| Гонка: 20 одночасних запитів на 1 місце через app-a і app-b | 1×201, 19×409, `free = 0` |
| Клієнт у браузері (Vite :5173 → nginx :8080) | 409 як Problem Details, 201, список 7 бронювань за 2 сторінки; знімки `docs/reports/img/web-lr1-*.jpg` |
| Журнал app-a/app-b | JSON з `time`, `instance`, `route`, `status`, `duration_ms`; `X-User-Id` — 0 входжень |
| `npm run build`, `eslint` клієнта | без помилок |

Регрес-перевірка: 3 відповіді 500 і 3 вади клієнта виправлено (звіт `docs/reports/web-lr1.md`).

Не зроблено в етапі 1: механізм ідемпотентності (ЛР2; зараз без `Idempotency-Key` — 201, план ЛР2: 400), вхід (ЛР3).

## Етап 0 — репозиторій і спільний Compose: **виконано** (2026-11-04)

Середовище: Windows 11, Docker Desktop 28.4.0 (WSL2), Compose v2.39.4; для WSL виділено 12 ГБ
(`.wslconfig` `memory=12GB`; з 4 ГБ Docker Desktop зависав — див. нижче), 4 CPU.

### Фактично виконані перевірки

| Перевірка | Результат |
|---|---|
| `docker compose config --quiet` | CONFIG_OK |
| `docker compose config --services` | 26 сервісів у типовому наборі (35 з `observability` + `extras`) |
| Збирання образів | `project-web`, `project-dpc-api`, `project-dpc-coordinator`, `project-dpc-spark`, `external-sim` — зібрано (по одному сервісу на образ) |
| `docker compose up -d` | усі сервіси `Up`; `migrate`, `broker-init` — `Exited (0)`; рестартів і OOM немає |
| `/health` :8080, :8001, :8002, :8000 | 200 на всіх чотирьох |
| Теми Kafka | `barrier-events` (1 партиція), `domain-events`, `domain-events.dlq`, `notifications` |
| Ім'я `kafka` з контейнера `api` | розв'язується в IP брокера |
| `docker compose run --rm migrate` | `no migrations in /migrations`, exit 0 |
| `docker compose --profile observability up -d` | Grafana: дашборди «Web project» (`web-project`) і «DPC project» (`dpc-project`); Prometheus: 7 цілей `up` (app-a, app-b, app-canary, gateway-a, gateway-b, api, ray-head); kafka-ui 200, jaeger 200 |
| `docker stats --no-stream` без навантаження | типовий набір ≈3.9 ГіБ; з `observability` ≈4.8 ГіБ. Найбільші: ray-head 739 МіБ, stream 549, grafana 480, spark-worker ×2 ≈455, kafka-ui 381, broker 335, ray-worker ×2 ≈297 |

### Інцидент із пам'яттю

З лімітом WSL 4 ГБ (3.8 ГіБ для Docker) типовий набір займав ≈2.7 ГіБ, але через кілька хвилин
Docker Desktop зависав (500 на всі запити до API), а після перезапуску не стартував через залишені
сокети в `%LOCALAPPDATA%\Docker\run\`. Виправлено: зупинка Docker Desktop, `wsl --shutdown`,
`memory=12GB` у `.wslconfig` (копія — `.wslconfig.bak`), каталог `run` перейменовано на
`run.stale-*`, запуск Docker Desktop. Перезавантаження комп'ютера не знадобилось.
