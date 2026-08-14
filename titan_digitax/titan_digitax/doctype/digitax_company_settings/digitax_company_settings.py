# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.password import get_decrypted_password


class DigitaxCompanySettings(Document):
	def validate(self):
		self._validate_company_country()
		self._set_api_key_fingerprint()
		self._ensure_callback_token()

	def on_update(self):
		# Credentials are cached per company by the resolver.
		frappe.clear_cache(doctype="Digitax Company Settings")

	def _validate_company_country(self):
		"""A company can only file to DigiTax if it's in its own configured target country."""
		if not self.enabled or not self.company:
			return

		if frappe.db.get_value("Company", self.company, "is_group"):
			frappe.throw(
				_("{0} is a group company and cannot file to DigiTax.").format(frappe.bold(self.company))
			)

		country = frappe.db.get_value("Company", self.company, "country")
		if self.target_country and country and country != self.target_country:
			frappe.throw(
				_("{0} is in {1}, but this Digitax Company Settings row targets {2}.").format(
					frappe.bold(self.company), frappe.bold(country), frappe.bold(self.target_country)
				)
			)

	def _set_api_key_fingerprint(self):
		"""Expose the last 4 characters so ops can spot two companies sharing a key."""
		key = self.get("api_key")
		# An unchanged Password field arrives masked; read the stored value instead.
		if not key or set(key) == {"*"}:
			key = (
				get_decrypted_password(
					self.doctype, self.name, "api_key", raise_exception=False
				)
				if not self.is_new()
				else None
			)

		self.api_key_fingerprint = f"…{key[-4:]}" if key and len(key) >= 4 else None

	def _ensure_callback_token(self):
		"""Give every company a distinct callback token.

		The DigiTax callback endpoint is guest-accessible and identifies invoices purely
		by trader_invoice_number. A per-company token lets the handler attribute a
		callback to a company instead of trusting the payload alone.
		"""
		if not self.get("callback_token"):
			self.callback_token = frappe.generate_hash(length=32)


def get_company_settings(company: str):
	"""Return the per-company DigiTax record, or None when the company has none."""
	if not company or not frappe.db.exists("Digitax Company Settings", company):
		return None
	return frappe.get_cached_doc("Digitax Company Settings", company)
