"""
test_pipeline.py — Tests invoice parsing across 3 different invoice formats.
Does NOT require Gmail or QuickBooks to run.

Run: python test_pipeline.py
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Make sure we're running from project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from invoice_parser import InvoiceParser, InvoiceData, LineItem
from qb_iif_exporter import QBIIFExporter
from config import config


# ------------------------------------------------------------------
# Sample invoice texts (3 different vendor formats)
# ------------------------------------------------------------------

INVOICE_FORMAT_1_STANDARD = """
INVOICE

From: Acme Supplies Inc.
123 Main Street, Dallas TX 75201
Phone: (214) 555-0100
Email: billing@acmesupplies.com

Bill To: Your Company LLC

Invoice Number: INV-2024-00421
Invoice Date: March 10, 2024
Due Date: April 9, 2024
Payment Terms: Net 30

ITEMS:
------------------------------------------------------------------
Description              Qty    Unit Price    Amount
Office Paper (Case)       5      $45.00        $225.00
Printer Toner Cartridge   2      $89.99        $179.98
Desk Organizer Set        3      $24.50        $73.50
------------------------------------------------------------------
Subtotal:                                      $478.48
Tax (8.25%):                                   $39.47
TOTAL DUE:                                     $517.95
"""

INVOICE_FORMAT_2_MINIMAL = """
TAX INVOICE #8821

Vendor: Global Tech Parts Ltd.
Date: 15/01/2024
Due: 15/02/2024

Customer: Your Company LLC

1x Server RAM 32GB DDR5 @ $320.00 = $320.00
4x CAT6 Cable 10m @ $12.00 = $48.00
2x Network Switch 24-port @ $185.00 = $370.00

Subtotal: $738.00
GST (10%): $73.80
Total: $811.80

Bank Transfer: BSB 062-000 Account 12345678
Terms: 30 days
"""

INVOICE_FORMAT_3_EUROPEAN = """
RECHNUNG / INVOICE

Lieferant / Supplier:
Deutsche Büroservice GmbH
Hauptstraße 47, 10115 Berlin, Germany
Tel: +49 30 12345678
E-Mail: rechnungen@dbs-berlin.de

Rechnungsnummer: RE-2024-0089
Rechnungsdatum: 22.02.2024
Fälligkeitsdatum: 22.03.2024
Zahlungsziel: 30 Tage netto

Position  Beschreibung              Menge   Einzelpreis    Gesamt
1         Bürostühle ergonomisch    4       EUR 349,00     EUR 1.396,00
2         Schreibtisch höhenverst.  2       EUR 599,00     EUR 1.198,00
3         Aktenschrank 4-türig      1       EUR 449,00     EUR 449,00

Nettobetrag:        EUR 3.043,00
MwSt 19%:           EUR 578,17
Rechnungsbetrag:    EUR 3.621,17
"""


# ------------------------------------------------------------------
# Mock Claude API responses
# ------------------------------------------------------------------

MOCK_RESPONSE_FORMAT_1 = {
    "invoice_number": "INV-2024-00421",
    "vendor_name": "Acme Supplies Inc.",
    "vendor_address": "123 Main Street, Dallas TX 75201",
    "vendor_email": "billing@acmesupplies.com",
    "vendor_phone": "(214) 555-0100",
    "invoice_date": "2024-03-10",
    "due_date": "2024-04-09",
    "subtotal": 478.48,
    "tax_amount": 39.47,
    "discount_amount": 0,
    "total_amount": 517.95,
    "payment_terms": "Net 30",
    "line_items": [
        {"description": "Office Paper (Case)", "quantity": 5, "unit_price": 45.00, "amount": 225.00, "item_code": ""},
        {"description": "Printer Toner Cartridge", "quantity": 2, "unit_price": 89.99, "amount": 179.98, "item_code": ""},
        {"description": "Desk Organizer Set", "quantity": 3, "unit_price": 24.50, "amount": 73.50, "item_code": ""},
    ],
    "confidence": "high",
    "warnings": []
}

MOCK_RESPONSE_FORMAT_2 = {
    "invoice_number": "8821",
    "vendor_name": "Global Tech Parts Ltd.",
    "vendor_address": "",
    "vendor_email": "",
    "vendor_phone": "",
    "invoice_date": "2024-01-15",
    "due_date": "2024-02-15",
    "subtotal": 738.00,
    "tax_amount": 73.80,
    "discount_amount": 0,
    "total_amount": 811.80,
    "payment_terms": "30 days",
    "line_items": [
        {"description": "Server RAM 32GB DDR5", "quantity": 1, "unit_price": 320.00, "amount": 320.00, "item_code": ""},
        {"description": "CAT6 Cable 10m", "quantity": 4, "unit_price": 12.00, "amount": 48.00, "item_code": ""},
        {"description": "Network Switch 24-port", "quantity": 2, "unit_price": 185.00, "amount": 370.00, "item_code": ""},
    ],
    "confidence": "high",
    "warnings": []
}

MOCK_RESPONSE_FORMAT_3 = {
    "invoice_number": "RE-2024-0089",
    "vendor_name": "Deutsche Büroservice GmbH",
    "vendor_address": "Hauptstraße 47, 10115 Berlin, Germany",
    "vendor_email": "rechnungen@dbs-berlin.de",
    "vendor_phone": "+49 30 12345678",
    "invoice_date": "2024-02-22",
    "due_date": "2024-03-22",
    "subtotal": 3043.00,
    "tax_amount": 578.17,
    "discount_amount": 0,
    "total_amount": 3621.17,
    "payment_terms": "30 Tage netto",
    "line_items": [
        {"description": "Bürostühle ergonomisch", "quantity": 4, "unit_price": 349.00, "amount": 1396.00, "item_code": "1"},
        {"description": "Schreibtisch höhenverstellbar", "quantity": 2, "unit_price": 599.00, "amount": 1198.00, "item_code": "2"},
        {"description": "Aktenschrank 4-türig", "quantity": 1, "unit_price": 449.00, "amount": 449.00, "item_code": "3"},
    ],
    "confidence": "high",
    "warnings": []
}


def _make_mock_response(data: dict):
    """Create a mock Anthropic API response object."""
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock()]
    mock_resp.content[0].text = json.dumps(data)
    return mock_resp


# ------------------------------------------------------------------
# Test cases
# ------------------------------------------------------------------

class TestInvoiceParsing(unittest.TestCase):

    def setUp(self):
        config.anthropic.api_key = "test-key"
        self.parser = InvoiceParser()

    @patch("anthropic.Anthropic")
    def _run_text_parse(self, text, mock_response_data, MockAnthropic):
        mock_client = MagicMock()
        MockAnthropic.return_value = mock_client
        mock_client.messages.create.return_value = _make_mock_response(mock_response_data)

        # Re-init parser with mock
        self.parser.client = mock_client
        return self.parser._parse_text(text)

    # ---- Format 1: Standard US invoice ----

    def test_format1_standard_invoice(self):
        """Standard US invoice with clear layout."""
        with patch.object(self.parser.client.messages, "create",
                          return_value=_make_mock_response(MOCK_RESPONSE_FORMAT_1)):
            invoice = self.parser._parse_text(INVOICE_FORMAT_1_STANDARD)

        self.assertIsNotNone(invoice, "Parser returned None for standard invoice")
        self.assertEqual(invoice.invoice_number, "INV-2024-00421")
        self.assertEqual(invoice.vendor_name, "Acme Supplies Inc.")
        self.assertAlmostEqual(invoice.total_amount, 517.95, places=2)
        self.assertEqual(len(invoice.line_items), 3)
        self.assertEqual(invoice.line_items[0].description, "Office Paper (Case)")
        self.assertAlmostEqual(invoice.line_items[0].amount, 225.00, places=2)
        self.assertEqual(invoice.confidence, "high")
        print("✅ Format 1 (Standard US): PASS")

    # ---- Format 2: Minimal tech invoice ----

    def test_format2_minimal_invoice(self):
        """Minimal invoice with no address and non-standard layout."""
        with patch.object(self.parser.client.messages, "create",
                          return_value=_make_mock_response(MOCK_RESPONSE_FORMAT_2)):
            invoice = self.parser._parse_text(INVOICE_FORMAT_2_MINIMAL)

        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.invoice_number, "8821")
        self.assertEqual(invoice.vendor_name, "Global Tech Parts Ltd.")
        self.assertAlmostEqual(invoice.total_amount, 811.80, places=2)
        self.assertEqual(len(invoice.line_items), 3)
        self.assertAlmostEqual(invoice.tax_amount, 73.80, places=2)
        print("✅ Format 2 (Minimal tech): PASS")

    # ---- Format 3: European/German invoice ----

    def test_format3_european_invoice(self):
        """European invoice with bilingual fields and comma decimal separators."""
        with patch.object(self.parser.client.messages, "create",
                          return_value=_make_mock_response(MOCK_RESPONSE_FORMAT_3)):
            invoice = self.parser._parse_text(INVOICE_FORMAT_3_EUROPEAN)

        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.invoice_number, "RE-2024-0089")
        self.assertEqual(invoice.vendor_name, "Deutsche Büroservice GmbH")
        self.assertAlmostEqual(invoice.total_amount, 3621.17, places=2)
        self.assertEqual(len(invoice.line_items), 3)
        self.assertEqual(invoice.invoice_date, "2024-02-22")
        print("✅ Format 3 (European/German): PASS")


class TestIIFExporter(unittest.TestCase):

    def setUp(self):
        self.exporter = QBIIFExporter()
        config.quickbooks.iif_output_dir = tempfile.mkdtemp()

    def _make_invoice(self, num="TEST-001", vendor="Test Corp", total=500.00):
        from invoice_parser import InvoiceData, LineItem
        return InvoiceData(
            invoice_number=num,
            vendor_name=vendor,
            vendor_address="1 Test St",
            vendor_email="test@test.com",
            vendor_phone="555-0000",
            invoice_date="2024-03-01",
            due_date="2024-04-01",
            subtotal=450.00,
            tax_amount=50.00,
            discount_amount=0,
            total_amount=total,
            payment_terms="Net 30",
            line_items=[
                LineItem("Widget A", 2, 100.00, 200.00),
                LineItem("Widget B", 5, 50.00, 250.00),
            ]
        )

    def test_iif_file_created(self):
        invoice = self._make_invoice()
        path = self.exporter.export(invoice)
        self.assertTrue(os.path.exists(path), "IIF file not created")
        print("✅ IIF file creation: PASS")

    def test_iif_contains_vendor(self):
        invoice = self._make_invoice(vendor="Acme Corp")
        path = self.exporter.export(invoice)
        with open(path) as f:
            content = f.read()
        self.assertIn("Acme Corp", content)
        print("✅ IIF vendor name: PASS")

    def test_iif_contains_amount(self):
        invoice = self._make_invoice(total=517.95)
        path = self.exporter.export(invoice)
        with open(path) as f:
            content = f.read()
        self.assertIn("517.95", content)
        print("✅ IIF amount accuracy: PASS")

    def test_iif_correct_line_count(self):
        invoice = self._make_invoice()
        path = self.exporter.export(invoice)
        with open(path) as f:
            lines = [l for l in f.readlines() if l.startswith("SPL")]
        self.assertEqual(len(lines), 2, f"Expected 2 SPL lines, got {len(lines)}")
        print("✅ IIF line item count: PASS")


# ------------------------------------------------------------------
# Runner
# ------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("  Invoice Automation — Test Suite")
    print("=" * 60)
    print()

    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestInvoiceParsing))
    suite.addTests(loader.loadTestsFromTestCase(TestIIFExporter))

    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)

    print()
    print("=" * 60)
    passed = result.testsRun - len(result.failures) - len(result.errors)
    print(f"  Results: {passed}/{result.testsRun} tests passed")
    if result.failures or result.errors:
        print("  ❌ Some tests FAILED — see above")
        sys.exit(1)
    else:
        print("  ✅ All tests PASSED")
    print("=" * 60)
