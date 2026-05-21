# System Design Patterns — cheat-sheet

Справочник архитектурных паттернов на основе Alex Xu, *System Design Interview – An Insider's Guide, Vol. 1*.

Цель — не учебник, а **быстрая шпаргалка**, к которой Claude обращается при архитектурных развилках на этапе `gsd-spec-phase` или `superpowers:brainstorming`. Если упёрся в выбор «что выбрать для X» — посмотри здесь, потом за деталями в книгу или `context7`.

---

## 0. 4-шаговый framework для архитектурного интервью / дизайна

(Глава 3, Сюй) — применять для любой крупной фичи на `gsd-spec-phase`.

1. **Clarify** — что строим, для кого, какой scale (DAU, RPS, объём данных), какие SLA, что ВНЕ скоупа. Зафиксировать в `SPEC.md`.
2. **High-level design** — крупными мазками: компоненты, потоки данных, API, БД-схема. Диаграмма на 5-10 блоков.
3. **Deep dive** — выбрать 2-3 самые рискованные части (обычно: БД-выбор, шардирование, кеш, очередь) и проработать детально.
4. **Wrap up** — узкие места, что сломается на 10× нагрузки, что мониторить, что улучшать в v2.

---

## 1. Scaling — масштабирование (Глава 1)

### Web/app-tier
- **Stateless серверы** + load balancer впереди → горизонтальное масштабирование тривиально. Сессия — в Redis, не в памяти процесса.
- **Vertical scaling** (мощнее железо) — лимит быстро упирается. Только как временная мера.
- **Multi-AZ / multi-region** — для HA. Active-active или active-passive failover.

### Database-tier
- **Read replicas** (master-slave) — снимает read-heavy нагрузку. Pitfall: replication lag → read-after-write breaks.
- **Multi-master** — пишет в несколько мастеров. Pitfall: write conflicts (нужен conflict resolution: LWW, vector clocks, CRDT).
- **Sharding**:
  - **Range** — по диапазону ключа. Pitfall: hot ranges.
  - **Hash** — `hash(key) mod N`. Pitfall: при добавлении/удалении ноды — пересчитывается всё. Лекарство — **consistent hashing** (см. §6).
  - **Geo** — по региону пользователя. Подходит для proximity-сервисов.
  - **Directory-based** — таблица `key → shard_id` (Vitess). Гибко, но lookup-таблица сама становится bottleneck.

---

## 2. Caching (Глава 1)

| Стратегия | Как работает | Когда |
|---|---|---|
| **Cache-aside** (lazy) | Приложение читает кеш; промах → читает БД → кладёт в кеш | универсальный default |
| **Read-through** | Кеш сам ходит в БД при промахе | когда хочешь скрыть БД от приложения |
| **Write-through** | Запись идёт в кеш + БД синхронно | strong consistency, но медленнее запись |
| **Write-back** (write-behind) | Запись в кеш, в БД асинхронно | максимальная скорость записи, риск потери при крэше |
| **Write-around** | Запись в БД минуя кеш | данные редко читаются |

**TTL** — обязательно. Без него — stale data навсегда.
**Eviction**: LRU (default), LFU (для горячих ключей), FIFO (просто).

**Pitfalls**:
- **Cache stampede** (много промахов одновременно → грохнули БД) — лекарство: lock на ключ при заполнении или probabilistic early refresh.
- **Hot key** — один ключ = вся нагрузка на одну ноду. Лекарство: replica для горячих ключей, шардирование по `key+random_suffix`.

---

## 3. Rate limiting (Глава 4)

| Алгоритм | Как | Плюс | Минус |
|---|---|---|---|
| **Token bucket** | Бакет наполняется со скоростью R, запрос ест 1 токен | плавный burst | сложнее distributed |
| **Leaky bucket** | Очередь фиксированной длины, обрабатывается с скоростью R | равномерный output | overflow → drop |
| **Fixed window counter** | Счётчик за интервал (минута), reset на границе | проще всех | spike на границе окна |
| **Sliding window log** | Лог timestamp'ов, фильтр по времени | точно | дорого по памяти |
| **Sliding window counter** | Гибрид fixed + взвешенное прошлое окно | хороший компромисс | приближённый |

**Где хранить counter**: Redis (atomic INCR + EXPIRE).
**Что делать при превышении**: 429 Too Many Requests + `Retry-After` header.

---

## 4. Unique ID generation (Глава 7)

| Способ | Плюс | Минус |
|---|---|---|
| **UUID v4** | децентрализованный, не нужен координатор | 128 бит, не сортируемый по времени |
| **Auto-increment в БД** | простой, sortable | single point, плохо для распределённой системы |
| **Snowflake** (Twitter) | 64 бит, sortable по времени, distributed | clock skew между нодами → дубликаты |
| **ULID / KSUID** | sortable + collision-resistant, текстовый | чуть длиннее UUID |
| **Database ticket server** | sortable | bottleneck |

**Snowflake формат** (64 бит): `1 sign | 41 timestamp | 10 machine_id | 12 sequence` → ~4096 ID/мс на машину.

В **VibecoSwaga reference**: UUID v4 в Postgres (`uuid-ossp` или генерация на бэке). Для high-throughput — переключайся на Snowflake/ULID.

---

## 5. Communication patterns (Главы 11, 12)

| Паттерн | Когда | Pitfall |
|---|---|---|
| **Short polling** (REST GET каждые N сек) | простые таймеры, низкая частота обновлений | нагрузка растёт линейно с клиентами |
| **Long polling** | хочешь real-time, не хочешь WebSocket | сервер держит соединение → ресурсы |
| **WebSocket** | bidirectional real-time (чат, игры) | нужен sticky session или Redis pub-sub |
| **SSE (Server-Sent Events)** | server → client one-way (ленты, нотификации) | only HTTP/1.1 keep-alive, нет binary |
| **Webhook** | внешний сервис → твой бэк (Stripe, GitHub) | retry policy, idempotency обязательна |
| **gRPC** | service-to-service внутри инфры | сложнее debug, меньше tooling |

**Для маркетплейса часов**: WebSocket для модераторской очереди (live updates), SSE для уведомлений покупателю «продавец принял заказ».

---

## 6. Consistent hashing (Глава 5)

Кольцо `[0, 2^32)`. Каждая нода — точка на кольце. Ключ → `hash(key)` → ближайшая по часовой стрелке нода. Добавили/убрали ноду — переезжает только `1/N` ключей.

**Pitfall**: неравномерное распределение (если N=3 — кто-то может схватить 50% кольца). Лекарство — **virtual nodes**: каждая физ-нода = M точек на кольце (M ≈ 100-200).

**Где применить в маркетплейсе**: шардирование `listings` по `seller_id` или `listing_id`, шардирование Redis-кеша.

---

## 7. Search & discovery

### Autocomplete (Глава 13)
**Trie** с топ-K кешированными в каждом узле. Обновление офлайн (раз в час из аналитики). На горячем пути — только лукап.

### Full-text search
**Elasticsearch / OpenSearch** — inverted index. Не дёргать на каждом запросе; денормализовать read-модель в индекс через очередь.

### Geo-queries (proximity, Vol. 2)
- **Geohash** — кодирует lat/lon в строку, префикс = ячейка. Подходит для Redis (sorted sets).
- **Quadtree** — рекурсивное деление 2D-пространства. Лучше для неравномерных распределений.
- **PostGIS** — если уже на Postgres, не плодить ещё одну БД.

**Для маркетплейса часов**: «пункты проверки рядом со мной» → PostGIS на Postgres (уже есть в стеке) + index `ST_GIST`.

---

## 8. Notification system (Глава 10)

Слои:
1. **Trigger** (event published) — чаще всего из бизнес-сервиса.
2. **Notification service** — формирует payload, выбирает каналы (email/SMS/push/in-app) по preferences пользователя.
3. **Provider** — Twilio, SendGrid, FCM, APNs.
4. **Worker** — асинхронный, через очередь.

**Patterns**:
- **Idempotency key** — уведомление не отправляется дважды при retry.
- **Rate limit per user** — антиспам.
- **Quiet hours** — учёт таймзоны.
- **Template + i18n** — шаблоны в БД, не в коде.

---

## 9. News feed / activity feed (Глава 11)

Два подхода к выдаче:

| Подход | Как | Когда |
|---|---|---|
| **Pull (fan-out on read)** | при запросе ленты — джойн «мои подписки + их посты» | мало активных читателей, много пишут |
| **Push (fan-out on write)** | при создании поста — записать в ленту каждого подписчика | много читают, мало пишут (большинство соц-сетей) |
| **Hybrid** | push для обычных, pull для celebrity (millions of followers) | реальные системы |

**Для маркетплейса**: «новые объявления по моему фильтру» → pull (фильтры персональные, дешевле каждый раз искать чем хранить ленту).

---

## 10. Reliability & correctness

### Idempotency (упоминается в главах 10, 12)
Любая мутирующая операция через сеть — обязана быть **идемпотентной**. Клиент шлёт `Idempotency-Key`, сервер хранит результат N часов и возвращает его повторно для того же ключа.

**Без этого**: retry → дубль платежа, дубль уведомления, дубль заказа. **Особенно критично для escrow/payments**.

### Circuit breaker
Падает downstream сервис → не долбить его retry'ами, а сразу возвращать ошибку клиенту. Через N секунд — пробуем half-open, успех → закрываем.

### Retry with exponential backoff + jitter
`delay = base * 2^attempt + random(0, jitter)`. Без jitter — все клиенты ретраят синхронно и кладут upstream повторно.

### Quorum (key-value stores, Vol. 1 Ch. 6)
W (writes) + R (reads) > N (replicas) → strong consistency.
Tradeoff: больше W/R → надёжнее, но медленнее.

### Read-your-writes consistency
Пользователь только что обновил профиль → должен увидеть обновление. Лекарство: его reads до конца сессии шлются на master, не на read replica.

---

## 11. Async work (Главы 9, 11)

**Очередь сообщений** (RabbitMQ, SQS, Redis Streams) — для:
- email/push отправки
- AI-модерации (heavy ML inference)
- генерации thumbnails / превью
- webhook-доставки во внешние системы

**Pub/Sub** (Kafka, NATS) — для event-driven архитектуры:
- `ListingApproved` → `notifications`, `search-indexer`, `analytics` независимо подписываются
- replay возможен (Kafka хранит события)

**Pitfalls**:
- **At-least-once delivery** — сообщение придёт ≥1 раз → consumer обязан быть идемпотентным.
- **DLQ (dead letter queue)** — для сообщений, которые упали N раз.
- **Ordering** — гарантируется только в пределах partition (Kafka), не глобально.

---

## 12. Storage choices (Главы 6, 14, 15)

| Тип | Когда | Примеры |
|---|---|---|
| **RDBMS** (Postgres, MySQL) | транзакции, joins, schema, ACID | default для бизнес-данных |
| **Key-value** (Redis, DynamoDB) | сессии, кеш, счётчики, leaderboards | низкая latency |
| **Document** (MongoDB, Firestore) | гибкая схема, вложенные доки | прототипы, CMS |
| **Wide-column** (Cassandra, ScyllaDB) | timeseries, write-heavy, distributed | логи, метрики, события |
| **Graph** (Neo4j) | связи > сущности (соцсеть, fraud detection) | редко default |
| **Object** (S3, MinIO) | файлы, медиа, бэкапы | картинки часов в маркетплейсе |
| **Search** (Elasticsearch) | full-text, фильтры по N полям | каталог объявлений |

**Default для VibecoSwaga**: Postgres + Redis (cache+sessions+queue). S3-совместимый для медиа. Elastic — добавляется когда search действительно нужен (не на старте).

---

## 13. Когда обращаться к этой шпаргалке

- **`gsd-spec-phase`** — на шаге «технические ограничения» проверить, не пропустил ли ключевой паттерн (rate limit, idempotency, кеш).
- **`superpowers:brainstorming`** — когда есть развилка «как делать X».
- **Stop-rule (CLAUDE.md §5)** — если 3-й раз не получается решить архитектурную проблему — посмотри сюда, потом `context7`, потом ищи реальный пример в книге.

Если в шпаргалке нет паттерна — добавь его сюда после того, как разобрался. Шпаргалка живёт вместе с проектом.
