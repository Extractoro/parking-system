# Web ЛР4. Довгі операції, події й зовнішня система: докладно

Скорочений опис і таблиця пунктів - [README.md](../../README.md), розділ 6. Тут - механізм, повні результати дослідів і межі гарантій.

## Шлях операції

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

## Механізм

**Операція.** Маршрут нічого не робить із симулятором і брокером: одна транзакція блокує рядок
бронювання (`FOR UPDATE`, той самий порядок, що й скасування), перевіряє власника (чуже - 404),
стан `confirmed`, відсутність в'їзду (у таблиці `entry` DPC або успішної операції), відсутність операції,
що виконується (PC-14), і чи зараз у межах `[from_hour, from_hour + hours)`, потім вставляє операцію
`pending` і подію `entry.requested`. Відповідь 202 зберігається в ключі ідемпотентності тієї самої
транзакції, тож повтор із тим самим ключем повертає ту саму операцію. `GET /v1/operations/{op}` -
лише власнику (`WHERE id = %s AND user_id = %s`), поки операція не завершена - з `Retry-After: 2`. Поле
`status` дублює `state` (назва поля в контракті комплекту, PC-13).

**Події.** Конверт CloudEvents 1.0: `specversion`, `id` (uuid), `source: parking-web`, `type`,
`subject` (id бронювання), `time` (UTC, мілісекунди, `Z`), `datacontenttype`, `data` з `entity_id`,
`entity_version` і полями події. Конверт цілком лежить у `outbox.payload`, тож повторна публікація
надсилає той самий `id`. Ключ повідомлення - id бронювання: усі події одного бронювання в одній партиції
`domain-events` (3 партиції) і читаються по порядку.

| Тип | Де записується | `data` |
|---|---|---|
| `booking.created` | транзакція `POST /v1/bookings` | `user_id`, `level_id`, `from_hour`, `hours` |
| `entry.requested` | транзакція `POST …/entries` | `operation_id`, `user_id`, `garage_id`, `plate` |
| `entry.opened` | транзакція результату обробника | `operation_id`, `user_id`, `garage_id`, `plate`, `opened_at`, `effect_id` |

**Публікатор** - окремий процес: порція до 100 рядків `WHERE published_at IS NULL ORDER BY seq FOR UPDATE
SKIP LOCKED`, надсилання кожного з очікуванням підтвердження брокера (`acks=all`), `published_at = now()`,
COMMIT. Збій до COMMIT лишає рядки неопублікованими - після перезапуску вони надсилаються знову з тим
самим `id` (щонайменше один раз; споживачі відкидають повтори за парою «споживач, подія»). Порожня
порція - пауза 200 мс. Той самий процес чистить прострочені ключі й сесії (ЛР2, ЛР3).

**Обробник** (група `worker`) обробляє лише `entry.requested`: пара `(worker, id події)` уже в
`processed_message` → пропуск; операція `running`; `POST /open` симулятора з `Idempotency-Key` = id
операції й тайм-аутом 10 с. `accepted` і `rejected` - остаточні; 503, тайм-аут чи мережева помилка -
нічого не фіксується, пауза 1, 2, 4, 8, 8, … с (не менше `Retry-After`) і той самий виклик з тим самим
ключем, поки симулятор не відповість (PC-15). Результат - одна транзакція: `processed_message`, операція
`succeeded` (`result.opened_at` - момент ефекту із симулятора, той самий на кожному повторі) або `failed`
(`error` - причина), сповіщення користувачу (лише через `add_notification`: позиція з лічильника
`notification_seq` під блокуванням рядка, `UNIQUE (user_id, source_event_id)`), для відкриття - подія
`entry.opened`. Offset підтверджується після COMMIT.

**Клієнт.** На картці бронювання, коли зараз у його годинах, є «Open barrier». Ключ - один на
натискання (`crypto.randomUUID()`), той самий у повторах POST після втраченої відповіді. Клієнт опитує
`Location`, чекаючи стільки, скільки каже `Retry-After`, і показує «Opened at …» або «Barrier not opened»
з причиною; після успіху читає бронювання раз на 2 с, доки DPC не запише в'їзд (бейдж Parked = `entered`).

Тести: `test_lr4.py` - 8 тестів (усього 51 з `test_lr1.py`, `test_lr2.py`, `test_lr3.py`, `test_rules.py`);
стенд ЛР4, команда та сама. Тести роблять власний паркінг `t-xxxx` з тарифом і рівнем (тариф видаляється
наприкінці), тож не займають годин і місць демоданих; DPC для нього відповідає 404, і relay записує
`relay.refused`.

## Зв'язок Web → DPC

Відкритий шлагбаум Web передає в DPC окремий процес `relay` (сервіс `relay`, той самий образ Web,
група `relay`). Він читає `entry.opened` з `domain-events` і надсилає в DPC команду, а не запис у таблицю:

```text
POST http://api:8000/v1/entries
Authorization: Bearer ${API_TOKEN}
Idempotency-Key: <id операції>
{"event_id": "<id операції>", "garage_id": "center", "plate": "LR22FDEX", "direction": "in",
 "event_time": "2026-11-11T10:16:46.249Z", "booking_id": "b0a80d9b-…"}
```

`event_time` - момент відкриття шлагбаума з результату операції (UTC, мілісекунди, `Z`, формат DPC).
DPC сам застосовує правило в'їзду за бронюванням (P3: бронювання `confirmed`, номер, інтервал, паркінг,
в'їзду ще не було) і змінює зайнятість; Web таблиць DPC не пише (B2). 202 → offset підтверджено;
429, 5xx, тайм-аут, мережа (DPC зупинено) → пауза 1, 2, 4, 8, 8, … с (не менше `Retry-After`) і той самий
запит; інший 4xx остаточний (`relay.refused`). Повтор безпечний: той самий `event_id` DPC повертає тим
самим запитом (202, той самий `Location`, `Repeated-Request: true`), другого в'їзду не створює, а другий
в'їзд за бронюванням забороняє ще й `UNIQUE (booking_id) WHERE direction = 'in'`. Web показує бронювання
як `entered`, коли в таблиці `entry` DPC є в'їзд за ним (читання, `LEFT JOIN`).

Наскрізний приклад (`results/web-lr4/e2e.txt`): бронювання `b0a80d9b` поточної години → `POST …/entries`
202 → операція `succeeded` (`effect_id bar-a293371559`, `opened_at 10:16:46.249Z`) → `relay.sent`
(`/v1/entries/17fb716c…`, `repeated: false`) → запит DPC `done` → `GET /v1/bookings/b0a80d9b…` - `entered`.
Недоступність DPC (`docker compose stop api` на 20 с): relay повторив 4 рази й надіслав після `start`,
бронювання `entered` через 33.5 с, в'їзд записано один раз (`dpcdown.txt`).

## Досліди

Стенд `docker compose up -d nginx publisher worker relay external-sim api`. Кожен дослід - бронювання user1 на
`center-1` поточної години з новим номером; наприкінці авто «виїжджає» (виїзд через API DPC), щоб повторні
запуски не заповнювали паркінг (PC-17).

```bash
uv run --no-project --python 3.13 --with httpx --with "psycopg[binary]" python experiments/web_lr4.py e2e | tee results/web-lr4/e2e.txt
```

(аналогічно `publisher`, `worker`, `rejected`, `simdown`, `dpcdown`, `api`).

| Дослід | Режим і команда | Що видно |
|---|---|---|
| Наскрізний шлях | `... web_lr4.py e2e` | 202 → `pending` → `running` → `succeeded` за 8 с (симулятор 1-3 с + повтор після 503); бронювання `entered`; запит DPC `done`; `effects = 1`, сповіщень 1, рядків `entry` за бронюванням 1; той самий `event_id` двічі надіслано в DPC → 202, той самий `Location`, `Repeated-Request: true`, запитів у DPC 1. `results/web-lr4/e2e.txt` |
| Відмова публікатора | `... publisher`: `touch /faults/publisher_before_send` у контейнері publisher, потім `POST …/entries` | через 0.6 с: операція `pending`, `entry.requested` не опублікована; `fault.hit` 10:18:34.693; Docker перезапустив процес, та сама подія опублікована 10:18:35.843 (1.15 с), операція `succeeded`, `effects = 1`, сповіщень 1, бронювання `entered`. `publisher.txt` |
| Відмова після зовнішнього ефекту | `... worker`: `touch /faults/after_external_before_commit` у контейнері worker | після виклику: `effects = 1`, операція `running`, сповіщень 0; `fault.hit` 10:17:33.127; після перезапуску той самий ключ → симулятор `event: repeat`, `status: accepted` (без нового ефекту); `succeeded`, `opened_at` = момент першого ефекту, `effects = 1`, сповіщень 1, в'їзд у DPC 1. `worker.txt` |
| Відхилення | `... rejected` (external-sim перестворено з `SIM_REJECT_SHARE=1`, потім повернено) | `failed`, `error: шлагбаум несправний`; `effects = 0`; сповіщення «Barrier not opened: шлагбаум несправний»; в outbox лише `booking.created`, `entry.requested` (без `entry.opened`); запитів у DPC 0, бронювання `confirmed`; нова спроба з новим ключем приймається (202) і теж `failed`. `rejected.txt` |
| Симулятор недоступний | `... simdown`: `POST :8090/admin/unavailable?seconds=20` | операція `running`, 6 повторів `worker.external_retry` (`http-503`, паузи 2, 2, 4, 8, 8, 8 с), `succeeded` через 37.3 с після запиту, `effects = 1`. `simdown.txt` |
| DPC недоступний | `... dpcdown`: `docker compose stop api` на 20 с | операція Web `succeeded` за 3 с, бронювання `confirmed`, поки DPC зупинено; relay: 4 повтори `ConnectError` (паузи 1, 2, 4, 8 с), `relay.sent` з 5-ї спроби; бронювання `entered` через 33.5 с після зупинки; рядків `entry` 1. `dpcdown.txt` |
| Перевірки маршруту | `... api` | див. О4.1 у розділі 6. `api.txt` |
| Ефекти симулятора | `curl -s localhost:8090/effects` | після всіх дослідів: ключів 20, ефектів 20, `keys_with_duplicates: []`. `effects-summary.txt` |

## Керовані відмови й межі гарантій

| Відмова | Місце | Увімкнення | Рядок журналу | Відновлення |
|---|---|---|---|---|
| `publisher_before_send` | `web/server/app/publisher.py`, `publish_batch`: порцію вибрано під `FOR UPDATE SKIP LOCKED`, нічого не надіслано | `docker compose exec publisher touch /faults/publisher_before_send` | `{"instance": "publisher", "event": "fault.hit", "place": "publisher_before_send"}` | Docker перезапускає publisher (≈1.2 с); транзакція порції відкотилася, рядки неопубліковані й надсилаються з тими самими `id` |
| `after_external_before_commit` | `web/server/app/worker.py`, `handle`: симулятор відповів `accepted`/`rejected`, результат не зафіксовано | `docker compose exec worker touch /faults/after_external_before_commit` | `{"instance": "worker", "event": "fault.hit", "place": "after_external_before_commit"}` | Docker перезапускає worker; offset не підтверджено, подія приходить знову; операція вже `running`, виклик із тим самим ключем повертає збережений результат симулятора |

У Git Bash перед `docker compose exec` задайте `MSYS_NO_PATHCONV=1`. Прапорець одноразовий (файл забирається
видаленням); якщо відмова не спрацювала - `docker compose exec <сервіс> rm -f /faults/<місце>`. Повернути
симулятор: `docker compose up -d external-sim` (значення з `.env`); скинути все - `docker compose down -v`.

Що підтверджено й що ні:

- Доставка подій - щонайменше один раз: збій публікатора після надсилання й до COMMIT дасть повторну
  публікацію тієї самої події; обробник відкидає її за `(worker, id)` у тій самій транзакції, що й результат
  (`test_lr4.py::test_3`). Relay окремої позначки не має: повтор `event_id` у DPC повертає той самий запит (P6).
- Один зовнішній ефект на операцію тримається на ключі симулятора = id операції й на тому, що симулятор
  пам'ятає остаточний результат на томі `simdata`. Після `docker compose down -v` пам'ять симулятора зникає.
- Поки симулятор недоступний, обробник чекає на одній події: наступні події тих самих партицій чекають
  (один процес групи `worker`, послідовне оброблення); межі спроб і карантину немає (Д4.4 не вибрано, PC-15).
- Стан `entered` означає запис в'їзду в DPC. Якщо DPC відхилить в'їзд (наприклад, `full` через авто, що не
  виїхали), операція Web лишається `succeeded`, а бронювання `confirmed`; причина видно на сторінці Barrier
  (PC-17). Зворотної події з DPC немає.
- Перевірки маршруту й вставка операції - одна транзакція під блокуванням рядка бронювання, тому два
  ключі одночасно не створять двох операцій (і частковий унікальний індекс у базі). Не досліджено: збій
  PostgreSQL посеред транзакції результату, падіння брокера під час надсилання порції (продюсер ідемпотентний,
  рядки лишаються неопублікованими до успіху), кілька екземплярів публікатора одночасно (`SKIP LOCKED`
  розраховане на це, але не запускалося).
- Повідомлення, яке не вдається розібрати як JSON, пропускається з рядком `message.undecodable`.
