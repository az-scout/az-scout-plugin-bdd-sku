# az-scout-plugin-bdd-sku

Plugin [az-scout](https://github.com/lrivallain/az-scout) qui met en cache les **prix retail des VM Azure**, les **taux d'éviction Spot** et l'**historique des prix Spot** dans une base PostgreSQL locale. Permet des requêtes rapides et hors-ligne sans appeler les APIs Azure à chaque fois.

## Architecture

```
┌────────────────────┐         ┌───────────────────────────┐         ┌──────────────┐
│     az-scout       │  GET    │   Plugin routes            │  async  │  PostgreSQL  │
│  (FastAPI :5001)   │ ──────▸ │  /plugins/bdd-sku/        │ ──────▸ │   (:5432)    │
│                    │         │    /status                 │         │              │
│  MCP server        │         │    /spot/eviction-rates    │         │ retail_prices│
│  4 outils exposés  │         │    /spot/eviction-rates/   │         │ spot_evict.  │
│                    │         │         history            │         │ spot_price_h.│
│                    │         │    /spot/price-history     │         │ job_runs     │
└────────────────────┘         └───────────────────────────┘         │ job_logs     │
                                                                     └──────┬───────┘
┌────────────────────┐                                                      │
│  Ingestion Jobs    │  INSERT (psycopg2)                                   │
│  (Container Apps)  │ ────────────────────────────────────────────────────┘
│                    │  ◂── Azure Retail Prices API (public, no auth)
│  3 jobs :          │  ◂── Azure Resource Graph API (SpotResources)
│  - daily (02:00)   │
│  - hourly (evict.) │
│  - manual          │
└────────────────────┘
```

**Deux composants indépendants :**

| Composant | Rôle | Technologie |
|---|---|---|
| **Plugin** (`src/az_scout_bdd_sku/`) | Tab UI + routes API + outils MCP, lecture seule depuis Postgres | FastAPI, psycopg (async), psycopg_pool |
| **Ingestion** (`ingestion/`) | Jobs CLI one-shot qui collectent les prix et les insèrent dans Postgres | requests, psycopg2-binary, azure-identity |

---

## Endpoints API

Tous les endpoints sont montés sous `/plugins/bdd-sku/` par az-scout.

### `GET /plugins/bdd-sku/status`

Statut global de la base de données : connectivité, comptage des tables et dernier run d'ingestion.

**Réponse :**

```json
{
  "db_connected": true,
  "retail_prices_count": 42000,
  "spot_eviction_rates_count": 11237,
  "spot_price_history_count": 243335,
  "last_run": {
    "run_id": "a1b2c3...",
    "status": "ok",
    "started_at_utc": "2026-01-15T10:00:00+00:00",
    "finished_at_utc": "2026-01-15T10:05:00+00:00",
    "items_read": 45000,
    "items_written": 42000,
    "error_message": null
  }
}
```

---

### `GET /plugins/bdd-sku/spot/eviction-rates`

Taux d'éviction Spot VM. Sans `job_id`, retourne uniquement le **dernier snapshot** (le plus récent `job_datetime`).

**Paramètres query :**

| Paramètre | Type | Requis | Description |
|---|---|---|---|
| `region` | string | Non | Filtre exact par région Azure (ex : `eastus`) |
| `sku_name` | string | Non | Filtre substring insensible à la casse (ex : `D2s` → `Standard_D2s_v3`) |
| `job_id` | string | Non | UUID du snapshot spécifique. Si omis, retourne le dernier |
| `limit` | int | Non | Nombre max de lignes (défaut : 200, min : 1, max : 5000) |

**Exemple :**

```bash
# Dernier snapshot, filtre sur eastus
curl "http://localhost:5001/plugins/bdd-sku/spot/eviction-rates?region=eastus"

# Snapshot spécifique
curl "http://localhost:5001/plugins/bdd-sku/spot/eviction-rates?job_id=abc-123"
```

**Réponse :**

```json
{
  "count": 150,
  "items": [
    {
      "sku_name": "Standard_D2s_v3",
      "region": "eastus",
      "eviction_rate": "5-10%",
      "job_id": "abc-123",
      "job_datetime": "2026-03-02T14:00:00+00:00"
    }
  ]
}
```

---

### `GET /plugins/bdd-sku/spot/eviction-rates/history`

Liste les **snapshots disponibles** des taux d'éviction. Chaque snapshot correspond à une exécution du collecteur (un `job_id` unique).

**Paramètres query :**

| Paramètre | Type | Requis | Description |
|---|---|---|---|
| `limit` | int | Non | Nombre max de snapshots (défaut : 50, min : 1, max : 500) |

**Exemple :**

```bash
curl "http://localhost:5001/plugins/bdd-sku/spot/eviction-rates/history"
```

**Réponse :**

```json
{
  "count": 24,
  "snapshots": [
    {
      "job_id": "abc-123",
      "job_datetime": "2026-03-02T14:00:00+00:00",
      "row_count": 11237
    },
    {
      "job_id": "def-456",
      "job_datetime": "2026-03-02T13:00:00+00:00",
      "row_count": 11235
    }
  ]
}
```

---

### `GET /plugins/bdd-sku/spot/price-history`

Historique des prix Spot VM (tableau de prix par SKU×région×OS).

**Paramètres query :**

| Paramètre | Type | Requis | Description |
|---|---|---|---|
| `region` | string | Non | Filtre exact par région Azure |
| `sku_name` | string | Non | Filtre substring insensible à la casse |
| `os_type` | string | Non | Filtre par OS (`Linux` ou `Windows`) |
| `limit` | int | Non | Nombre max de lignes (défaut : 200, min : 1, max : 5000) |

**Exemple :**

```bash
curl "http://localhost:5001/plugins/bdd-sku/spot/price-history?region=westeurope&os_type=Linux&sku_name=D4s"
```

**Réponse :**

```json
{
  "count": 5,
  "items": [
    {
      "sku_name": "Standard_D4s_v3",
      "os_type": "Linux",
      "region": "westeurope",
      "price_history": [
        {"timestamp": "2026-03-01T00:00:00Z", "spotPrice": 0.042},
        {"timestamp": "2026-02-28T00:00:00Z", "spotPrice": 0.045}
      ]
    }
  ]
}
```

---

## Outils MCP

Le plugin expose 4 outils sur le serveur MCP d'az-scout, utilisables par les LLMs dans le chat intégré.

| Outil | Paramètres | Description |
|---|---|---|
| `cache_status` | *(aucun)* | Statut de la base : connectivité, comptage par table, dernier run |
| `get_spot_eviction_rates` | `region?`, `sku_name?`, `job_id?` | Taux d'éviction Spot. Sans `job_id` → dernier snapshot |
| `get_spot_eviction_history` | *(aucun)* | Liste les snapshots d'éviction disponibles (50 derniers) |
| `get_spot_price_history` | `region?`, `sku_name?`, `os_type?` | Historique des prix Spot par SKU×région×OS |
| `v1_status` | *(aucun)* | Statut v1 : santé DB, stats par dataset |
| `v1_list_locations` | `limit?`, `cursor?` | Lister les régions Azure (paginé) |
| `v1_list_skus` | `search?`, `limit?`, `cursor?` | Lister les SKUs VM (paginé) |
| `v1_retail_prices` | `region?`, `sku?`, `currency?`, `limit?`, `cursor?` | Prix retail VM (paginé) |
| `v1_eviction_rates` | `region?`, `sku?`, `limit?`, `cursor?` | Taux d'éviction Spot (paginé) |
| `v1_eviction_rates_latest` | `region?`, `sku?`, `limit?` | Dernier taux d'éviction par (region, sku) |

---

## API v1 — Read-only (cursor-paginated)

L'API v1 est montée sous `/plugins/bdd-sku/v1/` et offre un accès en lecture seule
aux données PostgreSQL via une pagination par curseur (keyset) haute performance.

**Base path :** `/plugins/bdd-sku/v1`

### Pagination

Tous les endpoints paginés utilisent la **pagination keyset** :
- `limit` : nombre d'items par page (1–5000, défaut 1000)
- `cursor` : token opaque (base64url) renvoyé dans la réponse `page.cursor`
- `page.hasMore` : `true` si une page suivante existe

Pour parcourir toutes les pages :
```bash
# Page 1
curl ".../v1/locations?limit=100"
# → page.cursor = "eyJuYW1lIjoi..."
# Page 2
curl ".../v1/locations?limit=100&cursor=eyJuYW1lIjoi..."
```

### Contrat JSON

Toute réponse 2xx suit la structure `ListResponse<T>` :

```json
{
  "items": [...],
  "page": { "limit": 1000, "cursor": "...", "hasMore": true },
  "meta": { "dataSource": "local-db", "generatedAt": "2026-03-02T..." }
}
```

Les erreurs suivent `ErrorResponse` :

```json
{
  "error": { "code": "BAD_REQUEST", "message": "..." },
  "meta": { "dataSource": "local-db", "generatedAt": "..." }
}
```

### Endpoints v1

| # | Endpoint | Méthode | Paginé | Description |
|---|---|---|---|---|
| 1 | `/v1/status` | GET | Non | Santé DB, stats par dataset, version API |
| 2 | `/v1/locations` | GET | Oui | Noms de régions distincts (union 3 tables) |
| 3 | `/v1/skus` | GET | Oui | Noms de SKUs distincts (filtre `search` ILIKE) |
| 4 | `/v1/currencies` | GET | Oui | Codes devise distincts (retail) |
| 5 | `/v1/os-types` | GET | Oui | Types OS distincts (spot) |
| 6 | `/v1/retail/prices` | GET | Oui | Prix retail avec filtres (region, sku, currency, effectiveAt, updatedSince) |
| 7 | `/v1/retail/prices/latest` | GET | Oui | Dernier snapshot retail par clé unique |
| 8 | `/v1/spot/prices` | GET | Oui | Historique prix Spot (sample=raw uniquement, sinon 501) |
| 9 | `/v1/spot/eviction-rates` | GET | Oui | Taux d'éviction Spot (filtres region, sku, updatedSince) |
| 10 | `/v1/spot/eviction-rates/series` | GET | Non | Série temporelle agrégée (bucket=hour\|day\|week, agg=avg\|min\|max) |
| 11 | `/v1/spot/eviction-rates/latest` | GET | Non | Dernier taux par (region, sku) |

### Exemples curl

```bash
# Status
curl "http://localhost:5001/plugins/bdd-sku/v1/status"

# Prix retail (100 premiers en USD, region eastus)
curl "http://localhost:5001/plugins/bdd-sku/v1/retail/prices?region=eastus&currency=USD&limit=100"

# Taux d'éviction mis à jour depuis 24h
curl "http://localhost:5001/plugins/bdd-sku/v1/spot/eviction-rates?updatedSince=2026-03-01T00:00:00Z&limit=500"

# Série éviction horaire pour un SKU
curl "http://localhost:5001/plugins/bdd-sku/v1/spot/eviction-rates/series?region=eastus&sku=Standard_D2s_v3&bucket=hour"

# Prix Spot (raw)
curl "http://localhost:5001/plugins/bdd-sku/v1/spot/prices?region=westeurope&sku=Standard_D4s_v3"
```

> **Note :** `spot/prices` renvoie `price_history` tel quel (JSONB). Les paramètres `from`/`to` filtrent sur `job_datetime` (snapshot). Le mode `sample=hourly|daily` n'est pas encore implémenté (renvoie 501).

### Spécification OpenAPI

La spécification complète se trouve dans [`openapi/v1.yaml`](openapi/v1.yaml) (OpenAPI 3.0.3).

---

## Prérequis

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (gestionnaire de paquets)
- Docker & Docker Compose
- [az-scout](https://github.com/lrivallain/az-scout) installé (`uv pip install az-scout` ou en mode dev)

---

## Lancement local

### 1. Démarrer PostgreSQL

```bash
cd postgresql/
docker compose up -d
```

Cela démarre un conteneur PostgreSQL 17 avec :
- **Base** : `azscout`, **user** : `azscout`, **password** : `azscout`
- **Port** : `localhost:5432`
- Le schéma (`sql/schema.sql`) est appliqué automatiquement au premier démarrage

Vérifier que Postgres est prêt :

```bash
docker compose ps           # State: running (healthy)
docker compose logs postgres  # ... database system is ready to accept connections
```

### 2. Installer le plugin en mode développement

```bash
# Depuis la racine du repo
uv pip install -e ".[dev]"
```

Le plugin est découvert automatiquement par az-scout grâce à l'entry point `az_scout.plugins` dans `pyproject.toml`.

### 3. (Optionnel) Configurer la connexion PostgreSQL

Par défaut, le plugin se connecte à `localhost:5432/azscout` (user/pass: `azscout`). Pour personnaliser :

Créer `~/.config/az-scout/bdd-sku.toml` :

```toml
[database]
host = "localhost"
port = 5432
dbname = "azscout"
user = "azscout"
password = "azscout"
sslmode = "disable"
```

Ou pointer vers un fichier custom :

```bash
export AZ_SCOUT_BDD_SKU_CONFIG=/chemin/vers/mon/config.toml
```

### 4. Lancer az-scout

```bash
uv run az-scout web --host 0.0.0.0 --port 5001 --reload --no-open -v
```

Ouvrir http://localhost:5001 — l'onglet **SKU DB Cache** apparaît dans la barre de navigation.

### 5. Alimenter la base (ingestion)

L'ingestion est un **job Docker one-shot** séparé :

```bash
cd ingestion/

# Construire l'image
docker build -t bdd-sku-ingestion .

# Lancer l'ingestion
docker run --rm \
  --network postgresql_default \
  -e POSTGRES_HOST=postgres \
  -e ENABLE_AZURE_PRICING_COLLECTOR=true \
  bdd-sku-ingestion
```

> **Note :** `--network postgresql_default` permet au container d'atteindre le Postgres du `docker compose`. Le nom du réseau peut varier — vérifier avec `docker network ls`.

#### Variables d'environnement de l'ingestion

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_HOST` | `localhost` | Hôte PostgreSQL |
| `POSTGRES_PORT` | `5432` | Port PostgreSQL |
| `POSTGRES_DB` | `azscout` | Nom de la base |
| `POSTGRES_USER` | `azscout` | Utilisateur |
| `POSTGRES_PASSWORD` | `azscout` | Mot de passe |
| `POSTGRES_SSLMODE` | `disable` | Mode SSL (`disable`, `require`, `verify-full`) |
| `ENABLE_AZURE_PRICING_COLLECTOR` | `false` | Activer le collecteur de prix retail |
| `AZURE_PRICING_MAX_ITEMS` | `-1` | Limite d'items pricing (-1 = illimité) |
| `AZURE_PRICING_API_RETRY_ATTEMPTS` | `3` | Nombre de tentatives en cas d'erreur API pricing |
| `AZURE_PRICING_API_RETRY_DELAY` | `2.0` | Délai entre retries pricing (secondes) |
| `AZURE_PRICING_FILTERS` | `{}` | Filtres OData JSON (ex: `{"serviceName": "Virtual Machines"}`) |
| `ENABLE_AZURE_SPOT_COLLECTOR` | `false` | Activer le collecteur Spot (éviction + prix) |
| `AZURE_SPOT_EVICTION_ONLY` | `false` | Mode éviction uniquement (skip l'historique des prix Spot) |
| `AZURE_SPOT_MAX_ITEMS` | `-1` | Limite d'items spot (-1 = illimité) |
| `AZURE_SPOT_API_RETRY_ATTEMPTS` | `3` | Nombre de tentatives en cas d'erreur API spot |
| `AZURE_SPOT_API_RETRY_DELAY` | `2.0` | Délai entre retries spot (secondes) |
| `LOG_LEVEL` | `INFO` | Niveau de log (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `JOB_TYPE` | `manual` | Type de job (metadata) |

#### Exemple avec filtres

```bash
# Collecte pricing avec filtres
docker run --rm \
  --network postgresql_default \
  -e POSTGRES_HOST=postgres \
  -e ENABLE_AZURE_PRICING_COLLECTOR=true \
  -e 'AZURE_PRICING_FILTERS={"serviceName": "Virtual Machines"}' \
  -e AZURE_PRICING_MAX_ITEMS=1000 \
  -e LOG_LEVEL=DEBUG \
  bdd-sku-ingestion

# Collecte spot (éviction + prix) — nécessite des credentials Azure
docker run --rm \
  --network postgresql_default \
  -e POSTGRES_HOST=postgres \
  -e ENABLE_AZURE_SPOT_COLLECTOR=true \
  bdd-sku-ingestion

# Collecte éviction uniquement (mode historisation horaire)
docker run --rm \
  --network postgresql_default \
  -e POSTGRES_HOST=postgres \
  -e ENABLE_AZURE_SPOT_COLLECTOR=true \
  -e AZURE_SPOT_EVICTION_ONLY=true \
  bdd-sku-ingestion
```

### 6. Vérifier le statut

- **UI** : Onglet SKU DB Cache → cliquer "Refresh Status"
- **API** : `curl http://localhost:5001/plugins/bdd-sku/status`
- **MCP** : L'outil `cache_status` est exposé automatiquement sur le serveur MCP

La réponse `/status` :

```json
{
  "db_connected": true,
  "retail_prices_count": 42000,
  "spot_eviction_rates_count": 11237,
  "spot_price_history_count": 243335,
  "last_run": {
    "run_id": "a1b2c3...",
    "status": "ok",
    "started_at_utc": "2026-01-15T10:00:00+00:00",
    "finished_at_utc": "2026-01-15T10:05:00+00:00",
    "items_read": 45000,
    "items_written": 42000,
    "error_message": null
  }
}
```

---

## Déploiement sur Azure (Terraform)

L'infrastructure Azure est définie dans le dossier `infra/` et se déploie avec Terraform. Elle crée :

- **Azure Database for PostgreSQL – Flexible Server** (Burstable, PostgreSQL 17)
- **Azure Container Registry** (Basic) pour stocker l'image d'ingestion
- **Azure Container Apps Environment** avec Log Analytics
- **3 Container Apps Jobs** :
  - `{prefix}-sched` — cron quotidien (02:00 UTC) : collecte complète (pricing + spot)
  - `{prefix}-spot-evict` — cron horaire : éviction uniquement (historisation)
  - `{prefix}-manual` — déclenchement manuel à la demande

### Jobs d'ingestion

| Job | Cron | Collecteurs | CPU/Mém | Timeout |
|---|---|---|---|---|
| **Scheduled** (`-sched`) | `0 2 * * *` (02:00 UTC) | pricing + spot (éviction + prix) | 1 CPU / 2 Gi | 3600s |
| **Spot Eviction Hourly** (`-spot-evict`) | `0 * * * *` (chaque heure) | spot éviction uniquement | 0.5 CPU / 1 Gi | 900s |
| **Manual** (`-manual`) | On-demand | pricing + spot | 1 CPU / 2 Gi | 21600s |

Le job horaire (`spot-evict`) lance le collecteur Spot avec `AZURE_SPOT_EVICTION_ONLY=true`, ce qui ne collecte que les ~11 000 taux d'éviction sans requêter l'historique des prix (~243 000 lignes). Chaque exécution crée un nouveau snapshot dans `spot_eviction_rates` (identifié par `job_id`), permettant de suivre l'évolution des taux d'éviction dans le temps.

### 1. Prérequis

- [Terraform](https://developer.hashicorp.com/terraform/install) ≥ 1.5
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) connecté (`az login`)
- Un abonnement Azure avec les droits suffisants

### 2. Configuration

```bash
cd infra/

# Copier le fichier d'exemple et remplir vos valeurs
cp terraform.tfvars.example terraform.tfvars
```

Éditer `terraform.tfvars` — au minimum renseigner :

| Variable | Description |
|---|---|
| `subscription_id` | ID de votre abonnement Azure |
| `postgres_admin_password` | Mot de passe fort pour le serveur PostgreSQL |

### 3. Déployer

```bash
terraform init
terraform plan        # Vérifier les ressources qui seront créées
terraform apply       # Confirmer avec 'yes'
```

### 4. Construire et pousser l'image d'ingestion

Après le déploiement, construire l'image dans l'ACR :

```bash
# Récupérer le nom du registre
ACR_NAME=$(terraform output -raw container_registry_login_server)

# Construire directement dans l'ACR
az acr build --registry ${ACR_NAME%%.*} --image bdd-sku-ingestion:latest ../ingestion/
```

### 5. Appliquer le schéma PostgreSQL

```bash
PG_FQDN=$(terraform output -raw postgresql_fqdn)

psql "host=$PG_FQDN port=5432 dbname=azscout user=azscout sslmode=require" \
  -f ../sql/schema.sql
```

### 6. Déclencher un job manuel (optionnel)

```bash
az containerapp job start \
  --resource-group $(terraform output -raw resource_group_name) \
  --name $(terraform output -raw pricing_manual_job_name)
```

### 7. Détruire l'infrastructure

```bash
terraform destroy     # Confirmer avec 'yes'
```

> **Note :** `terraform.tfvars` contient des secrets (mot de passe PostgreSQL). Ce fichier est ignoré par `.gitignore` et ne doit **jamais** être committé.

---

## Intégration du plugin dans az-scout

### Installation depuis PyPI (ou un registre privé)

```bash
uv pip install az-scout-bdd-sku
```

### Installation depuis Git

```bash
uv pip install "az-scout-bdd-sku @ git+https://github.com/rsabile/az-scout-plugin-bdd-sku.git"
```

### Installation en mode développement

```bash
git clone https://github.com/rsabile/az-scout-plugin-bdd-sku.git
cd az-scout-plugin-bdd-sku
uv pip install -e ".[dev]"
```

### Via le gestionnaire de plugins az-scout

Dans l'interface az-scout : **Settings → Plugins → Install** et renseigner l'URL Git du repo.

### Vérifier que le plugin est chargé

Au démarrage d'az-scout, les logs affichent :

```
INFO: Discovered plugin: bdd-sku v0.1.0
INFO: Mounted plugin routes at /plugins/bdd-sku/
INFO: Registered MCP tools: cache_status, get_spot_eviction_rates, get_spot_eviction_history, get_spot_price_history
INFO: Loaded plugin tab: SKU DB Cache
```

Le plugin ajoute :

| Élément | Description |
|---|---|
| **Onglet UI** `SKU DB Cache` | Affiche les comptages (prix, éviction, historique) et le dernier run |
| **4 routes API** | `/status`, `/spot/eviction-rates`, `/spot/eviction-rates/history`, `/spot/price-history` |
| **4 outils MCP** | `cache_status`, `get_spot_eviction_rates`, `get_spot_eviction_history`, `get_spot_price_history` |

---

## Structure du projet

```
az-scout-plugin-bdd-sku/
├── pyproject.toml                  # Package config, entry point, dépendances
├── README.md
├── LICENSE.txt
├── sql/
│   └── schema.sql                  # Schéma PostgreSQL (5 tables)
├── postgresql/
│   └── docker-compose.yml          # Postgres 17 pour le développement local
├── infra/
│   ├── main.tf                     # Provider + Resource Group + PostgreSQL Flexible Server
│   ├── container-apps.tf           # ACR + Container Apps Environment + 3 Jobs
│   ├── variables.tf                # Variables Terraform
│   ├── outputs.tf                  # Outputs (FQDN, noms de ressources, etc.)
│   └── terraform.tfvars.example    # Exemple de fichier de variables
├── ingestion/
│   ├── Dockerfile                  # Image Docker pour les jobs CLI
│   ├── pyproject.toml              # Dépendances ingestion (psycopg2, requests, azure-identity)
│   └── app/
│       ├── main.py                 # Point d'entrée CLI
│       ├── core/
│       │   ├── base_collector.py   # Classe abstraite BaseCollector
│       │   └── orchestrator.py     # Orchestrateur de jobs
│       ├── collectors/
│       │   ├── azure_pricing_collector.py  # Collecteur Azure Retail Prices API
│       │   └── azure_spot_collector.py     # Collecteur Azure Spot (éviction + prix)
│       └── shared/
│           ├── config.py           # Gestion des variables d'environnement
│           └── pg_client.py        # Client PostgreSQL (sync)
├── src/
│   └── az_scout_bdd_sku/
│       ├── __init__.py             # Classe plugin + entry point
│       ├── plugin_config.py        # Configuration TOML du plugin
│       ├── db.py                   # Pool de connexions async (psycopg)
│       ├── routes.py               # Routes FastAPI (4 endpoints)
│       ├── tools.py                # Outils MCP (4 outils)
│       └── static/
│           ├── css/bdd-sku.css
│           ├── html/bdd-sku-tab.html
│           └── js/bdd-sku-tab.js
└── tests/
    └── test_bdd_sku.py             # Tests pytest (routes + outils MCP)
```

## Base de données

5 tables PostgreSQL :

| Table | Description |
|---|---|
| `job_runs` | Suivi des exécutions d'ingestion (status, durée, items lus/écrits) |
| `job_logs` | Logs détaillés par run |
| `retail_prices_vm` | Prix retail des VM Azure (25 colonnes, UPSERT via contrainte UNIQUE) |
| `spot_eviction_rates` | Taux d'éviction Spot par SKU×région (`UNIQUE (sku_name, region, job_id)` — un snapshot par exécution) |
| `spot_price_history` | Historique des prix Spot par SKU×région×OS (tableau JSONB de prix horodatés) |

---

## Quality checks

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

## License

[MIT](LICENSE.txt)

## Disclaimer

> **This tool is not affiliated with Microsoft.** All capacity, pricing, and latency information are indicative and not a guarantee of deployment success. Pricing values are dynamic and may change between ingestion and actual deployment.
