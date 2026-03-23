"""
qb_connector.py — Direct QuickBooks Desktop SDK integration via COM (Windows only).

Requirements:
  - Windows OS
  - QuickBooks Desktop Enterprise installed and company file OPEN
  - QB SDK installed: https://developer.intuit.com/app/developer/qbdesktop/docs/get-started
  - pip install pywin32

Usage: set config.quickbooks.mode = "sdk"
"""

import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

from invoice_parser import InvoiceData, LineItem
from config import config
from logger import log

# COM is Windows-only
if sys.platform == "win32":
    try:
        import win32com.client
        WIN32_AVAILABLE = True
    except ImportError:
        WIN32_AVAILABLE = False
        log.warning("pywin32 not installed. SDK mode unavailable. Run: pip install pywin32")
else:
    WIN32_AVAILABLE = False
    log.info("Non-Windows OS detected. QB SDK mode unavailable. Using IIF mode.")


class QBConnector:
    """
    Connects to QuickBooks Desktop via QBFC COM interface.
    Creates or updates Bill transactions for vendor invoices.
    """

    def __init__(self):
        self.session_manager = None
        self._connected = False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Open a QB session. QB Desktop must be running with a company file open."""
        if not WIN32_AVAILABLE:
            log.error("win32com not available — cannot use SDK mode")
            return False

        try:
            self.session_manager = win32com.client.Dispatch("QBFC16.QBSessionManager")
            self.session_manager.OpenConnection2(
                "",                              # AppID (empty = use AppName)
                config.quickbooks.app_name,
                1,                              # 1 = localQBD
            )
            self.session_manager.BeginSession(
                config.quickbooks.company_file,
                2,                              # 2 = multiUser mode
            )
            self._connected = True
            log.info("Connected to QuickBooks Desktop company file: %s",
                     config.quickbooks.company_file or "(currently open file)")
            return True
        except Exception as exc:
            log.error("Failed to connect to QuickBooks: %s", exc)
            return False

    def disconnect(self):
        if self._connected and self.session_manager:
            try:
                self.session_manager.EndSession()
                self.session_manager.CloseConnection()
                log.info("QuickBooks session closed")
            except Exception as exc:
                log.warning("Error closing QB session: %s", exc)
            finally:
                self._connected = False

    # ------------------------------------------------------------------
    # Main import method
    # ------------------------------------------------------------------

    def import_invoice(self, invoice: InvoiceData) -> bool:
        """
        Create or update a Bill in QuickBooks for the given invoice.
        Returns True on success.
        """
        if not self._connected:
            log.error("Not connected to QuickBooks")
            return False

        try:
            # Check if this invoice already exists
            existing_txn_id = self._find_existing_bill(invoice.invoice_number, invoice.vendor_name)

            if existing_txn_id:
                log.info("Bill #%s already exists (TxnID=%s) — updating",
                         invoice.invoice_number, existing_txn_id)
                return self._update_bill(invoice, existing_txn_id)
            else:
                log.info("Creating new Bill #%s for vendor: %s",
                         invoice.invoice_number, invoice.vendor_name)
                return self._create_bill(invoice)

        except Exception as exc:
            log.error("QB import failed for invoice #%s: %s", invoice.invoice_number, exc)
            return False

    # ------------------------------------------------------------------
    # qbXML helpers — build request strings
    # ------------------------------------------------------------------

    def _send_request(self, xml_str: str) -> ET.Element:
        """Send a qbXML request, return the parsed response."""
        request_set = self.session_manager.CreateMsgSetRequest("US", 16, 0)
        request_set.Attributes.OnError = 2    # stopOnError

        # Parse XML and submit
        raw_response = self.session_manager.DoRequests(xml_str)
        return ET.fromstring(raw_response)

    def _find_existing_bill(self, invoice_number: str, vendor_name: str) -> Optional[str]:
        """Query QB for a Bill with matching RefNumber and vendor. Returns TxnID or None."""
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<?qbxml version="16.0"?>
<QBXML>
  <QBXMLMsgsRq onError="stopOnError">
    <BillQueryRq requestID="1">
      <RefNumber>{self._xml_esc(invoice_number)}</RefNumber>
      <VendorName>{self._xml_esc(vendor_name)}</VendorName>
    </BillQueryRq>
  </QBXMLMsgsRq>
</QBXML>"""
        try:
            resp = self._send_request(xml)
            bills = resp.findall(".//BillRet")
            if bills:
                return bills[0].findtext("TxnID")
        except Exception as exc:
            log.debug("Bill query error (may not exist yet): %s", exc)
        return None

    def _create_bill(self, invoice: InvoiceData) -> bool:
        """Send BillAddRq to create a new Bill."""
        xml = self._build_bill_add_xml(invoice)
        try:
            resp = self._send_request(xml)
            status = resp.findtext(".//BillAddRs/@statusCode") or \
                     resp.find(".//BillAddRs").get("statusCode", "?")
            if status == "0":
                txn_id = resp.findtext(".//BillRet/TxnID")
                log.info("Bill created successfully. TxnID=%s", txn_id)
                return True
            else:
                msg = resp.findtext(".//BillAddRs/@statusMessage") or "unknown error"
                log.error("QB returned error creating bill: [%s] %s", status, msg)
                return False
        except Exception as exc:
            log.error("BillAdd failed: %s", exc)
            return False

    def _update_bill(self, invoice: InvoiceData, txn_id: str) -> bool:
        """Send BillModRq to update existing Bill."""
        # Get edit sequence first (required for mod)
        edit_seq = self._get_edit_sequence(txn_id)
        if not edit_seq:
            log.error("Cannot update bill — no EditSequence found for TxnID=%s", txn_id)
            return False

        xml = self._build_bill_mod_xml(invoice, txn_id, edit_seq)
        try:
            resp = self._send_request(xml)
            status = resp.find(".//BillModRs").get("statusCode", "?")
            if status == "0":
                log.info("Bill updated successfully. TxnID=%s", txn_id)
                return True
            else:
                msg = resp.find(".//BillModRs").get("statusMessage", "unknown error")
                log.error("QB returned error updating bill: [%s] %s", status, msg)
                return False
        except Exception as exc:
            log.error("BillMod failed: %s", exc)
            return False

    def _get_edit_sequence(self, txn_id: str) -> Optional[str]:
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<?qbxml version="16.0"?>
<QBXML>
  <QBXMLMsgsRq onError="stopOnError">
    <BillQueryRq requestID="2">
      <TxnID>{txn_id}</TxnID>
    </BillQueryRq>
  </QBXMLMsgsRq>
</QBXML>"""
        try:
            resp = self._send_request(xml)
            return resp.findtext(".//BillRet/EditSequence")
        except Exception:
            return None

    # ------------------------------------------------------------------
    # XML builders
    # ------------------------------------------------------------------

    def _build_bill_add_xml(self, invoice: InvoiceData) -> str:
        date     = self._fmt_date(invoice.invoice_date)
        due      = self._fmt_date(invoice.due_date) if invoice.due_date else date
        vendor   = self._xml_esc(invoice.vendor_name)
        inv_num  = self._xml_esc(invoice.invoice_number)
        terms    = self._xml_esc(invoice.payment_terms)
        memo     = self._xml_esc(f"Auto-imported invoice {invoice.invoice_number}")

        lines_xml = self._build_line_items_xml(invoice)

        return f"""<?xml version="1.0" encoding="utf-8"?>
<?qbxml version="16.0"?>
<QBXML>
  <QBXMLMsgsRq onError="stopOnError">
    <BillAddRq requestID="1">
      <BillAdd>
        <VendorRef><FullName>{vendor}</FullName></VendorRef>
        <TxnDate>{date}</TxnDate>
        <DueDate>{due}</DueDate>
        <RefNumber>{inv_num}</RefNumber>
        <TermsRef><FullName>{terms}</FullName></TermsRef>
        <Memo>{memo}</Memo>
        {lines_xml}
      </BillAdd>
    </BillAddRq>
  </QBXMLMsgsRq>
</QBXML>"""

    def _build_bill_mod_xml(self, invoice: InvoiceData, txn_id: str, edit_seq: str) -> str:
        date    = self._fmt_date(invoice.invoice_date)
        due     = self._fmt_date(invoice.due_date) if invoice.due_date else date
        vendor  = self._xml_esc(invoice.vendor_name)
        inv_num = self._xml_esc(invoice.invoice_number)
        memo    = self._xml_esc(f"Updated: invoice {invoice.invoice_number}")

        lines_xml = self._build_line_items_xml(invoice)

        return f"""<?xml version="1.0" encoding="utf-8"?>
<?qbxml version="16.0"?>
<QBXML>
  <QBXMLMsgsRq onError="stopOnError">
    <BillModRq requestID="1">
      <BillMod>
        <TxnID>{txn_id}</TxnID>
        <EditSequence>{edit_seq}</EditSequence>
        <VendorRef><FullName>{vendor}</FullName></VendorRef>
        <TxnDate>{date}</TxnDate>
        <DueDate>{due}</DueDate>
        <RefNumber>{inv_num}</RefNumber>
        <Memo>{memo}</Memo>
        {lines_xml}
      </BillMod>
    </BillModRq>
  </QBXMLMsgsRq>
</QBXML>"""

    def _build_line_items_xml(self, invoice: InvoiceData) -> str:
        """Build ExpenseLineAdd XML for each line item."""
        if not invoice.line_items:
            # Single summary line
            account = config.quickbooks.default_expense_account
            return f"""<ExpenseLineAdd>
  <AccountRef><FullName>{account}</FullName></AccountRef>
  <Amount>{invoice.total_amount:.2f}</Amount>
  <Memo>Invoice {self._xml_esc(invoice.invoice_number)}</Memo>
</ExpenseLineAdd>"""

        xml_parts = []
        for item in invoice.line_items:
            account = item.account or config.quickbooks.default_expense_account
            desc    = self._xml_esc(item.description)
            xml_parts.append(f"""<ExpenseLineAdd>
  <AccountRef><FullName>{self._xml_esc(account)}</FullName></AccountRef>
  <Amount>{item.amount:.2f}</Amount>
  <Memo>{desc}</Memo>
  <Quantity>{item.quantity}</Quantity>
  <UnitPrice>{item.unit_price:.2f}</UnitPrice>
</ExpenseLineAdd>""")
        return "\n".join(xml_parts)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _fmt_date(self, date_str: str) -> str:
        """YYYY-MM-DD → YYYY-MM-DD (QB SDK uses ISO dates, unlike IIF)."""
        if not date_str:
            return datetime.now().strftime("%Y-%m-%d")
        return date_str

    def _xml_esc(self, value: str) -> str:
        if not value:
            return ""
        return (str(value)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()
