frappe.ui.form.on("Sales Invoice", {
	refresh: function (frm) {
		// Show button for all submitted invoices (duplicate handling is done on backend)
		if (frm.doc.docstatus === 1) {
			add_send_to_digitax_button(frm);
		}
	},
});

const add_send_to_digitax_button = (frm) => {
	frm.add_custom_button(__("Send to Digitax"), function () {		
		frappe.call({
			method: "titan_digitax.titan_digitax.utils.sales.send_sales_invoice_to_digitax",
			args: {
				docname: frm.doc.name,
			},
			freeze: true,
			freeze_message: __("Sending Sales Invoice to Digitax..."),
			callback: function (r) {
				console.log("=== DIGITAX: Response received:", r);
				
				// Python exception occurred
				if (r.exc) {
					console.error("=== DIGITAX: Python exception occurred:", r.exc);
					frappe.msgprint({
						title: __("Error sending to Digitax"),
						message: __("Error sending to Digitax. Check error log and digitax_integration.log file."),
						indicator: "red",
					});
					frm.reload_doc();
					return;
				}

				// Check if message exists and has the expected structure
				if (r.message) {
					console.log("=== DIGITAX: Message object:", r.message);
					
					// Check if request was skipped
					if (r.message.skipped) {
						frappe.msgprint({
							title: __("Skipped"),
							message: __("Request skipped: {0}<br><br>Check digitax_integration.log for details.", [
								r.message.reason || "Unknown reason",
							]),
							indicator: "orange",
						});
						return;
					}
					
					// Check for general error
					if (r.message.error) {
						frappe.msgprint({
							title: __("Error"),
							message: __("Error: {0}<br><br>Check digitax_integration.log for details.", [
								r.message.message || r.message.error,
							]),
							indicator: "red",
						});
						frm.reload_doc();
						return;
					}
					
					// Check for API error response (has 'code' property indicating error)
					// But skip 409 as it means invoice already exists (handled as success)
					if (r.message.code && r.message.code !== '409') {
						frappe.msgprint({
							title: __("Digitax API Error"),
							message: __("Digitax Error: {0}<br><br>Check digitax_integration.log for details.", [
								r.message.message || "Unknown error",
							]),
							indicator: "orange",
						});
						frm.reload_doc();
						return;
					}

					// Success - check for expected success properties
					if (r.message.id && r.message.status) {
						// Check if invoice already existed in Digitax
						if (r.message.already_exists) {
							frappe.msgprint({
								title: __("Already Synced"),
								message: __("This invoice was already sent to Digitax previously.<br><br>Sale ID: {0}<br>Status: {1}", [
									r.message.id,
									r.message.status,
								]),
								indicator: "green",
							});
						} else {
							frappe.msgprint({
								title: __("Success"),
								message: __("Sales Invoice sent to Digitax successfully!<br><br>Sale ID: {0}<br>Status: {1}", [
									r.message.id,
									r.message.status,
								]),
								indicator: "green",
							});
						}
						frm.reload_doc();
						return;
					}
					
					// Unknown response format
					console.warn("=== DIGITAX: Unknown response format:", r.message);
					frappe.msgprint({
						title: __("Unknown Response"),
						message: __("Received unexpected response from Digitax. Check digitax_integration.log for details."),
						indicator: "orange",
					});
				} else {
					console.warn("=== DIGITAX: No message in response");
					frappe.msgprint({
						title: __("No Response"),
						message: __("No response received from Digitax. Check digitax_integration.log for details."),
						indicator: "orange",
					});
				}
				
				frm.reload_doc();
			},
			error: function (r) {
				console.error("=== DIGITAX: AJAX error:", r);
				// This catches network errors or server errors
				frappe.msgprint({
					title: __("Connection Error"),
					message: __("Failed to connect to Digitax. Check digitax_integration.log for details."),
					indicator: "red",
				});
				frm.reload_doc();
			},
		});
	}, __("Digitax Actions"));
};
