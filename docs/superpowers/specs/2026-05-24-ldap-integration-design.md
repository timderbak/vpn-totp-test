# LDAP Integration — Design Spec

**Дата:** 2026-05-24
**Статус:** Draft, ожидает ревью пользователя.
**Контекст:** Перевод стенда `vpn-totp-test` с локальных Linux-юзеров (заводимых через `users.env` + `useradd` в entrypoint ocserv) на LDAP как единый источник истины для VPN-юзеров. Админка остаётся источником истины для TOTP-секретов (файлы в shared volume) и продолжает управлять revoke/enable через PAM denylist. Admin-учётки остаются локальными в `admin.db`.

**Предшествующие документы:**
- `docs/architecture.md` — текущая архитектура (file-based)
- `docs/ldap-flow.md` — наглядная схема LDAP-перехода
- `docs/superpowers/specs/2026-05-23-admin-panel-design.md` — спек админки

---

## 1. Решения (зафиксировано в brainstorming)

| Аспект | Решение |
|---|---|
| LDAP-сервер | OpenLDAP (osixia/openldap) в том же docker-compose |
| Базовая иерархия | `dc=vpn,dc=local`, `ou=users`, `ou=groups` |
| Фильтр VPN-юзеров | Группа `cn=vpn-users,ou=groups,dc=vpn,dc=local` (членство через `memberUid`) |
| Миграция | Полная замена `users.env` + `useradd`. alice и bob льются в LDAP при первом старте через seed-LDIF, оба с паролем `123` |
| Auth-метод ocserv | PAM-стек: `pam_listfile` + `pam_ldap` + `pam_google_authenticator` |
| Admin-учётки | Остаются локальными в `admin.db` (бэкап-пароль + TOTP). LDAP-аутентификации админов нет |
| Получение списка юзеров в админке | In-memory кэш TTL 30с + skeleton-загрузка через htmx + кнопка Refresh + индикатор актуальности |
| LDAP-bind для админки | Service account `cn=admin-readonly,dc=vpn,dc=local` (только read) |
| Транспорт LDAP | Plain `ldap://` внутри docker-network (для лаба). Прод: LDAPS на 636 — вне scope |

---

## 2. Архитектура и compose-топология

```
docker-compose.yml
├── ocserv                  (изменения PAM и Dockerfile)
│   ├── PAM: pam_listfile + pam_ldap [NEW] + pam_google_authenticator
│   ├── + /etc/ldap/ldap.conf (config для pam_ldap, монтируется из ./ocserv-ldap/)
│   ├── + apt-get install libpam-ldap (в Dockerfile)
│   ├── - удалено: блок useradd/chpasswd в entrypoint.sh
│   └── + wait-for-ldap (5 retries × 2с) перед запуском ocserv
│
├── ldap                    [NEW сервис]
│   ├── image: osixia/openldap:1.5.0
│   ├── ports: внутренний 389 (на хост не пробрасываем)
│   ├── env:
│   │   ├── LDAP_ORGANISATION="VPN Lab"
│   │   ├── LDAP_DOMAIN=vpn.local        → BASE=dc=vpn,dc=local
│   │   ├── LDAP_ADMIN_PASSWORD=<secret> (root LDAP-админ для рукопашных правок)
│   │   └── LDAP_CONFIG_PASSWORD=<secret>
│   ├── volumes:
│   │   ├── ldap-data:/var/lib/ldap
│   │   ├── ldap-config:/etc/ldap/slapd.d
│   │   └── ./ldap/bootstrap:/container/service/slapd/assets/config/bootstrap/ldif/custom (RO)
│   └── healthcheck: ldapsearch -x -H ldap://localhost -b dc=vpn,dc=local
│
└── admin                   (изменения)
    ├── + ldap_client.py (ldap3 lib + кэш)
    ├── + ADMIN_LDAP_* env vars
    └── + /users/_list, /users/_refresh routes
```

**Сеть:** все три сервиса в одном compose-network. ocserv и admin обращаются к `ldap:389` по имени сервиса. LDAP не выставлен на хост-порт по умолчанию (можно опционально для phpLDAPadmin / отладки).

**Что удаляется из текущего стенда:**
- `users.env` (файл и `env_file` ссылка в docker-compose)
- Блок `for entry in $LAB_USERS; do useradd…done` в `scripts/entrypoint.sh`
- Блок `chsh -s /bin/bash` для существующих юзеров (юзеры теперь предоставляются LDAP через nss/pam_ldap, нет /etc/passwd-записей в ocserv)

---

## 3. LDAP-схема и seed-data

**Иерархия:**
```
dc=vpn,dc=local                       (root)
├── ou=users
│   ├── uid=alice    (uidNumber=2001)
│   └── uid=bob      (uidNumber=2002)
├── ou=groups
│   └── cn=vpn-users (gidNumber=3000) — memberUid: alice, bob
└── cn=admin-readonly (service account для admin-контейнера, только read)
```

**Атрибуты юзера** (objectClass: `inetOrgPerson` + `posixAccount`):

| Атрибут | Назначение | Пример |
|---|---|---|
| `uid` | Логин (username) | `alice` |
| `uidNumber` | Linux uid для chown home-папки | `2001` |
| `gidNumber` | Linux gid | `2001` |
| `homeDirectory` | Формальный путь | `/home/alice` |
| `cn` | Display name | `Alice Liddell` |
| `mail` | Email | `alice@vpn.local` |
| `userPassword` | SSHA-хеш | `{SSHA}…` |
| `loginShell` | Для PAM account stage | `/bin/bash` |

**Группа `vpn-users`** (objectClass: `posixGroup`):
- `cn: vpn-users`
- `gidNumber: 3000`
- `memberUid: alice`
- `memberUid: bob`

(`memberUid` — стандартный атрибут `posixGroup`. `pam_ldap` использует именно его при `pam_member_attribute memberUid`.)

**Service account** (`cn=admin-readonly`):
- objectClass: `simpleSecurityObject`, `organizationalRole`
- `userPassword: {SSHA}…` (генерим, в .env: `ADMIN_LDAP_BIND_PASSWORD`)
- ACL в LDAP: read-only на `dc=vpn,dc=local` (default Manager-ACL подойдёт, либо явный `access to … by dn="cn=admin-readonly,dc=vpn,dc=local" read`)

**Seed-LDIF** (`ldap/bootstrap/`):

| Файл | Содержимое |
|---|---|
| `01-base.ldif` | `ou=users`, `ou=groups` |
| `02-seed-users.ldif` | alice (uid=2001, pass=`123`), bob (uid=2002, pass=`123`) |
| `03-vpn-group.ldif` | `cn=vpn-users` + memberUid alice, bob |
| `04-readonly.ldif` | `cn=admin-readonly` с предхешированным паролем |

Пароли в LDIF предхешируем `slappasswd -h '{SSHA}' -s 123` оффлайн и кладём результат в LDIF (никаких plaintext в файлах, кроме `ldap/README.md` где документируем «alice/bob пароль = 123» как памятку для лаба).

---

## 4. Модуль `ldap_client.py` и изменения в `users.py`

### Новый `admin/app/ldap_client.py`

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class LdapUser:
    username: str          # uid
    uid_number: int        # для chown home
    gid_number: int
    display_name: str | None
    email: str | None


class LdapUnavailable(Exception):
    """LDAP не отвечает (timeout) или bind упал."""


def list_users() -> list[LdapUser]:
    """Все члены vpn-users. Кэш TTL=ADMIN_LDAP_CACHE_TTL секунд."""

def get_user(username: str) -> LdapUser | None:
    """Один юзер по uid. Использует кэш если свежий."""

def invalidate_cache() -> None:
    """Сбрасывает in-memory кэш."""

def cache_age_seconds() -> int | None:
    """Сколько секунд кэшу. None если кэш пуст."""
```

**Внутри:**
- `ldap3.Connection` с `auto_bind=True`, bind как `ADMIN_LDAP_BIND_DN` + `ADMIN_LDAP_BIND_PASSWORD`
- Один поиск: `(&(objectClass=posixAccount)(memberOf=<vpn-group-dn>))` — если schema поддерживает memberOf, иначе двушаговый: search vpn-users → `memberUid` list → search каждого по uid.
- Кэш: модульная переменная `_cache = {"data": list, "ts": int}`, TTL из config.
- На любом `LDAPException` или socket timeout → ловим, `LdapUnavailable` + сохраняем последний успешный `_stale_data` для UX-фолбэка.

### Конфиг (`admin/app/config.py`)

```python
class Settings(BaseSettings):
    # … existing …
    ldap_url: str = "ldap://ldap:389"
    ldap_bind_dn: str
    ldap_bind_password: str
    ldap_base_dn: str = "dc=vpn,dc=local"
    ldap_user_ou: str = "ou=users,dc=vpn,dc=local"
    ldap_vpn_group_dn: str = "cn=vpn-users,ou=groups,dc=vpn,dc=local"
    ldap_cache_ttl: int = 30
    ldap_timeout: int = 5
```

### Изменения в `admin/app/users.py`

| Функция | Было | Станет |
|---|---|---|
| `list_users(home, denylist, conn)` | `os.listdir("/home")` + проверка `.google_authenticator` | `ldap_client.list_users()` → для каждого `os.path.exists(home/<u>/.google_authenticator)` + статус из denylist + last_issued_at из enrollments |
| `enroll_user(home, conn, username, …)` | `safe_home_path` + write file | + перед write: `ldap_user = ldap_client.get_user(username)` → если None → `UserNotFound`. `ensure_home(home, ldap_user)` создаёт `/home/<u>/` с `chown(uid_number, gid_number)` и `chmod 700`. Дальше как было. |
| `revoke_user(…)` | denylist + удалить файл | то же + `ldap_client.invalidate_cache()` |
| `enable_user(…)` | убрать из denylist | то же + invalidate_cache |

**Новый helper:**
```python
def ensure_home(home_dir: str, ldap_user: LdapUser) -> Path:
    home = safe_home_path(home_dir, ldap_user.username)  # regex-валидация остаётся
    if not home.exists():
        home.mkdir(parents=True)
        os.chown(home, ldap_user.uid_number, ldap_user.gid_number)
        os.chmod(home, 0o700)
    return home
```

**Что не меняется:** `safe_home_path()` (regex `^[a-z][a-z0-9_-]{0,31}$` + `is_relative_to`), формат `.google_authenticator`, fcntl-locked denylist, audit-log, sessions, CSRF, rate-limit.

---

## 5. PAM-стек ocserv

### `pam/ocserv` (полная замена)

```
# 1. Denylist — управляется админкой через shared volume.
auth requisite pam_listfile.so onerr=succeed item=user sense=deny \
     file=/etc/ocserv/control/disabled-users

# 2. Пароль через LDAP. pam_ldap читает /etc/ldap/ldap.conf.
auth required pam_ldap.so

# 3. Второй фактор — TOTP. Файл в /home/<u>/.google_authenticator.
auth required pam_google_authenticator.so debug

# Account-stage: pam_ldap проверяет членство через pam_groupdn в ldap.conf.
account required pam_ldap.so

# Session: ocserv shell не спавнит — pam_permit достаточно.
session required pam_permit.so
```

### `/etc/ldap/ldap.conf` (новый файл, монтируется в ocserv)

```
uri        ldap://ldap:389
base       dc=vpn,dc=local

binddn     cn=admin-readonly,dc=vpn,dc=local
bindpw     <тот же ADMIN_LDAP_BIND_PASSWORD>

nss_base_passwd     ou=users,dc=vpn,dc=local?one
nss_base_group      ou=groups,dc=vpn,dc=local?one

# Только участники vpn-users проходят account-stage
pam_groupdn         cn=vpn-users,ou=groups,dc=vpn,dc=local
pam_member_attribute  memberUid

# fail-closed таймауты
bind_timelimit      5
timelimit           5
network_timeout     5

# не используем sasl, plain bind
ssl                 no
```

Файл монтируется через `./ocserv-ldap/ldap.conf:/etc/ldap/ldap.conf:ro` в docker-compose. Пароль bind подставляется через `envsubst` в entrypoint или через `template` (см. реализацию).

### Поведение PAM по сценариям

| Сценарий | Где ловится | Результат |
|---|---|---|
| Юзер в denylist | шаг 1 (pam_listfile) | DENY мгновенно, без password prompt |
| Юзера нет в LDAP | шаг 2 (pam_ldap auth) | DENY |
| Юзер есть, пароль неверный | шаг 2 | DENY |
| Юзер не в vpn-users | account-stage | DENY (TOTP-prompt не показывается) |
| Юзер есть в группе, нет TOTP | шаг 3 | DENY |
| LDAP timeout | pam_ldap → 5с → FAIL | DENY (fail-closed) |
| Всё ок | — | ALLOW |

### `scripts/entrypoint.sh`

- **Удаляется** блок `for entry in $LAB_USERS; do useradd … chpasswd …`
- **Удаляется** блок `chsh -s /bin/bash` для существующих юзеров
- **Остаётся:** генерация серта, создание `/etc/ocserv/control/disabled-users`
- **Добавляется:**
  ```bash
  # wait-for-ldap (5 retries × 2с)
  for i in 1 2 3 4 5; do
      ldapsearch -x -H "$LDAP_URL" -b "$LDAP_BASE_DN" -s base \
          -D "$LDAP_BIND_DN" -w "$LDAP_BIND_PASSWORD" \
          >/dev/null 2>&1 && break
      log "waiting for LDAP ($i/5)…"
      sleep 2
  done
  ```
- **Добавляется:** подстановка пароля в `ldap.conf` через `envsubst` или sed (чтобы не хранить пароль в git):
  ```bash
  envsubst < /etc/ldap/ldap.conf.tmpl > /etc/ldap/ldap.conf
  chmod 600 /etc/ldap/ldap.conf
  ```

### Изменения в `Dockerfile` (ocserv)

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
        ocserv \
        libpam-google-authenticator \
        libpam-ldap \                # NEW
        ldap-utils \                  # NEW (для wait-for-ldap)
        gettext-base \                # NEW (envsubst)
        # … остальное как было …
```

---

## 6. UX в админке

### Дашборд

```
┌──────────────────────────────────────────────────────────────┐
│ ocserv admin                              admin1 [Logout]    │
│ Users | API Tokens | Audit                                   │
├──────────────────────────────────────────────────────────────┤
│ [🔄 Refresh]   список обновлён 12 сек назад · из LDAP        │
├──────────────────────────────────────────────────────────────┤
│ User    TOTP    Status      Last issued     Actions          │
│ ──────────────────────────────────────────────────────────── │
│ ⏳ Загружаем юзеров из LDAP...                                │
│                                                              │
│ ↓ через ~200мс htmx подменяет:                                │
│                                                              │
│ alice   ✓       active      2026-05-24     [Re-issue][Revoke]│
│ bob     ✗       no key      —              [Issue]           │
└──────────────────────────────────────────────────────────────┘
```

### Новые/изменённые routes

| Метод | Путь | Что делает |
|---|---|---|
| `GET` | `/` | Рендер шаблона с skeleton + htmx-trigger на `/users/_list` |
| `GET` | `/users/_list` | HTML-fragment (tbody) — список из `ldap_client.list_users()` |
| `POST` | `/users/_refresh` | `invalidate_cache()` → 303 на `/` |

CSRF на `/users/_refresh` — да (POST, состояние меняет кэш). Существующие action-routes (`/users/{u}/enroll` и т.д.) не меняются по интерфейсу.

### Обработка недоступного LDAP

```
┌──────────────────────────────────────────────────────────────┐
│ ⚠ LDAP недоступен (timeout 5с)                                │
│   Список может быть устаревшим. Показан кэш от 14:32.        │
│   [Попробовать снова]                                        │
├──────────────────────────────────────────────────────────────┤
│ alice   ✓       active     ...      [Revoke]                 │
│ bob     ✗       no key     —        [Issue ⚠ недоступно]    │
```

- `LdapUnavailable` ловится в route → рендер шаблона с `ldap_error=True` + stale data
- Кнопки Issue/Enable отключаются (`disabled` атрибут) пока LDAP не вернётся — иначе `get_user()` упадёт
- Revoke остаётся доступным (он не нуждается в LDAP — только denylist + удаление файла)
- API возвращает `503 Service Unavailable` + JSON `{"error": "ldap_unavailable", "stale_age_seconds": N}`

### Тексты ошибок

| Случай | Сообщение |
|---|---|
| LDAP timeout | «LDAP не отвечает (5с). Проверь `docker compose logs ldap`.» |
| LDAP bind fail | «Не удалось подключиться к LDAP. Проверь `ADMIN_LDAP_BIND_PASSWORD` в .env.» |
| User не в LDAP | «Юзер `<name>` не найден в LDAP. Возможно, удалён.» |
| User не в vpn-users | (не показывается в админке — его в списке просто нет) |

---

## 7. Тесты

**Стек:** добавляется `ldap3==2.9.1`. Для unit-тестов используется `ldap3.Connection(client_strategy=MOCK_SYNC)` — виртуальный LDAP в памяти, без реального slapd.

**Новые тестовые файлы:**

| Файл | Покрытие |
|---|---|
| `test_ldap_client.py` | `list_users` возвращает только членов vpn-users; кэш срабатывает (повторный вызов не дёргает Mock); `invalidate_cache` сбрасывает; `get_user(unknown)` → None; bind с неверным паролем → понятная ошибка; timeout → `LdapUnavailable` |
| `test_users.py` (доп) | `enroll_user` зовёт `get_user`; `ensure_home` создаёт папку с правильными правами; revoke инвалидирует кэш |
| `test_routes_web.py` (доп) | `GET /users/_list` возвращает HTML-fragment с alice/bob; `POST /users/_refresh` дёргает invalidate; banner про LDAP-fail рендерится; кнопка Issue disabled при `ldap_error=True` |
| `test_routes_api.py` (доп) | API enroll работает с mock LDAP; 404 для unknown; 503 при `LdapUnavailable` |
| `test_ldap_failure.py` | при недоступном LDAP stale-cache используется; revoke продолжает работать без LDAP |

**E2E (`tests/e2e/test_admin_e2e.sh` обновляется):**
1. `docker compose up -d --build` (включая LDAP)
2. Wait LDAP healthy
3. Через API минтим токен, делаем GET `/api/v1/users` → видим alice, bob
4. Enroll alice → `/home/alice/` создана с правами 700/2001:2001, файл `.google_authenticator` есть
5. openconnect alice / `123` / TOTP → `Connected as 192.168.99.x`
6. Revoke alice → alice в denylist → openconnect fail на pam_listfile

---

## 8. Файлы (карта изменений)

```
ldap/                                       # [NEW] директория
├── bootstrap/
│   ├── 01-base.ldif                        # ou=users, ou=groups
│   ├── 02-seed-users.ldif                  # alice, bob ({SSHA}123)
│   ├── 03-vpn-group.ldif                   # cn=vpn-users + memberUid
│   └── 04-readonly.ldif                    # cn=admin-readonly
└── README.md                               # инструкция как добавить юзера в LDAP

ocserv-ldap/                                # [NEW] конфиг pam_ldap
└── ldap.conf.tmpl                          # шаблон, envsubst подставит пароль

admin/app/
├── ldap_client.py                          # [NEW]
├── config.py                               # +ADMIN_LDAP_* fields
├── users.py                                # list_users из LDAP, ensure_home, get_user в enroll
├── routes_web.py                           # +GET /users/_list, +POST /users/_refresh, ловит LdapUnavailable
├── routes_api.py                           # ловит LdapUnavailable → 503
└── templates/
    ├── dashboard.html                      # skeleton + indicator + Refresh + ldap_error banner
    └── _users_table.html                   # [NEW] htmx partial (tbody)

admin/tests/
├── test_ldap_client.py                     # [NEW]
├── test_ldap_failure.py                    # [NEW]
└── (доп тесты в test_users.py, test_routes_web.py, test_routes_api.py)

# модификации:
docker-compose.yml                          # +ldap service, +ldap-data/ldap-config volumes, +ocserv-ldap mount
Dockerfile                                  # +libpam-ldap, +ldap-utils, +gettext-base
pam/ocserv                                  # pam_unix → pam_ldap (auth+account)
scripts/entrypoint.sh                       # -useradd-блок, -chsh-блок, +wait-for-ldap, +envsubst ldap.conf
README.md                                   # +раздел LDAP, обновить «Quick start»
.env.example                                # +ADMIN_LDAP_*
admin/requirements.txt                      # +ldap3==2.9.1

# УДАЛЯЕТСЯ:
users.env                                   # юзеры теперь в LDAP
```

---

## 9. Границы первой итерации (вне scope)

- Admins в LDAP — остаются локальными в `admin.db`
- LDAP-репликация / HA — single instance в compose
- LDAPS (порт 636 + TLS) — пока plain ldap внутри docker-network. Для прода — LDAPS, см. отдельный спек.
- Web-UI для управления самим LDAP (phpLDAPadmin) — `ldapadd` руками или поднимать отдельный контейнер опционально
- Изменение пароля юзера через админку — право LDAP-админа
- Группы кроме `vpn-users` (роли, ACL по группам) — потом
- Hot-миграция без даунтайма — стенд, `docker compose down -v && up`
- TOTP-секрет в LDAP-атрибуте — остаётся в файлах
- Аудит LDAP-операций — slapd сам логирует, в admin.db не дублируем

---

## 10. Риски и митигации

| Риск | Митигация |
|---|---|
| LDAP лёг → VPN не работает | `pam_ldap` timeout 5с → fail-closed (по спеке). Health-check в compose. |
| LDAP лёг → админка не отрисует список | Stale cache + banner + Refresh-кнопка. Revoke остаётся работоспособным. |
| Bind-пароль утечёт через `ldap.conf` или env | `ldap.conf` chmod 600 после envsubst. `.env` в gitignore. Bind-юзер read-only (даже utility-стянуть → не сможет менять юзеров). |
| Конфликт uid в LDAP и существующем `/home/<u>/` | `ensure_home` создаёт ТОЛЬКО если нет. Если папка есть — оставляем (предполагаем, что это валидная). Edge-case при переименовании юзера в LDAP — отдельный сценарий, в первой итерации не поддерживаем. |
| memberUid vs member (DN) — какой атрибут использовать | `memberUid` (как в `posixGroup`) — стандарт для Unix-фокусированных групп. pam_ldap по умолчанию умеет, `ldap_client` тоже. |
