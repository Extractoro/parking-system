# Задачі Spark

Каталог підключено до всіх контейнерів Spark як `/jobs`, тому зміни видно без перезбирання образу.

| Файл | Як запускається |
|---|---|
| `report.py` (ЛР4) | `api` викликає `POST http://spark-runner:8000/jobs` – приклад у шапці файлу |
| `stream.py` (ЛР5) | сервіс `stream`. Після зміни виконайте `docker compose restart stream` |

Запускач `../runner.py` має два маршрути:

- `POST /jobs {script, args, py_files}` → `202 {run, state}`
- `GET /jobs/<run>` – стан `running | done | failed` і останні рядки журналу

`{run}` в `args` замінюється ідентифікатором запуску. Допоміжні модулі передавайте в `py_files`.

Журнали подій усіх задач зберігаються в `/data/spark-events`. Їх показує сервер історії (http://localhost:18080).
