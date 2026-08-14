# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Create the Digitax Sync User service account and point every existing
Digitax Company Settings row at it (D-role-split).

Automatic/background Digitax work (invoice submit, the hourly retry sweep,
item auto-sync) needs a consistent identity to run as - a background job
enqueued from a user action runs AS that user, not as Administrator, so
requiring a Digitax role directly on that path would silently break normal
invoice submission for anyone without it. A dedicated service account sidesteps
that: automatic paths always run as this user, and manual actions switch to it
too once the initiating human's own role has been checked - so every Digitax
write is attributed to one consistent account instead of whichever user or
background job happened to trigger it.

Idempotent: leaves an existing user/field value untouched.
"""

import frappe

SYNC_USER_EMAIL = "digitaxsyncuser@example.com"
SYNC_USER_FULL_NAME = "Digitax Sync User"
COMPANY_DOCTYPE = "Digitax Company Settings"
ROLE_FIELDS = ("send_role", "item_sync_role", "virtual_amendment_role")


def execute():
	if not frappe.db.exists("DocType", COMPANY_DOCTYPE):
		return

	logger = frappe.logger("digitax_integration")

	roles = {"System Manager"}
	if frappe.db.table_exists(COMPANY_DOCTYPE):
		for fieldname in ROLE_FIELDS:
			if frappe.db.has_column(COMPANY_DOCTYPE, fieldname):
				roles.update(
					r
					for r in frappe.get_all(COMPANY_DOCTYPE, pluck=fieldname, filters={fieldname: ["is", "set"]})
					if r
				)

	if not frappe.db.exists("User", SYNC_USER_EMAIL):
		user = frappe.new_doc("User")
		user.email = SYNC_USER_EMAIL
		user.first_name = SYNC_USER_FULL_NAME
		user.user_type = "System User"
		user.send_welcome_email = 0
		user.flags.ignore_permissions = True
		user.flags.no_welcome_mail = True
		for role in sorted(roles):
			user.append("roles", {"role": role})
		user.insert()
		logger.info(f"create_digitax_sync_user: created {SYNC_USER_EMAIL} with roles {sorted(roles)}")
	else:
		# Already exists (e.g. re-run, or created by hand before this patch shipped) -
		# make sure it still has every role currently configured across companies.
		user = frappe.get_doc("User", SYNC_USER_EMAIL)
		existing_roles = {r.role for r in user.get("roles") or []}
		missing = roles - existing_roles
		if missing:
			for role in sorted(missing):
				user.append("roles", {"role": role})
			user.flags.ignore_permissions = True
			user.save()
			logger.info(f"create_digitax_sync_user: added missing roles {sorted(missing)} to {SYNC_USER_EMAIL}")

	if not frappe.db.has_column(COMPANY_DOCTYPE, "digitax_sync_user"):
		return

	updated = 0
	for company in frappe.get_all(
		COMPANY_DOCTYPE, filters={"digitax_sync_user": ["in", ["", None]]}, pluck="name"
	):
		frappe.db.set_value(COMPANY_DOCTYPE, company, "digitax_sync_user", SYNC_USER_EMAIL, update_modified=False)
		updated += 1

	frappe.db.commit()
	logger.info(f"create_digitax_sync_user: set digitax_sync_user on {updated} company row(s)")
