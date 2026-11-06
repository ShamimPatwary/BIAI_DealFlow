# Data dictionary

Source: `sample-data/intern/task2-corporate-leads/corporate-leads.csv` (25 fictional records). Original column strings are stored as JSON on each lead before edits.

| CSV column | Stored field | Use and validation |
|---|---|---|
| lead id | `id` | Stable source row identifier (L01–L25); primary key. |
| company | `company` | Required company display name; normalized only while detecting exact-name duplicates. |
| industry | `industry` | Optional category, used in filters. |
| contact name | `contact` | Required contact field; whitespace-only is treated as missing. |
| contact role | `role` | Optional role/title. |
| contact email | `email` | Contact field; required by this dataset audit when absent; syntax checked when present. |
| contact phone | `phone` | Contact field; required by this dataset audit when absent. |
| source | `source` | Original lead source. |
| stage | `stage` | Must match one of the seven supplied stages. Paid and Lost are terminal. |
| deal value (BDT) | `value` | Numeric BDT; negative values are invalid. Zero is allowed. |
| last contact | `last` | Optional ISO date. Invalid non-empty source date is flagged. |
| next follow-up | `next` | Optional ISO date. A date before last contact is flagged; past dates are overdue only in non-terminal stages. |
| notes | `notes` | Original context used in follow-up generation and source inconsistency checks. |
| (not in CSV) | `owner` | Initialized as `Unassigned`; no owner field was supplied. |

The source CSV has no created date, meeting date, or invoice date, so no validation is invented for those relationships. An absent email or phone is individually reported, but the app does not infer a missing value. Follow-up timestamps use the `Asia/Dhaka` timezone by default.
