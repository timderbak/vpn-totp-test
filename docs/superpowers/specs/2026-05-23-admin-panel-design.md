# Admin Panel — Design Spec

**Дата:** 2026-05-23
**Статус:** Draft, ожидает ревью пользователя.
**Контекст:** Расширение лаба `vpn-totp-test` (ocserv + TOTP) веб-админкой для управления TOTP-ключами уже существующих Linux-юзеров. Юзеры заводятся вне админки (текущий механизм: `users.env` + `entrypoint.sh`). Админка только enroll'ит / revoke'ает TOTP и блокирует/разблокирует VPN-доступ.

---

## 1. Решения (зафиксировано в brainstorming)

| Аспект | Решение |
|---|---|
| Scope | TOTP re-enroll для уже созданных Linux-юзеров. Админка юзеров **не создаёт**. |
| Admin auth | Login + password + TOTP (sym с тем, что админка управляет). |
| API auth | Per-system API-токены (`Authorization: Bearer ...`), bcrypt-hash в БД. |
| Storage | SQLite в shared volume `admin-data`. |
| Topology | Отдельный docker-compose сервис `admin`. |
| Stack | Python 3.12 + FastAPI + Jinja2 + htmx + pico.css. |
| User source | `os.listdir("/home")` через shared volume `ocserv-home`. |
| Revoke | Append в `disabled-users` (PAM `pam_listfile.so` блокирует auth) + `rm .google_authenticator`. |
| Exposure | HTTPS self-signed на хост-порту `8443`. |
| Audit | Все действия → таблица `audit_log` + страница `/audit` в UI. |
| Bridge admin→ocserv | Pure-volume + PAM denylist. Без `docker.sock`, без privileged. |

---

## 2. Архитектура и топология

```
docker-compose.yml
├── ocserv               (как сейчас, + изменения)
│   ├── volumes:
│   │   ├── ocserv-ssl       (как сейчас)
│   │   ├── ocserv-home      (как сейчас, теперь shared с admin)
│   │   └── ocserv-control   [NEW] — содержит disabled-users
│   ├── PAM: первая строка добавлена —
│   │   auth requisite pam_listfile.so onerr=succeed item=user
│   │       sense=deny file=/etc/ocserv/control/disabled-users
│   └── entrypoint.sh: создаёт /etc/ocserv/control/disabled-users (пустой) при первом старте
│
└── admin                [NEW]
    ├── build: ./admin
    ├── ports: 8443:8443/tcp
    ├── volumes:
    │   ├── ocserv-home    (RW)  — управление .google_authenticator
    │   ├── ocserv-control (RW)  — disabled-users
    │   ├── admin-data     (RW)  — admin.db
    │   └── admin-ssl      (RW)  — self-signed серт админки
    ├── env_file: .env (ADMIN_BOOTSTRAP_USERNAME, ADMIN_BOOTSTRAP_PASSWORD_HASH)
    └── без NET_ADMIN, без docker.sock, без privileged
```

**Изоляция:** admin-контейнер не имеет привилегий ocserv; не вызывает `useradd`, `passwd -l` или `docker exec`. Все его действия — операции с файлами в двух volume.

**Bootstrap:** при первом запуске admin читает `ADMIN_BOOTSTRAP_USERNAME` и `ADMIN_BOOTSTRAP_PASSWORD_HASH` (bcrypt) из env, создаёт запись в `admins`. При первом логине этого админа форсируется TOTP-enroll.

---

## 3. Storage (SQLite, `admin-data/admin.db`)

```sql
admins(
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  totp_secret TEXT,                  -- base32, NULL до enroll
  totp_enrolled_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL
);

api_tokens(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,                -- "ci-bot", "ansible-prod"
  token_hash TEXT NOT NULL,          -- bcrypt(plaintext)
  token_prefix TEXT NOT NULL,        -- первые 8 символов
  scopes TEXT NOT NULL,              -- JSON: ["enroll","revoke","read"]
  created_by_admin_id INTEGER REFERENCES admins(id),
  created_at TIMESTAMP NOT NULL,
  revoked_at TIMESTAMP,
  last_used_at TIMESTAMP
);

enrollments(
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL,
  action TEXT NOT NULL,              -- 'issued' | 're-issued' | 'revoked' | 'enabled'
  actor_type TEXT NOT NULL,          -- 'admin' | 'api'
  actor_id INTEGER NOT NULL,
  totp_fingerprint TEXT,             -- sha256(secret)[:16]
  ts TIMESTAMP NOT NULL
);

audit_log(
  id INTEGER PRIMARY KEY,
  ts TIMESTAMP NOT NULL,
  actor_type TEXT NOT NULL,          -- 'admin' | 'api' | 'anonymous'
  actor_id INTEGER,
  action TEXT NOT NULL,              -- 'login.ok', 'login.fail', 'enroll', ...
  target_user TEXT,
  ip TEXT,
  user_agent TEXT,
  result TEXT NOT NULL,              -- 'ok' | 'fail'
  details TEXT                       -- JSON
);

sessions(
  id TEXT PRIMARY KEY,               -- random 256-bit token (hex)
  admin_id INTEGER NOT NULL REFERENCES admins(id),
  created_at TIMESTAMP NOT NULL,
  last_seen_at TIMESTAMP NOT NULL,
  ip TEXT,
  user_agent TEXT
);

CREATE INDEX idx_audit_ts ON audit_log(ts DESC);
CREATE INDEX idx_enrollments_user_ts ON enrollments(username, ts DESC);
```

**Не хранится:**
- TOTP-секреты VPN-юзеров (только в `/home/<u>/.google_authenticator`).
- Plaintext API-токенов (только bcrypt-hash + 8-символьный префикс).
- Plaintext паролей админов.

**Миграции:** inline `CREATE TABLE IF NOT EXISTS` в `app/db.py` при старте. Alembic избыточен для лаба.

---

## 4. Эндпоинты

### Web (Jinja, формы + htmx)

Все POST требуют CSRF-токен. Все защищённые требуют валидную сессию.

| Метод | Путь | Что делает |
|---|---|---|
| GET | `/login` | Форма username + password (шаг 1) |
| POST | `/login` | Проверка пароля → редирект на `/login/totp` |
| GET | `/login/totp` | Форма 6-значного кода (или enroll-форма) |
| POST | `/login/totp` | Проверка TOTP → сессия |
| GET | `/login/enroll-totp` | QR для admin authenticator (только при первом входе) |
| POST | `/logout` | Уничтожить сессию (запись в БД) |
| GET | `/` | Дашборд: таблица VPN-юзеров |
| POST | `/users/{u}/enroll` | Сгенерить TOTP, записать `.google_authenticator`, показать QR один раз |
| POST | `/users/{u}/revoke` | Append `disabled-users` + удалить TOTP-файл |
| POST | `/users/{u}/enable` | Убрать из `disabled-users` |
| GET | `/tokens` | Список API-токенов |
| POST | `/tokens` | Создать токен → показать plaintext один раз |
| POST | `/tokens/{id}/revoke` | Mark `revoked_at` |
| GET | `/audit` | Пагинированный audit_log с фильтрами |

### JSON API (`Authorization: Bearer <token>`)

| Метод | Путь | Scope | Ответ |
|---|---|---|---|
| GET | `/api/v1/users` | `read` | `[{username, has_totp, disabled, last_issued_at}]` |
| GET | `/api/v1/users/{u}` | `read` | один user |
| POST | `/api/v1/users/{u}/enroll` | `enroll` | `{secret, scratch_codes[], qr_png_base64}` (один раз) |
| POST | `/api/v1/users/{u}/revoke` | `revoke` | `{ok: true}` |
| POST | `/api/v1/users/{u}/enable` | `revoke` | `{ok: true}` |
| GET | `/api/v1/audit` | `read` | пагинированный |
| GET | `/api/v1/openapi.json` | — | OpenAPI-схема |

**Инвариант:** и web, и API ходят через одни и те же функции в `app/users.py`. Нет дублирования бизнес-логики.

### UI mockup (дашборд)

```
┌─────────────────────────────────────────────────────────────┐
│ ocserv admin              admin1 (admin)  │  [Logout]       │
├─────────────────────────────────────────────────────────────┤
│  Users   API Tokens   Audit                                 │
├─────────────────────────────────────────────────────────────┤
│  User    TOTP   Status     Last issued    Actions           │
│  alice   ✓      active     2026-05-22     [Re-issue][Revoke]│
│  bob     ✓      active     2026-05-21     [Re-issue][Revoke]│
│  carol   ✗      no key     —              [Issue]           │
│  eve     ✗      disabled   2026-05-20     [Issue][Enable]   │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Security

**TLS:** uvicorn с `--ssl-keyfile/--ssl-certfile`, серт self-signed (entrypoint генерит в `admin-ssl`). HTTP выключен. HSTS `max-age=31536000`.

**Сессии:** cookie `__Host-admin_session`, `Secure`, `HttpOnly`, `SameSite=Strict`. Содержимое — 256-bit random, маппинг session_id→admin_id в таблице `sessions`. Idle 30 мин, absolute 12 ч. Logout удаляет запись.

**CSRF:** double-submit. Токен на сессию, поле формы + cookie `__Host-csrf`, сравнение через `hmac.compare_digest`.

**Bcrypt:** passlib, cost 12. Bootstrap-пароль — хеш в `.env` (генерится оффлайн `htpasswd -nbB`).

**Rate-limit:**
- 5 failed logins / IP / 15 мин → IP-блок 1 час.
- 10 failed logins / username / 15 мин → username-блок 1 час.
- `/api/v1/users/{u}/enroll` — 1 req/min per username.
- Счётчики — sliding-window через SQL `count() WHERE ip=? AND ts>?`.
- Превышение → `429 Too Many Requests` + `Retry-After`.

**API-токены:** формат `vpa_<32-base32>`. Bcrypt-hash + первые 8 символов (для distinct в UI/логе). Сравнение через bcrypt + `compare_digest`. Plaintext виден один раз.

**Username validation:** регэксп `^[a-z][a-z0-9_-]{0,31}$`. Пути через `pathlib.Path("/home") / username`, проверка `resolve().is_relative_to("/home")`.

**Disabled-users:** чтение/запись с `fcntl.flock`. Атомарная запись через temp + `os.rename`. Формат: одна username на строку, `#` — комментарий.

**Заголовки:**
- `Content-Security-Policy: default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'`
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: same-origin`

**Логирование:** каждый запрос → `audit_log`. Пароли, TOTP-коды, plaintext-токены — никогда. Sanitizer в `audit.py`.

**Вне scope:** WebAuthn, SIEM-интеграция, KMS, принудительная ротация паролей.

---

## 6. Тесты

**Стек:** pytest + pytest-asyncio + httpx.AsyncClient.

**Fixtures:**
- `admin_db`: tmp SQLite + apply schema + dependency override.
- `mock_home`: tmp_path с поддиректориями alice/bob, override `HOME_DIR`.
- `mock_control`: tmp_path с пустым disabled-users.

**Integration-тесты (обязательное покрытие):**
- `test_login.py` — правильный пароль → шаг 2 → правильный код → сессия; fail flow; rate-limit.
- `test_admin_totp_enroll.py` — первый вход без TOTP → форма enroll → после enroll'а login требует код.
- `test_user_list.py` — список home-дир, флаги `has_totp` и `disabled` корректны.
- `test_enroll_user.py` — POST enroll → файл создан, формат валидный, журнал обновлён, секрет в ответе один раз.
- `test_revoke.py` — POST revoke → username в denylist, TOTP-файл удалён, audit-запись. Enable обратно.
- `test_tokens.py` — создание токена → plaintext один раз. Revoked token → 401.
- `test_api.py` — JSON-контракт, scope-check.
- `test_security.py` — CSRF без токена → 403; path traversal в username → 400; rate-limit срабатывает.
- `test_concurrency.py` — 10 параллельных revoke одного юзера → одна запись (flock).

**E2E smoke:** `tests/test_e2e_pam.sh` — `docker compose up -d` → API enroll → openconnect авторизуется → revoke → openconnect получает fail.

---

## 7. Структура файлов

```
admin/
├── Dockerfile
├── requirements.txt
├── entrypoint.sh
├── app/
│   ├── main.py             # FastAPI app + middleware
│   ├── config.py           # env vars (pydantic-settings)
│   ├── db.py               # sqlite init + schema
│   ├── security.py         # session, CSRF, rate-limit, headers
│   ├── auth.py             # password + admin TOTP
│   ├── tokens.py           # API tokens CRUD + verify
│   ├── users.py            # /home read, enroll, revoke, enable
│   ├── totp.py             # генерация + .google_authenticator формат
│   ├── audit.py            # writer + sanitizer
│   ├── deps.py             # FastAPI dependencies
│   ├── routes_web.py
│   ├── routes_api.py
│   ├── templates/          # base, login, login_totp, enroll_admin_totp,
│   │                       # dashboard, qr_once, tokens, token_once, audit
│   └── static/             # pico.min.css, htmx.min.js
└── tests/

# изменения в существующих файлах:
pam/ocserv                  # +первая строка pam_listfile
scripts/entrypoint.sh       # +mkdir /etc/ocserv/control; touch disabled-users
docker-compose.yml          # +сервис admin, +3 volume
README.md                   # +раздел Admin Panel
.env.example                # +ADMIN_BOOTSTRAP_USERNAME, +ADMIN_BOOTSTRAP_PASSWORD_HASH
```

---

## 8. Границы первой итерации (вне scope)

- Email/Slack notifications о revoke.
- Multi-tenant / роли (только один уровень «admin»).
- Backup/restore admin.db через UI.
- LDAP/OIDC SSO для админов.
- WebAuthn вместо TOTP для админов.
- Принудительная смена пароля при первом входе (bootstrap-пароль меняется через CLI/файл).
- Создание юзеров через админку (юзеры заводятся вне).
