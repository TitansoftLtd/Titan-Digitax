# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now, now_datetime


class DigitaxSyncJob(Document):
	"""DocType for tracking Digitax sync jobs."""
	pass


@frappe.whitelist()
def execute_digitax_sync(sync_type=None, company=None):
	"""
	Entry point for UI-triggered sync.
	Updates the single DocType record and enqueues background task.

	Args:
		sync_type: The type of sync to execute (Customers or Items)
		company: The company whose Digitax Company Settings to sync with
	"""
	# Validate sync type is provided and not empty
	if not sync_type or sync_type.strip() == "":
		frappe.throw("Please select a Sync Type before executing.")

	if not company:
		frappe.throw("Please select a Company before executing.")

	# Validate sync type is a valid option
	valid_types = ["Customers", "Items"]
	if sync_type not in valid_types:
		frappe.throw(f"Invalid sync type: {sync_type}. Must be one of: {', '.join(valid_types)}")

	# Update status fields in the single document
	frappe.db.set_single_value("Digitax Sync Job", {
		"company": company,
		"status": "Running",
		"started_at": now_datetime(),
		"finished_at": None,
		"last_result": None,
		"error_traceback": None,
		"job_id": None
	})
	frappe.db.commit()

	# Enqueue background job
	job_id = frappe.enqueue(
		"titan_digitax.titan_digitax.doctype.digitax_sync_job.digitax_sync_job.run_digitax_sync_background",
		queue="long",
		timeout=3600,
		job_name=f"digitax_sync_{sync_type.lower()}",
		sync_type=sync_type,
		company=company,
	)

	# Save job ID (convert Job object to string)
	job_id_str = str(job_id.id) if hasattr(job_id, 'id') else str(job_id)
	frappe.db.set_single_value("Digitax Sync Job", "job_id", job_id_str)
	frappe.db.commit()

	return {
		"status": "success",
		"message": f"Sync job started for {sync_type}. Refresh to monitor status.",
		"job_id": job_id_str
	}


def run_digitax_sync_background(sync_type, company):
	"""
	Background worker for UI-triggered sync.
	Updates the single DocType document with results.
	"""
	try:
		# Call core sync function
		result = run_digitax_sync(sync_type, company)

		# Update job as completed
		frappe.db.set_single_value("Digitax Sync Job", {
			"status": "Completed",
			"finished_at": now_datetime(),
			"last_result": result.get("summary", "Sync completed successfully.")
		})
		frappe.db.commit()

	except Exception as e:
		# Update job as failed
		import traceback
		error_trace = traceback.format_exc()

		frappe.db.set_single_value("Digitax Sync Job", {
			"status": "Failed",
			"finished_at": now_datetime(),
			"last_result": f"Sync failed: {str(e)}",
			"error_traceback": error_trace
		})
		frappe.db.commit()

		# Log error
		frappe.log_error(
			title=f"Digitax Sync Failed - {sync_type}",
			message=error_trace
		)


def run_digitax_sync(sync_type, company, start_time=None, end_time=None):
	"""
	Core sync function that can be called by UI or scheduler.

	Args:
		sync_type: "Customers" or "Items"
		company: The company whose Digitax Company Settings to sync with
		start_time: Optional datetime for filtering (used by scheduler)
		end_time: Optional datetime for filtering (used by scheduler)

	Returns:
		dict with summary of sync results
	"""
	frappe.logger().info(f"Starting Digitax sync for type: {sync_type}, company: {company}")

	# Route to specific sync handler
	if sync_type == "Customers":
		return sync_customers_from_digitax(company, start_time, end_time)
	elif sync_type == "Items":
		return sync_items_from_digitax(company, start_time, end_time)
	else:
		frappe.throw(f"Unknown sync type: {sync_type}")


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


def _sync_item_price_to_standard_selling(item_name, price):
	"""
	Helper function to create/update Item Price for Standard Selling price list.
	
	Args:
		item_name (str): Item code
		price (float): Price to set
	"""
	if not price or price <= 0:
		return
	
	price_list = "Standard Selling"
	
	# Check if Item Price already exists
	existing_price = frappe.db.get_value(
		"Item Price",
		{
			"item_code": item_name,
			"price_list": price_list
		},
		["name", "price_list_rate"],
		as_dict=True
	)
	
	if existing_price:
		# Update existing price if it changed
		if existing_price.price_list_rate != price:
			frappe.db.set_value(
				"Item Price",
				existing_price.name,
				"price_list_rate",
				price
			)
			frappe.logger().info(
				f"Updated Standard Selling price for {item_name}: "
				f"{existing_price.price_list_rate} → {price}"
			)
	else:
		# Create new Item Price
		try:
			item_price = frappe.get_doc({
				"doctype": "Item Price",
				"item_code": item_name,
				"price_list": price_list,
				"price_list_rate": price,
				"currency": "KES"
			})
			item_price.insert(ignore_permissions=True)
			frappe.logger().info(
				f"Created Standard Selling price for {item_name}: {price}"
			)
		except Exception as e:
			frappe.logger().error(
				f"Failed to create Item Price for {item_name}: {str(e)}"
			)


def get_or_create_digitax_item(item_data):
	"""
	Get or create item from Digitax data.
	Similar pattern to MIS get_or_create_item but with Digitax-specific fields.
	
	Priority:
	1. Check by item_name first (prevents duplicate names)
	2. Check by custom_digitax_id (secondary validation)
	3. If both exist but don't match, flag as error for review
	
	Args:
		item_data (dict): Item data from Digitax API
	
	Returns:
		dict: Result with status (created/updated/exists) and item_name
	"""
	from frappe.utils import now_datetime
	
	digitax_id = item_data.get("id")
	item_name = (item_data.get("item_name") or "").strip()
	
	if not item_name:
		frappe.throw(f"Item name is required. Digitax ID: {digitax_id}")
	
	# PRIMARY CHECK: Find by item_name (prevents duplicate names)
	item_by_name = None
	if frappe.db.exists("Item", item_name):
		item_by_name = frappe.get_doc("Item", item_name)
	
	# SECONDARY CHECK: Find by Digitax ID
	item_by_digitax_id = None
	if digitax_id:
		existing_name = frappe.db.get_value("Item", {"custom_digitax_id": digitax_id}, "name")
		if existing_name:
			item_by_digitax_id = frappe.get_doc("Item", existing_name)
	
	# CONFLICT DETECTION: If both exist but don't match, flag error
	if item_by_name and item_by_digitax_id:
		if item_by_name.name != item_by_digitax_id.name:
			error_msg = (
				f"DATA CONFLICT: Item name '{item_name}' exists but with different Digitax ID. "
				f"Digitax ID '{digitax_id}' is assigned to item '{item_by_digitax_id.name}'. "
				f"Please review and resolve this conflict manually."
			)
			frappe.logger().error(error_msg)
			frappe.log_error(error_msg, "Digitax Item Sync - Data Conflict")
			frappe.throw(error_msg)
	
	# Use item_by_name as priority (if it exists)
	existing_item = item_by_name or item_by_digitax_id
	
	# Prepare field values (excluding timestamp fields from initial comparison)
	item_fields = {
		# Standard ERPNext fields
		"item_name": item_name,
		"item_group": "Services",
		"stock_uom": "Nos",
		"is_stock_item": 1 if item_data.get("is_stock_item") else 0,
		"is_sales_item": 1,  # Always enable for sales
		"disabled": 0 if item_data.get("active") else 1,
		
		# Custom Digitax fields (editable codes)
		"custom_item_class_code": item_data.get("item_class_code"),
		"custom_tax_type_code": item_data.get("tax_type_code"),
		
		# Custom Digitax fields (read-only sync data)
		"custom_digitax_id": digitax_id,
		"custom_digitax_etims_item_code": item_data.get("etims_item_code"),
		"custom_digitax_item_type_code": item_data.get("item_type_code"),
		"custom_digitax_origin_nation_code": item_data.get("origin_nation_code"),
		"custom_digitax_package_unit_code": item_data.get("package_unit_code"),
		"custom_digitax_quantity_unit_code": item_data.get("quantity_unit_code"),
		"custom_digitax_active": 1 if item_data.get("active") else 0,
		"custom_digitax_status": item_data.get("status"),
		
		# Sync tracking (will only be updated if there are other changes)
		"custom_digitax_synced": 1,
	}
	
	if existing_item:
		# Check if Digitax ID matches before updating
		current_digitax_id = existing_item.get("custom_digitax_id")
		
		# If item already has a Digitax ID and it doesn't match, skip this item
		if current_digitax_id and current_digitax_id != digitax_id:
			frappe.logger().warning(
				f"SKIPPED: Item '{item_name}' has Digitax ID '{current_digitax_id}', "
				f"but incoming data has '{digitax_id}'. Not updating. "
				f"Change Digitax ID manually in UI if needed."
			)
			return {"status": "skipped", "item_name": existing_item.name, "reason": "digitax_id_mismatch"}
		
		# Digitax ID matches (or not set) - check for changes in other fields
		# Exclude custom_digitax_id from comparison if it's already set
		fields_to_update = {}
		for field, value in item_fields.items():
			# Skip digitax_id if already set and matches
			if field == "custom_digitax_id" and current_digitax_id == digitax_id:
				continue
			
			current_value = getattr(existing_item, field, None)
			
			# Normalize values for comparison (handle type mismatches)
			# Convert None to empty string for string fields
			if value == "" and current_value is None:
				continue
			if value is None and current_value == "":
				continue
			# Convert boolean/int comparisons (1 vs True, 0 vs False)
			if isinstance(value, (bool, int)) and isinstance(current_value, (bool, int)):
				if bool(value) == bool(current_value):
					continue
			
			# Check if value actually changed
			if current_value != value:
				fields_to_update[field] = value
		
		# Only update if there are actual changes
		if fields_to_update:
			# Apply all changed fields
			for field, value in fields_to_update.items():
				setattr(existing_item, field, value)
			
			# Update timestamp only when there are actual changes
			existing_item.custom_digitax_last_sync = now_datetime()
			
			existing_item.save(ignore_permissions=True)
			frappe.logger().info(
				f"Updated Digitax item: {existing_item.name} "
				f"(changed fields: {', '.join(fields_to_update.keys())})"
			)
			
			# Sync price to Item Price if available
			default_unit_price = item_data.get("default_unit_price")
			if default_unit_price:
				_sync_item_price_to_standard_selling(existing_item.name, default_unit_price)
			
			return {"status": "updated", "item_name": existing_item.name}
		else:
			# No changes detected - don't save
			frappe.logger().debug(f"No changes for item: {existing_item.name}")
			return {"status": "exists", "item_name": existing_item.name}
	
	else:
		# Create new item
		try:
			# Add timestamp for new items
			new_item = frappe.get_doc({
				"doctype": "Item",
				"item_code": item_name,
				**item_fields,
				"custom_digitax_last_sync": now_datetime()
			})
			new_item.insert(ignore_permissions=True)
			frappe.logger().info(f"Created Digitax item: {new_item.name}")
			
			# Sync price to Item Price if available
			default_unit_price = item_data.get("default_unit_price")
			if default_unit_price:
				_sync_item_price_to_standard_selling(new_item.name, default_unit_price)
			
			return {"status": "created", "item_name": new_item.name}
			
		except frappe.DuplicateEntryError:
			# Item was created by another process - just return the name
			frappe.logger().info(f"Item {item_name} already exists (created by concurrent process)")
			return {"status": "exists", "item_name": item_name}


def sync_items_from_digitax(company, start_time=None, end_time=None):
	"""
	Sync items from Digitax to ERPNext.

	Fetches items from Digitax API and creates/updates them using get_or_create_digitax_item.

	Args:
		company: The company whose Digitax Company Settings to sync with
		start_time: Optional filter for items modified after this time
		end_time: Optional filter for items modified before this time

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
		return {
			"summary": error_msg,
			"created": 0,
			"updated": 0,
			"skipped": 0,
			"errors": [error_msg]
		}
	
	created = 0
	updated = 0
	skipped = 0
	errors = []
	
	# Process each item using helper function (similar to MIS pattern)
	for item_data in items:
		try:
			digitax_id = item_data.get("id")
			item_name = item_data.get("item_name")
			
			if not item_name:
				error = f"Missing item_name for Digitax ID: {digitax_id}"
				frappe.logger().warning(error)
				errors.append(error)
				skipped += 1
				continue
			
			# Use helper function to create/update item
			result = get_or_create_digitax_item(item_data)
			
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
	Pulls items from last 1 hour, once per enabled company.
	"""
	from datetime import timedelta
	from frappe.utils import now_datetime
	from titan_digitax.titan_digitax.utils.company_config import get_enabled_digitax_companies

	end_time = now_datetime()
	start_time = end_time - timedelta(hours=1)

	for company in get_enabled_digitax_companies():
		try:
			result = run_digitax_sync("Items", company, start_time, end_time)
			frappe.logger().info(f"Scheduled items sync completed for {company}: {result.get('summary')}")
		except Exception as e:
			frappe.log_error(
				title=f"Scheduled Digitax Items Sync Failed - {company}",
				message=frappe.get_traceback()
			)
