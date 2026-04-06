# Deploy Kanapowiec v2 na Azure — instrukcja krok po kroku

## Co potrzebujesz
- Konto Azure (darmowe: portal.azure.com)
- Konto Azure DevOps (darmowe: dev.azure.com)
- Azure CLI zainstalowane lokalnie

---

## KROK 1 — Azure CLI i logowanie

```bash
# Zainstaluj Azure CLI (macOS)
brew install azure-cli

# Zaloguj się
az login
# Otworzy przeglądarkę → zaloguj kontem Microsoft

# Sprawdź subskrypcję
az account show
```

---

## KROK 2 — Utwórz zasoby Azure

```bash
# Zmienne (ustaw swoje wartości)
RESOURCE_GROUP="kanapowiec-rg"
LOCATION="westeurope"
APP_NAME="kanapowiec-v2"
DB_SERVER="kanapowiec-db"
DB_NAME="kanapowiec"
DB_USER="kanapowiec_admin"
DB_PASS="TwojeSilneHaslo123!"

# 1. Grupa zasobów
az group create \
  --name $RESOURCE_GROUP \
  --location $LOCATION

# 2. PostgreSQL Flexible Server
az postgres flexible-server create \
  --resource-group $RESOURCE_GROUP \
  --name $DB_SERVER \
  --location $LOCATION \
  --admin-user $DB_USER \
  --admin-password $DB_PASS \
  --sku-name Standard_B1ms \
  --tier Burstable \
  --version 15 \
  --yes

# 3. Baza danych
az postgres flexible-server db create \
  --resource-group $RESOURCE_GROUP \
  --server-name $DB_SERVER \
  --database-name $DB_NAME

# 4. Zezwól na połączenia z Azure
az postgres flexible-server firewall-rule create \
  --resource-group $RESOURCE_GROUP \
  --name $DB_SERVER \
  --rule-name AllowAzureServices \
  --start-ip-address 0.0.0.0 \
  --end-ip-address 0.0.0.0

# 5. App Service Plan (Linux, B1 = ~$13/mies)
az appservice plan create \
  --name kanapowiec-plan \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --is-linux \
  --sku B1

# 6. Web App (Python 3.11)
az webapp create \
  --resource-group $RESOURCE_GROUP \
  --plan kanapowiec-plan \
  --name $APP_NAME \
  --runtime "PYTHON|3.11" \
  --startup-file 'gunicorn "app:create_app()" --bind 0.0.0.0:8000 --workers 2'
```

---

## KROK 3 — Zmienne środowiskowe w Azure

```bash
# Connection string do PostgreSQL
DB_URL="postgresql://$DB_USER:$DB_PASS@$DB_SERVER.postgres.database.azure.com/$DB_NAME?sslmode=require"

az webapp config appsettings set \
  --resource-group $RESOURCE_GROUP \
  --name $APP_NAME \
  --settings \
    SECRET_KEY="wygeneruj-losowy-string-min-32-znaki" \
    DATABASE_URL="$DB_URL" \
    TMDB_KEY="8265bd1679663a7ea12ac168da84d2e8" \
    GOOGLE_CLIENT_ID="twoj-google-client-id" \
    GOOGLE_CLIENT_SECRET="twoj-google-client-secret" \
    ANTHROPIC_API_KEY="twoj-klucz-anthropic" \
    STRIPE_PUBLIC_KEY="pk_live_..." \
    STRIPE_SECRET_KEY="sk_live_..." \
    STRIPE_WEBHOOK_SECRET="whsec_..." \
    STRIPE_PRICE_ID="price_..." \
    FLASK_ENV="production" \
    SCM_DO_BUILD_DURING_DEPLOYMENT="true"
```

---

## KROK 4 — Azure DevOps — repozytorium

```bash
# Wejdź na dev.azure.com
# Utwórz organizację → projekt "Kanapowiec-v2"
# W Repos → zainicjuj repo

# Lokalnie — dodaj remote ADO
cd ~/Desktop/kanapowiec-v2
git init
git add .
git commit -m "Initial commit — Kanapowiec v2"
git remote add azure https://dev.azure.com/TWOJA_ORG/Kanapowiec-v2/_git/Kanapowiec-v2
git push azure main
```

---

## KROK 5 — Service Connection w ADO

```
ADO → Project Settings → Service connections → New → Azure Resource Manager
→ Service Principal (automatic)
→ Wybierz subskrypcję
→ Nazwa: "Azure-Kanapowiec"
→ Save
```

---

## KROK 6 — Pipeline w ADO

```
ADO → Pipelines → New Pipeline
→ Azure Repos Git
→ Wybierz repo Kanapowiec-v2
→ Existing Azure Pipelines YAML file
→ Ścieżka: /azure-pipelines.yml
→ Run
```

W pipeline dodaj zmienną `AZURE_SUBSCRIPTION` = nazwa Service Connection z Kroku 5.

---

## KROK 7 — Google OAuth (opcjonalne)

```
1. Wejdź na console.cloud.google.com
2. Utwórz projekt "Kanapowiec"
3. APIs & Services → Credentials → Create OAuth 2.0 Client ID
4. Application type: Web application
5. Authorized redirect URIs: https://kanapowiec-v2.azurewebsites.net/auth/google/callback
6. Skopiuj Client ID i Secret → wklej do zmiennych Azure (Krok 3)
```

---

## KROK 8 — Stripe (opcjonalne)

```
1. Wejdź na dashboard.stripe.com
2. Products → Add product → "Kanapowiec Pro"
3. Cena: 5 PLN / miesiąc (recurring)
4. Skopiuj Price ID → zmienna STRIPE_PRICE_ID
5. Developers → Webhooks → Add endpoint
   URL: https://kanapowiec-v2.azurewebsites.net/payments/webhook
   Events: customer.subscription.deleted, invoice.payment_succeeded
```

---

## KROK 9 — Anthropic API

```
1. Wejdź na console.anthropic.com
2. API Keys → Create Key
3. Skopiuj klucz → zmienna ANTHROPIC_API_KEY
```

---

## KROK 10 — Własna domena (opcjonalne)

```bash
# Dodaj domenę do App Service
az webapp config hostname add \
  --resource-group $RESOURCE_GROUP \
  --webapp-name $APP_NAME \
  --hostname "kanapowiec.pl"

# SSL — Azure zarządza automatycznie
az webapp config ssl bind \
  --resource-group $RESOURCE_GROUP \
  --name $APP_NAME \
  --certificate-thumbprint ... \
  --ssl-type SNI
```

---

## Sprawdź czy działa

```bash
# Logi na żywo
az webapp log tail \
  --resource-group $RESOURCE_GROUP \
  --name $APP_NAME

# URL aplikacji
echo "https://$APP_NAME.azurewebsites.net"
```

---

## Koszty miesięczne (szacunek)

| Zasób | SKU | Koszt |
|---|---|---|
| App Service | B1 Linux | ~$13 |
| PostgreSQL | Standard_B1ms | ~$15 |
| Bandwidth | 5GB free | $0 |
| Azure DevOps | 5 users free | $0 |
| **Razem** | | **~$28/mies** |

Przy 1000 użytkownikach i 1000 PLN przychodu — opłacalne.
