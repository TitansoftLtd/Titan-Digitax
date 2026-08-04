# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Force-resync the Titan Digitax workspace sidebar from its JSON fixture.

`workspace_sidebar/titan_digitax.json` is a standard-record fixture: bench migrate
only re-imports it when the file's `modified` timestamp is newer than the DB row's
(see frappe.modules.import_file.import_file_by_path). That file has been hand-edited
directly rather than through the desk UI (which stamps `modified` with the real save
time on every change), so its timestamp is just whatever was typed in by hand and
gives no guarantee of being newer than what's already stored in any given site's DB —
local happened to sync because its stored value was older, but there's no reason
production's would be. The result: sidebar edits landed in the repo but silently
never applied anywhere the DB's `modified` already outran the file's.

This patch sidesteps the timestamp comparison with force=True, so the fixture is
guaranteed to apply here regardless of what's already stored. Idempotent: re-running
it just re-applies the same file content.
"""

import os

import frappe
from frappe.modules.import_file import import_file_by_path


def execute():
	path = os.path.join(frappe.get_app_path("titan_digitax"), "workspace_sidebar", "titan_digitax.json")
	if os.path.exists(path):
		import_file_by_path(path, force=True)
