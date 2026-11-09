.PHONY: install db-roles migrate run worker test lint format typecheck imports check ci up down

UV ?= uv
DB_SUPERUSER ?= $(USER)

install:            ## Install all dependencies into .venv
	$(UV) sync

db-roles:           ## Create local database + roles (needs a PostgreSQL superuser)
	psql -d postgres -U $(DB_SUPERUSER) -v ON_ERROR_STOP=1 -v db_name=tilltree \
	  -v owner_password=tilltree_owner_dev -v app_password=tilltree_app_dev \
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

ci: lint typecheck imports check test

up:
	docker compose up --build

down:
	docker compose down
