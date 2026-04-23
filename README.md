# Shopify Backoffice — Backend

FastAPI backend that communicates with the Shopify Admin GraphQL API. All Shopify API calls are made server-side — the frontend never talks to Shopify directly.

## Requirements

- [Anaconda](https://www.anaconda.com/download) or Miniconda
- A Shopify store with a private app access token

## Setup

### 1. Create the conda environment

```bash
conda create -n shopify-env python=3.10
conda activate shopify-env
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy the sample file and fill in your values:

```bash
cp .env.sample .env
```

Then edit `.env`:

```
SHOPIFY_STORE=your-store.myshopify.com
SHOPIFY_ACCESS_TOKEN=shpua_xxxxxxxxxxxxxxxxxxxx
```

The access token must come from a Shopify custom app with the following scopes:

- `read_products`
- `write_draft_orders`
- `read_shipping`
- `read_discounts`

### 4. Run the server

```bash
uvicorn main:app --port 3001 --reload
```

The API will be available at `http://localhost:3001`.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/products/search?q=` | Search products by title, SKU, or product type |
| `POST` | `/api/orders/calculate` | Preview order pricing without creating it |
| `POST` | `/api/orders` | Create and complete the order |

## Logs

Application logs are written to `logs/app.log` with rotation at 5 MB (3 backups kept). Logs are also printed to stdout.

## Project Structure

```
shopify_backend/
├── main.py                  # FastAPI app entry point, CORS config, logging
├── requirements.txt
├── .env                     # Shopify credentials (not committed)
├── .env.sample              # Template — copy to .env and fill in values
├── logs/
│   └── app.log              # Rotating log file
└── app/
    ├── config.py            # Reads .env via pydantic-settings
    ├── shopify.py           # GraphQL client — single query() function
    ├── models.py            # Pydantic request models
    └── routers/
        ├── products.py      # Product search — two parallel GraphQL queries
        └── orders.py        # Order calculate, discount lookup, create, complete
```
