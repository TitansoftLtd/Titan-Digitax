// Copyright (c) 2026, Titan and contributors
// For license information, please see license.txt

frappe.ui.form.on('Digitax Item', {
	refresh: function(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__('Sync to Digitax'), function() {
				sync_digitax_item(frm);
			}).addClass('btn-primary');
		}

		if (frm.doc.synced) {
			frm.page.set_indicator(__('Synced'), 'green');
		} else {
			frm.page.set_indicator(__('Not Synced'), 'orange');
		}
	}
});

function sync_digitax_item(frm) {
	frappe.call({
		method: 'titan_digitax.titan_digitax.utils.digitax_item_sync.sync_digitax_item',
		args: { digitax_item_name: frm.doc.name },
		freeze: true,
		freeze_message: __('Syncing to Digitax...'),
		callback: function(r) {
			if (r.message) {
				frappe.show_alert({
					message: __('{0}: {1}', [frm.doc.name, r.message.status]),
					indicator: 'green'
				}, 5);
				frm.reload_doc();
			}
		}
	});
}
