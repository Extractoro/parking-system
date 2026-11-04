# Каркас сервісу `api` (приклад на Python 3.14, FastAPI)

Той самий образ запускає `api`, `ray-head`, `ray-worker` і `ray-worker-extra`. Версії Python і
Ray у клієнті й кластері мають збігатися, а виконавці – імпортувати код ваших задач. Каркас містить налаштування, службові маршрути й допоміжні модулі. Місця для пунктів позначено `TODO (Ox.y)` у `app/main.py`.

| Модуль | Що дає |
|---|---|
| `config.py` | налаштування й режими дослідів зі змінних середовища |
| `db.py` | пул з'єднань PostgreSQL |
| `auth.py` | токен API (`Authorization: Bearer`) для маршрутів `/v1` |
| `service.py`, `metrics.py` | `/health`, `/metrics` (JSON), `/metrics/prom` (для Prometheus). `metrics.gauge("queue_length", ...)` додає ваше значення |
| `middleware.py`, `jsonlog.py`, `problems.py` | `X-Request-ID`, журнал JSON, Problem Details |
| `faults.py` | `fault("after_effect_before_ack")` – керована відмова |

В образі є `py-spy` (О2.3), запуск – `docker compose exec api py-spy top --pid 1`.
Сервіс `api` можна написати іншою мовою.

Запуск без контейнера виконується з цього каталогу. Інфраструктура з `compose.yaml` при цьому
має працювати.

```sh
uv sync
DATABASE_URL=postgresql://app:app@localhost:5433/app uv run uvicorn app.main:app --reload
```

На Windows цей спосіб не підключається до бази, бо асинхронний драйвер psycopg не працює зі
стандартним циклом подій Windows. Запускайте `api` у Docker (`docker compose up -d api`) або у WSL.
