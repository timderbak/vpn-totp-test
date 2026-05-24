# Установка admin-панели на боевой ocserv + LDAP

Этот документ — для сетевиков. Предполагается что на сервере **уже** работают `ocserv` (OpenConnect VPN) и LDAP. Из этого репо берётся **только admin-панель** + минимальная интеграция с существующим стеком.

Что добавится:
- 1 контейнер `ocserv-admin` (Python/FastAPI на 8443/HTTPS) — управление TOTP-ключами через web + JSON API
- 1 файл-denylist для «отозвать пользователю VPN-доступ»
- 1 SQLite-БД для метаданных админки (локально, в volume)
- Минимальная правка `/etc/pam.d/ocserv` (1 строка)
- 1 service account в LDAP (read-only)

Что **не** добавится:
- Свой ocserv — используется ваш
- Свой LDAP — используется ваш
- Внешние БД — admin metadata в локальном SQLite

---

## 1. Что нужно от LDAP (один раз)

Создать read-only service account для админ-панели:

```ldif
dn: cn=admin-readonly,dc=YOUR,dc=BASE
objectClass: simpleSecurityObject
objectClass: organizationalRole
cn: admin-readonly
userPassword: {SSHA}<хеш-вашего-пароля>
description: Read-only account for VPN admin panel
```

Сгенерить хеш: `slappasswd -h '{SSHA}' -s '<your-bind-password>'`.

ACL: дать этому DN **read** на ветку с юзерами и группами. Обычно достаточно базового read.

**Какие LDAP-объекты ожидает админка:**
- Юзеры: `objectClass: posixAccount` + `uid`, `uidNumber`, `gidNumber`, `homeDirectory`, `cn`, `mail` (опц.)
- Группа `cn=vpn-users` (имя настраивается): `objectClass: posixGroup` + `memberUid: <login>` для каждого VPN-юзера. Только они будут видны в админке и допущены до VPN.

Если у вас не `posixGroup` (например AD с `group` + `member` DN-ссылками) — придётся поменять одну SQL-подобную строку в `admin/app/ldap_client.py` (`(&(objectClass=posixGroup)(memberUid=...))` → ваш фильтр). Скажите если так — поможем.

---

## 2. Что нужно от ocserv

### 2.1 PAM-стек

Открыть `/etc/pam.d/ocserv` и **вставить первой строкой**:

```
auth requisite pam_listfile.so onerr=succeed item=user sense=deny file=/etc/ocserv/control/disabled-users
```

Создать пустой файл:
```bash
mkdir -p /etc/ocserv/control
touch /etc/ocserv/control/disabled-users
chmod 644 /etc/ocserv/control/disabled-users
```

Что это даёт: когда админка кладёт в этот файл `username`, ocserv мгновенно отказывает этому юзеру в auth ещё до prompt'а пароля.

### 2.2 TOTP-модуль

Если у вас ещё не подключён google-authenticator — поставить:
```bash
apt-get install -y libpam-google-authenticator
```

И добавить в `/etc/pam.d/ocserv` (после строки с pam_ldap или другим password-модулем):
```
auth required pam_google_authenticator.so
```

Если уже есть свой 2FA-механизм (HOTP, RADIUS-OTP, etc.) — админка для него **не подойдёт**, она специально работает с `~/.google_authenticator` файлами.

### 2.3 Где живут TOTP-секреты

Админка пишет файлы в **домашние папки юзеров**: `/home/<username>/.google_authenticator`. Если у вас:
- юзеры из LDAP с `homeDirectory: /home/<user>` — админка сама `mkdir`+`chown` по uid/gid из LDAP при первом Issue
- юзеры из `/etc/passwd` (локальные) — home-папки должны уже существовать, админка просто запишет файл

**Этот каталог `/home` должен быть доступен и для ocserv-процесса (для `pam_google_authenticator`) и для admin-контейнера (для записи).** Самое простое: смонтировать **одну и ту же директорию** в оба процесса. Если ocserv не в контейнере — bind-mount `/home` в admin-контейнер read-write.

---

## 3. Установка admin-панели

### 3.1 Получить код

```bash
git clone https://github.com/<this-repo-url> /opt/vpn-admin
cd /opt/vpn-admin
```

### 3.2 Конфиг

```bash
cp .env.example .env
```

Открыть `.env` и заполнить:

```env
# Bootstrap-админ (первый юзер админки)
ADMIN_BOOTSTRAP_USERNAME=admin1
ADMIN_BOOTSTRAP_PASSWORD_HASH=$$2y$$12$$<bcrypt-хеш>     # см. ниже
ADMIN_COOKIE_SECRET=<64 hex>                              # openssl rand -hex 32

# LDAP (ваш существующий)
ADMIN_LDAP_URL=ldap://your-ldap-host:389                  # или ldaps://...:636
ADMIN_LDAP_BIND_DN=cn=admin-readonly,dc=YOUR,dc=BASE
ADMIN_LDAP_BIND_PASSWORD=<пароль service account>
ADMIN_LDAP_BASE_DN=dc=YOUR,dc=BASE
ADMIN_LDAP_USER_OU=ou=users,dc=YOUR,dc=BASE
ADMIN_LDAP_VPN_GROUP_DN=cn=vpn-users,ou=groups,dc=YOUR,dc=BASE
ADMIN_LDAP_CACHE_TTL=30
ADMIN_LDAP_TIMEOUT=5
```

**Сгенерить bcrypt-хеш админского пароля** (оффлайн):
```bash
docker run --rm httpd:alpine htpasswd -nbBC 12 admin1 'СильныйПароль123' | cut -d: -f2
```

⚠ **Важно про `$` в .env**: docker-compose интерпретирует `$X` как переменную. В bcrypt-хеше много `$` — эскейпьте их как `$$`. Пример: хеш `$2y$12$abc...` пишется как `$$2y$$12$$abc...`. В коде это нормализуется обратно.

### 3.3 Минимальный docker-compose

В этом репо лежит полный `docker-compose.yml` с LDAP и ocserv для лаба. **Для прода вам нужен только сервис `admin`**. Создайте отдельный `/opt/vpn-admin/docker-compose.prod.yml`:

```yaml
services:
  admin:
    build:
      context: ./admin
      dockerfile: Dockerfile
    image: vpn-admin:latest
    container_name: vpn-admin
    hostname: vpn-admin

    ports:
      - "8443:8443/tcp"

    env_file:
      - .env

    volumes:
      # КРИТИЧНО: эти два пути должны указывать туда же, куда смотрит ocserv.
      - /home:/home                                    # юзерские home + TOTP-файлы
      - /etc/ocserv/control:/etc/ocserv/control         # denylist
      # Локальные данные админки
      - admin-data:/var/lib/admin                       # SQLite-БД
      - admin-ssl:/etc/admin/ssl                        # self-signed серт для 8443

    restart: unless-stopped

volumes:
  admin-data:
  admin-ssl:
```

### 3.4 Запуск

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Проверка:
```bash
curl -sk https://localhost:8443/healthz
# {"status":"ok"}

docker compose -f docker-compose.prod.yml logs admin | tail -20
```

### 3.5 Первый вход

Открыть в браузере **`https://<сервер>:8443/`** (серт self-signed — принять).

Логин: `admin1` + пароль из шага 3.2.

На первом входе попросит отсканировать QR-код для админского TOTP-второго фактора. После этого каждый вход = пароль + код.

Дальше:
1. Дашборд показывает список юзеров из `cn=vpn-users` (через LDAP-кэш 30с)
2. Кнопка **Issue** напротив юзера — генерит TOTP, пишет `/home/<u>/.google_authenticator`, показывает QR один раз
3. Кнопка **Revoke** — добавляет в `disabled-users` + удаляет TOTP-файл

### 3.6 API-токены для интеграций

Если внешние системы (CI, Ansible, тикетная система) должны управлять выпуском — в админке вкладка **API Tokens** → создать токен со scope `read,enroll,revoke`. Plaintext показывается **один раз**. Использование:

```bash
TOKEN=vpa_...
curl -sk -H "Authorization: Bearer $TOKEN" https://vpn-admin:8443/api/v1/users
curl -sk -X POST -H "Authorization: Bearer $TOKEN" https://vpn-admin:8443/api/v1/users/alice/enroll
curl -sk -X POST -H "Authorization: Bearer $TOKEN" https://vpn-admin:8443/api/v1/users/alice/revoke
```

---

## 4. Чек-лист перед сдачей в прод

- [ ] Service account `cn=admin-readonly` создан в LDAP, есть read-ACL
- [ ] Группа `cn=vpn-users` существует, реальные VPN-юзеры в неё внесены (`memberUid`)
- [ ] В `/etc/pam.d/ocserv` добавлена строка `pam_listfile` (см. 2.1)
- [ ] Создан `/etc/ocserv/control/disabled-users` (пустой)
- [ ] Подключён `pam_google_authenticator.so` если не было (см. 2.2)
- [ ] `/home` доступен и для ocserv-процесса и для admin-контейнера
- [ ] `.env` заполнен, **в git не закоммичен** (он в `.gitignore`)
- [ ] Bcrypt-хеш bootstrap-пароля сгенерён, `$` эскейплены как `$$`
- [ ] `docker compose -f docker-compose.prod.yml up -d --build` отработал, healthz возвращает ok
- [ ] Браузер открывает `https://<сервер>:8443/`, первый login прошёл, TOTP админа отсканирован
- [ ] Тест: Issue для одного юзера → у него появился `~/.google_authenticator` → реально может подключиться по VPN с паролем+TOTP
- [ ] Тест: Revoke → юзер мгновенно отказан в auth (видно в логах ocserv)
- [ ] За HTTPS на 8443 поставлен валидный серт (Let's Encrypt / корпоративный CA) — self-signed только для тестов
- [ ] Открыть 8443 только для админских IP (firewall / VPN-only)

---

## 5. Чего не делает эта установка (важно знать)

- **Не создаёт LDAP-юзеров.** Юзера в LDAP заводит ваш существующий процесс (HR-интеграция, IDM, ldapadd). Админка только видит их и выдаёт/отзывает TOTP.
- **Не меняет пароли юзеров.** Пароль остаётся в LDAP, админка его не видит и не трогает.
- **Не делает HA / clustering.** Один контейнер, локальная SQLite. Для HA — несколько инстансов с общей БД (Postgres) — потребует доработки.
- **Не интегрируется с SIEM по умолчанию.** Аудит-лог в локальной SQLite, можно слать через docker-logs → syslog → SIEM.
- **Не управляет правами админок.** Один уровень `admin`. Если нужны роли — доработка.
- **Админы локальные, не LDAP.** Bootstrap-юзер в `.env`, дополнительных админов добавлять руками через SQL в `admin.db`. Если нужен LDAP-логин для админов — доработка.

---

## 6. Документация для углубления

В этом репо есть подробные доки:
- `docs/architecture.md` — общая архитектура с диаграммами
- `docs/ldap-flow.md` — пошагово как заводится юзер и выпускается TOTP
- `docs/pam-deep-dive.md` — глубокий разбор PAM-стека, как PAM модули общаются с ocserv
- `docs/ocserv-explained.md` — про сам ocserv (если будете двигать его конфиг)
- `docs/superpowers/specs/` — design-spec'и админки и LDAP-интеграции
- `README.md` — общий обзор стенда (полный compose с собственным LDAP и ocserv для разработки)

---

## 7. Контакт

Если что-то непонятно — пиши автору репозитория. На проблемные шаги обычно нужно реальное состояние вашей системы: `cat /etc/pam.d/ocserv`, `ldapsearch -b ou=users... uid memberOf`, версия ocserv.
