# Архитектура и потоки — ocserv-totp-lab + admin

## 1. Контейнеры, volume'ы и где что лежит

```mermaid
flowchart TB
    Host["💻 macOS host<br/>порты 4443 (VPN) + 8443 (admin)"]

    subgraph Compose["docker compose"]
        subgraph OCSERV["📦 ocserv (контейнер)<br/>образ: ocserv-totp-lab"]
            OcServd["ocserv демон"]
            PAM["/etc/pam.d/ocserv<br/>1️⃣ pam_listfile (denylist)<br/>2️⃣ pam_unix (password)<br/>3️⃣ pam_google_authenticator (TOTP)"]
            Linux["Linux users<br/>alice, bob (uid 1000+)<br/>shell: /bin/bash"]
            Shadow["/etc/shadow<br/>(bcrypt пароли)"]
        end

        subgraph ADMIN["📦 admin (контейнер)<br/>образ: ocserv-admin<br/>Python 3.12 + FastAPI"]
            App["app/main.py<br/>uvicorn 8443/HTTPS"]
            App --> Web["routes_web.py<br/>(Jinja страницы)"]
            App --> Api["routes_api.py<br/>(JSON API)"]
        end
    end

    subgraph Volumes["🗄️ shared volumes"]
        VHome["ocserv-home<br/>/home/<u>/.google_authenticator<br/>(TOTP-секреты юзеров)"]
        VCtl["ocserv-control<br/>disabled-users<br/>(denylist)"]
        VSsl["ocserv-ssl<br/>CA + server cert"]
        VData["admin-data<br/>admin.db (SQLite)"]
        VAdminSsl["admin-ssl<br/>admin cert"]
    end

    Host -- "tcp/udp 4443→443" --> OcServd
    Host -- "tcp 8443" --> App

    OcServd --> PAM
    PAM --> Shadow
    PAM -. читает .-> VCtl
    PAM -. читает .-> VHome
    OcServd --> VSsl

    Web --> VData
    Api --> VData
    Web -. RW .-> VHome
    Web -. RW .-> VCtl
    Api -. RW .-> VHome
    Api -. RW .-> VCtl
    App --> VAdminSsl

    classDef vol fill:#fef3c7,stroke:#f59e0b
    class VHome,VCtl,VSsl,VData,VAdminSsl vol
```

**Принцип**: admin-контейнер **не имеет** привилегий ocserv (нет CAP_NET_ADMIN, нет docker.sock). Влияет на ocserv только через **два общих файловых volume** (`ocserv-home` и `ocserv-control`).

---

## 2. Кто откуда берёт юзеров

```mermaid
flowchart LR
    EnvFile["users.env<br/>LAB_USERS=&quot;alice:123 bob:bob-pass-123&quot;"]
    Entry["scripts/entrypoint.sh<br/>(при старте ocserv)"]
    LinuxDB[("/etc/passwd + /etc/shadow<br/>в ocserv-контейнере")]
    HomeDir[("/home/alice/<br/>/home/bob/<br/>в ocserv-home")]

    EnvFile --> Entry
    Entry -- "useradd + chpasswd" --> LinuxDB
    Entry -- "mkdir home" --> HomeDir

    AdminUI["Admin UI / API<br/>GET /api/v1/users"]
    AdminLogic["app/users.py<br/>list_users()"]

    AdminUI --> AdminLogic
    AdminLogic -- "os.listdir(/home)" --> HomeDir
    AdminLogic -- "проверяет .google_authenticator?" --> HomeDir
    AdminLogic -- "читает denylist" --> Denylist[("ocserv-control/<br/>disabled-users")]
    AdminLogic -- "берёт last_issued_at" --> SQLite[("admin.db<br/>enrollments")]
    AdminLogic --> Result["список юзеров<br/>+ has_totp + disabled + last_issued"]

    classDef src fill:#dbeafe,stroke:#3b82f6
    classDef store fill:#fef3c7,stroke:#f59e0b
    class EnvFile,Entry src
    class LinuxDB,HomeDir,Denylist,SQLite store
```

**Ключевое**: админка **не создаёт** юзеров. Просто **читает** `/home` через shared volume. Заводятся в `users.env` + `entrypoint.sh`.

---

## 3. Поток выпуска ключа (Issue / Re-issue)

```mermaid
sequenceDiagram
    participant Admin as Admin (web/CLI)
    participant Web as routes_web.py
    participant Logic as users.enroll_user()
    participant TOTP as totp.py
    participant FS as ocserv-home<br/>(shared volume)
    participant DB as admin.db
    participant PAM as pam_google_authenticator<br/>(в ocserv при auth)

    Admin->>Web: POST /users/alice/enroll<br/>+ CSRF token
    Web->>Web: проверка сессии (require_admin_web)
    Web->>Web: проверка CSRF (csrf.verify)
    Web->>Logic: enroll_user(alice, admin)

    Logic->>Logic: safe_home_path() — antitraversal
    Logic->>TOTP: generate_enrollment(alice)
    TOTP-->>Logic: secret (32 base32) + 5 scratch codes
    TOTP->>TOTP: build_qr_png_base64()

    Logic->>FS: write /home/alice/.google_authenticator<br/>(atomic: tmp + os.replace)
    Logic->>FS: chmod 600, chown alice:alice

    Logic->>DB: INSERT INTO enrollments<br/>(action='issued', actor='admin#1', fingerprint=sha256(secret))
    Logic-->>Web: EnrollResult{secret, qr_b64, scratch_codes}

    Web-->>Admin: HTML qr_once.html<br/>(secret виден ОДИН раз)

    Note over PAM,FS: позже при VPN-логине:
    PAM->>FS: читает /home/alice/.google_authenticator
    PAM->>PAM: проверяет TOTP-код
```

**API-эндпоинт** (`POST /api/v1/users/{u}/enroll`) идёт через ту же `users.enroll_user()` — единый источник истины. Различаются только auth (`Authorization: Bearer vpa_…` vs сессия + CSRF) и формат ответа (JSON vs HTML).

---

## 4. Поток отзыва (Revoke) и обратно (Enable)

```mermaid
sequenceDiagram
    participant Admin
    participant Logic as users.revoke_user()
    participant Denylist as ocserv-control/<br/>disabled-users
    participant FS as ocserv-home/<br/>alice/
    participant DB as admin.db
    participant PAM as pam_listfile<br/>(в ocserv при auth)

    Admin->>Logic: POST /users/alice/revoke

    rect rgb(254, 226, 226)
        Note over Logic,FS: REVOKE
        Logic->>Logic: fcntl.flock(denylist, LOCK_EX)
        Logic->>Denylist: append "alice"<br/>(atomic: tmp + os.rename)
        Logic->>FS: rm .google_authenticator
        Logic->>DB: INSERT enrollments(action='revoked')
    end

    Note over Admin,PAM: попытка alice подключиться:
    Admin->>PAM: SSL+username=alice
    PAM->>Denylist: pam_listfile проверяет<br/>"alice" в файле?
    PAM-->>Admin: ❌ DENY мгновенно<br/>(до того как покажет Password:)

    rect rgb(220, 252, 231)
        Note over Logic,FS: ENABLE (обратно)
        Admin->>Logic: POST /users/alice/enable
        Logic->>Denylist: убрать "alice"<br/>(с flock)
        Logic->>DB: INSERT enrollments(action='enabled')
        Note over FS: ⚠ TOTP-файл НЕ восстанавливается,<br/>надо нажать Issue
    end
```

---

## 5. Поток логина админа в саму админку

```mermaid
sequenceDiagram
    participant U as Admin (browser)
    participant Web as routes_web.py
    participant Auth as auth.py
    participant Sess as sessions.py
    participant DB as admin.db

    U->>Web: GET /
    Web-->>U: 303 → /login

    U->>Web: POST /login {username, password}
    Web->>Auth: verify_password()
    Auth->>DB: SELECT admin
    Auth->>Auth: bcrypt.verify()
    Auth-->>Web: ok
    Web->>Web: ratelimit check (5 fail/15min/IP)
    Web-->>U: 303 → /login/totp<br/>+ cookie __Host-admin_pending=1

    U->>Web: GET /login/totp
    Web->>DB: admin.totp_secret?
    alt secret IS NULL (первый вход)
        Web-->>U: 303 → /login/enroll-totp<br/>(показать QR)
        U->>Web: POST /login/enroll-totp {code}
        Web->>Auth: set_admin_totp()
        Web-->>U: 303 → /login/totp
    else secret уже есть
        Web-->>U: HTML форма "введите код"
    end

    U->>Web: POST /login/totp {code}
    Web->>Auth: verify_admin_totp() (pyotp)
    Web->>Sess: create_session()
    Sess->>DB: INSERT sessions(random 256-bit token, admin_id, ip, ua)
    Web-->>U: 303 → /<br/>+ cookie __Host-admin_session<br/>(Secure, HttpOnly, SameSite=Strict)
```

---

## 6. Куда что писать (карта модулей)

| Что | Файл | Куда пишет/читает |
|---|---|---|
| Список юзеров | `admin/app/users.py:list_users` | читает `/home/*`, `disabled-users`, `enrollments` |
| Issue TOTP | `admin/app/users.py:enroll_user` | пишет `/home/<u>/.google_authenticator`, `enrollments` |
| Revoke | `admin/app/users.py:revoke_user` | пишет `disabled-users`, удаляет TOTP-файл, `enrollments` |
| Enable | `admin/app/users.py:enable_user` | пишет `disabled-users`, `enrollments` |
| Login юзера в админку | `admin/app/auth.py` + `routes_web.py` | читает `admins`, пишет `sessions` |
| Создание API-токена | `admin/app/tokens.py:create_token` | пишет `api_tokens` (bcrypt-hash plaintext) |
| Verify API-токена | `admin/app/tokens.py:verify_token` | читает `api_tokens` |
| Любое действие | `admin/app/audit.py:write_audit` | пишет `audit_log` |
| Rate limit | `admin/app/ratelimit.py` | читает `audit_log` (sliding window) |
| TOTP файл-формат | `admin/app/totp.py` | формирует совместимое с `pam_google_authenticator` содержимое |
