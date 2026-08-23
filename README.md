# hexlet-task-manager-infra

Платформенный репозиторий: composite action для ИИ-ревью пул-реквестов и
сервис семантического поиска по продуктовой документации. Используется
демо-репозиторием `hexlet-task-manager`, сам по себе не запускается
целиком — два независимых куска, каждый со своим запуском.

## review-action/

Composite GitHub Action, агентный цикл ИИ-ревью PR. Не запускается
локально сам по себе — подключается из workflow вызывающего репозитория:

```yaml
- uses: Obolduy/hexlet-task-manager-infra/review-action@main
  with:
    api-key: ${{ secrets.OPENROUTER_API_KEY }}
    search-url: http://localhost:8002   # или пусто, если search_docs не нужен
    model: anthropic/claude-sonnet-4.5
```

Требования к вызывающему workflow — `permissions: contents: read,
pull-requests: write` и `actions/checkout@v4` с `fetch-depth: 0` (иначе
`git diff` между базовой веткой и веткой PR не работает).

Локальная отладка промпта — напрямую через `review.py`:

```bash
python3 -m venv review-action/.venv
review-action/.venv/bin/pip install -r review-action/requirements.txt
cp review-action/.env.example review-action/.env   # вписать свой OPENROUTER_API_KEY
```

Дальше `review.py` запускается с переменными окружения `REPO_PATH`,
`BASE_SHA`, `HEAD_SHA` (диапазон коммитов для `git diff`), `GITHUB_TOKEN`,
`GITHUB_REPOSITORY`, `PR_NUMBER` — в реальном PR их подставляет
`action.yml`, для локального прогона нужно выставить самому.

`SINGLE_SHOT=1` отключает агентный цикл и `search_docs` — один запрос без
инструментов, страховка на случай проблем с моделью или инструментами.

## knowledge-service/

`/search` — принимает текст, считает эмбеддинг, ищет ближайшие чанки в
Postgres (pgvector). `mcp_server.py` — MCP-обёртка над тем же
`/search` для локального агента разработчика.

### Первый запуск

1. Поднять Postgres с pgvector и накатить схему — `schema.sql`.
2. Скопировать `.env.example` в `.env`, вписать `DATABASE_URL`.
3. Пересоздать индекс из `sources/` (нужен локальный Python, не докер —
   индексатор коннектится к базе напрямую):

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r knowledge-service/requirements.txt
   cd knowledge-service && ../.venv/bin/python indexer.py
   ```

   Команда полностью пересоздаёт `documents`/`chunks` из файлов в
   `sources/` — гонять заново при любом изменении исходников.
4. Поднять сервисы:

   ```bash
   cd knowledge-service && docker compose up --build
   ```

   `search` слушает `:8002` на хосте (внутри compose-сети — `:8000`, порт
   поменяли, чтобы не конфликтовать с `api` из `hexlet-task-manager`,
   который тоже просит `:8000`), `mcp` — `:8001` (streamable-http).
5. Проверить:

   ```bash
   curl -X POST http://localhost:8002/search \
     -H 'Content-Type: application/json' \
     -d '{"query": "можно ли закрыть задачу без ревью"}'
   ```