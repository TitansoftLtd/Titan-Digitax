frappe.ui.form.on("Sales Invoice", {
	refresh: function (frm) {
		if (frm.doc.docstatus !== 1 || !frm.doc.company) {
			return;
		}

		frappe.call({
			method: "titan_digitax.titan_digitax.utils.company_config.get_company_digitax_status",
			args: { company: frm.doc.company },
			callback: function (r) {
				if (!r.message || !r.message.eligible) {
					return;
				}
				add_send_to_digitax_button(frm);
				add_print_digitax_button(frm);
				add_virtual_amendment_buttons(frm);
			},
		});
	},
});

const add_send_to_digitax_button = (frm) => {
	frm.add_custom_button(__("Send to Digitax"), () => send_to_digitax(frm, 0), __("Digitax Actions"));
};

// send_anyway is only ever 1 after the user confirms the server's
// requires_confirmation answer (company blocks automatic sending - D21).
const send_to_digitax = (frm, send_anyway) => {
	frappe.call({
		method: "titan_digitax.titan_digitax.utils.sales.send_sales_invoice_to_digitax",
		args: {
			docname: frm.doc.name,
			send_anyway: send_anyway,
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

				if (r.message.requires_confirmation) {
					frappe.confirm(r.message.message, () => send_to_digitax(frm, 1));
					return;
				}

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
};

const add_print_digitax_button = (frm) => {
	if (!frm.doc.custom_sent_to_digitax && !frm.doc.custom_sale_id) {
		return;
	}

	frm.add_custom_button(__("Print Digitax Invoice"), function () {
		frappe.utils.print(frm.doctype, frm.doc.name, "Sales Invoice(Digitax)");
	}, __("Digitax Actions"));
};

const add_virtual_amendment_buttons = (frm) => {
	if (frm.doc.is_return || !frm.doc.custom_sent_to_digitax) {
		return;
	}

	frappe.call({
		method: "titan_digitax.titan_digitax.utils.sales.get_digitax_virtual_amendment_status",
		args: {
			invoice_name: frm.doc.name,
		},
		callback: function (r) {
			const status = r.message || {};
			if (!status.can_create) {
				return;
			}

			if (status.can_send_reversal) {
				frm.add_custom_button(__("Send Virtual Reversal"), function () {
					prompt_virtual_amendment_reason(frm, "reversal");
				}, __("Digitax Actions"));
			}

			if (status.can_send_sale) {
				frm.add_custom_button(__("Send Corrected Virtual Sale"), function () {
					prompt_virtual_amendment_reason(frm, "sale");
				}, __("Digitax Actions"));
			}
		},
	});
};

const prompt_virtual_amendment_reason = (frm, action) => {
	const dialog = new frappe.ui.Dialog({
		title: __("Digitax Virtual Amendment Reason"),
		fields: [
			{
				fieldname: "correction_reason",
				fieldtype: "Small Text",
				label: __("Correction Reason"),
				reqd: 1,
				description: __("Example: Missing Customer PIN, wrong Customer PIN, or corrected customer details."),
			},
		],
		primary_action_label: __("Preview"),
		primary_action(values) {
			dialog.hide();
			preview_virtual_amendment(frm, action, values.correction_reason);
		},
	});

	dialog.show();
};

const preview_virtual_amendment = (frm, action, correction_reason) => {
	const preview_method = action === "reversal"
		? "titan_digitax.titan_digitax.utils.sales.preview_virtual_digitax_reversal"
		: "titan_digitax.titan_digitax.utils.sales.preview_virtual_digitax_sale";

	frappe.call({
		method: preview_method,
		args: {
			invoice_name: frm.doc.name,
			correction_reason,
		},
		freeze: true,
		freeze_message: __("Preparing Digitax amendment preview..."),
		callback: function (r) {
			if (!r.message) {
				return;
			}

			show_virtual_amendment_preview(frm, action, correction_reason, r.message);
		},
	});
};

const show_virtual_amendment_preview = (frm, action, correction_reason, preview) => {
	const dialog = new frappe.ui.Dialog({
		title: __(preview.action || "Digitax Virtual Amendment"),
		size: "large",
		fields: [
			{
				fieldname: "preview_html",
				fieldtype: "HTML",
				options: build_preview_html(preview),
			},
		],
		primary_action_label: __("Confirm Send"),
		primary_action() {
			dialog.hide();
			send_virtual_amendment(frm, action, correction_reason);
		},
		secondary_action_label: __("Cancel"),
		secondary_action() {
			dialog.hide();
		},
	});

	dialog.show();
};

const send_virtual_amendment = (frm, action, correction_reason) => {
	const send_method = action === "reversal"
		? "titan_digitax.titan_digitax.utils.sales.send_virtual_digitax_reversal"
		: "titan_digitax.titan_digitax.utils.sales.send_virtual_digitax_sale";

	frappe.call({
		method: send_method,
		args: {
			invoice_name: frm.doc.name,
			correction_reason,
		},
		freeze: true,
		freeze_message: __("Sending Digitax virtual amendment..."),
		callback: function (r) {
			const response = r.message || {};
			if (response.success) {
				frappe.msgprint({
					title: __("Digitax Amendment Sent"),
					message: __("Digitax amendment sent successfully.<br><br>Sale ID: {0}<br>Status: {1}", [
						response.id || "",
						response.status || "Sent",
					]),
					indicator: "green",
				});
			} else {
				frappe.msgprint({
					title: __("Digitax Amendment Failed"),
					message: __(response.message || response.error || "Digitax amendment failed. Check the amendment row for details."),
					indicator: "red",
				});
			}

			frm.reload_doc();
		},
	});
};

const build_preview_html = (preview) => {
	const before = preview.before || {};
	const after = preview.after || {};
	const keys = Array.from(new Set(Object.keys(before).concat(Object.keys(after))));
	const rows = keys.map((key) => {
		return `
			<tr>
				<td>${escape_html(key)}</td>
				<td>${escape_html(before[key] || "")}</td>
				<td>${escape_html(after[key] || "")}</td>
			</tr>
		`;
	}).join("");

	return `
		<div class="digitax-amendment-preview">
			<p><strong>${__("Invoice")}:</strong> ${escape_html(preview.invoice_name || "")}</p>
			<p><strong>${__("Customer")}:</strong> ${escape_html(preview.customer || "")}</p>
			<p><strong>${__("New Trader Invoice No.")}:</strong> ${escape_html(preview.trader_invoice_number || "")}</p>
			<p><strong>${__("Correction Reason")}:</strong> ${escape_html(preview.correction_reason || "")}</p>
			<table class="table table-bordered">
				<thead>
					<tr>
						<th>${__("Field")}</th>
						<th>${__("Before")}</th>
						<th>${__("After")}</th>
					</tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>
			<div class="alert alert-warning">
				${escape_html(preview.warning || "")}
			</div>
		</div>
	`;
};

const escape_html = (value) => {
	if (value === null || value === undefined) {
		return "";
	}

	return String(value)
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;")
		.replace(/'/g, "&#039;");
};
