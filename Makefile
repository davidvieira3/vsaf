.PHONY: keys cert migrations build up down logs ps

keys:  ## Génère DJANGO_SECRET_KEY et APP_ENCRYPTION_KEY à coller dans .env
	@python3 -c "import secrets,base64,os;print('DJANGO_SECRET_KEY='+secrets.token_urlsafe(50));print('APP_ENCRYPTION_KEY='+base64.urlsafe_b64encode(os.urandom(32)).decode())"

cert:  ## Certificat auto-signé de démarrage (à remplacer par un certificat de votre PKI)
	openssl req -x509 -newkey rsa:3072 -nodes -days 825 -subj "/CN=$${CN:-inventaire.local}" \
	  -keyout nginx/certs/server.key -out nginx/certs/server.crt

migrations:  ## Génère les migrations Django dans le dépôt (à commiter)
	docker compose build web
	docker compose run --rm --no-deps --user "$$(id -u):$$(id -g)" -v "$$PWD:/app" web makemigrations

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100 web worker beat

ps:
	docker compose ps
