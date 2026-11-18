# Вимоги DPC — «Розподілені та паралельні обчислення»

Джерело: `parallel-project-guide-2026.pdf` (методичка 2026). Тема C09 «Паркінги».
«Ядро» перевіряється завжди; «О» — бажані пункти, «Д» — додаткові. Вибрані пункти поза ядром перелічуються в `checks.yaml` (`dpc.items`).
Статуси: `not_started` → `in_progress` → `done` (лише з фактичною перевіркою в Evidence) / `skipped`.
Опис — скорочена назва пункту; повний текст — у методичці.

## ЛР1

| Lab | ID | Ядро/О/Д | Описание | Компонент | Файлы | Тесты | Evidence | Status |
|---|---|---|---|---|---|---|---|---|
| ЛР1 | О1.1 | Ядро | Приймання запитів. | api | `dpc/api/app/entries.py`, `migrations/005_dpc_entries.sql`, `903_dpc_seed.sql` | `test_accept_202_and_final_state`, `test_validation_and_auth` | 202 + `Location`, стани queued → processing → done/rejected/failed; `results/dpc-lr1/rules.txt`, `pytest.txt` | done |
| ЛР1 | О1.2 | Ядро | Обмежена черга й перевантаження. | api | `entries.py` (`asyncio.Queue`, `Admission`) | `test_full_queue_429_not_stored` | 60 клієнтів × 30 с, черга 50, 4 обробники: 396×202, 42263×429, `/health` p95 52.6 мс (`results/dpc-lr1/overload.txt`) | done |
| ЛР1 | О1.3 | Ядро | Пул обробників. | api | `entries.py` (`start`, `_worker`, `_process`) | `test_time_marks_order` | рівно `WORKERS` задач; `in_flight` max 4 при WORKERS=4; розпізнавання 100-500 мс поза секцією | done |
| ЛР1 | О1.4 | Ядро | Захист спільного ресурсу. | api | `entries.py` (`_apply_locked`, `_apply_unsafe`) | `test_invariant_after_200_parallel_requests` | `dpc-race`, 200 запитів, WORKERS=8: unsafe occupied 5 / inside 27 `invariant_ok=false`, locked 5/5 `true` (`results/dpc-lr1/race.txt`) | done |
| ЛР1 | О1.5 | О | Час виконання й метрики. | api | `entries.py` (`Marks`, `get_entry`), `metrics.py` | `test_time_marks_order`, `test_metrics` | 300 запитів: POST HTTP p95 132.9 мс, повне виконання p95 2022.7 мс; порядок позначок 300/300 (`results/dpc-lr1/timing.txt`) | done |
| ЛР1 | Д1.1 | Д | Дві моделі виконання (потоки й асинхронність або процеси) на тому самому навантаженні. | — | — | — | — | skipped |
| ЛР1 | Д1.2 | Д | Перевірка закону Літтла на вимірах. | — | — | — | — | skipped |
| ЛР1 | Д1.3 | Д | Власна черга на м’ютексі й умовній змінній. | — | — | — | — | skipped |
| ЛР1 | Д1.4 | Д | Завершення роботи. | api | `entries.py` (`_install_sigterm`, `_drain_then_exit`) | - | `docker compose stop api` з повною чергою: 35×503, `api.drained count=50`, 61 прийнятий запит у кінцевому стані (`results/dpc-lr1/drain.txt`) | done |

## ЛР2

| Lab | ID | Ядро/О/Д | Описание | Компонент | Файлы | Тесты | Evidence | Status |
|---|---|---|---|---|---|---|---|---|
| ЛР2 | О2.1 | Ядро | Паралельне інтегрування. | api | `dpc/api/app/integrate.py`, `compute.py` | `test_1_2_4_workers_within_tolerance`, `test_small_n_parallel_equals_sequential`, `test_validation`, `test_second_computation_429` | 3 методи × p = 1, 2, 4, n = 2·10⁷: rel ≤ 1e-9; малі n = послідовному циклу; `/health` p95 8.0 мс під p = 4 (`results/dpc-lr2/pytest.txt`, `health.md`) | done |
| ЛР2 | О2.2 | О | Дослідження точності. | experiments | `experiments/dpc_lr2_compute.py accuracy` | - | n і 2n, n = 100…12800: [0; 14] 16/16/16, [0; 10] 4/4/16; допуск 1e-9 (`results/dpc-lr2/accuracy.md`, PC-8) | done |
| ЛР2 | О2.3 | Ядро | Прискорення та причина обмеження. | experiments | `dpc_lr2_compute.py bench`, `dpc/tools/bench_table.py`, py-spy | - | n = 6·10⁸, 5 замірів: блок 10⁶ S₄ 1.76; гіпотеза з профілю, блок 10⁴: T₁ 3.78 → 1.15 с, S₄ 2.12 (`results/dpc-lr2/speedup-*.md`, `profile-p4.svg`) | done |
| ЛР2 | О2.4 | Ядро | Спільна сума та локальні результати. | api | `integrate.py` (`part_sum`, `run`) | `test_shared_sum_modes` | p = 4, 5 запусків: none 0/5 у допуску (втрата 2.9-8.2 %), lock 5/5, local 5/5 (`results/dpc-lr2/modes.md`) | done |
| ЛР2 | Д2.1 | Д | Інший метод із картки й порівняння з інтегруванням. | — | — | — | — | skipped |
| ЛР2 | Д2.2 | Д | Екстраполяція Річардсона. | — | — | — | — | skipped |
| ЛР2 | Д2.3 | Д | Порівняння динамічного й статичного розподілу частин. | — | — | — | — | skipped |
| ЛР2 | Д2.4 | Д | Порівняння послідовного, попарного й компенсованого сумування; таблиця похибок для n і 2n. | — | — | — | — | skipped |
| ЛР2 | Д2.5 | Д | Паралельна гістограма | — | — | — | — | skipped |
| ЛР2 | Д2.6 | Д | Скасування. | — | — | — | — | skipped |

## ЛР3

| Lab | ID | Ядро/О/Д | Описание | Компонент | Файлы | Тесты | Evidence | Status |
|---|---|---|---|---|---|---|---|---|
| ЛР3 | О3.1 | Ядро | Виконання задач у Ray. | api, ray-head, ray-worker | `dpc/api/app/raytasks.py`, `entries.py` (`_ray_worker`, `_submit`, `_await_result`), `compose.yaml` (`EXECUTION=ray`) | `test_request_through_the_stand_and_repeat`; `test_lr1.py` у режимі ray | `max_retries=3`, ключ `event_id` в усіх спробах, результати за готовністю; 42 passed (ray), 19 passed (local); local проти ray 200 запитів: p95 1814/2067 мс, інваріант в обох (`results/dpc-lr3/pytest*.txt`, `compare.txt`) | done |
| ЛР3 | О3.2 | О | Обмеження кількості незавершених задач. | api | `entries.py` (семафор `IN_FLIGHT_LIMIT`, `in_flight`), `experiments/dpc_lr3.py inflight` | `test_metrics` | 60 клієнтів × 30 с: приймач 0/500 мс - max `in_flight` 8/8, задач у Ray ≤ 8, черга 50, 429 98.6/99.4 %, 18.65/8.80 запиту/с (`results/dpc-lr3/inflight.txt`) | done |
| ЛР3 | О3.3 | Ядро | Атомарне приймання ефекту. | api | `migrations/008_dpc_effects.sql`, `entries.py` (`receive_effect`, `admit`) | `test_repeat_returns_stored_decision`, `test_rejected_decision_is_stored`, `test_concurrent_deliveries_one_effect`, `test_receiver_applies_the_rule_of_lab_1`, `test_receiver_checks_key_and_token` | повтор і 8 одночасних доставок - один ефект; `rejected` зберігається (`results/dpc-lr3/pytest.txt`) | done |
| ЛР3 | О3.4 | Ядро | Відмова після запису ефекту. | ray-worker | `raytasks.py` (`fault("after_effect_before_ack")`), `experiments/dpc_lr3.py fault` | - | `fault.hit` на ray-head, спроба 0 FAILED WORKER_DIED, 1 FINISHED; `effect` 1, `entry` 1, `occupied` 0 → 1, `invariant_ok` true; відхилений: `rejected` 1, `occupied` 5 → 5 (`results/dpc-lr3/fault.txt`) | done |
| ЛР3 | Д3.1 | Д | Порівняння акторів і задач Ray на тому самому сценарії. | — | — | — | — | skipped |
| ЛР3 | Д3.2 | Д | Розрахунок ЛР2 через Ray на кількох вузлах. | — | — | — | — | skipped |
| ЛР3 | Д3.3 | Д | Ідемпотентність API. | — | — | — | — | skipped |
| ЛР3 | Д3.4 | Д | Повільний приймач і зворотний тиск. | — | — | — | — | skipped |

## ЛР4

| Lab | ID | Ядро/О/Д | Описание | Компонент | Файлы | Тесты | Evidence | Status |
|---|---|---|---|---|---|---|---|---|
| ЛР4 | О4.1 | О | Дані для дослідження продуктивності. | generator | `dpc/tools/generator/spec.c09.json`, `columns.c09.py`, `compose.yaml` (`generator`) | - | small 10⁵ рядків 2.7 МБ, big 10⁷ 252 МБ, seed 909, частка `mall` 0.4471 / 0.4502; повторна генерація - ті самі SHA-256 (`results/dpc-lr4/datasets.txt`) | done |
| ЛР4 | О4.2 | Ядро | Пакетний звіт через API. | api, spark-runner | `dpc/spark/jobs/report.py`, `dpc/api/app/reports.py`, `web/client/src/ReportPage.tsx` | `test_refresh_then_report_equals_reference`, `test_refresh_validation_and_auth` | refresh 202, повторний 409 `report-running`, 10 рядків за 28 с (`results/dpc-lr4/pytest.txt`, `compare-small.txt`) | done |
| ЛР4 | О4.3 | Ядро | Незалежний еталон. | api | `dpc/api/app/reference.py`, `experiments/dpc_lr4_compare.py` | `test_reference_on_fixed_set`, `test_reference_reads_in_batches`, `test_compare_by_keys` | small і big: 0 розбіжностей за `garage_id` (цілі точно, середнє 1e-9) (`compare-small.txt`, `skew.txt`, `executor.txt`) | done |
| ЛР4 | О4.4 | О | План виконання та перекіс даних. | spark | `report.py` (`--join shuffle`, `--aqe`, `--force-skew`), `experiments/dpc_lr4.py skew` | - | big, 3 запуски: частина `mall` 23 МБ із 54, max/med 5.1; AQE skew join: стадія 17.9 → 8.3 с, найдовша задача 13.5 → 5.0 с, звіт = еталон (`results/dpc-lr4/skew.txt`, `plan-*.txt`, PC-27) | done |
| ЛР4 | О4.5 | О | Втрата виконавця Spark. | spark | `experiments/dpc_lr4.py executor` | - | kill `spark-worker-1` під час refresh big: 2 × ExecutorLostFailure, повтор на worker-2, `done`, звіт = еталон (`results/dpc-lr4/executor.txt`) | done |
| ЛР4 | Д4.1 | Д | Реалізація й порівняння обох способів усунення перекосу. | — | — | — | — | skipped |
| ЛР4 | Д4.2 | Д | Порівняння з’єднання з розсиланням довідника (broadcast join) і з’єднання з перетасуванням (shuffle join). | — | — | — | — | skipped |
| ЛР4 | Д4.3 | Д | Атомарна публікація звіту. | spark | `report.py` (`publish`: tmp + `os.replace`) | - | невдалий запуск (`PATH_NOT_FOUND`) - `current` побайтно той самий; успішний - новий inode (`results/dpc-lr4/atomic.txt`) | done |
| ЛР4 | Д4.4 | Д | Вплив одного фактора. | — | — | — | — | skipped |

## ЛР5

| Lab | ID | Ядро/О/Д | Описание | Компонент | Файлы | Тесты | Evidence | Status |
|---|---|---|---|---|---|---|---|---|
| ЛР5 | О5.1 | О | Сценарії джерела та дублікати подій. | generator, stream | `dpc/tools/generator/events.c09.json`, `events_c09.sh`, `dpc/spark/jobs/stream.py` (`dropDuplicatesWithinWatermark`) | `test_duplicates_are_counted_once_and_tick_is_not_counted` | normal і duplicates (+610 повторів) на окремих темах: ті самі 48 вікон, 12 000 подій (`results/dpc-lr5/dedup.txt`) | done |
| ЛР5 | О5.2 | Ядро | Агрегування за часом події. | stream, api | `dpc/spark/jobs/stream.py`, `dpc/api/app/panel.py`, `migrations/010_dpc_panel.sql` | `test_window_boundaries_of_5_minutes`, `test_only_windows_before_the_watermark_are_final` | 48 вікон по 5 хв × 4 паркінги, watermark 5 хв, останнє вікно фіналізує `tick` 08:10 (`results/dpc-lr5/main.txt`) | done |
| ЛР5 | О5.3 | О | Вплив допустимого запізнення. | stream | `stream.py` (`WATERMARK_MINUTES`, `TRACK_EVENTS`), `experiments/dpc_lr5.py predict\|lateness` | - | `late.jsonl`, пауза 20 мс: watermark 2 хв - відкинуто 637/12 000 (5.31 %), затримка 16.5 с; 5 хв - 65 (0.54 %), 37.7 с; прогноз 938 і 209 (`results/dpc-lr5/lateness.txt`) | done |
| ЛР5 | О5.4 | Ядро | Відновлення без повторного результату. | stream | checkpoint `/data/checkpoints/<run>`, upsert у `write_batch` | `test_repeated_micro_batch_overwrites_the_same_window` | `docker compose kill stream` після половини подій, рестарт повторив пакет 27; 48 вікон = прогону без відмови (`results/dpc-lr5/restart.txt`) | done |
| ЛР5 | О5.5 | Ядро | Порівняння з пакетним розрахунком. | api | `dpc/api/app/panel_batch.py`, `GET /v1/panel/batch` | `test_control_example_matches_expected`, `test_stream_panel_equals_batch` | 48 = 48 вікон, 0 розбіжностей (`results/dpc-lr5/main.txt`); контрольний приклад = `expected.json` | done |
| ЛР5 | Д5.1 | Д | Другий профіль вікна. | — | — | — | — | skipped |
| ЛР5 | Д5.2 | Д | Власний стан із тайм-аутом для сеансу. | — | — | — | — | skipped |

## ЛР6

| Lab | ID | Ядро/О/Д | Описание | Компонент | Файлы | Тесты | Evidence | Status |
|---|---|---|---|---|---|---|---|---|
| ЛР6 | О6.1 | Ядро | Отримання ролі через оренду. | — | — | — | — | not_started |
| ЛР6 | О6.2 | Ядро | Захист ресурсу перевіркою покоління. | — | — | — | — | not_started |
| ЛР6 | О6.3 | О | Старий власник після паузи та перевірка історії. | — | — | — | — | not_started |
| ЛР6 | Д6.1 | Д | Перевірка лінеаризовності історії одного регістра. | — | — | — | — | not_started |
| ЛР6 | Д6.2 | Д | Годинники Лемпорта в журналах координаторів і ресурсу. | — | — | — | — | not_started |
| ЛР6 | Д6.3 | Д | Запис архітектурного рішення у відповідь на зміну обмеження. | — | — | — | — | not_started |
| ЛР6 | Д6.4 | Д | Розгортання платформи на кількох вузлах (Docker Swarm або Kubernetes) і повторення дослідів ЛР3 і ЛР6 з від… | — | — | — | — | not_started |
