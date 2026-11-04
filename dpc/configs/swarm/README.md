# Docker Swarm (Д6.4)

Потрібні два вузли. На першому вузлі працюють база, etcd, `api` і `ray-head`. Виконавці Ray і координатори
розміщуються по одному на вузол. Команди – з кореня проєкту на першому вузлі.

```sh
docker swarm init --advertise-addr <IP>            # на другому вузлі – надрукована команда join
docker node update --label-add db=true <перший вузол>

docker service create --name registry --publish 5000:5000 registry:2
docker build -t localhost:5000/project-dpc-api:dev ./api
docker build -t localhost:5000/project-dpc-coordinator:dev ./coordinator
docker build -f configs/swarm/Dockerfile.migrate -t localhost:5000/project-dpc-migrate:dev .
docker push localhost:5000/project-dpc-api:dev
docker push localhost:5000/project-dpc-coordinator:dev
docker push localhost:5000/project-dpc-migrate:dev

docker stack deploy -c configs/swarm/stack.yaml dpc
docker service ps dpc_ray-worker dpc_coordinator
```

Щоб перевірити відмову вузла, вимкніть другий вузол (або `docker node update --availability drain <вузол>`)
під час досліду ЛР3 чи ЛР6 і подивіться, що відбувається із задачами Ray і роллю.
Spark і Kafka до стека не входять.

## Керовані відмови

У базовому стеку `FAULTS=off`, бо локальні томи різних вузлів не утворюють спільного каталогу.
Конфігурація [faults.yaml](faults.yaml) вмикає механізм і підключає `/faults`. Для неї на кожному
вузлі в один абсолютний шлях, наприклад `/srv/course-faults`, має бути змонтована **та сама спільна
файлова система**. Вона має підтримувати атомарне видалення файлу між клієнтами й дозволяти запис
контейнерам.
Окремі локальні каталоги з однаковими назвами не підходять.

```sh
export FAULTS_DIR=/srv/course-faults
docker stack deploy -c configs/swarm/stack.yaml -c configs/swarm/faults.yaml dpc
```

Файл точки відмови створюють у цьому каталозі, а журнал перевіряють через
`docker service logs <ім’я_сервісу>`. Механізм та імена точок описано в [FAULTS.md](../../docs/FAULTS.md).

## Реєстр і змінні

Стек бере образи з реєстру, доступного з усіх вузлів. Наведений локальний реєстр призначений
для навчального середовища, інший задається через `REGISTRY`. `docker stack deploy` не читає
`.env`, тому потрібні змінні експортуйте в оболонці.
