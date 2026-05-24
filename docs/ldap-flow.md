# Как заводятся юзеры и куда кладётся TOTP

## 1. Что сейчас (без LDAP)

```mermaid
flowchart TB
    Op["👤 Оператор"]
    EnvFile["📄 users.env<br/>LAB_USERS=&quot;alice:123 bob:bob-pass&quot;"]
    Entry["⚙ scripts/entrypoint.sh<br/>(при старте ocserv)"]
    Linux["/etc/passwd + /etc/shadow<br/>(внутри ocserv-контейнера)"]
    Home["📁 /home/alice/<br/>📁 /home/bob/<br/>(shared volume ocserv-home)"]
    Web["🌐 Админка"]
    TOTPfile["📄 /home/alice/.google_authenticator<br/>(секрет TOTP)"]
    PAM["🔒 PAM при логине в VPN"]

    Op -- "дописать строку" --> EnvFile
    EnvFile --> Entry
    Entry -- "useradd alice<br/>+ chpasswd" --> Linux
    Entry -- "создаёт home" --> Home
    Web -- "ls /home<br/>видит alice" --> Home
    Op -- "жмёт Issue" --> Web
    Web -- "пишет файл" --> TOTPfile
    PAM -- "читает пароль" --> Linux
    PAM -- "читает TOTP-секрет" --> TOTPfile

    classDef now fill:#dbeafe,stroke:#3b82f6
    class EnvFile,Entry,Linux now
```

**Простыми словами:**
1. Оператор дописывает строку в `users.env`
2. Перезапускает контейнер → `entrypoint.sh` сам делает `useradd` + создаёт папку `/home/alice/`
3. Админка видит папку → юзер появился в списке
4. Жмёшь "Issue" → файл `.google_authenticator` записывается в эту папку
5. При логине VPN: PAM читает пароль из `/etc/shadow`, TOTP-секрет из файла

---

## 2. Что станет с LDAP

```mermaid
flowchart TB
    Op["👤 Оператор"]
    LDAP["🗄 LDAP-сервер<br/>(юзеры + пароли)"]
    Web["🌐 Админка"]
    Home["📁 /home/alice/<br/>📁 /home/bob/<br/>(shared volume)"]
    TOTPfile["📄 /home/alice/.google_authenticator"]
    PAM["🔒 PAM при логине в VPN<br/>(pam_ldap + pam_google_authenticator)"]

    Op -- "ldapadd bob" --> LDAP
    Web -- "ldapsearch<br/>видит bob" --> LDAP
    Op -- "жмёт Issue для bob" --> Web

    Web -- "1. Запрос uid+gid bob из LDAP" --> LDAP
    Web -- "2. mkdir /home/bob<br/>   chown по uid из LDAP" --> Home
    Web -- "3. пишет файл" --> TOTPfile

    PAM -- "проверка пароля" --> LDAP
    PAM -- "читает TOTP-секрет" --> TOTPfile

    classDef new fill:#dcfce7,stroke:#16a34a
    class LDAP,Web new
```

**Что изменилось:**
- ❌ Нет `users.env`, нет `useradd` — юзера сразу в LDAP
- ✅ Админка вместо `ls /home` идёт в LDAP и берёт оттуда список
- ✅ При "Issue" админка **сама** создаёт папку (берёт uid/gid из LDAP) и кладёт туда TOTP-файл
- ✅ PAM меняет один модуль: `pam_unix` → `pam_ldap`. Проверка TOTP остаётся та же

**Что НЕ изменилось — куда кладётся TOTP:**
По-прежнему `/home/<user>/.google_authenticator`, тот же формат, тот же `pam_google_authenticator` для проверки. Только **папку теперь создаёт админка**, а не entrypoint при старте контейнера.

---

## 3. Один экран — пошагово для bob

```mermaid
sequenceDiagram
    autonumber
    participant Op as Оператор
    participant LDAP
    participant Web as Админка
    participant FS as /home (volume)
    participant Bob as bob (VPN-клиент)
    participant PAM as PAM в ocserv

    Op->>LDAP: ldapadd bob (uid=2001, password)
    Note over LDAP: bob появился в LDAP

    Op->>Web: открывает дашборд
    Web->>LDAP: search всех юзеров
    LDAP-->>Web: [alice, bob, carol]
    Web-->>Op: показывает список,<br/>у bob отметка "нет TOTP"

    Op->>Web: нажимает [Issue] для bob
    Web->>LDAP: search bob → uid=2001, gid=2001
    Web->>FS: mkdir /home/bob<br/>chown 2001:2001<br/>chmod 700
    Web->>FS: write .google_authenticator<br/>chown 2001:2001<br/>chmod 600
    Web-->>Op: показывает QR-код<br/>(один раз)

    Op->>Bob: передаёт QR
    Bob->>Bob: сканирует в Google Authenticator

    Bob->>PAM: подключается VPN<br/>(login=bob, password, TOTP-код)
    PAM->>LDAP: pam_ldap: проверить пароль bob
    LDAP-->>PAM: ✅ ok
    PAM->>FS: pam_google_authenticator:<br/>читать /home/bob/.google_authenticator
    FS-->>PAM: секрет
    PAM->>PAM: сверить TOTP-код
    PAM-->>Bob: ✅ туннель поднят
```

---

## Главное

- **Создаёт home-папку** = админка, в момент когда жмёшь "Issue".
  Не PAM, не cron, не отдельный скрипт. Один источник истины — админка.
- **Куда положить TOTP** не меняется: тот же файл, та же папка.
- **Что меняется** только: откуда взять список юзеров (LDAP), кто создаёт home (админка), какой PAM-модуль для паролей (`pam_ldap`).
