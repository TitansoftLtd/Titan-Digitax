# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

import uuid

import frappe
from frappe.model.document import Document
from frappe.utils import now

DIRECTION_TO_DIGITAX = "To DigiTax"
DIRECTION_FROM_DIGITAX = "From DigiTax"

# Which Sync Type values are valid for each Direction. "Customers" exists only as a
# placeholder — DigiTax has no customer endpoint yet — and is special-cased in
# execute_digitax_sync to return "coming soon" without enqueueing anything.
SYNC_TYPES_BY_DIRECTION = {
	DIRECTION_TO_DIGITAX: ["Invoices"],
	DIRECTION_FROM_DIGITAX: ["Customers", "Items"],
}

NOT_YET_AVAILABLE_SYNC_TYPES = {"Customers"}


class DigitaxSyncJob(Document):
	"""One row per Digitax sync run (manual or scheduled), tracking live progress."""
	pass


def _update_job_record(job_id=None, direction=None, sync_type=None, company=None, **fields):
	"""Create-or-update the job row for job_id. First call for a job_id creates the row
	(direction/sync_type/company are only used then); every call after that only writes
	whichever kwargs are non-None. Commits immediately so pollers see progress live.
	"""
	if not job_id:
		return

	try:
		if not frappe.db.exists("Digitax Sync Job", job_id):
			doc = frappe.get_doc({
				"doctype": "Digitax Sync Job",
				"job_id": job_id,
				"direction": direction,
				"sync_type": sync_type,
				"company": company,
				"status": fields.get("status") or "Queued",
				"started_at": fields.get("started_at") or now(),
			})
			doc.insert(ignore_permissions=True)
			frappe.db.commit()

		updates = {k: v for k, v in fields.items() if v is not None}
		if updates:
			frappe.db.set_value("Digitax Sync Job", job_id, updates)
			frappe.db.commit()
	except Exception:
		frappe.log_error(
			title=f"Digitax Sync Job Tracking Failed - {job_id}",
			message=frappe.get_traceback(),
		)


@frappe.whitelist()
def get_last_running_job(company=None):
	"""Return the current user's own in-flight job, if any, so the page can resume
	polling on load instead of assuming nothing is running."""
	filters = {"owner": frappe.session.user, "status": "Running"}
	if company:
		filters["company"] = company

	jobs = frappe.get_all(
		"Digitax Sync Job",
		filters=filters,
		fields=[
			"name", "job_id", "direction", "sync_type", "company", "status",
			"percentage_complete", "last_message", "total_records", "processed_records",
			"skipped_records", "errors", "started_at", "ended_at",
		],
		order_by="creation desc",
		limit=1,
	)
	return jobs[0] if jobs else None


@frappe.whitelist()
def get_recent_sync_jobs(limit=10, company=None):
	"""Return the most recent jobs across all users for the queue table."""
	filters = {"company": company} if company else None

	jobs = frappe.get_all(
		"Digitax Sync Job",
		filters=filters,
		fields=[
			"name", "job_id", "direction", "sync_type", "company", "status",
			"percentage_complete", "last_message", "total_records", "processed_records",
			"skipped_records", "errors", "started_at", "ended_at", "owner", "creation",
		],
		order_by="creation desc",
		limit=int(limit),
	)
	for job in jobs:
		job["owner_name"] = frappe.db.get_value("User", job["owner"], "full_name") or job["owner"]
		job["is_current_user"] = job["owner"] == frappe.session.user
	return jobs


@frappe.whitelist()
def execute_digitax_sync(direction=None, sync_type=None, company=None):
	"""
	Entry point for UI-triggered sync.
	Creates a new job row and enqueues the background task; the caller polls the
	returned job_id for progress.

	Args:
		direction: "To DigiTax" (push to DigiTax) or "From DigiTax" (pull from DigiTax)
		sync_type: The type of sync to execute — valid options depend on direction,
			see SYNC_TYPES_BY_DIRECTION
		company: The company whose Digitax Company Settings to sync with
	"""
	# Bulk action (an hour-long job, per company or across the catalogue) with no
	# other caller than this page's own "Execute" button - System Manager only,
	# same as the other bulk/multi-company Digitax entrypoints.
	frappe.only_for("System Manager")

	if not direction or direction.strip() == "":
		frappe.throw("Please select a Direction before executing.")

	if direction not in SYNC_TYPES_BY_DIRECTION:
		frappe.throw(f"Invalid direction: {direction}.")

	if not sync_type or sync_type.strip() == "":
		frappe.throw("Please select a Sync Type before executing.")

	if not company:
		frappe.throw("Please select a Company before executing.")

	valid_types = SYNC_TYPES_BY_DIRECTION[direction]
	if sync_type not in valid_types:
		frappe.throw(
			f"'{sync_type}' is not a valid Sync Type for {direction}. "
			f"Must be one of: {', '.join(valid_types)}"
		)

	if sync_type in NOT_YET_AVAILABLE_SYNC_TYPES:
		# No job row created — this option exists as a placeholder for when DigiTax
		# exposes the endpoint.
		return {
			"status": "unavailable",
			"message": f"{sync_type} sync is not available yet. Coming soon.",
		}

	job_id = str(uuid.uuid4())
	_update_job_record(
		job_id=job_id, direction=direction, sync_type=sync_type, company=company,
		status="Queued", started_at=now(), total_records=0, processed_records=0,
		skipped_records=0, errors=0, percentage_complete=0,
		last_message="Job queued, starting soon...",
	)

	frappe.enqueue(
		"titan_digitax.titan_digitax.doctype.digitax_sync_job.digitax_sync_job.run_digitax_sync_background",
		queue="long",
		timeout=3600,
		job_name=f"digitax_sync::{frappe.scrub(company)}::{job_id}",
		sync_job_id=job_id,
		direction=direction,
		sync_type=sync_type,
		company=company,
	)

	return {
		"status": "queued",
		"message": f"Sync job started for {sync_type}.",
		"job_id": job_id,
	}


def run_digitax_sync_background(sync_job_id, direction, sync_type, company):
	"""Background worker for UI-triggered sync — updates the job row throughout."""
	job_id = sync_job_id
	try:
		_update_job_record(job_id=job_id, status="Running", percentage_complete=0, last_message="Job started")

		result = run_digitax_sync(sync_type, company, job_id=job_id)

		summary = result.get("summary", "Sync completed successfully.")
		_update_job_record(
			job_id=job_id, status="Success", ended_at=now(), percentage_complete=100,
			last_result=summary, last_message=summary,
			errors=len(result.get("errors") or []),
		)

	except Exception as e:
		error_trace = frappe.get_traceback()
		_update_job_record(
			job_id=job_id, status="Failed", ended_at=now(),
			last_result=f"Sync failed: {e}", last_message=f"Sync failed: {e}",
			error_traceback=error_trace,
		)
		frappe.log_error(title=f"Digitax Sync Failed - {sync_type}", message=error_trace)


def run_digitax_sync(sync_type, company, start_time=None, end_time=None, job_id=None):
	"""
	Core sync function that can be called by UI or scheduler.

	Args:
		sync_type: "Invoices", "Customers", or "Items"
		company: The company whose Digitax Company Settings to sync with
		start_time: Optional datetime for filtering (used by scheduler)
		end_time: Optional datetime for filtering (used by scheduler)
		job_id: Optional Digitax Sync Job name to report progress against

	Returns:
		dict with summary of sync results
	"""
	frappe.logger().info(f"Starting Digitax sync for type: {sync_type}, company: {company}")

	if sync_type == "Invoices":
		return sync_invoices_to_digitax(company, job_id=job_id)
	elif sync_type == "Customers":
		return sync_customers_from_digitax(company, start_time, end_time)
	elif sync_type == "Items":
		return sync_items_from_digitax(company, start_time, end_time, job_id=job_id)
	else:
		frappe.throw(f"Unknown sync type: {sync_type}")


def sync_invoices_to_digitax(company, job_id=None):
	"""
	Push this company's not-yet-sent Sales Invoices to DigiTax, ticking per-invoice
	progress on job_id if given (manual, on-demand — the hourly retry cron at
	titan_digitax.utils.sales.job_retry_sending_sales_invoices is unaffected and keeps
	running its own, job-row-less path).

	Args:
		company: The company whose unsent invoices to (re)send
		job_id: Optional Digitax Sync Job name to report progress against

	Returns:
		dict: Summary with attempted/sent/still_unsent counts
	"""
	from titan_digitax.titan_digitax.utils.company_config import get_digitax_settings, is_digitax_enabled_for_company

	frappe.logger().info(f"DIGITAX INVOICE SYNC: Starting sync to Digitax for {company}")

	if not is_digitax_enabled_for_company(company):
		summary = f"Digitax integration is not enabled for {company}"
		return {"summary": summary, "created": 0, "updated": 0, "skipped": 0, "errors": [summary]}

	settings = get_digitax_settings(company)

	target_country = settings.get("target_country") or "Kenya"
	company_country = frappe.db.get_value("Company", company, "country")
	if company_country != target_country:
		summary = f"{company} is not in the Digitax target country ({target_country})"
		return {"summary": summary, "created": 0, "updated": 0, "skipped": 0, "errors": [summary]}

	try:
		retry_count = int(settings.get("max_retry_attempts") or 5)
		if retry_count <= 0:
			retry_count = 5
	except (ValueError, TypeError):
		retry_count = 5

	# The automatic (unchecked) entrypoint - this runs inside a background job
	# already gated at execute_digitax_sync (System Manager click to start it),
	# not a per-invoice interactive send. Using the checked wrapper here would
	# additionally require whoever clicked Execute to separately hold send_role
	# too, even though they already passed a stricter gate to get here.
	from titan_digitax.titan_digitax.utils.sales import send_sales_invoice_to_digitax_automatic

	invoices = frappe.get_all(
		"Sales Invoice",
		filters={
			"docstatus": 1,
			"custom_sent_to_digitax": 0,
			"company": company,
			"custom_retry_count": ["<", retry_count],
		},
		pluck="name",
	)

	total = len(invoices)
	_update_job_record(
		job_id=job_id, total_records=total, percentage_complete=20,
		last_message=f"Found {total} unsent invoice(s) for {company}",
	)

	errors = []
	for idx, invoice in enumerate(invoices, 1):
		try:
			send_sales_invoice_to_digitax_automatic(invoice)
		except Exception as e:
			errors.append(f"{invoice}: {e}")

		percentage = 20 + int((idx / total) * 70) if total else 90
		_update_job_record(
			job_id=job_id, processed_records=idx, errors=len(errors),
			percentage_complete=percentage, last_message=f"Processed {idx}/{total}: {invoice}",
		)

	still_unsent = (
		frappe.db.count(
			"Sales Invoice",
			{"docstatus": 1, "custom_sent_to_digitax": 0, "company": company, "name": ["in", invoices]},
		)
		if invoices
		else 0
	)
	sent = max(total - still_unsent, 0)

	summary = f"Invoices synced to Digitax: Attempted={total}, Sent={sent}, Still Unsent={still_unsent}"
	frappe.logger().info(summary)

	return {
		"summary": summary,
		"created": sent,
		"updated": 0,
		"skipped": still_unsent,
		"errors": errors,
	}


def sync_customers_from_digitax(company, start_time=None, end_time=None):
	"""
	Sync customers from Digitax to ERPNext.

	TODO: Implement when Digitax customer endpoint is provided.
	"""
	from titan_digitax.titan_digitax.utils.digitax_client import DigitaxClient

	client = DigitaxClient(company)

	# Placeholder - will be implemented when endpoint is provided
	customers = client.fetch_customers(start_time, end_time)

	created = 0
	updated = 0
	skipped = 0
	errors = []

	# TODO: Loop through customers and create/update in ERPNext
	# for customer_data in customers:
	#     try:
	#         # Map and save customer
	#         pass
	#     except Exception as e:
	#         errors.append(str(e))

	summary = f"Customers synced: Created={created}, Updated={updated}, Skipped={skipped}, Errors={len(errors)}"
	frappe.logger().info(summary)

	return {
		"summary": summary,
		"created": created,
		"updated": updated,
		"skipped": skipped,
		"errors": errors
	}


def get_or_create_digitax_item(item_data, company):
	"""
	Get or create a Digitax Item from DigiTax's own catalogue data (inbound pull-sync).

	Priority:
	1. Check by digitax_id first (this company's authoritative match).
	2. Check by item_name for this company (docname is deterministic —
	   "{item_name}({company_abbr})" — so this is really the same record unless the
	   id was reassigned).
	3. If both exist but don't match, flag as error for review.

	Args:
		item_data (dict): Item data from Digitax API
		company: The company this DigiTax account belongs to

	Returns:
		dict: Result with status (created/updated/exists) and item_name
	"""
	from frappe.utils import now_datetime

	digitax_id = item_data.get("id")
	item_name = (item_data.get("item_name") or "").strip()

	if not item_name:
		frappe.throw(f"Item name is required. Digitax ID: {digitax_id}")

	abbr = frappe.get_cached_value("Company", company, "abbr")
	docname_by_name = f"{item_name}({abbr})"

	# PRIMARY CHECK: Find by item_name for this company (deterministic docname)
	item_by_name = None
	if frappe.db.exists("Digitax Item", docname_by_name):
		item_by_name = frappe.get_doc("Digitax Item", docname_by_name)

	# SECONDARY CHECK: Find by Digitax ID, scoped to this company
	item_by_digitax_id = None
	if digitax_id:
		existing_name = frappe.db.get_value(
			"Digitax Item", {"digitax_id": digitax_id, "company": company}, "name"
		)
		if existing_name:
			item_by_digitax_id = frappe.get_doc("Digitax Item", existing_name)

	# CONFLICT DETECTION: If both exist but don't match, flag error
	if item_by_name and item_by_digitax_id:
		if item_by_name.name != item_by_digitax_id.name:
			error_msg = (
				f"DATA CONFLICT: Digitax Item '{docname_by_name}' exists but with a different "
				f"Digitax ID. Digitax ID '{digitax_id}' is assigned to '{item_by_digitax_id.name}'. "
				f"Please review and resolve this conflict manually."
			)
			frappe.logger().error(error_msg)
			frappe.log_error(error_msg, "Digitax Item Sync - Data Conflict")
			frappe.throw(error_msg)

	existing_item = item_by_name or item_by_digitax_id

	# Prepare field values (excluding timestamp fields from initial comparison)
	item_fields = {
		"item_name": item_name,
		"company": company,
		"item_class_code": item_data.get("item_class_code"),
		"item_type_code": item_data.get("item_type_code"),
		"tax_type_code": item_data.get("tax_type_code"),
		"origin_nation_code": item_data.get("origin_nation_code"),
		"package_unit_code": item_data.get("package_unit_code"),
		"quantity_unit_code": item_data.get("quantity_unit_code"),
		"default_unit_price": item_data.get("default_unit_price") or 0,
		"enabled": 1 if item_data.get("active") else 0,
		"digitax_id": digitax_id,
		"etims_item_code": item_data.get("etims_item_code"),
		"synced": 1,
	}

	if existing_item:
		current_digitax_id = existing_item.get("digitax_id")

		if current_digitax_id and digitax_id and current_digitax_id != digitax_id:
			frappe.logger().warning(
				f"SKIPPED: Digitax Item '{existing_item.name}' has Digitax ID '{current_digitax_id}', "
				f"but incoming data has '{digitax_id}'. Not updating. "
				f"Change the Digitax ID manually in UI if needed."
			)
			return {"status": "skipped", "item_name": existing_item.name, "reason": "digitax_id_mismatch"}

		fields_to_update = {}
		for field, value in item_fields.items():
			if field in ("item_name", "company"):
				# set_only_once identity fields — never rewritten post-creation.
				continue
			if field == "digitax_id" and current_digitax_id == digitax_id:
				continue

			current_value = existing_item.get(field)

			if value == "" and current_value is None:
				continue
			if value is None and current_value == "":
				continue
			if isinstance(value, (bool, int)) and isinstance(current_value, (bool, int)):
				if bool(value) == bool(current_value):
					continue

			if current_value != value:
				fields_to_update[field] = value

		if fields_to_update:
			for field, value in fields_to_update.items():
				existing_item.set(field, value)

			existing_item.last_sync = now_datetime()
			existing_item.flags.ignore_permissions = True
			existing_item.save()
			frappe.logger().info(
				f"Updated Digitax Item: {existing_item.name} "
				f"(changed fields: {', '.join(fields_to_update.keys())})"
			)
			return {"status": "updated", "item_name": existing_item.name}
		else:
			frappe.logger().debug(f"No changes for Digitax Item: {existing_item.name}")
			return {"status": "exists", "item_name": existing_item.name}

	else:
		try:
			new_item = frappe.get_doc({
				"doctype": "Digitax Item",
				**item_fields,
				"last_sync": now_datetime(),
			})
			new_item.flags.ignore_permissions = True
			new_item.insert()
			frappe.logger().info(f"Created Digitax Item: {new_item.name}")
			return {"status": "created", "item_name": new_item.name}

		except frappe.DuplicateEntryError:
			frappe.logger().info(f"Digitax Item {docname_by_name} already exists (created concurrently)")
			return {"status": "exists", "item_name": docname_by_name}


def sync_items_from_digitax(company, start_time=None, end_time=None, job_id=None):
	"""
	Sync items from Digitax to ERPNext.

	Fetches items from Digitax API and creates/updates them using get_or_create_digitax_item,
	ticking per-item progress on job_id if given.

	Args:
		company: The company whose Digitax Company Settings to sync with
		start_time: Optional filter for items modified after this time
		end_time: Optional filter for items modified before this time
		job_id: Optional Digitax Sync Job name to report progress against

	Returns:
		dict: Summary with created, updated, skipped, and error counts
	"""
	from titan_digitax.titan_digitax.utils.digitax_client import DigitaxClient

	frappe.logger().info("=" * 80)
	frappe.logger().info(f"DIGITAX ITEM SYNC: Starting sync from Digitax for {company}")
	frappe.logger().info("=" * 80)

	client = DigitaxClient(company)

	# Fetch items from Digitax
	try:
		items = client.fetch_items(start_time, end_time)
		frappe.logger().info(f"Fetched {len(items)} items from Digitax")
	except Exception as e:
		error_msg = f"Failed to fetch items from Digitax: {str(e)}"
		frappe.logger().error(error_msg)
		frappe.log_error(frappe.get_traceback(), "Digitax Item Sync Error")
		_update_job_record(job_id=job_id, errors=1, last_message=error_msg)
		return {
			"summary": error_msg,
			"created": 0,
			"updated": 0,
			"skipped": 0,
			"errors": [error_msg]
		}

	total = len(items)
	_update_job_record(
		job_id=job_id, total_records=total, percentage_complete=20,
		last_message=f"Fetched {total} item(s) from Digitax",
	)

	created = 0
	updated = 0
	skipped = 0
	errors = []

	# Process each item using helper function (similar to MIS pattern)
	for idx, item_data in enumerate(items, 1):
		try:
			digitax_id = item_data.get("id")
			item_name = item_data.get("item_name")

			if not item_name:
				error = f"Missing item_name for Digitax ID: {digitax_id}"
				frappe.logger().warning(error)
				errors.append(error)
				skipped += 1
			else:
				# Use helper function to create/update item
				result = get_or_create_digitax_item(item_data, company)

				if result["status"] == "created":
					created += 1
				elif result["status"] == "updated":
					updated += 1
				elif result["status"] == "skipped":
					# Skipped due to Digitax ID mismatch
					skipped += 1
				else:  # exists (no changes)
					skipped += 1

		except Exception as e:
			error_msg = f"Error processing item {item_data.get('item_name', 'Unknown')}: {str(e)}"
			frappe.logger().error(error_msg)
			frappe.log_error(frappe.get_traceback(), f"Digitax Item Sync Error - {item_data.get('item_name', 'Unknown')}")
			errors.append(error_msg)

		percentage = 20 + int((idx / total) * 70) if total else 90
		_update_job_record(
			job_id=job_id, processed_records=idx, skipped_records=skipped, errors=len(errors),
			percentage_complete=percentage,
			last_message=f"Processed {idx}/{total}: {item_data.get('item_name', 'Unknown')}",
		)

	# Commit all changes
	frappe.db.commit()

	summary = f"Items synced from Digitax: Created={created}, Updated={updated}, Skipped={skipped}, Errors={len(errors)}"
	frappe.logger().info("=" * 80)
	frappe.logger().info(summary)
	frappe.logger().info("=" * 80)

	return {
		"summary": summary,
		"created": created,
		"updated": updated,
		"skipped": skipped,
		"errors": errors
	}


# Scheduler entry points
def digitax_sync_customers_hourly():
	"""
	Hourly scheduler entry point for customer sync.
	Pulls customers from last 1 hour, once per enabled company.
	"""
	from datetime import timedelta
	from frappe.utils import now_datetime
	from titan_digitax.titan_digitax.utils.company_config import get_enabled_digitax_companies

	end_time = now_datetime()
	start_time = end_time - timedelta(hours=1)

	for company in get_enabled_digitax_companies():
		try:
			result = run_digitax_sync("Customers", company, start_time, end_time)
			frappe.logger().info(f"Scheduled customer sync completed for {company}: {result.get('summary')}")
		except Exception as e:
			frappe.log_error(
				title=f"Scheduled Digitax Customer Sync Failed - {company}",
				message=frappe.get_traceback()
			)


def digitax_sync_items_hourly():
	"""
	Hourly scheduler entry point for items sync.
	Pulls items from last 1 hour, once per enabled company. Each company's run gets its
	own job row so it shows up in the same queue as manually-triggered syncs.
	"""
	from datetime import timedelta
	from frappe.utils import now_datetime
	from titan_digitax.titan_digitax.utils.company_config import get_enabled_digitax_companies

	end_time = now_datetime()
	start_time = end_time - timedelta(hours=1)

	for company in get_enabled_digitax_companies():
		job_id = str(uuid.uuid4())
		_update_job_record(
			job_id=job_id, direction=DIRECTION_FROM_DIGITAX, sync_type="Items", company=company,
			status="Running", started_at=now(), percentage_complete=0,
			last_message="Scheduled items sync started",
		)
		try:
			result = sync_items_from_digitax(company, start_time, end_time, job_id=job_id)
			summary = result.get("summary")
			_update_job_record(
				job_id=job_id, status="Success", ended_at=now(), percentage_complete=100,
				last_result=summary, last_message=summary, errors=len(result.get("errors") or []),
			)
			frappe.logger().info(f"Scheduled items sync completed for {company}: {summary}")
		except Exception:
			error_trace = frappe.get_traceback()
			_update_job_record(
				job_id=job_id, status="Failed", ended_at=now(),
				last_result=f"Sync failed: {error_trace}", last_message="Scheduled items sync failed",
				error_traceback=error_trace,
			)
			frappe.log_error(
				title=f"Scheduled Digitax Items Sync Failed - {company}",
				message=error_trace,
			)
