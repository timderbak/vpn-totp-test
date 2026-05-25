# ocserv + TOTP lab

Локальный учебный стенд OpenConnect VPN (ocserv) с двухфакторной
аутентификацией. Цель — руками пройти весь путь: создать пользователя,
сгенерировать ему TOTP-секрет, отсканировать QR в приложение на телефоне,
подключиться с Mac VPN-клиентом по схеме «пароль + 6-значный код».

Это **тестовый стенд**, не production. Локальные системные юзеры, plaintext
пароли в репозитории — всё осознанно, чтобы видеть механику. Для прода нужен
LDAP/IdP, хэши, секреты в vault и так далее.

> Шаблон-репозиторий, из которого собран этот стенд, описан в
> [TEMPLATE.md](./TEMPLATE.md). Правила работы Claude Code — в
> [CLAUDE.md](./CLAUDE.md).

---

## 📚 Документация

| Документ | Для кого / зачем |
|---|---|
| [docs/production-install.md](./docs/production-install.md) | Установка на боевой сервер (общая инструкция, для сетевиков) |
| [docs/my-deploy-checklist.md](./docs/my-deploy-checklist.md) | Личная пошаговая шпаргалка для разворачивания на готовом ocserv+LDAP сервере |
| [docs/architecture.md](./docs/architecture.md) | Общая архитектура с mermaid-диаграммами (контейнеры, потоки данных, flows) |
| [docs/ldap-flow.md](./docs/ldap-flow.md) | Пошагово как заводится юзер и выпускается TOTP |
| [docs/ocserv-explained.md](./docs/ocserv-explained.md) | Построчный разбор `ocserv.conf` — если будете двигать конфиг |
| [docs/pam-deep-dive.md](./docs/pam-deep-dive.md) | Глубокий разбор PAM-стека + nslcd + troubleshooting |
| [docs/security-review-2026-05-25.md](./docs/security-review-2026-05-25.md) | Security-аудит + что закрыто / что осталось как tech-debt |
| [ldap/README.md](./ldap/README.md) | Как добавить нового VPN-юзера в LDAP |
| [docs/superpowers/specs/](./docs/superpowers/specs/) | Design-spec'и: админка и LDAP-интеграция |

---

## Что внутри

```
.
├── docker-compose.yml          # ocserv + ldap + admin
├── Dockerfile                  # debian:bookworm-slim + ocserv + libpam-ldapd + nslcd + libpam-google-authenticator
├── config/ocserv.conf          # конфиг ocserv (с подробными комментариями)
├── pam/ocserv                  # PAM-стек: denylist + pam_ldap + TOTP
├── ocserv-ldap/nslcd.conf.tmpl # envsubst-шаблон для nslcd (читается pam_ldap.so из libpam-ldapd)
├── ldap/bootstrap/*.ldif       # seed-юзеры alice/bob и группа vpn-users
├── scripts/
│   ├── entrypoint.sh           # генерит серты, ждёт LDAP, рендерит nslcd.conf, стартует nslcd+ocserv
│   └── totp-enroll             # legacy CLI, больше не нужен — TOTP выпускает админка
└── admin/                      # FastAPI админка (LDAP-клиент, выпуск TOTP, denylist, аудит)
```

---

## Источник юзеров: LDAP

Юзеры **не** живут в `/etc/passwd` контейнера и **не** заводятся через `users.env` (этот файл удалён). Источник истины — OpenLDAP-контейнер, поднимаемый тем же `docker compose`.

Layout:

```
dc=vpn,dc=local
├── ou=users (alice, bob)
└── ou=groups
    └── cn=vpn-users (только эти юзеры могут в VPN)
```

Чтобы добавить нового юзера — см. [`ldap/README.md`](./ldap/README.md).

---

## Быстрый старт

### 1. Поднять стенд

```bash
docker compose up -d --build
```

Сборка занимает 1–2 минуты на Apple Silicon (образ нативный arm64, без QEMU).
Проверить что ocserv действительно слушает:

```bash
docker compose logs ocserv | tail -20
# должно быть видно: "starting ocserv on tcp/udp 443 (mapped to host 4443)"
```

### 2. Выпустить TOTP

Открой админку https://localhost:8443/, залогинься (`admin1` / см. `.env`), на дашборде нажми **Issue** напротив alice. QR-код покажется один раз — отсканируй любым TOTP-приложением (Google Authenticator, Authy, 1Password, Bitwarden).

Файл секрета админка кладёт в shared volume `/home/alice/.google_authenticator` (home-папка создаётся автоматически с правильным uid/gid из LDAP).

> Старый CLI `docker exec -it ocserv totp-enroll alice` больше не работает — юзеры теперь LDAP-ные и home-папка появляется в момент выпуска TOTP через админку.

### 3. Подключиться VPN-клиентом

Поставь openconnect:

```bash
brew install openconnect
```

Подключайся:

```bash
sudo openconnect --protocol=anyconnect --user=alice localhost:4443
```

Флаги:
- `--protocol=anyconnect` — ocserv совместим с Cisco AnyConnect, openconnect
  по этому протоколу с ним разговаривает.
- Серт у нас self-signed, поэтому openconnect ругнётся при первом подключении:
  `Certificate from VPN server "localhost" failed verification.` → введи
  `yes` чтобы принять. Альтернативно — запинить fingerprint:
  ```bash
  FP=$(openssl s_client -connect localhost:4443 </dev/null 2>/dev/null \
       | openssl x509 -fingerprint -sha256 -noout | sed 's/^.*=//' \
       | tr -d ':' | tr 'A-F' 'a-f')
  sudo openconnect --protocol=anyconnect \
       --servercert=sha256:$FP --user=alice localhost:4443
  ```
  Тогда никаких prompt'ов по сертификату.

Когда клиент попросит пароль — введи **пароль и TOTP-код одной строкой,
без пробела и разделителя**:

```
Password: alice-pass-123482104
                          └─┬─┘
                            └─ 6 цифр из TOTP-приложения, прямо в конце пароля
```

Как это работает: в PAM-стеке (`pam/ocserv`) стоит флаг `forward_pass` —
модуль `pam_google_authenticator` отрезает последние 6 цифр, проверяет код,
а пароль `alice-pass-123` передаёт дальше в `pam_unix` через `use_first_pass`.
Один prompt, два фактора.

Если используешь не openconnect, а GUI-клиент Cisco AnyConnect и он
показывает два отдельных поля — это другой режим PAM (см. альтернативный
блок в `pam/ocserv`).

После успешного auth увидишь что-то вроде:

```
Connected as 192.168.99.x, using ssl + DTLS
```

### 4. Проверить что 2FA реально работает

| Попытка | Результат |
|---|---|
| Правильный пароль + правильный код | ✅ подключение |
| Правильный пароль + неправильный код | ❌ отказ |
| Неправильный пароль + правильный код | ❌ отказ |
| Правильный пароль без кода | ❌ отказ |

Логи смотреть так:

```bash
docker compose logs -f ocserv
```

При неуспешной auth увидишь строчки про `pam_unix(ocserv:auth): authentication
failure` или `pam_google_authenticator: Invalid verification code`.

---

## Что попробовать руками

Всё это — для понимания как устроен TOTP, а не для галочки.

1. **Второй пользователь.**
   ```bash
   docker exec -it ocserv totp-enroll bob
   sudo openconnect --protocol=anyconnect localhost:4443 --user=bob -k
   ```

2. **Посмотреть содержимое секрета.**
   ```bash
   docker exec ocserv cat /home/alice/.google_authenticator
   ```
   Первая строка — base32-секрет. Дальше — флаги (RATE_LIMIT, DISALLOW_REUSE,
   WINDOW_SIZE, TOTP_AUTH). В конце — 5 scratch-кодов. **Никакого «списка
   будущих кодов» нет** — клиент и сервер просто вычисляют HMAC-SHA1 от
   `(секрет, текущее_время_в_30-секундных_окнах)` и берут 6 цифр. Поэтому
   часы должны идти синхронно.

3. **Использовать scratch-код вместо TOTP.**
   Возьми любой из 8-значных scratch-кодов внизу `.google_authenticator`,
   введи его на месте 6-значного кода — пройдёт один раз и больше никогда.

4. **Перегенерировать секрет.**
   ```bash
   docker exec -it ocserv totp-enroll alice
   ```
   Старый QR в приложении перестанет работать сразу — секрет на сервере
   другой. Поэтому в проде не enroll'ят повторно без явной причины.

5. **Сломать время.**
   В Docker Desktop VM время синхронизировано с хостом. Если вручную сдвинуть
   часы на телефоне на 5 минут вперёд — TOTP не сойдётся, и ты увидишь это в
   логах. Это и есть та проблема, из-за которой ±2-3 минуты — норма, а ±5 —
   уже отказ (`-w 3` в `totp-enroll`).

---

## Админ-панель

Веб-админка для управления TOTP-ключами уже существующих юзеров. Поднимается тем же `docker compose up -d`.

### Первый запуск

1. Создай `.env` из `.env.example`. Сгенерируй bcrypt-хеш пароля админа:

   ```bash
   htpasswd -nbB admin1 'your-strong-password' | cut -d: -f2
   ```

   Вставь хеш в `ADMIN_BOOTSTRAP_PASSWORD_HASH`. Туда же — рандомный `ADMIN_COOKIE_SECRET` (64 hex).

2. Подними стек:

   ```bash
   docker compose up -d --build
   ```

3. Открой `https://localhost:8443/` (серт self-signed, браузер поругается → принять).

4. Войди под `admin1` + пароль. На первом входе админка попросит отсканировать QR в authenticator-приложении — это TOTP-второй фактор для самой админки. Подтверди, дальше каждый вход = пароль + код.

### Что умеет

- **Users** — список Linux-юзеров из `/home`. Видно у кого есть TOTP, кто заблокирован, когда последний раз выпускали ключ. Кнопки: Issue / Re-issue / Revoke / Enable.
- **API Tokens** — выпуск токенов для внешних систем со scopes (`read`, `enroll`, `revoke`). Plaintext показывается **один раз**.
- **Audit** — журнал всех действий.

### API

`Authorization: Bearer vpa_…`

```bash
TOKEN=vpa_xxx...

# список юзеров
curl -sk https://localhost:8443/api/v1/users -H "Authorization: Bearer $TOKEN"

# выпустить ключ (вернёт secret + QR один раз)
curl -sk -X POST https://localhost:8443/api/v1/users/alice/enroll \
     -H "Authorization: Bearer $TOKEN"

# отозвать
curl -sk -X POST https://localhost:8443/api/v1/users/alice/revoke \
     -H "Authorization: Bearer $TOKEN"
```

### Безопасность

- HTTPS only (self-signed для лаба).
- Admin: пароль (bcrypt) + TOTP.
- API: per-system токены, bcrypt-hash в БД.
- Все действия — в `audit_log`.
- Rate-limit на login (5 fail/15 мин/IP), на API enroll (1/мин/юзер).
- Изоляция: admin-контейнер без `docker.sock`, без `privileged`. Влияет на ocserv только через два shared volume.

### Как реализовано «отозвать»

В `pam/ocserv` первой строкой стоит `pam_listfile.so` с файлом `/etc/ocserv/control/disabled-users` (shared volume `ocserv-control`). Админка дописывает в него username при revoke и удаляет `.google_authenticator`. При enable — убирает из файла. Enable **не** возвращает TOTP — после enable надо нажать Issue, чтобы сгенерить новый ключ.

---

## Troubleshooting

### `/dev/net/tun` недоступен

```
ERROR: cannot open /dev/net/tun: No such file or directory
```

На большинстве Docker Desktop install'ов TUN внутри VM есть и работает. Если
у тебя нет — в `docker-compose.yml` закомментируй блок `cap_add` + `devices`
и раскомментируй `privileged: true`. Сделай `docker compose up -d --build`
заново.

### openconnect ругается на сертификат

```
Server certificate verify failed: ...
```

Используй флаг `-k`. Альтернатива — взять fingerprint и запиниться:

```bash
docker exec ocserv certtool --infile=/etc/ocserv/ssl/server-cert.pem \
    --certificate-info | grep -i "sha256 fingerprint"

sudo openconnect --protocol=anyconnect localhost:4443 --user=alice \
    --servercert pin-sha256:<вставь сюда>
```

### `Address already in use` на 4443

Что-то на хосте занимает 4443. Поменяй маппинг в `docker-compose.yml`,
например `5443:443/tcp`, и подключайся к `localhost:5443`.

### Проверить что ocserv вообще слушает

```bash
docker exec ocserv ss -tlnp | grep 443
# или с хоста:
nc -vz localhost 4443
```

### Рассинхрон времени

Самая частая проблема TOTP. Проверь время в контейнере и на телефоне:

```bash
docker exec ocserv date
```

Если время в контейнере поехало — рестарт Docker Desktop обычно лечит
(VM пересинхронизируется с хостом при старте).

### Auth-only режим vs полный туннель

Acceptance criterion #3 («подключился клиентом — успех») здесь означает:
**handshake + TLS + auth (пароль + TOTP) прошли, ocserv выдал клиенту IP из
192.168.99.0/24**. Это и есть то, что мы проверяем — механику 2FA.

Прокачка реального трафика хоста через туннель на macOS+Docker Desktop —
**отдельная и тяжёлая задача**: Docker Desktop изолирует контейнерную сеть в
Linux VM, и пробросить весь трафик Mac'а через VPN-туннель, который
терминируется внутри этой VM, без хаков не получится. На Apple Silicon это
особенно сложно. Если очень нужен полный туннель — это уже не задача стенда,
это задача отдельного исследования (host networking, рутинг через `pfctl` на
Mac, и т. п.).

---

## Очистка

Снести стенд целиком (включая сертификаты и home-папки юзеров):

```bash
docker compose down -v
```

Без `-v` — контейнер уйдёт, но именованные volume'ы `ocserv-ssl` и
`ocserv-home` останутся. Это полезно если хочешь сохранить enrollment между
рестартами.

---

## Что НЕ так как в проде

Сознательные упрощения для стенда:

- Локальные системные пользователи Linux вместо LDAP/IdP.
- Пароли в открытом виде в `users.env` (для прода — хэши, vault).
- `isolate-workers = false` в ocserv.conf (для прода — обязательно `true`).
- Самоподписанный сертификат + `-k` на клиенте (для прода — Let's Encrypt
  или корпоративный CA, и pinning).
- Нет fail2ban, нет rate-limit на уровне сети, нет логирования в SIEM.
- Все секреты живут в git и в env-файле (для прода — Docker secrets / SOPS /
  HashiCorp Vault).

В коде эти места отмечены комментариями.
