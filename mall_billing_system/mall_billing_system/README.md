# Mall Billing System (Flask + SQLite)

Offline-ready POS with roles (Admin, Cashier), barcode scanning, cart/checkout, and printable receipts.

## Quick Start

```bash
# 1) Create venv & install deps
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate
pip install -r requirements.txt

# 2) Initialize database with sample data
flask --app app initdb

# 3) Run
flask --app app run  # http://127.0.0.1:5000
```

Login:
- Admin — `admin / admin123`
- Cashier — `cashier / cashier123`

## Features
- Admin: dashboard, products CRUD, order list
- Cashier: POS with barcode input, cart, cash payment, printable receipt
- Stock deduction on sale
- SQLite database (file `app.db`)
- Simple, clean UI
