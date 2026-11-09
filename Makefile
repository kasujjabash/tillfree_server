.PHONY: install db-roles migrate run worker test lint format typecheck imports check schema ci up down

UV ?= uv
DB_SUPERUSER ?= $(USER)

install:            ## Install all dependencies into .venv
	$(UV) sync

db-roles:           ## Create local database + roles (needs a PostgreSQL superuser)
	psql -d postgres -U $(DB_SUPERUSER) -v ON_ERROR_STOP=1 -v db_name=tillfree \
	  -v owner_password=tillfree_owner_dev -v app_password=tillfree_app_dev \
	  -f infrastructure/db/create_roles.sql

migrate:
	$(UV) run python manage.py migrate

run:
	$(UV) run python manage.py runserver

worker:
	DJANGO_SETTINGS_MODULE=$${DJANGO_SETTINGS_MODULE:-config.settings.development} \
	  $(UV) run celery -A config worker --loglevel=INFO

test:
	$(UV) run pytest --cov

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

typecheck:
	$(UV) run mypy .

imports:            ## Domain-boundary contracts
	$(UV) run lint-imports

check:              ## Pending-migration check
	DJANGO_SETTINGS_MODULE=config.settings.testing $(UV) run python manage.py makemigrations --check --dry-run

schema:             ## OpenAPI schema must generate without warnings
	DJANGO_SETTINGS_MODULE=config.settings.testing $(UV) run python manage.py spectacular --validate --fail-on-warn --file /dev/null

ci: lint typecheck imports check schema test

up:
	docker compose up --build

down:
	docker compose down
