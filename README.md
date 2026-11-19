# Stock Trading System

This project is for IFT 401 (Fall 2026). This is a simulated stock-market web application for 
training new stockbrokers. Customers (trainee brokers) sign up, deposit simulated cash, and buy 
and sell simulated stocks whose prices move during market hours. Administrators create stocks 
and control the market hours. All money and stocks are simulated. Built with Python (Flask), 
HTML/CSS/JavaScript and MySQL 8, as a 3-tier web application.

## Requirements

- Python 3.14 or newer
- MySQL 8 Server (and MySQL Workbench)
- The Python packages in `requirements.txt`

## Project layout

```
app/
  __init__.py     builds the app, registers the pages and the init-db command
  config.py       reads the settings from .env
  db.py           the ONLY file that talks to MySQL (all SQL is here)
  money.py        exact money helpers (Decimal, rounding to cents, $1,234.56 formatting)
  clock.py        the one place that reads the current time
  cli.py          the run-price-generator command
  routes/         the web pages and buttons (login, trading, cash, admin, logos)
  services/       the business rules (trading, cash, market hours, admin, prices, uploads)
  templates/      the HTML pages
  static/         the stylesheet (ASU maroon and gold) and a small script for the trade page
schema.sql        creates the six database tables
seed.py           the demo data loaded by init-db
```

Each request goes browser -> `routes/` (reads the form) -> `services/` (applies the rules) ->
`db.py` (talks to MySQL). Every file starts with a comment explaining what it does.

Uploaded company logos are saved in `instance/uploads/logos` on the machine running the app
(part of the .gitignore).

## Setup (Locally, once per computer)

1. **Create a virtual environment and install the packages** (Windows PowerShell, in this folder):

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

   If PowerShell says "running scripts is disabled", run this once and try again:
   `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

2. **Create the database and a database user.** In MySQL Workbench, open a query tab and run
   (choose your own password):

   ```sql
   CREATE DATABASE stock_trading;
   CREATE USER 'stock_app'@'localhost' IDENTIFIED BY 'your-db-password';
   GRANT ALL ON stock_trading.* TO 'stock_app'@'localhost';
   ```
   
3. **(OPTIONAL) If you want to delete the database and database user.** In MySQL Workbench, 
	open a query tab and run:
	
	```sql
	DROP DATABASE IF EXISTS stock_trading;
	DROP USER IF EXISTS 'stock_app'@'localhost';
	```

4. **Create your settings file.** Copy `.env.example` to a new file named `.env` and fill it in:

   ```
   DATABASE_URL=mysql://stock_app:your-db-password@localhost:3306/stock_trading
   SECRET_KEY=any-long-random-text
   SEED_ADMIN_PASSWORD=password-for-the-admin-account
   SEED_CUSTOMER_PASSWORD=password-for-the-demo-customer
   ```

   If the database password contains `@`, `:` or `/`, write them as `%40`, `%3A`, `%2F`.
   **NEVER** upload `.env` anywhere (it is already listed in `.gitignore`).

5. **Build the tables and load the demo data:**

   ```powershell
   flask --app app init-db
   ```

   You should see `users: 2, stocks: 6, holdings: 0, transactions: 0, market_schedule: 7,
   market_settings: 1`. Running this again later **Erases All Data** and starts over.

## Run (Locally)

Activate the virtual environment in each terminal you use (`.\.venv\Scripts\Activate.ps1`).

- **Terminal 1 - the website** (keep it open):

  ```powershell
  flask --app app run
  ```

  Open http://127.0.0.1:5000. Stop it with Ctrl+C.

- **Terminal 2 - the price generator** (technically optional; makes prices move):

  ```powershell
  flask --app app run-price-generator
  ```

  Options: `--seconds 5` (update every 5 seconds instead of every 60), `--once` (one update and
  exit), `--seed 42` (repeatable random moves). Stop it with Ctrl+C. If a price changes between
  a customer's review screen and Confirm, the app asks them to review the order again.

## Logging in

`init-db` creates two accounts:

| Account | Username | Password |
|---|---|---|
| Customer (starts with $10,000) | `trainee1` | the `SEED_CUSTOMER_PASSWORD` from `.env` |
| Administrator | `admin` | the `SEED_ADMIN_PASSWORD` from `.env` |

Anyone can also create a customer account with **Create an account** on the login page. New
accounts start with $0.00, so deposit cash on **Financial Records** before trading.

## Market hours

`seed.py` has two ready-made schedules and a switch near the top; exactly one line is active:

```python
# MARKET_SCHEDULE = TEST_SCHEDULE          # <- active: open 24/7
MARKET_SCHEDULE = BUSINESS_SCHEDULE    # <- weekdays 9-5, closed Sat/Sun
```

To switch, comment out the other line, save, then run `flask --app app init-db` (this also
resets all data). With the 9-5 schedule, trading and price changes only happen Monday-Friday
9:00 AM - 5:00 PM. To change hours without resetting data, or to force the market open or closed,
use **Market Hours** on the Administrator Dashboard. Times are in the `MARKET_TIMEZONE` from `.env`
(New York time by default).
