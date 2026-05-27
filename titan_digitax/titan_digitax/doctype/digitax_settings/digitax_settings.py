# Copyright (c) 2025, Titansoft Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class DigitaxSettings(Document):
    def validate(self):
        self._validate_company_configurations()

    def _validate_company_configurations(self):
        target_country = self.target_country or "Kenya"
        seen = set()

        for row in self.company_configurations or []:
            if not row.company:
                continue

            if row.company in seen:
                frappe.throw(
                    _("Duplicate company in Eligible Companies table: {0}. Each company may only appear once.").format(
                        row.company
                    )
                )
            seen.add(row.company)

            if not row.enabled:
                # Disabled rows are always allowed regardless of country or group status.
                continue

            is_group = frappe.db.get_value("Company", row.company, "is_group")
            if is_group:
                frappe.throw(
                    _("{0} is a group (parent) company and cannot be enabled for Digitax. Enable individual leaf companies instead.").format(
                        row.company
                    )
                )

            country = frappe.db.get_value("Company", row.company, "country")
            if country != target_country:
                frappe.throw(
                    _(
                        "{0} is in {1}, but the Digitax target country is {2}. "
                        "Either change the country on the company, update the Target Country in Digitax Settings, "
                        "or disable this row."
                    ).format(row.company, country or "unknown", target_country)
                )
