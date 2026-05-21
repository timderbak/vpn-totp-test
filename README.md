# VibecoSwagaTemplate

📊 **[Презентация о шаблоне →](https://timderbak.github.io/VibecoSwagaPlatform/)** ([PDF](./docs/presentation.pdf))

Шаблон-репозиторий для **соло-вайбкодера**. Внутри только то, что нужно чтобы новый проект сразу стартовал с хорошими привычками:

- 🧠 **CLAUDE.md** на базе четырёх принципов Karpathy против типичных болезней LLM-кода + локальные практики.
- 🔌 **Готовый набор плагинов** Claude Code (superpowers, GSD, claude-mem, context7, context-mode, frontend-design, reflexion, github, supabase).
- 📚 **Шпаргалка по system design** под рукой (`docs/system-design-patterns.md`).

Шаблон ничего не пишет за тебя — он только настраивает Claude Code так, чтобы дальше с ним было приятно работать.

---

## Как начать новый проект

```bash
# 1. На GitHub: жми "Use this template" → создаёшь свой репо
git clone git@github.com:<you>/<your-project>.git
cd <your-project>

# 2. Включи плагины глобально (один раз на машину; требует jq)
./scripts/install-plugins.sh
# Скрипт допишет enabledPlugins в ~/.claude/settings.json (с бэкапом).
# Печатает один раз список marketplaces для регистрации через
# /plugin marketplace add внутри `claude` — это нужно сделать вручную
# один раз, потом плагины подтянутся сами на старте claude.

# 3. Запусти Claude и опиши идею
claude
> хочу маркетплейс лимитированных кроссовок с auth через Supabase и оплатой Stripe
```

Дальше Claude:
- Прочитает CLAUDE.md и поймёт правила работы.
- Если идея с UI — сначала набросает HTML-мокап через `frontend-design` / `gsd-sketch`, обсудит с тобой, и только потом полезет в бэк (см. CLAUDE.md §3).
- Если идея явно многофазная — предложит `gsd-new-project` (структура `.planning/PROJECT.md` + ROADMAP).
- Если фича на один заход — сразу через `superpowers:brainstorming` → `writing-plans` → `test-driven-development`.

Стек **не предписан**. Выбираешь сам или просишь Claude рекомендовать под задачу. Шаблон стек-нейтрален.

---

## Какие плагины и зачем

### Воркфлоу (ставятся всегда)

| Плагин | Когда сработает |
|---|---|
| **superpowers** | Дисциплина одной задачи: brainstorming, writing-plans, test-driven-development, systematic-debugging, verification-before-completion. |
| **gsd** | Опционально. Многофазные проекты с артефактами в `.planning/`. Если задача крупнее одного PR — берёшь GSD. См. [`docs/gsd-vs-superpowers.md`](docs/gsd-vs-superpowers.md). |
| **claude-mem** | Память между сессиями: `mem-search` («это уже решали?»). |
| **context7** | Свежие доки библиотек — когда упёрся в баг библиотеки, вместо галлюцинаций по памяти. |
| **context-mode** | Перехватывает большие выводы команд (`ctx_batch_execute`). Защищает контекстное окно. |
| **frontend-design** | Дизайн-система и UI-компоненты с осмысленной эстетикой. Используется в §3 для HTML-мокапов. |
| **reflexion** | Мульти-перспективная критика после крупной фичи. |
| **github** | `gh` операции из чата (PR, issues, branches). |

### Deploy и БД (ставятся всегда — под рукой при выборе стека)

| Плагин | Когда сработает |
|---|---|
| **vercel** | Vercel-семейство: AI SDK, deploy, env, next-upgrade, shadcn, performance. Дефолт для Next.js. |
| **railway** | Бэк + Postgres в один клик. Дефолт для full-stack MVP. |

### Опциональные — поставь когда реально понадобится

| Плагин | Когда брать |
|---|---|
| **supabase** | Если выбрал Supabase (Postgres + Auth) как БД-слой. |
| **neon** | Если хочешь serverless Postgres с ветками для preview-деплоев. |
| **prisma** | Если хочешь типизированный ORM поверх Postgres. |
| **auth0** | Если нужен managed auth (OAuth/SSO) без привязки к Supabase. |
| **ui-ux-pro-max** | Когда `frontend-design` не хватает — 67 стилей, 96 палитр, 57 пар шрифтов. |

Decision-tree по стекам — в [CLAUDE.md §10](./CLAUDE.md). Полный список и описание — `.claude-plugins.json`. Скрипт `./scripts/install-plugins.sh` ставит все required и печатает команды для optional.

---

## Что в CLAUDE.md (краткий обзор)

CLAUDE.md — главный артефакт шаблона. Базируется на [наблюдениях Karpathy](https://x.com/karpathy/status/2015883857489522876) о том, как LLM системно ошибаются:

> *«Модели делают неправильные допущения на твой счёт и просто идут с ними. Они не управляют своей confusion, не просят уточнений, не подсвечивают противоречия, не показывают tradeoffs, не пушат обратно когда должны. Они переусложняют код, раздувают абстракции, не чистят мёртвый код».*

Четыре принципа против этого:

1. **Думай до кода** — проговори допущения, предложи варианты, push back.
2. **Минимум кода** — никаких фич / абстракций / гибкости на будущее. Regex до LLM.
3. **Хирургические правки** — трогай только то, что должен; не «улучшай» рядом.
4. **Goal-driven исполнение** — формулируй задачи как success criteria, чтобы цикл крутился сам.

Плюс локальные практики: **UI-набросок до бэка** (§3), Docker, integration-тесты с реальной БД, research-first + stop-rule (три провала → стоп и читать доки), секреты только через `.env`, осознанный `/compact` на breakpoints, conventional commits.

Подробно — [CLAUDE.md](./CLAUDE.md).

---

## Структура шаблона

```
.
├── CLAUDE.md                          правила работы Claude Code (Karpathy + локальные практики)
├── README.md                          этот файл
├── .claude-plugins.json               список плагинов с описанием
├── scripts/
│   └── install-plugins.sh             jq-мерджит enabledPlugins в ~/.claude/settings.json
└── docs/
    ├── presentation.md                MARP-презентация о шаблоне
    ├── system-design-patterns.md      шпаргалка по Alex Xu (Vol. 1)
    └── gsd-vs-superpowers.md          как сосуществуют два плагина с пересекающейся функциональностью
```

Шаблон **не пишет код**. После описания идеи Claude сам создаёт:
- `.planning/` — если ты или Claude выбрали GSD (PROJECT.md, ROADMAP.md, phases/).
- `docs/superpowers/` — спеки и планы от superpowers.
- Код в выбранном стеке.

---

## Источники

- [Karpathy: random notes from claude coding (Jan 2026)](https://x.com/karpathy/status/2015883857489522876) — диагноз болезней LLM-кода.
- [forrestchang/andrej-karpathy-skills](https://github.com/forrestchang/andrej-karpathy-skills) — четыре принципа в одном CLAUDE.md.
- [affaan-m/everything-claude-code](https://github.com/affaan-m/everything-claude-code) — кит-синк референс по Claude Code (агенты, хуки, скиллы).

---

## Лицензия

MIT.
