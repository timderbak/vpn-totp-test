# Шпаргалка по разворачиванию на боевом сервере

Личная пошаговая инструкция: зашёл на сервер → пройти по пунктам сверху вниз → в конце всё работает.

Предпосылки на сервере (что у нас уже есть):
- ✅ ocserv установлен и работает (нативно, не контейнер)
- ✅ LDAP установлен и работает (внешний или локальный)
- ❌ admin-панели нет
- ❌ TOTP пока не подключён к ocserv

Что **ты** сделаешь по этой шпаргалке:
- Поднимешь admin-контейнер
- Пропатчишь ocserv (1 строка в PAM)
- Создашь service-account в LDAP
- Установишь `pam_google_authenticator`
- Проверишь полный цикл «выпустил TOTP → подключился»

---

## ✅ Шаг 0 — войти на сервер и осмотреться

```bash
ssh root@<сервер>     # или sudo -i после ssh

# 0.1 проверь что ocserv реально работает
systemctl status ocserv
ss -tlnp | grep -E "(ocserv|:443)"

# 0.2 проверь что LDAP отвечает
ldapsearch -x -H ldap://<ldap-host>:389 -b "dc=YOUR,dc=BASE" -s base 2>&1 | head -5

# 0.3 есть ли docker?
docker --version && docker compose version
```

**Если docker нет**:
```bash
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
```

---

## ✅ Шаг 1 — Клонировать репо

```bash
mkdir -p /opt && cd /opt
git clone https://github.com/timderbak/vpn-totp-test.git vpn-admin
cd vpn-admin
ls docs/         # должны быть production-install.md и пр.
```

---

## ✅ Шаг 2 — Подготовить LDAP (service account)

```bash
# 2.1 узнать base DN и формат твоего LDAP
ldapsearch -x -H ldap://<ldap-host>:389 -b "" -s base namingContexts

# 2.2 сгенерить пароль service account-а и SSHA-хеш
ADMIN_RO_PW=$(openssl rand -hex 16)
echo "Запиши: $ADMIN_RO_PW"      # ← это плейн-пароль, в .env пойдёт
HASH=$(slappasswd -h '{SSHA}' -s "$ADMIN_RO_PW")
echo "$HASH"                      # ← хеш для LDIF

# 2.3 LDIF для добавления service account
cat > /tmp/admin-ro.ldif <<EOF
dn: cn=admin-readonly,dc=YOUR,dc=BASE
objectClass: simpleSecurityObject
objectClass: organizationalRole
cn: admin-readonly
description: Read-only account for VPN admin panel
userPassword: $HASH
EOF

# 2.4 добавить (используй вашего LDAP-admin'а):
ldapadd -x -D cn=admin,dc=YOUR,dc=BASE -W -f /tmp/admin-ro.ldif

# 2.5 проверить что bind работает
ldapsearch -x -H ldap://<ldap-host>:389 \
    -D cn=admin-readonly,dc=YOUR,dc=BASE -w "$ADMIN_RO_PW" \
    -b ou=users,dc=YOUR,dc=BASE "(objectClass=posixAccount)" uid | head -10
# должны вернуться юзеры
```

**Проверь у юзеров атрибуты:**
```bash
ldapsearch -x -D cn=admin-readonly,dc=YOUR,dc=BASE -w "$ADMIN_RO_PW" \
    -b ou=users,dc=YOUR,dc=BASE "(uid=<какой-нибудь-юзер>)" \
    uid uidNumber gidNumber homeDirectory cn mail
```
Должны быть `uidNumber` и `gidNumber`. Если их нет — это блокер, идти к LDAP-админу за расширением схемы.

**Создать группу `vpn-users` если её нет:**
```bash
cat > /tmp/vpn-group.ldif <<EOF
dn: cn=vpn-users,ou=groups,dc=YOUR,dc=BASE
objectClass: posixGroup
cn: vpn-users
gidNumber: 3000
memberUid: <user1>
memberUid: <user2>
EOF
ldapadd -x -D cn=admin,dc=YOUR,dc=BASE -W -f /tmp/vpn-group.ldif
```

---

## ✅ Шаг 3 — Установить `pam_google_authenticator` на сервер

```bash
# Debian/Ubuntu
apt update && apt install -y libpam-google-authenticator

# RHEL/Rocky/Alma
dnf install -y google-authenticator
```

Проверка:
```bash
ls /lib/*/security/pam_google_authenticator.so   # debian
ls /usr/lib64/security/pam_google_authenticator.so   # rhel
```

---

## ✅ Шаг 4 — Подготовить `/etc/pam.d/ocserv`

⚠ **Сделай бэкап перед правкой!**
```bash
cp /etc/pam.d/ocserv /etc/pam.d/ocserv.bak.$(date +%Y%m%d)
```

Открой `/etc/pam.d/ocserv` и добавь **первой строкой** (denylist) и **последней auth-строкой** (TOTP):

```
# первая строка — denylist
auth requisite pam_listfile.so onerr=succeed item=user sense=deny file=/etc/ocserv/control/disabled-users

# существующие строки auth (твой LDAP/pam_unix) — оставить как было

# в самом конце auth-блока — добавить TOTP
auth required pam_google_authenticator.so
```

Создай пустой denylist:
```bash
mkdir -p /etc/ocserv/control
touch /etc/ocserv/control/disabled-users
chmod 644 /etc/ocserv/control/disabled-users
```

**Перезапусти ocserv:**
```bash
systemctl restart ocserv
journalctl -u ocserv -n 30 --no-pager     # ошибок быть не должно
```

---

## ✅ Шаг 5 — Подготовить `.env`

```bash
cd /opt/vpn-admin
cp .env.example .env

# 5.1 сгенерить хеш bootstrap-пароля админки
docker run --rm httpd:alpine htpasswd -nbBC 12 admin1 'ВпишиСюдаСильныйПароль' | cut -d: -f2
# скопируй вывод, в нём заменить $ на $$ (для docker-compose эскейпа)

# 5.2 сгенерить cookie secret
openssl rand -hex 32

vi .env
```

В `.env` заполни:
```env
ADMIN_BOOTSTRAP_USERNAME=admin1
ADMIN_BOOTSTRAP_PASSWORD_HASH=$$2y$$12$$<остаток-хеша-с-замененными-$>
ADMIN_COOKIE_SECRET=<64 hex из openssl rand>

# LDAP — реальные значения
ADMIN_LDAP_URL=ldap://<ldap-host>:389
ADMIN_LDAP_BIND_DN=cn=admin-readonly,dc=YOUR,dc=BASE
ADMIN_LDAP_BIND_PASSWORD=<плейн-пароль из шага 2.2>
ADMIN_LDAP_BASE_DN=dc=YOUR,dc=BASE
ADMIN_LDAP_USER_OU=ou=users,dc=YOUR,dc=BASE
ADMIN_LDAP_VPN_GROUP_DN=cn=vpn-users,ou=groups,dc=YOUR,dc=BASE
ADMIN_LDAP_CACHE_TTL=30
ADMIN_LDAP_TIMEOUT=5
```

Проверь права:
```bash
chmod 600 .env
ls -la .env       # должно быть -rw------- root root
```

---

## ✅ Шаг 6 — Сделать `docker-compose.prod.yml` (только admin-сервис)

```bash
cat > docker-compose.prod.yml <<'EOF'
services:
  admin:
    build:
      context: ./admin
      dockerfile: Dockerfile
    image: vpn-admin:latest
    container_name: vpn-admin

    ports:
      - "127.0.0.1:8443:8443/tcp"     # ТОЛЬКО localhost, доступ через SSH-tunnel

    env_file:
      - .env

    volumes:
      - /home:/home                                    # сюда админка кладёт .google_authenticator
      - /etc/ocserv/control:/etc/ocserv/control         # denylist
      - admin-data:/var/lib/admin
      - admin-ssl:/etc/admin/ssl

    restart: unless-stopped

volumes:
  admin-data:
  admin-ssl:
EOF
```

---

## ✅ Шаг 7 — Запустить admin-контейнер

```bash
docker compose -f docker-compose.prod.yml up -d --build

# логи
docker compose -f docker-compose.prod.yml logs admin --tail 30

# healthz
curl -sk https://localhost:8443/healthz
# ожидание: {"status":"ok"}
```

Если `{"status":"ok"}` не приходит — `docker compose logs admin` покажет почему (чаще всего .env разъезжается).

---

## ✅ Шаг 8 — Первый вход в админку

С твоего ноута:
```bash
# SSH-tunnel чтобы добраться до 127.0.0.1:8443 на сервере
ssh -L 8443:127.0.0.1:8443 root@<сервер>
```

Открой в браузере: **https://localhost:8443/**

Серт self-signed — принять. Логин: `admin1` + пароль из шага 5.1.

После пароля будет страница с QR-кодом — отсканируй в Google Authenticator (или Яндекс.Ключ). Введи 6-значный код для подтверждения. Готово.

---

## ✅ Шаг 9 — Smoke test (выпустить TOTP юзеру, подключиться)

В админке:
1. На дашборде должны быть видны юзеры из `cn=vpn-users`. Если нет — `/users/_refresh` (кнопка Refresh) — это форсит обновление кэша.
2. Жми **Issue** напротив тестового юзера → появится QR. Отсканируй любым TOTP-app.
3. Закрой QR-страницу (секрет уже больше не покажется).

С другой машины (или своего ноута) проверь VPN:
```bash
sudo openconnect --protocol=anyconnect --user=<тест-юзер> <сервер>
# password: пароль из LDAP
# Verification code: 6 цифр из authenticator-app
# должно дать: Connected as <ip-из-пула>
```

Если **подключился** — всё работает.

---

## ✅ Шаг 10 — Создать API-токены / второго админа

**Второй админ:**
```bash
docker compose -f docker-compose.prod.yml exec admin python -m app.cli add-admin admin2
# спросит пароль дважды
```
Под `admin2` потом зайдёт в web-UI, отсканит свой QR — готов.

**API-токен** (для внешних систем — HR/тикеты/Ansible):
В web-UI → вкладка **API Tokens** → **Create** → имя + scopes → получишь `vpa_...` ОДИН раз.

Использовать:
```bash
curl -sk -H "Authorization: Bearer vpa_..." https://localhost:8443/api/v1/users
```

---

## ✅ Шаг 11 — Прод-чек-лист (что не забыть до сдачи)

- [ ] `.env` лежит **только** на сервере, **не** в git
- [ ] Бэкап `/var/lib/docker/volumes/vpn-admin_admin-data/` (там SQLite с админскими паролями+TOTP+токенами)
- [ ] За HTTPS на 8443 поставлен валидный серт (Let's Encrypt / корпоративный CA) либо доступ только через SSH-tunnel (как сейчас)
- [ ] Firewall: `127.0.0.1:8443` уже привязан только к localhost (✅ из коробки), но **проверь** что наружу не торчит: `ss -tlnp | grep 8443`
- [ ] `journalctl -u ocserv -f` смотреть когда тестируешь — там вся PAM-конверсация видна
- [ ] Логи админки: `docker compose -f docker-compose.prod.yml logs admin -f`
- [ ] Audit-log внутри: `docker compose -f docker-compose.prod.yml exec admin sqlite3 /var/lib/admin/admin.db 'SELECT ts, actor_type, action, target_user, result FROM audit_log ORDER BY id DESC LIMIT 20'`

---

## 🚑 Если что-то сломалось — куда смотреть

| Симптом | Где смотреть |
|---|---|
| `docker compose up` ругается на .env | `docker compose config` покажет финальные значения с эскейпом `$$` → `$` |
| Web-UI открывается, но логин fail на пароле | в `.env` хеш bcrypt не тот; проверь что `$` эскейплены `$$` |
| Дашборд пустой | LDAP-bind не работает: `docker compose logs admin` → ищи `LdapUnavailable`. Проверь `ADMIN_LDAP_BIND_PASSWORD` |
| Юзер не появляется в дашборде | юзера нет в `cn=vpn-users` (или другая группа в `ADMIN_LDAP_VPN_GROUP_DN`) |
| Issue работает, но VPN не пускает | `journalctl -u ocserv -f` при попытке; обычно — нет `pam_google_authenticator.so` в стеке или `.google_authenticator` файл с неправильными правами |
| Revoke не блочит | проверь что в `/etc/pam.d/ocserv` первая строка — `pam_listfile` с правильным путём |

Полный troubleshooting: `docs/pam-deep-dive.md` секция «Главные точки отказа».

---

## 📋 Ссылки на остальные доки

- `docs/production-install.md` — то же что эта инструкция, но более общая (для других сетевиков)
- `docs/architecture.md` — общая архитектура с диаграммами
- `docs/pam-deep-dive.md` — глубоко про PAM (если что-то не работает на стороне auth)
- `docs/security-review-2026-05-25.md` — security-аудит + что осталось как tech-debt
