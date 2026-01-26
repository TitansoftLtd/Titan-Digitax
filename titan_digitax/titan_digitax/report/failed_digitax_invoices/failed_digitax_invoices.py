# Copyright (c) 2026, Titansoft Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	"""Define report columns"""
	return [
		{
			"fieldname": "name",
			"label": _("Invoice ID"),
			"fieldtype": "Link",
			"options": "Sales Invoice",
			"width": 180
		},
		{
			"fieldname": "posting_date",
			"label": _("Date"),
			"fieldtype": "Date",
			"width": 100
		},
		{
			"fieldname": "customer",
			"label": _("Customer"),
			"fieldtype": "Link",
			"options": "Customer",
			"width": 200
		},
		{
			"fieldname": "grand_total",
			"label": _("Grand Total"),
			"fieldtype": "Currency",
			"width": 120
		},
		{
			"fieldname": "is_return",
			"label": _("Is Return"),
			"fieldtype": "Check",
			"width": 80
		},
		{
			"fieldname": "custom_retry_count",
			"label": _("Retry Count"),
			"fieldtype": "Int",
			"width": 100
		},
		{
			"fieldname": "custom_error_message",
			"label": _("Error Message"),
			"fieldtype": "Data",
			"width": 300
		},
		{
			"fieldname": "modified",
			"label": _("Last Modified"),
			"fieldtype": "Datetime",
			"width": 150
		},
		{
			"fieldname": "company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"width": 150
		}
	]


def get_data(filters):
	"""
	Get Sales Invoices that failed to send to Digitax.
	Failed = custom_sent_to_digitax = 0 AND custom_error_message is not empty
	"""
	conditions = []
	values = {}
	
	# Core condition: Failed invoices (not sent but have error message)
	conditions.append("si.docstatus = 1")
	conditions.append("si.custom_sent_to_digitax = 0")
	conditions.append("si.custom_error_message IS NOT NULL")
	conditions.append("si.custom_error_message != ''")
	
	# Optional filters
	if filters.get("company"):
		conditions.append("si.company = %(company)s")
		values["company"] = filters.get("company")
	
	if filters.get("from_date"):
		conditions.append("si.posting_date >= %(from_date)s")
		values["from_date"] = filters.get("from_date")
	
	if filters.get("to_date"):
		conditions.append("si.posting_date <= %(to_date)s")
		values["to_date"] = filters.get("to_date")
	
	if filters.get("customer"):
		conditions.append("si.customer = %(customer)s")
		values["customer"] = filters.get("customer")
	
	if filters.get("is_return") is not None:
		conditions.append("si.is_return = %(is_return)s")
		values["is_return"] = filters.get("is_return")
	
	# Max retry filter
	if filters.get("max_retry_count"):
		conditions.append("si.custom_retry_count <= %(max_retry_count)s")
		values["max_retry_count"] = filters.get("max_retry_count")
	
	where_clause = " AND ".join(conditions)
	
	query = f"""
		SELECT
			si.name,
			si.posting_date,
			si.customer,
			si.grand_total,
			si.is_return,
			si.custom_retry_count,
			si.custom_error_message,
			si.modified,
			si.company
		FROM
			`tabSales Invoice` si
		WHERE
			{where_clause}
		ORDER BY
			si.modified DESC
	"""
	
	data = frappe.db.sql(query, values, as_dict=1)
	
	return data
