# Inventory Management System

A Flask + SQLite inventory manager with authentication, product CRUD, stock
in/out tracking with a full audit trail, low-stock alerts, and reports.

No external APIs are used — everything runs locally.

## Features

- Signup/login with hashed passwords (first user to sign up becomes admin)
- Product CRUD (SKU, name, price, category, supplier, reorder level)
- Stock in / stock out / manual adjustment, each written to a `StockLog`
  table so quantity is never silently overwritten — you get a full history
- Low-stock dashboard alerts
- Category & supplier management
- Reports: total stock valuation, most-active products by movement

## Project Structure

```
inventory_management/
├── app.py              # Flask routes / application entry point
├── models.py           # SQLAlchemy models (User, Category, Supplier, Product, StockLog)
├── extensions.py       # Flask-SQLAlchemy instance
├── requirements.txt
├── templates/           # Jinja2 HTML templates
└── static/
    └── style.css
```

---

## Running this project in Visual Studio Code

### 1. Prerequisites

- Install [Visual Studio Code](https://code.visualstudio.com/)
- Install [Python 3.10+](https://www.python.org/downloads/) and make sure
  it's added to your PATH (on Windows, check "Add python.exe to PATH"
  during install)
- In VS Code, install the **Python extension** (by Microsoft) from the
  Extensions panel (`Ctrl+Shift+X` / `Cmd+Shift+X`)

### 2. Open the project

- Unzip the `inventory_management` folder somewhere on your machine
- In VS Code: **File → Open Folder...** and select `inventory_management`

### 3. Create a virtual environment

Open a terminal inside VS Code: **Terminal → New Terminal** (or `` Ctrl+` ``).

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

You should see `(venv)` appear at the start of your terminal prompt.
If VS Code shows a popup asking "Select this environment for the workspace?",
click **Yes** — this points VS Code's Python interpreter at your venv.

You can also set the interpreter manually: press `Ctrl+Shift+P`
(`Cmd+Shift+P` on Mac) → type **"Python: Select Interpreter"** → choose the
one inside `venv`.

### 4. Install dependencies

With the venv active:
```bash
pip install -r requirements.txt
```

### 5. Run the app

```bash
python app.py
```

You should see output ending in something like:
```
Running on http://127.0.0.1:5005
```

Open that URL in your browser. The SQLite database file (`inventory.db`)
is created automatically on first run — no manual DB setup needed.

### 6. First-time use

1. Go to `/signup` and create an account — this first account is
   automatically made an **admin**.
2. Log in.
3. Add a category or supplier first (optional), then add your first product
   under **Products → + Add Product**.
4. Use the **Stock** action on a product to record stock in/out — this is
   the only way quantity changes, so every change is logged and auditable.

### Stopping the server

Press `Ctrl+C` in the VS Code terminal.

---

## Notes before deploying anywhere public

- Change `app.config["SECRET_KEY"]` in `app.py` to a long random value
  (don't hardcode it — read it from an environment variable instead).
- Set `debug=False` in `app.run(...)` before deploying — debug mode exposes
  an interactive debugger that can execute arbitrary code.
- Consider switching from SQLite to PostgreSQL for multi-user production use.

## Possible next steps

- Barcode/QR code generation and scanning for products
- Excel/PDF export of reports
- Multi-location / warehouse support
- Role-based permissions (restrict delete actions to admins only)
