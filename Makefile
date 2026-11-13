.PHONY: help up down build logs ps test lint clean

help:
	@echo "Gemuse - Available commands:"
	@echo "  make up       - Start all services in detached mode"
	@echo "  make down     - Stop all services"
	@echo "  make build    - Build Docker images"
	@echo "  make logs     - Tail service logs"
	@echo "  make ps       - Show container status"
	@echo "  make test     - Run backend pytest suite locally"
	@echo "  make clean    - Stop containers and remove volumes (CAUTION)"

up:
	docker compose up -d --build

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

test:
	cd backend && pytest -v

clean:
	docker compose down -v
