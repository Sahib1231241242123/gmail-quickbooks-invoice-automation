"""
qb_iif_exporter.py — Generates QuickBooks IIF (Intuit Interchange Format) files
from parsed invoice data. IIF can be imported into QB Desktop via:
  File > Utilities > Import > IIF Files

This is the cross-platform path (works on Mac/Linux/Windows without QB SDK).
"""

import os
from datetime import datetime
from invoice_parser import InvoiceData
from config import config
from logger import log


class QBIIFExporter:
    """
    Generates IIF files that create Bills (vendor invoices) in QuickBooks Desktop.
    One IIF file is created per invoice for auditability.
    """

    def export(self, invoice: InvoiceData) -> str:
        """
        Generate an IIF file for the given invoice.
        Returns the path to the created file.
        """
        os.makedirs(config.quickbooks.iif_output_dir, exist_ok=True)

        safe_inv = invoice.invoice_number.replace("/", "-").replace("\\", "-")
        filename = f"invoice_{safe_inv}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.iif"
        path = os.path.join(config.quickbooks.iif_output_dir, filename)

        lines = self._build_iif(invoice)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        log.info("IIF file created: %s", path)
        return path

    # ------------------------------------------------------------------
    # IIF generation
    # ------------------------------------------------------------------

    def _build_iif(self, invoice: InvoiceData) -> list[str]:
        """
        IIF format for Bills (vendor invoices):

        !TRNS header defines transaction columns.
        !SPL   header defines split-line columns.
        TRNS   is the transaction (master) record.
        SPL    is each line item.
        ENDTRNS closes the transaction.
        """
        lines = []

        # ---- Vendor record (ensures vendor exists in QB) ----
        lines += self._vendor_header()
        lines += self._vendor_record(invoice)

        # ---- Bill transaction ----
        lines += self._trns_header()
        lines += self._trns_record(invoice)

        # AP line (credit to Accounts Payable)
        lines += self._ap_split(invoice)

        # Expense lines (debit each line item)
        for idx, item in enumerate(invoice.line_items):
            lines += self._expense_split(invoice, item, idx)

        # If no line items, create a single summary line
        if not invoice.line_items:
            lines += self._summary_split(invoice)

        lines.append("ENDTRNS")

        return lines

    # ------------------------------------------------------------------
    # Vendor section
    # ------------------------------------------------------------------

    def _vendor_header(self) -> list[str]:
        return [
            "!VEND\tNAME\tREFNUM\tTIMESTAMP\tNAME\tPRINTAS\tADDR1\tPHONE1\tEMAIL",
        ]

    def _vendor_record(self, invoice: InvoiceData) -> list[str]:
        name   = self._esc(invoice.vendor_name)
        addr   = self._esc(invoice.vendor_address)
        phone  = self._esc(invoice.vendor_phone)
        email  = self._esc(invoice.vendor_email)
        return [
            f"VEND\t{name}\t\t\t{name}\t{name}\t{addr}\t{phone}\t{email}",
        ]

    # ------------------------------------------------------------------
    # Transaction section
    # ------------------------------------------------------------------

    def _trns_header(self) -> list[str]:
        return [
            "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO\tDUEDATE\tTERMS",
            "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO\tQNTY\tPRICE",
            "!ENDTRNS",
        ]

    def _trns_record(self, invoice: InvoiceData) -> list[str]:
        """Master TRNS line — the Bill header."""
        ap_account = config.quickbooks.ap_account
        date     = self._format_date(invoice.invoice_date)
        due      = self._format_date(invoice.due_date) if invoice.due_date else date
        vendor   = self._esc(invoice.vendor_name)
        inv_num  = self._esc(invoice.invoice_number)
        terms    = self._esc(invoice.payment_terms)

        # In IIF, Bill amount is negative on the AP account (credit)
        amount   = -abs(invoice.total_amount)

        return [
            f"TRNS\tBILL\t{date}\t{ap_account}\t{vendor}\t{amount:.2f}\t{inv_num}\t"
            f"Invoice {inv_num}\t{due}\t{terms}",
        ]

    def _ap_split(self, invoice: InvoiceData) -> list[str]:
        """One SPL line crediting Accounts Payable (mirrors TRNS, keeps QB balanced)."""
        # QB handles the AP credit via the TRNS line itself; we only need expense splits.
        # Some QB versions need an explicit AP SPL — we skip it to avoid double-counting.
        return []

    def _expense_split(self, invoice: InvoiceData, item, idx: int) -> list[str]:
        """One SPL line per invoice line item (debit to expense account)."""
        date    = self._format_date(invoice.invoice_date)
        vendor  = self._esc(invoice.vendor_name)
        account = item.account or config.quickbooks.default_expense_account
        desc    = self._esc(item.description)
        amount  = abs(item.amount)
        qty     = item.quantity
        price   = item.unit_price

        return [
            f"SPL\tBILL\t{date}\t{account}\t{vendor}\t{amount:.2f}\t"
            f"\t{desc}\t{qty}\t{price:.2f}",
        ]

    def _summary_split(self, invoice: InvoiceData) -> list[str]:
        """Fallback: single line when no itemized breakdown available."""
        date    = self._format_date(invoice.invoice_date)
        vendor  = self._esc(invoice.vendor_name)
        account = config.quickbooks.default_expense_account
        amount  = abs(invoice.total_amount)
        return [
            f"SPL\tBILL\t{date}\t{account}\t{vendor}\t{amount:.2f}\t"
            f"\tInvoice {invoice.invoice_number}\t1\t{amount:.2f}",
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _format_date(self, date_str: str) -> str:
        """Convert YYYY-MM-DD to MM/DD/YYYY (QB IIF format)."""
        if not date_str:
            return datetime.now().strftime("%m/%d/%Y")
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.strftime("%m/%d/%Y")
        except ValueError:
            return datetime.now().strftime("%m/%d/%Y")

    def _esc(self, value: str) -> str:
        """Escape tabs and newlines for IIF format."""
        if not value:
            return ""
        return str(value).replace("\t", " ").replace("\n", " ").replace("\r", "")
