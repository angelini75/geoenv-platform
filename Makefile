# GeoEnv Platform — VM deploy helpers
# Usage: run these commands FROM THE VM (ssh angel@<VM-IP>), not locally.
# Local: make push  →  git push origin main (then SSH and run make deploy)

VM_DIR  = /opt/mi-stack/geoenv
COMPOSE = sudo docker compose -f $(VM_DIR)/docker-compose.yml

# ── Local commands (run on dev machine) ────────────────────────────────────────

push:
	git push origin main

# ── VM commands (run after SSH into the VM) ────────────────────────────────────

deploy:
	@echo "==> Pulling latest code..."
	sudo git -C $(VM_DIR) pull origin main
	@echo "==> Rebuilding containers..."
	$(COMPOSE) build --no-cache backend
	$(COMPOSE) up -d
	@echo "==> Done. Checking health..."
	sleep 5
	curl -s https://indicadores.soildecisions.com/health | python3 -m json.tool

restart:
	$(COMPOSE) restart backend
	sleep 3
	curl -s https://indicadores.soildecisions.com/health | python3 -m json.tool

logs:
	$(COMPOSE) logs -f --tail=100 backend

# ── Pre-flight checks (run before first deploy on a new VM) ───────────────────

doctor:
	@echo "=== GeoEnv Platform — Doctor ==="
	@printf "GEMINI_API_KEY ... "
	@test -f /opt/mi-stack/.env && grep -q "GEMINI_API_KEY=." /opt/mi-stack/.env \
		&& echo "✓ set" || echo "✗ MISSING — add to /opt/mi-stack/.env"
	@printf ".env symlink    ... "
	@test -L $(VM_DIR)/.env && echo "✓ OK" \
		|| echo "✗ MISSING — run: sudo ln -sf /opt/mi-stack/.env $(VM_DIR)/.env"
	@printf "GEE credentials ... "
	@test -f /opt/mi-stack/secrets/credentials.json && echo "✓ OK" \
		|| echo "✗ MISSING — copy credentials.json to /opt/mi-stack/secrets/"
	@printf "Docker running  ... "
	@sudo docker compose -f $(VM_DIR)/docker-compose.yml ps --quiet backend | grep -q . \
		&& echo "✓ running" || echo "✗ NOT running — run: make deploy"
	@printf "Health endpoint ... "
	@curl -sf https://indicadores.soildecisions.com/health | python3 -m json.tool \
		|| echo "✗ unreachable"
	@echo "==================================="

setup-symlink:
	sudo ln -sf /opt/mi-stack/.env $(VM_DIR)/.env
	@echo "✓ Symlink created: $(VM_DIR)/.env → /opt/mi-stack/.env"

setup-git:
	git config --global --add safe.directory $(VM_DIR)
	@echo "✓ Git safe.directory configured"

.PHONY: push deploy restart logs doctor setup-symlink setup-git
