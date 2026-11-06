# Data quality report

This report describes the audit of the supplied original CSV. The app preserves source row values in each lead's `original_json` snapshot and reports the number of distinct original lead IDs with one or more unresolved findings.

**Records analyzed:** 25  
**Rows flagged:** 9  
**Issue findings:** 11

| Lead(s) | Finding | Why it is flagged | Treatment |
|---|---|---|---|
| L01, L23 | Duplicate company | Same normalized company name, contact, and deal details. | Show both; do not merge automatically. |
| L02, L03 | Possible duplicate company | Company names differ, but contact, email, phone, stage, and value match. | Human review; names are retained as imported. |
| L06 | Missing phone | Phone field is empty. | Request verified value; no guess. |
| L06 | Contact note mismatch | Notes claim no phone or email is on file, but an email is populated. | Verify the source enquiry. |
| L12 | Stage/notes mismatch | Paid means full amount received; notes say only 50% advance and a second payment is still due. | Confirm payment and stage. |
| L18 | Possible email typo | The note explicitly says to check the unusual spelling. | Verify with the contact; no auto-correction. |
| L24 | Missing contact name | Contact name is blank. | Request verified value. |
| L24 | Missing email | Email is blank (phone is populated). | Request verified value. |
| L25 | Date order | Follow-up 21 Oct 2026 precedes last contact 28 Oct 2026. | Keep both original dates and review; no replacement is invented. |

The 11 issue findings affect 9 unique source rows because L06 and L24 each have two findings. Overdue status is a separate time-dependent metric, not included in the 9-row data-quality result. The audit does not silently change, archive, or delete any sample record.
