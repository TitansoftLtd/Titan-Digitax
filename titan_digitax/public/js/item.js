// Copyright (c) 2026, Titan and contributors
// For license information, please see license.txt

frappe.ui.form.on('Item', {
	refresh: function(frm) {
		// Add "Sync to Digitax" button
		if (!frm.is_new()) {
			frm.add_custom_button(__('Sync to Digitax'), function() {
				sync_item_to_digitax(frm);
			}, __('Digitax Actions'));
		}
	}
});

function sync_item_to_digitax(frm) {
	// Registration is per company: each company registers the item in its own DigiTax
	// catalogue and gets its own id back, so ask which company this sync is for.
	frappe.prompt(
		[
			{
				label: __('Company'),
				fieldname: 'company',
				fieldtype: 'Link',
				options: 'Company',
				reqd: 1,
				get_query: () => ({ filters: { is_group: 0 } }),
			},
		],
		function (values) {
			const already = (frm.doc.custom_digitax_registrations || []).find(
				(r) => r.company === values.company && r.digitax_id
			);
			if (already) {
				frappe.msgprint({
					title: __('Already Synced'),
					indicator: 'blue',
					message: __('This item is already synced to Digitax for {0} (ID {1})', [
						values.company,
						already.digitax_id,
					]),
				});
				return;
			}

			frappe.call({
				method: 'titan_digitax.titan_digitax.utils.items.sync_item_to_digitax',
				args: {
					item_name: frm.doc.name,
					company: values.company
				},
				freeze: true,
				freeze_message: __('Syncing to Digitax...'),
				callback: function(r) {
					if (r.message && r.message.status === 'success') {
						frappe.show_alert({
							message: __(r.message.message),
							indicator: 'green'
						}, 5);
						
						// Reload form to show updated Digitax fields
						frm.reload_doc();
					}
				},
				error: function(r) {
					// Error already shown by frappe.throw
					frappe.show_alert({
						message: __('Failed to sync to Digitax. Check error log.'),
						indicator: 'red'
					}, 5);
				}
			});
		}
	);
}
