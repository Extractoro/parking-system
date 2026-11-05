# Стан проєкту C09 «Паркінги»

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
