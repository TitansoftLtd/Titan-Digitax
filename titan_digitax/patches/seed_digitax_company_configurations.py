"""
Seed Digitax Settings → Eligible Companies from existing Company data.

This patch is idempotent:
- Only runs when Braeburn's custom_enable_company field exists (titan_digitax does
  not require braeburn as a dependency).
- Only inserts rows that are not already present.
- Only inserts enabled leaf companies in the target country.
- Does not modify or delete any Company fields.
"""

import frappe


def execute():
    if not frappe.db.has_column("Company", "custom_enable_company"):
        # Braeburn app is not installed; nothing to backfill.
        return

    settings = frappe.get_single("Digitax Settings")
    target_country = settings.target_country or "Kenya"

    existing_companies = {row.company for row in (settings.company_configurations or [])}

    candidates = frappe.get_all(
        "Company",
        filters={
            "country": target_country,
            "custom_enable_company": 1,
            "is_group": 0,
        },
        pluck="name",
    )

    added = 0
    for company in candidates:
        if company in existing_companies:
            continue
        settings.append("company_configurations", {
            "company": company,
            "enabled": 1,
            "description": "Seeded by migration from custom_enable_company flag.",
        })
        existing_companies.add(company)
        added += 1

    if added:
        settings.flags.ignore_validate = True
        settings.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.logger().info(
            f"seed_digitax_company_configurations: added {added} company row(s) to Digitax Settings."
        )
    else:
        frappe.logger().info(
            "seed_digitax_company_configurations: no new rows needed, all eligible companies already present."
        )
