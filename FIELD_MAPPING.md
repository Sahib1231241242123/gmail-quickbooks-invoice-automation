# Field Mapping: Invoice → QuickBooks Desktop

## Bill Transaction

| Invoice Field         | QuickBooks Object     | QB Field Name      | Notes                                      |
|-----------------------|-----------------------|--------------------|--------------------------------------------|
| vendor_name           | VendorRef             | FullName           | Must match Vendor list exactly             |
| invoice_number        | Bill                  | RefNumber          | Used for duplicate detection               |
| invoice_date          | Bill                  | TxnDate            | Format: YYYY-MM-DD (SDK) / MM/DD/YY (IIF) |
| due_date              | Bill                  | DueDate            | If missing, defaults to TxnDate            |
| payment_terms         | TermsRef              | FullName           | Must exist in QB Terms list                |
| total_amount          | Bill                  | Amount             | Negative on AP account (credit)            |

## Bill Expense Lines (per line item)

| Invoice Field         | QuickBooks Object     | QB Field Name      | Notes                                      |
|-----------------------|-----------------------|--------------------|--------------------------------------------|
| line_item.description | ExpenseLine           | Memo               | Free-text description                      |
| line_item.amount      | ExpenseLine           | Amount             | Positive (debit to expense account)        |
| line_item.quantity    | ExpenseLine           | Quantity           | Optional                                   |
| line_item.unit_price  | ExpenseLine           | UnitPrice          | Optional                                   |
| line_item.account     | AccountRef            | FullName           | Falls back to config default_expense_account|

## Vendor Record (auto-created if missing)

| Invoice Field         | QuickBooks Object     | QB Field Name      |
|-----------------------|-----------------------|--------------------|
| vendor_name           | Vendor                | Name               |
| vendor_address        | Vendor                | Addr1              |
| vendor_phone          | Vendor                | Phone1             |
| vendor_email          | Vendor                | Email              |

## IIF vs SDK Differences

| Feature               | IIF Mode              | SDK Mode            |
|-----------------------|-----------------------|---------------------|
| OS                    | Any                   | Windows only        |
| QB must be open       | No                    | Yes                 |
| Real-time             | No (manual import)    | Yes (auto)          |
| Create/update         | Create only           | Create + update     |
| Date format           | MM/DD/YYYY            | YYYY-MM-DD          |
| Duplicate check       | No                    | Yes (by RefNumber)  |
