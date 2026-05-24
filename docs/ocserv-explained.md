# Как работает ocserv — разбор от и до

Этот файл объясняет нашу VPN-часть стенда **простым языком**: как устроен сам ocserv (что за процессы, как они общаются), как он связан с PAM/LDAP/TOTP, и что значит каждая строка в нашем `config/ocserv.conf`.

---

## 1. Что такое ocserv

**ocserv** = OpenConnect VPN server. Это **open-source реализация Cisco AnyConnect**-протокола. Любой клиент, который умеет AnyConnect (включая `openconnect` CLI на Mac/Linux/Windows и сам Cisco Secure Client), может коннектиться к ocserv так, будто это настоящий Cisco-сервер.

**Что значит «AnyConnect-протокол»** на пальцах:
- Клиент устанавливает **TLS-соединение** на 443/tcp.
- По нему происходит **аутентификация** (HTTP-подобные запросы внутри TLS): `POST /auth` с логином, ответами на prompts, и т.д.
- Сервер выдаёт **session cookie**.
- Клиент открывает **второе соединение** (тоже TLS) и шлёт `CONNECT` с этим cookie. Этот канал становится **CSTP-туннелем** (Cisco SSL Tunneling Protocol).
- Опционально клиент поднимает **третье соединение** по DTLS (TLS поверх UDP) — для трафика, чтобы не было TCP-over-TCP замедлений. Это типичный AnyConnect шаблон.

---

## 2. Архитектура процессов внутри ocserv

ocserv — это **не один процесс**, а небольшая иерархия. Это важно знать чтобы понимать логи:

```
┌─────────────────────────────────────────────────────────────┐
│  ocserv main (PID 1 в контейнере)                            │
│  Слушает 443/tcp и 443/udp.                                  │
│  На каждое соединение спавнит worker.                        │
└─────────────────────────────────────────────────────────────┘
           │
           ├──► ocserv-sm (sec-mod)
           │    Изолированный процесс безопасности.
           │    ЕДИНСТВЕННЫЙ имеет доступ к приватному ключу серта,
           │    к PAM-конверсации, к secrets. Workers ходят к нему
           │    через unix-socket чтобы что-то «дешифровать» или
           │    «попросить пароль».
           │
           ├──► worker (один на одно клиентское соединение)
           │    Делает TLS handshake (за помощью к sec-mod),
           │    общается с клиентом, инициирует auth через sec-mod.
           │    Когда клиент логинится — поднимает туннель и
           │    форвардит пакеты между TUN и TLS.
           │
           └──► worker (ещё один клиент)
                ...
```

**В логах ты часто видишь** что-то вроде:
```
ocserv[1]:  main: ...           ← main-процесс
ocserv[27]: sec-mod: ...        ← sec-mod
ocserv[34]: worker[alice]: ...  ← конкретный worker, обслуживающий alice
```

Цифра в `[27]` — это PID. По ним можно отслеживать одну сессию через все процессы.

**Почему такое разделение:** worker — это «зона недоверия», она напрямую трогает сетевой ввод от клиента. Если у worker'а уязвимость и его взломают, у него **нет доступа к** приватному ключу серта или /etc/shadow. Только sec-mod знает секреты. Это аналог privilege separation в OpenSSH (sshd → privsep).

В нашем лабе мы упростили: `isolate-workers = false` → workers НЕ запускаются под `nobody`. В проде надо `true`.

---

## 3. Как ocserv связан с PAM + LDAP + TOTP (наш стек)

Это **самая важная картинка** — кто что проверяет:

```
Клиент → openconnect → ocserv worker
                          │
                          │ "пользователь alice хочет войти"
                          ↓
                       sec-mod
                          │
                          │ открывает PAM-сессию для "alice"
                          ↓
                  ┌───────────────────────────────────────────────┐
                  │ /etc/pam.d/ocserv (наш PAM-стек)             │
                  ├───────────────────────────────────────────────┤
                  │ 1. pam_listfile.so   sense=deny               │
                  │    Проверка: alice в /etc/ocserv/control/     │
                  │              disabled-users?                  │
                  │    Если ДА → STOP, return DENY                │
                  │    Если НЕТ → дальше                          │
                  ├───────────────────────────────────────────────┤
                  │ 2. pam_ldap.so       (auth)                   │
                  │    Запрашивает у клиента "Password:"          │
                  │    → nslcd → bind в LDAP как uid=alice + pwd  │
                  │    Если LDAP сказал ok → дальше               │
                  │    Если нет → STOP, return DENY               │
                  ├───────────────────────────────────────────────┤
                  │ 3. pam_google_authenticator.so                │
                  │    Запрашивает "Verification code:"           │
                  │    Читает /home/alice/.google_authenticator   │
                  │    Сверяет TOTP-код с current time            │
                  │    Если ok → дальше                           │
                  ├───────────────────────────────────────────────┤
                  │ 4. pam_ldap.so       (account)                │
                  │    nslcd ищет alice в группе vpn-users.       │
                  │    pam_authz_search фильтр:                   │
                  │      memberUid=alice в cn=vpn-users?          │
                  │    Если да → ALLOW, auth complete             │
                  │    Если нет → DENY (даже после правильного    │
                  │              пароля и TOTP)                   │
                  └───────────────────────────────────────────────┘
                          │
                          ↓
                  возвращает в sec-mod: SUCCESS / FAILURE
                          │
                          ↓
                  worker получает сигнал «всё ок»,
                  выдаёт клиенту IP 192.168.99.x,
                  поднимает TUN,
                  поехали пакеты
```

**Каждый шаг — отдельный модуль**, можно поменять/добавить. Например, можно вставить **`pam_faillock.so`** для блокировки после N неудачных попыток — это всё конфиг, без пересборки ocserv.

---

## 4. Наш `config/ocserv.conf` строка за строкой

Файл живёт в `/Users/timderbak/vpn-totp-test/config/ocserv.conf` и монтируется в контейнер.

### Логирование

```ini
log-level = 4
```
Уровень детальности логов. От 0 (только ошибки) до ~9 (трассировка каждого пакета). У нас **4** = INFO + DEBUG, чтобы видеть `PAM-auth conv:` и `received auth reply message`. В проде `0` или `1`.

### Ban-система

```ini
max-ban-score = 0
ban-reset-time = 1
```
ocserv по дефолту копит «штрафные баллы» за каждое подозрительное событие (битый TLS, неверный пароль). Когда `score >= max-ban-score` — IP блокируется на `ban-reset-time` секунд. У нас **отключено** (`max-ban-score = 0`), потому что **Docker Desktop сам стучится на 4443 для health-check**, шлёт битые TCP-пакеты — ocserv их считает за атаку и забанил бы хост. Для прода надо вернуть нормальные значения (типа `max-ban-score = 50`, `ban-reset-time = 300`).

### Аутентификация

```ini
auth = "pam[gid-min=1000]"
```
Главная директива. Говорит ocserv: «используй **PAM** для проверки». Параметр `gid-min=1000` — фильтр на уровне ocserv: «принимай только юзеров с primary GID >= 1000». Это страховка чтобы случайно нельзя было залогиниться как `root`, `daemon` и т.п. У наших LDAP-юзеров GID = 2001, 2002, ... — все проходят фильтр.

**После этой строки ocserv знает только что использовать PAM. Какой именно PAM-стек — это уже в `/etc/pam.d/ocserv`** (см. секцию 3 выше).

### Слушающие сокеты

```ini
tcp-port = 443
udp-port = 443
```
Внутри контейнера слушаем 443 (для TCP — это CSTP-туннель, для UDP — DTLS). Docker-compose маппит хост-порт `4443 → 443`. То есть клиент с хоста идёт на `localhost:4443`, попадает на 443 внутри ocserv.

### TLS / сертификат

```ini
server-cert = /etc/ocserv/ssl/server-cert.pem
server-key  = /etc/ocserv/ssl/server-key.pem
```
Где лежат серт и приватный ключ. Их создаёт `scripts/entrypoint.sh` при первом старте через `certtool` (gnutls). У нас self-signed CA + server-cert с SAN-ом на `vpn.local`, `localhost`, `127.0.0.1`.

### Процесс-модель

```ini
isolate-workers = false
```
Workers НЕ форкаются под отдельного юзера. PAM (в частности pam_unix, который мы убрали но шаблонно) требует root-доступ к /etc/shadow. Раздельный privsep сложен для лаба. В проде надо `true`.

```ini
socket-file = /var/run/ocserv-socket
use-occtl   = true
pid-file    = /var/run/ocserv.pid
```
`occtl` — это CLI-утилита для управления ocserv на лету: посмотреть текущие сессии, дисконнект юзера, перезагрузка конфига. Общается с ocserv через unix-socket. Попробуй: `docker exec ocserv occtl show users`.

### Имя

```ini
default-domain = vpn.local
```
Косметика. Некоторые клиенты показывают это в UI.

### Лимиты

```ini
max-clients      = 16
max-same-clients = 2
```
Не больше 16 одновременных VPN-сессий вообще; не больше 2 от одного юзера. Защита от исчерпания ресурсов.

### Таймауты

```ini
keepalive       = 32400   # ~9 часов
dpd             = 90      # Dead Peer Detection раз в 90 сек
mobile-dpd      = 1800    # для мобильных — раз в 30 мин (батарея)
auth-timeout    = 240     # 4 минуты на ввод пароля + TOTP
min-reauth-time = 300     # 5 минут throttle после fail'ной auth
cookie-timeout  = 300     # session cookie живёт 5 мин до connect
```
**DPD (Dead Peer Detection)** — ocserv периодически шлёт keepalive-пакет клиенту и ждёт ответа. Если нет ответа N раз — дисконнект. Иначе зомби-сессии копились бы навсегда.

**`auth-timeout = 240`** — у тебя 4 минуты с момента нажатия Connect, чтобы успеть ввести пароль и TOTP. Иначе сервер скажет `auth expired`.

### MTU discovery

```ini
try-mtu-discovery = true
```
Автоматически определяет максимальный размер пакета через канал. Без этого бывают пакости вида «connect успешен, но половина сайтов не открывается» — пакет 1500 байт не пролезает, мелкие пролезают.

### Сеть для клиентов

```ini
ipv4-network = 192.168.99.0
ipv4-netmask = 255.255.255.0
```
Клиентский пул адресов. Когда alice коннектится — ей выдаётся, например, `192.168.99.50`. Это её адрес «внутри туннеля». Это **не** адрес контейнера, это адрес в виртуальной подсетке туннеля.

### Split-tunnel (КРИТИЧНО на macOS)

```ini
route = 192.168.99.0/255.255.255.0
```
ocserv по дефолту пушит клиенту **default-route** (`0.0.0.0/0`) — это значит «весь твой трафик в туннель». На macOS+Docker это убьёт интернет хоста: трафик попадает в туннель → в контейнер → у контейнера никакого выхода в интернет (так у Docker Desktop устроено) → ничего не работает.

С этой строкой клиент роутит **только подсетку 192.168.99.0/24** в туннель. Остальное идёт мимо, по обычному пути. Это **split-tunnel** режим. Безопаснее для лаба.

```ini
#dns = 1.1.1.1
```
Если бы был full-tunnel — пушили бы DNS клиенту. В split-режиме не нужно. Закомментировано чтобы macOS-овский `vpnc-script` не пытался переписать `/etc/resolv.conf` и не ругался.

### TUN-устройство

```ini
device = vpns
```
Имя tun-интерфейса который ocserv создаёт внутри контейнера. Можно увидеть: `docker exec ocserv ip addr show vpns`.

### IP-стабильность

```ini
predictable-ips = true
```
alice всегда получает один и тот же IP при реконнекте. Хеш от username → IP в пуле. Удобно для скриптов / firewall-правил.

### Cisco-совместимость

```ini
cisco-client-compat = true
```
Включает несколько мелких quirks которые ожидает оригинальный Cisco AnyConnect-клиент: некоторые специфические HTTP-заголовки, формат XML-профиля, поведение при cookie expiry. Без этого openconnect-CLI всё равно работает (он гибкий), но Cisco Secure Client может капризничать.

### Re-key

```ini
rekey-time   = 172800   # 48 часов
rekey-method = ssl
```
Каждые 48 часов ocserv инициирует re-negotiation TLS-ключа сессии (новый session-key, тот же серт). Гигиена: чем меньше один ключ использован для шифрования — тем меньше материала у злоумышленника. `rekey-method = ssl` = делать это через стандартный TLS renegotiation.

### Прочее

```ini
deny-roaming = false
```
Разрешить клиенту менять source-IP в течение сессии (например когда телефон переключился с Wi-Fi на 4G). Если `true` — при смене IP сессия рвётся. У нас `false` — удобнее для мобильных.

```ini
ping-leases = false
```
Не пинговать клиентский IP перед выдачей нового. Полезно только если у тебя были «зомби»-лизы и ты хочешь чтобы старые не утекали. У нас не актуально.

---

## 5. Что НЕ описано в нашем конфиге, но стоит знать

ocserv имеет ещё ~70 опций. Я перечислю те, которые **скорее всего понадобятся** если будешь двигать стенд к продакшену.

| Опция | Что делает | Когда нужна |
|---|---|---|
| `tls-priorities` | Список разрешённых TLS-ciphers | Compliance / отключить старые слабые шифры |
| `compression = true` | LZ4-сжатие данных в туннеле | Узкий канал |
| `select-group` + `config-per-user` | Разные настройки для разных юзеров (например split-tunnel для одних, full для других) | Когда юзеры разной категории |
| `user-profile = /path/to/profile.xml` | XML-профиль, отдаваемый AnyConnect | Когда хочешь чтобы Cisco-клиент сам подхватил настройки без ручной правки |
| `camouflage = true` | Запросы без правильного User-Agent получают HTTP 404 — VPN выглядит как обычный web-сервер | Анти-цензура (DPI bypass) |
| `listen-clear-file` | Дополнительный сокет без TLS для http-only auth | Когда впереди reverse-proxy с TLS-termination |
| `cgroup` | Запуск в cgroup для ограничения CPU/RAM | Кооперативный shared-хост |
| `pre-login-banner` / `motd` | Текст, показываемый клиенту перед auth | Юридический ban-banner |

---

## 6. Лайфхаки для отладки

### Посмотреть кто сейчас подключён
```bash
docker exec ocserv occtl show users
```
Выведет таблицу: ID, username, since, IP, bytes-in/out.

### Принудительно дисконнект юзера
```bash
docker exec ocserv occtl disconnect user alice
```

### Перечитать конфиг без рестарта
```bash
docker exec ocserv occtl reload
```

### Понять почему отказали в auth
1. В контейнере есть `/var/log/auth.log` (через rsyslog) — там debug от PAM
2. Контейнерные логи (`docker compose logs ocserv`) — там worker/sec-mod конверсация
3. `docker exec ocserv pamtester ocserv alice authenticate` — изолированный тест PAM-стека без VPN-клиента, очень помогает

### Включить debug ocserv на лету
```bash
docker exec ocserv occtl reload   # сначала измени log-level в config
```
Или через `--debug=9` в команде запуска (`exec ocserv --debug=9 ...` в entrypoint.sh).

---

## 7. Что почитать дальше

- `man ocserv.conf` — авторитетная справка по всем директивам (`docker exec ocserv man ocserv.conf` если man-pages поставлены)
- `man ocserv` — флаги командной строки + общая модель
- `man occtl` — все команды управления
- Исходники ocserv на gitlab: https://gitlab.com/openconnect/ocserv — особенно `src/worker-auth.c` (вся логика auth-state-machine) и `src/sec-mod-auth.c` (PAM-конверсация)
- Тут же `src/ipc.proto` — формат сообщений между worker и sec-mod (там же определены значения `AUTH_REP` = OK/MSG/FAILED, о которых я тебе говорил раньше)
- Wiki OpenConnect-клиента: https://www.infradead.org/openconnect/ — особенно секция «vpnc-script» которая объясняет что происходит на стороне клиента после connect

Если есть конкретные вопросы по конкретной фразе из конфига или непонятно почему какая-то опция нужна — спрашивай, разверну.
