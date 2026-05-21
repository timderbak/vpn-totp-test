---
marp: true
theme: gaia
paginate: true
backgroundColor: #fff
color: #1a1a1a
style: |
  /* Force every slide to a clean light theme — override gaia's lead/invert defaults */
  section, section.lead, section.invert {
    background: #ffffff !important;
    color: #1a1a1a !important;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
    font-size: 28px;
  }
  h1, h2 { color: #0EA5E9 !important; }
  h3, h4 { color: #1a1a1a; }
  section.lead h1 { font-size: 64px; color: #0EA5E9 !important; }
  section.lead h2 { font-weight: 400; color: #555 !important; }
  p, li, td, th { color: inherit; }

  /* Inline code in body — light grey block, dark text */
  code {
    background: #f3f4f6;
    color: #1a1a1a !important;
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: 500;
  }
  /* Inline code inside headings — softer blue block */
  h1 code, h2 code, h3 code {
    background: #e0f2fe;
    color: #0369a1 !important;
  }
  /* Inline code inside table cells — neutral grey on white td bg */
  td code { background: #f3f4f6; color: #1a1a1a !important; }
  /* Inline code inside the cyan <th> — keep readable: white bg + dark text */
  th code { background: #ffffff; color: #0369a1 !important; }

  /* Fenced code blocks — dark plate, light text */
  pre {
    background: #1a1a1a !important;
    color: #e5e5e5 !important;
    padding: 16px;
    border-radius: 8px;
    font-size: 22px;
  }
  pre code {
    background: transparent !important;
    color: #e5e5e5 !important;
    padding: 0;
    font-weight: normal;
  }

  blockquote {
    border-left: 4px solid #0EA5E9;
    padding-left: 20px;
    color: #555 !important;
    font-style: italic;
  }

  table { font-size: 22px; border-collapse: collapse; }
  th { background: #0EA5E9 !important; color: #ffffff !important; padding: 8px 12px; }
  td { padding: 8px 12px; border-bottom: 1px solid #e5e7eb; }

  a { color: #0EA5E9; }
  strong { font-weight: 700; }

  .small { font-size: 20px; }
  .tiny { font-size: 16px; color: #888 !important; }
---

<!-- _class: lead -->

# VibecoSwagaTemplate

## Шаблон для соло-вайбкодера

<br>

Все передовые практики Claude Code в одном `Use this template`.

<span class="tiny">2026 · MIT</span>

---

# Зачем этот шаблон существует

LLM-агенты системно ошибаются одинаково. Их **надо учить хорошим привычкам через `CLAUDE.md`** — иначе они:

- Делают допущения за тебя и идут с ними молча
- Не управляют своей confusion, не задают вопросов
- Переусложняют код и раздувают абстракции
- Не чистят за собой мёртвый код
- Правят рядом с задачей то, что трогать не просили

<br>

**Шаблон закладывает противоядие сразу.** Клонируешь → описываешь идею → дальше Claude уже знает как себя вести.

---

<!-- _class: lead -->

# Диагноз Karpathy (Jan 2026)

> *«Модели делают неправильные допущения на твой счёт и просто идут с ними. Они не управляют своей confusion, не просят уточнений, не подсвечивают противоречия, не показывают tradeoffs, не пушат обратно когда должны. Они переусложняют код, раздувают абстракции, не чистят мёртвый код».*

<span class="tiny">— Andrej Karpathy, [твит про Claude Code](https://x.com/karpathy/status/2015883857489522876)</span>

---

# Решение: 4 принципа в CLAUDE.md

| Принцип | Лечит |
|---|---|
| **1. Думай до кода** | Допущения, скрытая confusion, упущенные tradeoffs |
| **2. Минимум кода** | Переусложнение, раздутые абстракции |
| **3. Хирургические правки** | Орто-правки, трогает то, что не должен |
| **4. Goal-driven исполнение** | Слабые критерии → нужны постоянные уточнения |

<br>

Источник интерпретации: [forrestchang/andrej-karpathy-skills](https://github.com/forrestchang/andrej-karpathy-skills). Адаптировано на русский + добавлены локальные практики.

---

# 1. Думай до кода

**Не предполагай. Не прячь свою confusion. Поднимай tradeoffs.**

- Проговори допущения вслух. Не уверен — **спроси**.
- Несколько интерпретаций — **назови все**, не выбирай молча.
- Видишь более простой путь — **скажи**. Push back.
- Никаких `"of course!"` в ответ на критику. Прав → фикси. Не прав → аргументируй.

---

# 2. Минимум кода

**Только то, что решает задачу. Ничего на будущее.**

- Никаких фич сверх запрошенного.
- Никаких абстракций для одноразового кода.
- Никакой обработки невозможных ошибок.
- 200 строк, а можно 50? → **перепиши**.
- **Regex до LLM**: парсинг структурированного текста — regex, LLM только когда структура нерегулярная.

---

# 3. Хирургические правки

**Трогай только то, что должен. Чисти только за собой.**

- Не «улучшай» соседний код, комментарии, форматирование.
- Не рефактори то, что не сломано.
- Заметил несвязанный мёртвый код — **скажи**, не удаляй.
- Убирай только те хвосты, которые создали твои изменения.

<br>

**Тест:** каждая изменённая строка должна прямо трассироваться к запросу пользователя.

---

# 4. Goal-driven исполнение

**Определи критерий успеха. Крути цикл, пока не зелёный.**

- «Добавь валидацию» → «Тесты на невалидные входы, потом сделай их зелёными».
- «Почини баг» → «Тест воспроизводит, потом фикс, тест зелёный».
- «Зарефактори» → «Тесты зелёные до и после».

<br>

Сильные критерии = агент крутит цикл сам.
Слабые («сделай чтобы работало») = постоянные уточнения.

---

# Локальные практики (поверх Karpathy)

| Раздел | Суть |
|---|---|
| **§3 UI первой** | Любая фича/проект с UI начинается с HTML-мокапа через `frontend-design` или `gsd-sketch`. **Потом** бэк. |
| **§4 Docker** | Тесты, запуски — только в контейнерах. Никакого "у меня локально работает". |
| **§5 Тесты** | Integration с реальной БД. Без mock. TDD: RED → GREEN → REFACTOR → COMMIT. |
| **§6 Research-first + Stop rule** | 5 минут grep / mem-search / context7 перед сложным. Три провала → стоп. |
| **§7 Секреты** | Через `.env` + `.env.example`. Никакого `--no-verify`. |
| **§8 Контекст** | Большой вывод → `context-mode`. `/compact` по breakpoints, не по 95%. |

---

# §3: UI — сначала набросок, потом код

**90% переделок бэка случаются потому, что UI оказался не таким.**

```
Старт фичи с экраном:
  1. frontend-design / gsd-sketch
     → HTML-мокап (throwaway, 30 мин)
  2. Обсуждение с пользователем
     → "да, такое" / "нет, переделай"
  3. Контракт API под этот UI
  4. Имплементация (бэк + фронт)
```

Дешевле перерисовать мокап, чем мигрировать схему.

---

# Стек плагинов: воркфлоу

| Плагин | Когда сработает |
|---|---|
| **superpowers** | Дисциплина задачи: brainstorm, plans, TDD, debugging |
| **gsd** _(opt)_ | Многофазные проекты, артефакты в `.planning/` |
| **claude-mem** | Память между сессиями: `mem-search` |
| **context7** | Свежие доки библиотек, не галлюцинации |
| **context-mode** | Защита контекстного окна от больших выводов |
| **frontend-design** | Дизайн-система + HTML-мокапы для §3 |
| **reflexion** | Мульти-перспективная критика после крупной фичи |
| **github** | `gh` операции из чата |

---

# Стек плагинов: Deploy и БД

**Always-on (под рукой при выборе стека):**

| Плагин | Зачем |
|---|---|
| **vercel** | Vercel deploy + AI SDK + Next.js + shadcn |
| **railway** | Бэк + Postgres в один клик (full-stack MVP) |

**Opt-in (ставишь когда понадобится):**

| Плагин | Зачем |
|---|---|
| **supabase** | Managed Postgres + Auth + Storage |
| **neon** | Serverless Postgres с ветками |
| **prisma** | Типизированный ORM поверх Postgres |
| **auth0** | Managed auth (если не Supabase) |

Decision-tree по стекам — в CLAUDE.md §10.

---

<!-- _class: lead -->

# Quick start

```bash
# 1. Use this template на GitHub → clone
git clone git@github.com:<you>/<project>.git
cd <project>

# 2. Один раз: включи плагины глобально
./scripts/install-plugins.sh

# 3. Запусти Claude и опиши идею
claude
> хочу маркетплейс кроссовок с auth и Stripe
```

---

# Что делает `install-plugins.sh`

Нативный путь — никаких фейков:

1. **Бэкап** `~/.claude/settings.json` (timestamp в имени).
2. **`jq`-merge** `enabledPlugins` map: `{name@marketplace: true}`.
3. Выводит список marketplaces для **одноразовой** ручной регистрации внутри `claude` (`/plugin marketplace add thedotmack/claude-mem` и т.п.).

<br>

Claude Code на старте сам подтянет плагины и держит их обновлёнными.
Никаких `npm install` per-project, никаких per-repo плагинов — всё глобально.

---

# Структура шаблона

```
.
├── CLAUDE.md                       правила Claude Code
│                                   (Karpathy + локальные практики)
├── README.md                       старт
├── .claude-plugins.json            список плагинов с описанием
├── scripts/
│   └── install-plugins.sh          jq-мерджит enabledPlugins
│                                   в ~/.claude/settings.json
└── docs/
    ├── presentation.md             эта презентация
    ├── system-design-patterns.md   шпаргалка Alex Xu
    └── gsd-vs-superpowers.md       GSD ↔ superpowers
```

Никакого backend/frontend кода. Стек выбираешь сам или с Claude.

---

# Источники

- **[karpathy/status/2015883857489522876](https://x.com/karpathy/status/2015883857489522876)** — диагноз болезней LLM-кода
- **[forrestchang/andrej-karpathy-skills](https://github.com/forrestchang/andrej-karpathy-skills)** — 4 принципа в одном CLAUDE.md
- **[affaan-m/everything-claude-code](https://github.com/affaan-m/everything-claude-code)** — донор для research-first, regex-vs-LLM, strategic /compact
- **superpowers** + **gsd** + **claude-mem** — workflow plugins
- **Alex Xu, System Design Interview Vol. 1** — шпаргалка в `docs/`

---

<!-- _class: lead -->

# Поехали

```bash
git clone git@github.com:<you>/<project>.git
cd <project>
./scripts/install-plugins.sh
claude
```

<br>

<span class="tiny">github.com/timderbak/VibecoSwagaPlatform · MIT</span>
