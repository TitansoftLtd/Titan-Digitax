// Copyright (c) 2026, Titansoft Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Failed Digitax Invoices"] = {
	"filters": [
		{
			"fieldname": "company",
			"label": __("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"default": frappe.defaults.get_user_default("Company")
		},
		{
			"fieldname": "from_date",
			"label": __("From Date"),
			"fieldtype": "Date",
			"default": frappe.datetime.add_days(frappe.datetime.get_today(), -30)
		},
		{
			"fieldname": "to_date",
			"label": __("To Date"),
			"fieldtype": "Date",
			"default": frappe.datetime.get_today()
		},
		{
			"fieldname": "customer",
			"label": __("Customer"),
			"fieldtype": "Link",
			"options": "Customer"
		},
		{
			"fieldname": "is_return",
			"label": __("Is Return"),
			"fieldtype": "Select",
			"options": "\nYes\nNo",
			"on_change": function() {
				var is_return = frappe.query_report.get_filter_value('is_return');
				if (is_return === "Yes") {
					frappe.query_report.set_filter_value('is_return', 1);
				} else if (is_return === "No") {
					frappe.query_report.set_filter_value('is_return', 0);
				}
			}
		},
		{
			"fieldname": "max_retry_count",
			"label": __("Max Retry Count"),
			"fieldtype": "Int",
			"description": __("Show invoices with retry count less than or equal to this value")
		}
	],
	"formatter": function(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		
		// Highlight rows based on retry count
		if (column.fieldname === "custom_retry_count") {
			if (data.custom_retry_count >= 5) {
				value = `<span style="color: red; font-weight: bold;">${data.custom_retry_count}</span>`;
			} else if (data.custom_retry_count >= 3) {
				value = `<span style="color: orange; font-weight: bold;">${data.custom_retry_count}</span>`;
			}
		}
		
		// Truncate long error messages for better readability
		if (column.fieldname === "custom_error_message" && data.custom_error_message) {
			if (data.custom_error_message.length > 100) {
				value = `<span title="${data.custom_error_message}">${data.custom_error_message.substring(0, 100)}...</span>`;
			}
		}
		
		return value;
	}
};
