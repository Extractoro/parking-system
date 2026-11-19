# DPC ЛР6. Координація менеджера динамічного тарифу: докладно

Скорочений опис і таблиця пунктів - [README.md](../../README.md), розділ 6. Тут - механізм, повні результати дослідів
і межі гарантій. Стенд: `postgres api etcd coordinator-a coordinator-b` (решта сервісів працювала, як у звичайному
запуску), etcd 3.7.2, ВМ Docker з 4 логічними CPU. Сценарій - `experiments/dpc_lr6.py`.

## Роль і покоління (О6.1)

```text
coordinator:  grant(TTL 5) -> txn IF create_revision(/roles/tariff-manager) == 0
                                 THEN PUT key=<INSTANCE> lease   ELSE RANGE key (ревізія для watch)
  succeeded -> generation = create_revision(key) -> POST /v1/tariff/generations для mall, center
            -> потік keepalive кожні 1.5 с; кожні 3 с: GET /v1/garage/{id} -> tariff.prepared -> PUT /v1/tariff/{id}
  failed    -> watch(key, start_revision = ревізія + 1) до DELETE -> знову txn
  втрата    -> keepalive TTL 0 | немає продовження весь TTL | 409 від ресурсу -> role.lost, запис припинено
  SIGTERM   -> revoke(lease): role.lost (stopping), role.released
```

Покоління - ревізія etcd, монотонна для всього сховища, тому кожне нове захоплення має більше покоління. Дані etcd
лежать у томі `etcddata`: без нього ревізії після перестворення контейнера почалися б з 1, і ресурс (правильно)
відхиляв би нового власника назавжди.

## Перехід ролі (О6.1)

`dpc_lr6.py transition --repeats 5` (`transition.txt`, `.json`). Від: старт команди `docker compose stop|kill <власник>`
(монотонний годинник хоста). До: ключ ролі в etcd містить іншого координатора (= його `role.acquired`) і рядок `fence`
паркінгу `mall` містить його покоління (= `generation.registered` на ресурсі). Опитування кожні 20 мс; поряд - час
рядка журналу нового власника мінус настінний час команди (годинник ВМ Docker).

| Дія | До `role.acquired`, с (мін-макс) | Медіана | До реєстрації, медіана | За журналом, медіана | Сама команда, медіана |
|---|---|---|---|---|---|
| `stop` (SIGTERM, оренду відкликано) | 0.651-1.183 | 0.981 | 0.985 | 0.940 | 1.411 |
| `kill` (оренда спливає за TTL) | 4.490-5.625 | 5.513 | 5.515 | 5.479 | 0.807 |

При `stop` роль переходить раніше, ніж повертається сама команда: координатор відкликає оренду, ключ видаляється,
і очікувач, що стоїть у watch, одразу виконує транзакцію. При `kill` ключ ще до 5 с вказує на мертвого власника
(останнє продовження до 1.5 с до відмови, тож оренда спливає через 3.5-5 с після неї, плюс запуск команди ~0.8 с).
Проміжку «без власника» `etcd_watch.py` з опитуванням 0.1 с не побачив ні разу (`etcd-watch.txt`): ключ видаляє
закінчення оренди, і новий власник створює його за мілісекунди. Реєстрація покоління додає 2-4 мс.

## Перевірка покоління на ресурсі (О6.2)

`PUT /v1/tariff/{garage_id}` і `POST /v1/tariff/generations` - одна транзакція з `SELECT … FROM fence WHERE
resource = 'tariff/<garage_id>' FOR UPDATE`. Реєстрація: більше за зареєстроване - так; те саме покоління того самого
власника - 200 `repeated` (повтор після втраченої відповіді); інакше 409. Запис: рівне - `UPDATE tariff` + історія
`accepted`; менше - `stale`, більше - `unregistered`, обидва 409 і рядок історії `accepted = false`. Тести
`test_lr6.py`: лише більше покоління, застаріле й незареєстроване відхилено, тариф лишився від прийнятого запису.

## Пауза старого власника (О6.3)

`dpc_lr6.py pause` (`pause.txt`): coordinator-a - власник з `WRITE_GAP_MS=2000`; одразу після його `tariff.prepared`
- `docker compose pause coordinator-a`, 9 с, `unpause`.

```text
coordinator-a owns the role, generation 70; WRITE_GAP_MS=2000
unpaused; during the pause the role was held by coordinator-b, generation 72
coordinator-b  11:49:36.702 role.acquired 72; 36.708 generation.registered mall 72; 36.716 center 72
coordinator-a  11:49:42.434 role.lost (lease-expired)
               11:49:42.437 tariff.write mall 3100 generation 70 accepted false reason stale registered 72
history mall:  921 coordinator-b 72 registered | 923, 925 coordinator-b 72 3000 accepted
               927 coordinator-a 70 3100 False stale | 928 coordinator-b 72 3000 accepted
/v1/history/check?garage_id=mall: ok=True   /v1/history/check: ok=True
```

Пауза в довільний момент без зазору (`--gap-ms 0`, `pause-random.txt`) дала те саме: B захопив роль із поколінням 80,
A після відновлення виявив `lease-expired`, а запис, що вже пройшов перевірку ролі в координаторі (покоління 78),
дійшов до ресурсу через 52 мс після `role.lost` і був відхилений `stale`. Саме цей проміжок між «я ще власник» і
записом закриває перевірка на ресурсі.

## Без захисту (О6.3)

`dpc_lr6.py unfenced` (`unfenced.txt`): api з `FENCING=off` (перевірку пропущено лише для `dpc-race`), координатори з
`TARIFF_GARAGES=dpc-race`, `dpc-race` - 2 з 5 місць, база 2750 → 3850: A округлює до 3900, B до 3800. Той самий
сценарій паузи.

```text
970 coordinator-a gen 86 3900 unfenced   | 971 coordinator-b gen 88 registered
972, 973 coordinator-b gen 88 3800 unfenced | 974 coordinator-a gen 86 3900 unfenced | 975 coordinator-b 3800
/v1/history/check?garage_id=dpc-race: ok=False
  violations: stale-accepted id 974, coordinator-a, generation 86, registered 88
```

Обидва менеджери записали різні тарифи, і тариф `dpc-race` між записами 974 і 975 був 3900 від старого власника.
Після досліду api і координатори повернуто (`FENCING=on`, `mall,center`), тариф, `fence` та історію `dpc-race` видалено.

## Спільний проєкт: тариф у Web

`dpc_lr6.py booking` (`booking.txt`): user1 бронює `center-1` на 2 год; DPC `GET /v1/tariff` = 5800 (coordinator-a,
покоління 114), каталог Web = 5800, бронювання `tariff_minor_per_hour` 5800, `price_minor` 11600. Тести Web не
залежать від фіксованих 3000/4000: ціну порівнюють з тарифом у відповіді, тестові паркінги мають власний тариф.

## Регрес

| Перевірка | Результат |
|---|---|
| `pytest dpc/api/tests` (координатори працюють) | 61 passed, 2 skipped (`pytest.txt`) |
| `pytest web/server/tests` | 59 passed (`pytest-web.txt`) |
| Наскрізний шлях Web ЛР4 (`web_lr4.py e2e`) | `succeeded`, бронювання `entered`, DPC `done`, повтор - той самий запит (`e2e-lr4.txt`) |
| `redocly lint dpc/openapi.yaml`; `docker compose config --quiet` | valid, 5 давніх попереджень; CONFIG_OK |

## Межі гарантій

- Між захопленням ролі новим власником і реєстрацією його покоління (2-10 мс) старий власник ще може записати з
  поточним зареєстрованим поколінням: такий запис прийнято, і він не є порушенням (нове покоління ще не зареєстроване).
- Перевірка захищає лише записи через ресурс; пряму зміну таблиці `tariff` в обхід API вона не бачить.
- Після `kill` власника тариф не оновлюється до 5.6 с (TTL); пауза довша за TTL коштує власникові ролі.
- etcd - один вузол; його відмову не досліджували (координатори пишуть `etcd.error` і повторюють, ресурс працює далі
  з останнім зареєстрованим поколінням).
