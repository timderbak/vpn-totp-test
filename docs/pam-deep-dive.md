# PAM-аутентификация: как это всё работает

Полный разбор **только** аутентификационной части. Без сети, без TLS, без конфига ocserv-сетевых штук. Только: что такое PAM, как ocserv с ним разговаривает, что делает каждый модуль в нашем стеке, и почему именно так.

---

## 1. Что вообще такое PAM

**PAM = Pluggable Authentication Modules.** Линуксовый стандарт «не пиши auth-код сам, используй модули из системы».

Без PAM: каждое приложение (sshd, login, sudo, ocserv, …) пишет свою логику «проверь пароль → проверь не expired ли → проверь не в blocklist'е → ...». Это адский повтор кода и адский разнобой.

С PAM: приложение говорит **«я хочу авторизовать юзера»** → PAM открывает файл `/etc/pam.d/<имя приложения>` → читает оттуда список модулей → запускает их по очереди → отдаёт обратно `SUCCESS` или `FAILURE`. Приложение само НЕ знает что за модули, как они проверяют, и т.п.

**Конкретно у нас:** ocserv в конфиге написано `auth = "pam[gid-min=1000]"`. Значит ocserv открывает `/etc/pam.d/ocserv` и доверяет ему всю auth-логику. Если завтра ты захочешь добавить, скажем, аутентификацию через биометрию или Kerberos — меняешь только этот файл, **ocserv не пересобирается**.

---

## 2. Анатомия `/etc/pam.d/<service>` файла

Каждая строка имеет 3 обязательных поля + аргументы:

```
<type>  <control>  <module>  [args...]
```

**`type`** — на какой стадии работает модуль:
| Type | Когда вызывается | У нас используется? |
|---|---|---|
| `auth` | Проверка «кто ты» (пароль, токен, биометрия) | Да, основная фаза |
| `account` | Проверка «можно ли тебе вообще» (не expired, не disabled, не вне рабочих часов) | Да, для проверки членства в vpn-users |
| `password` | Смена пароля | Нет — мы пароли через ocserv не меняем |
| `session` | Действия при начале/конце сессии (mount home, открыть лог) | Да, no-op (`pam_permit`) — ocserv shell не спавнит |

**Важный момент:** auth и account — это **две разные стадии** одной аутентификации. PAM запускает их **последовательно**: сначала весь auth-стек, потом весь account-стек, и потом если оба сказали ОК — финальный ALLOW. Поэтому у нас `pam_ldap.so` появляется **дважды** в файле — один раз как `auth` (проверь пароль), второй раз как `account` (проверь группу).

**`control`** — что делать с результатом модуля:
| Control | Поведение |
|---|---|
| `required` | Модуль ДОЛЖЕН вернуть SUCCESS. Если FAIL — стек продолжает выполняться (важно!), но финальный результат уже FAILURE. |
| `requisite` | То же что required, **но** при FAIL стек обрывается немедленно. Без «продолжать для маскировки». |
| `sufficient` | Если SUCCESS — стек завершается с SUCCESS немедленно (пропуская дальнейшие модули). Если FAIL — игнорируется. |
| `optional` | Результат влияет только если он единственный в стеке. |

**Тонкость про `required` vs `requisite`:** `required` всегда **дочитывает весь стек**, даже если уже точно знает что fail. Зачем? **Чтобы не отдать timing-инфу атакующему.** Если pam_unix падает мгновенно при неверном логине, а только на правильном логине переходит к pam_google_authenticator (медленному) — атакующий по времени ответа понимает: «о, этот логин валидный». С `required` всё всегда выполняется до конца, время ответа одинаковое.

`requisite` использует когда хочется быстро прервать на самой первой проверке (например, denylist) — в этом случае timing-leak не критичен, потому что речь не о валидности логина.

**Поэтому в нашем стеке:**
- `pam_listfile` → `requisite` (denylist — нет смысла продолжать)
- остальные `auth` → `required` (продолжаем для timing-консистентности)
- `account` → `required` (стандарт)

**`module`** — имя `.so`-файла. Лежит в `/lib/<arch>/security/`. Например `/lib/aarch64-linux-gnu/security/pam_ldap.so`. PAM найдёт сам.

**`args`** — флаги конкретного модуля. Документированы в man-странице модуля (`man pam_listfile`, `man pam_google_authenticator`).

---

## 3. Наш PAM-стек целиком, с комментариями

Файл `pam/ocserv` (монтируется в `/etc/pam.d/ocserv`):

```
# 1. Denylist managed by admin panel (shared volume ocserv-control).
auth requisite pam_listfile.so onerr=succeed item=user sense=deny \
     file=/etc/ocserv/control/disabled-users

# 2. Password via LDAP. pam_ldap reads /etc/nslcd.conf via nslcd daemon.
auth required pam_ldap.so

# 3. Second factor — TOTP. File in /home/<u>/.google_authenticator.
auth required pam_google_authenticator.so debug

# Account-stage: pam_ldap enforces membership in pam_groupdn (vpn-users).
account required pam_ldap.so

# Session: ocserv never spawns a shell — pam_permit suffices.
session required pam_permit.so
```

Разбираю модуль за модулем.

---

### 3.1 `pam_listfile.so` — denylist

```
auth requisite pam_listfile.so onerr=succeed item=user sense=deny \
     file=/etc/ocserv/control/disabled-users
```

**Аргументы:**
| Arg | Значение |
|---|---|
| `item=user` | Что проверяем — это **username** (а не PAM_RHOST или PAM_TTY). |
| `sense=deny` | Файл — это **deny-list**. Если username **в файле** → возвращай FAIL. (Альтернатива: `sense=allow` — allow-list.) |
| `file=/etc/ocserv/control/disabled-users` | Путь к файлу. Одна username на строку. |
| `onerr=succeed` | Если файл недоступен (нет, не читается) → **возвращай SUCCESS** (fail-open). Альтернатива: `fail` (fail-closed). |

**`onerr=succeed` — это компромисс безопасности**, но обоснованный: если denylist-файл случайно пропал, мы не хотим заблокировать всех. Защита от revoke всё равно есть на следующем уровне: при revoke админка ещё и удаляет `.google_authenticator`, так что без TOTP-секрета юзер всё равно не войдёт (pam_google_authenticator на следующем шаге свалится).

**Что происходит на практике:**
- alice пытается подключиться → PAM открывает `/etc/ocserv/control/disabled-users` → если там есть строка `alice` → возвращает `PAM_AUTH_ERR` (= FAILURE).
- Из-за `requisite` стек **обрывается немедленно**. PAM не доходит даже до prompt'а пароля. Поэтому пользователь видит «отказ» мгновенно, без ввода чего-либо. Это то поведение которое мы хотели для revoke.

---

### 3.2 `pam_ldap.so` (auth stage) — проверка пароля

```
auth required pam_ldap.so
```

**Без аргументов** — у нашего libpam-ldapd модуля все аргументы берутся из конфига **nslcd**, а не из PAM-строки.

**Что происходит:**

1. PAM просит pam_ldap.so вернуть SUCCESS/FAIL для auth-стадии.
2. pam_ldap.so говорит ocserv'у: **«спроси у клиента пароль»** через callback (см. секцию 4 ниже).
3. Клиент возвращает строку — это пароль alice.
4. pam_ldap.so передаёт `(alice, "123")` в **nslcd** по UNIX-сокету `/var/run/nslcd/socket`.
5. nslcd берёт это, делает к LDAP-серверу:
   - сначала **anonymous search**: «найди DN юзера с uid=alice». Результат: `uid=alice,ou=users,dc=vpn,dc=local`.
   - потом **bind**: «попробуй залогиниться под этим DN с паролем `123`».
   - LDAP-сервер пробует bind → если хеш в `userPassword` совпадает с `{SSHA}` от введённого пароля → bind success.
6. nslcd возвращает pam_ldap.so: `PAM_SUCCESS` или `PAM_AUTH_ERR`.
7. pam_ldap.so возвращает PAM.

**Ключевое:** мы НЕ кладём пароль alice в наш конфиг. Пароль идёт прямо от клиента → через PAM-conversation → через nslcd-socket → в LDAP **в открытом виде** (но всё внутри TLS-туннеля к LDAP, либо внутри docker-network). LDAP сам сравнивает хеш.

**Что nslcd использует из своего конфига:**
- `uri ldap://ldap:389` — куда стучаться
- `binddn cn=admin-readonly,dc=vpn,dc=local` + `bindpw …` — service account для anonymous search (на случай если anonymous search в LDAP запрещён, а у нас он именно так)
- `base passwd ou=users,dc=vpn,dc=local` — где искать `(uid=alice)`

---

### 3.3 `pam_google_authenticator.so` — TOTP

```
auth required pam_google_authenticator.so debug
```

**Аргументы:**
| Arg | Значение |
|---|---|
| `debug` | Включает подробные логи в syslog (`/var/log/auth.log`). У нас включено для лаба. |

**Что происходит:**

1. PAM просит модуль вернуть SUCCESS/FAIL.
2. Модуль читает **`~alice/.google_authenticator`** (это secret-файл с base32-секретом и флагами). Путь определяется по `getpwnam("alice")` → `pw_dir` → home directory. У нас это `/home/alice/` (LDAP-аттрибут `homeDirectory`).
3. Файл должен иметь permissions `0400` или `0600` и owner = тот же юзер. Иначе модуль откажет (security: чтобы никто кроме alice не мог украсть её секрет).
4. Содержимое файла:
   ```
   BD3E7EB3ZNKIXDQOIPYZBQL35G2LVRLG    ← base32 secret
   " RATE_LIMIT 3 30                    ← rate-limit-флаги
   " DISALLOW_REUSE 59320682             ← последний использованный TOTP-step
   " TOTP_AUTH                           ← TOTP режим (vs HOTP)
   " WINDOW_SIZE 3                       ← ± 3 шагов tolerance
   91295600                              ← scratch (emergency) codes
   07927126
   ...
   ```
5. Модуль через callback просит у ocserv'а: **«спроси у клиента verification code»**.
6. Клиент возвращает 6-значное число.
7. Модуль:
   - Берёт текущее unix-time, делит на 30 → текущий **step**.
   - Считает HMAC-SHA1(secret, step) → берёт 6 цифр.
   - Сравнивает с введённым кодом. Также пробует step±1 (т.к. `WINDOW_SIZE 3`).
   - Если совпало — проверяет, что этот step **не в DISALLOW_REUSE** (защита от replay в пределах окна).
   - Если ok — пишет step в DISALLOW_REUSE → возвращает SUCCESS.
   - Иначе — пробует sравнить с scratch-codes (если клиент ввёл 8-значное). Если совпало — вычёркивает scratch-код из файла, SUCCESS.
   - Иначе — FAIL.

**Что у нас прописано в файле** (специфическое для google-authenticator):
- `RATE_LIMIT 3 30` — не более 3 успешных аутентификаций за 30 секунд (сам модуль трекает; для нас не критично).
- `DISALLOW_REUSE` — без аргументов изначально, по мере использования копит steps.
- `TOTP_AUTH` — режим TOTP (counter = время / 30).
- `WINDOW_SIZE 3` — допускает коды из step-1, step, step+1.

**Где модуль логирует:**
- В `/var/log/auth.log` через syslog (если rsyslog запущен — у нас да).
- Строки вида `Accepted google_authenticator for alice` (успех) или `Invalid verification code` (фейл).
- Если включён `debug` — ещё `start of google_authenticator for "alice"`, `Secret file permissions are 0400`, и т.д.

---

### 3.4 `pam_ldap.so` (account stage) — проверка членства в группе

```
account required pam_ldap.so
```

Та же самая `.so`-библиотека, но вызывается на **другой стадии** PAM (`account`, не `auth`).

**Что происходит:**

1. PAM спрашивает: «alice прошла auth-стек. Разрешено ли ей в принципе пользоваться этим сервисом?»
2. pam_ldap.so передаёт вопрос в nslcd: «alice valid?»
3. nslcd смотрит на свою конфигурацию **`pam_authz_search`**:
   ```
   pam_authz_search (&(objectClass=posixGroup)(cn=vpn-users)(memberUid=$username))
   ```
4. nslcd подставляет `$username = alice` → получает фильтр:
   ```
   (&(objectClass=posixGroup)(cn=vpn-users)(memberUid=alice))
   ```
5. Делает к LDAP search с этим фильтром. Если хоть один результат — alice **в группе vpn-users** → SUCCESS. Если 0 результатов → FAIL.

**Почему именно так, а не через `memberOf`:** атрибут `memberOf` — это **виртуальный** атрибут, который добавляется в LDAP только если включён `memberOf overlay` (его надо ставить плагином). У дефолтного OpenLDAP его нет. Стандартный `posixGroup` хранит членство через `memberUid` **внутри объекта группы**, не на стороне юзера. Поэтому мы ищем группу и проверяем что username в её `memberUid`.

**Это объясняет почему наш стек НЕ пускает carol если её нет в группе:** auth (пароль) пройдёт, TOTP пройдёт (если есть `.google_authenticator`), но account вернёт FAIL → итоговый FAILURE.

---

### 3.5 `pam_permit.so` (session) — no-op

```
session required pam_permit.so
```

session-стадия запускается **уже после** успешного auth+account, обычно для подготовки окружения юзера: mount home-папки, открыть лог-файл, инициализировать переменные.

Нам это не нужно — ocserv **не запускает shell под юзером**, не открывает файлы от его имени. Туннель — это просто пересылка пакетов между TCP и TUN. Поэтому `pam_permit.so` (всегда возвращает SUCCESS) — заглушка.

---

## 4. Как ocserv разговаривает с PAM (callback-механика)

Самая магическая часть. Как pam_ldap «просит у клиента пароль», если pam_ldap не знает что такое VPN-клиент?

**Ответ:** PAM использует **conversation function** — callback, который ocserv регистрирует при открытии PAM-сессии.

Псевдокод что делает sec-mod процесс ocserv'а при auth:

```c
// 1. Открываем PAM-сессию для юзера "alice"
pam_handle_t *pamh;
struct pam_conv conv = { my_conversation_fn, /* userdata */ };
pam_start("ocserv", "alice", &conv, &pamh);

// 2. Запускаем auth-стек
int ret = pam_authenticate(pamh, 0);

// 3. Запускаем account-стек
if (ret == PAM_SUCCESS) {
    ret = pam_acct_mgmt(pamh, 0);
}

// 4. Финал
if (ret == PAM_SUCCESS) {
    // юзер пущен в VPN
} else {
    // отказ
}
```

**Что такое `my_conversation_fn`:**

```c
int my_conversation_fn(int num_msg, const struct pam_message **msgs,
                       struct pam_response **resp, void *userdata)
{
    // PAM-модуль вызвал нас и хочет показать юзеру msgs (один или несколько prompt'ов)
    // и получить от него ответы (resp).
    for (i = 0; i < num_msg; i++) {
        switch (msgs[i]->msg_style) {
            case PAM_PROMPT_ECHO_OFF:   // "Password:" — не эхать ввод
            case PAM_PROMPT_ECHO_ON:    // "Username:" — эхать
                // 1) шлём через ocserv worker → клиенту → openconnect показывает prompt
                send_prompt_to_client(msgs[i]->msg);
                // 2) ждём ответ от клиента (HTTP POST с введённой строкой)
                resp[i]->resp = wait_for_client_response();
                break;
            case PAM_TEXT_INFO:         // информационное сообщение (без ввода)
                show_info_to_client(msgs[i]->msg);
                break;
            case PAM_ERROR_MSG:
                show_error_to_client(msgs[i]->msg);
                break;
        }
    }
    return PAM_SUCCESS;
}
```

**Ключевое:** ocserv не знает что pam_ldap собирается спросить «Password:», а pam_google_authenticator — «Verification code:». Он просто запускает PAM-стек и обрабатывает любые prompt'ы которые модули хотят показать. Поэтому добавить третий фактор (или убрать TOTP, или поменять порядок) — это просто правка `/etc/pam.d/ocserv`, ocserv-код не трогается.

В наших логах ты видишь именно эту конверсацию:

```
sec-mod: auth init for user 'alice'                              ← pam_start + pam_authenticate
PAM-auth conv: echo-off, msg: 'Password: '                       ← pam_ldap.so → conversation
worker[alice]: received auth reply message (value: 2)            ← клиент prompted, ждём ответа
worker[alice]: sending message 'sm: auth cont' to secmod         ← клиент прислал пароль
PAM-auth conv: echo-off, msg: 'Verification code: '              ← pam_google_authenticator → conversation
worker[alice]: received auth reply message (value: 2)
worker[alice]: sending message 'sm: auth cont' to secmod         ← клиент прислал код
worker[alice]: received auth reply message (value: 1)            ← OK = 1, auth complete
```

---

## 5. Worker ↔ sec-mod протокол: AUTH_REP коды

В логах часто видишь `received auth reply message (value: N)`. Это значения enum из `src/ipc.proto` ocserv:

```protobuf
enum AUTH_REP {
    OK = 1;       // auth полностью прошла, выдавайте cookie
    MSG = 2;      // нужен ещё один prompt от юзера (continue)
    FAILED = 3;   // отказ
}
```

**Это значение от sec-mod к worker'у**, не PAM-код. Перевод:
| `value: N` | Что это значит |
|---|---|
| `1` (OK) | PAM-стек полностью отработал, юзер пущен. Worker генерит cookie и начинает CSTP-туннель. |
| `2` (MSG) | sec-mod передаёт worker'у очередной prompt (`Password:` или `Verification code:`). Worker должен показать его клиенту и слать back ответ через `sm: auth cont`. |
| `3` (FAILED) | Auth провалилась окончательно. Worker дисконнектит клиента. |

Я как-то по ошибке думал что `value: 1` = fail (потому что в C-обычно `0` = success), но на самом деле тут `1` = OK. Это меня подвело в начале отладки. Запомни: **в ocserv `value: 1` это победа, `value: 3` это поражение, `value: 2` это «продолжаем разговор»**.

---

## 6. Что мы используем из nslcd — детально

nslcd — это **прокси** между pam_ldap.so / NSS и реальным LDAP-сервером. Без него pam_ldap.so не работает (в Debian Bookworm пакет `libpam-ldap` deprecated, заменён на `libpam-ldapd`, который требует nslcd).

Наш конфиг (`/etc/nslcd.conf`, рендерится из `nslcd.conf.tmpl`):

```
uid nslcd                                                # под каким юзером работает демон
gid nslcd

uri ldap://ldap:389                                      # где LDAP-сервер
base dc=vpn,dc=local                                     # корневой DN

binddn cn=admin-readonly,dc=vpn,dc=local                 # service account для NSS/anon-search
bindpw <secret>

base passwd ou=users,dc=vpn,dc=local                     # где искать posixAccount entries
base group ou=groups,dc=vpn,dc=local                     # где искать posixGroup entries

pam_authz_search (&(objectClass=posixGroup)(cn=vpn-users)(memberUid=$username))
                                                          # фильтр для account-stage (см. 3.4)

bind_timelimit 5                                          # timeouts (fail-closed)
timelimit 5
idle_timelimit 10

ssl off                                                   # plain ldap внутри docker-net
```

**`$username`** — это плейсхолдер nslcd, не shell-переменная. nslcd сам подставляет туда юзернейм во время каждого запроса. Поэтому в нашем entrypoint мы делаем `envsubst` **с явным allowlist'ом** переменных — иначе envsubst сожрал бы `$username` (как пустую shell-переменную) и фильтр сломался бы. Этот баг уже случился, починили.

**Двойная роль nslcd:**

1. **Для NSS** (Name Service Switch): когда контейнер делает `getent passwd alice`, glibc смотрит в `/etc/nsswitch.conf`, видит `passwd: files ldap`, и для `ldap` запрашивает данные у nslcd. nslcd → LDAP → возвращает строку `alice:*:2001:2001:Alice Liddell:/home/alice:/bin/bash`. Без NSS-интеграции PAM не смог бы даже узнать UID юзера alice.

2. **Для PAM** (через libpam-ldapd): pam_ldap.so передаёт auth-запросы nslcd через UNIX-сокет. nslcd делает bind в LDAP.

Эти две роли работают через один демон с одним конфигом. Удобно.

---

## 7. Полный поток одной аутентификации с временной шкалой

Допустим, alice вводит в openconnect: пароль `123`, TOTP `709723`.

```
t=0     openconnect    → TCP 443 → ocserv main → spawn worker[34]
t=10ms  worker[34]     → TLS handshake (через sec-mod[27], потому что приватный ключ только у sec-mod)
t=80ms  TLS handshake ✓
t=85ms  worker[34]     → "alice хочет логин" → sec-mod[27]
t=86ms  sec-mod[27]    → pam_start("ocserv", "alice", conv_fn)
t=87ms  sec-mod[27]    → pam_authenticate()
        │
        │  PAM запускает auth-стек:
        │  ─ pam_listfile.so: проверка denylist → alice не в файле → PAM_SUCCESS
        │  ─ pam_ldap.so: → conv_fn("Password:", ECHO_OFF)
        │       │
        │       │ conv_fn → worker[34] → клиенту: "msg: 'Password: '"
        │       │ worker[34] → клиенту value: 2 (MSG, need input)
        │       │ openconnect показывает prompt, alice вводит "123"
        │       │ openconnect → POST с паролем → worker[34] → sec-mod: "auth cont, data='123'"
        │       │ conv_fn возвращает "123"
        │       │
        │       │ pam_ldap.so → nslcd: "verify alice:123"
        │       │ nslcd → LDAP: anonymous search uid=alice → DN
        │       │         → LDAP: bind DN с паролем "123" → success
        │       │ nslcd → pam_ldap: PAM_SUCCESS
        │       │
        │  ─ pam_google_authenticator.so: → conv_fn("Verification code:", ECHO_OFF)
        │       │
        │       │ (тот же цикл: prompt → wait → response → возвращаем код)
        │       │
        │       │ модуль читает /home/alice/.google_authenticator
        │       │ вычисляет TOTP(secret, now/30) → "709723" → совпало → PAM_SUCCESS
        │
        │  auth-стек ВЕСЬ вернул SUCCESS → pam_authenticate() = PAM_SUCCESS
        │
t=2100ms sec-mod[27]    → pam_acct_mgmt()
         │
         │  PAM запускает account-стек:
         │  ─ pam_ldap.so (account): → nslcd: "is alice authorized?"
         │       nslcd: search (&(objectClass=posixGroup)(cn=vpn-users)(memberUid=alice))
         │       LDAP: 1 result (cn=vpn-users содержит memberUid: alice)
         │       PAM_SUCCESS
         │
t=2150ms sec-mod[27]    → worker[34]: value: 1 (OK)
t=2151ms worker[34]     → клиенту: success + session-cookie
t=2160ms openconnect    → второе TCP-соединение с cookie → CSTP-туннель
t=2200ms worker[34]     → выдаёт alice IP 192.168.99.50
t=2210ms ALIVE: туннель работает
```

Это полная картина. Каждый шаг видно в логах ocserv (`docker compose logs ocserv`) + в auth.log внутри контейнера (`docker exec ocserv cat /var/log/auth.log`).

---

## 8. Главные точки отказа — куда смотреть когда «не пускает»

| Симптом | Что вероятно | Где смотреть |
|---|---|---|
| `value: 3` сразу после auth init, **без prompt'а** | denylist отвергает (`pam_listfile` `requisite`) | `docker exec ocserv cat /etc/ocserv/control/disabled-users` |
| `Password:` показался, но после ввода → `value: 3` | pam_ldap отверг пароль (неверный, или LDAP недоступен, или nslcd упал) | auth.log → `pam_ldap` строки. Также `docker exec ocserv pgrep nslcd`. |
| Дошло до `Verification code:`, после ввода → `value: 3` | pam_google_authenticator отверг код (неверный, или time drift, или reuse, или файла нет) | auth.log → `Accepted google_authenticator` (успех) или `Invalid verification code` |
| Пароль и TOTP правильные, всё равно `value: 3` | account-stage: alice не в группе vpn-users | `docker compose exec ldap ldapsearch -x ... -b cn=vpn-users,... memberUid` — есть ли alice |
| `Verification code:` не появляется вовсе | pam_ldap auth failed → стек прервался, до google_auth не дошли | auth.log + проверь `pamtester ocserv alice authenticate` |
| Auth прошла, но сразу дисконнект | проблема **не в auth**, а дальше: cookie validation, TUN claim, DTLS, MTU. См. `worker-vpn.c` логи |

**Универсальный инструмент**: `docker exec ocserv pamtester ocserv alice authenticate`. Это запускает наш PAM-стек **без VPN**, можно ввести пароль+код руками и сразу увидеть SUCCESS/FAILURE. Если pamtester проходит — значит auth настроена правильно, и проблема в ocserv'е/туннеле/клиенте. Если pamtester не проходит — копаешь PAM/LDAP/TOTP.

---

## 9. Что почитать чтобы укрепить понимание

- `man pam.conf` или `man pam.d` — синтаксис файла, control flags, типы стадий. Самая важная справка.
- `man pam_listfile` / `man pam_ldap` / `man pam_google_authenticator` / `man pam_permit` — по каждому модулю детально.
- `man nslcd.conf` — все опции nslcd, включая полный синтаксис `pam_authz_search` и переменные типа `$username`, `$dn`, и т.д.
- Linux-PAM Application Developers' Guide: http://www.linux-pam.org/Linux-PAM-html/adg-introduction.html — как pam_start/pam_authenticate/pam_acct_mgmt работают изнутри. Полезно если будешь делать свой PAM-aware сервис.
- google-authenticator README в `libpam-google-authenticator` source — формат файла `.google_authenticator`, все флаги, как работает HOTP vs TOTP, scratch-codes.
- Исходники ocserv: `src/sec-mod-auth.c` и `src/auth/pam.c` — там точная реализация conversation-function. Не страшно читать, ~200 строк.

Если что-то конкретно непонятно — назови шаг или модуль, разверну глубже.
