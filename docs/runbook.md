# Client Data Intake Runbook

Sample runbook written for this portfolio project. The team, clients and service levels are fictional.

## Monthly calendar

Client files for a month are due by the 10th of the following month. Intake runs every business day from the 1st to the 15th. The control run that decides what loads for the month happens on the 15th. Held files must be corrected and resent within five business days of the follow-up email. Billing and valuation teams receive the loaded data on the 20th.

## Receiving a file

Every file that lands on the inbound share is registered before anyone opens it. Registration records the file name, client, file type, period, size, arrival time and a SHA-256 hash. A file whose hash matches a file already received is rejected as a duplicate, even if it was renamed. Files are copied to the archive under client and month, stamped with their arrival time, and the original is never edited. Unrecognised file names go to an unrecognised folder and the client is asked to resend with the agreed name.

## Late and missing files

A file received after the due date is loaded as normal but flagged late, and lateness counts against the client's timeliness score. A file not received by the due date is listed as missing on the control run, and the client follow-up asks for it with the original due date. Two missing months in a row for the same client are escalated to the Client Administration manager.

## Mapping a client layout

Each client's layout is described once in the client configuration: which source column feeds each standard field, the date format, the code values for sex, smoker, status, transaction and claim type, and the units (for example face amounts in thousands or premiums in cents). Unknown codes and unreadable dates are never guessed; they are kept as received and flagged. If a client renames, adds or drops a column, the file is held until the layout is confirmed.

## Onboarding a new client

Ask the client for a sample file, their data dictionary and their code lists. Draft the mapping (the AI mapping assistant can suggest one from the sample), then confirm every field with the client's administration team. Test the mapping on the sample: every row should map, every code should be recognised, and the control total should agree. Record the treaty limits (issue ages, jumbo limit, currency) in the client configuration. A new client goes live only after Client Administration signs off the test results.

## Control totals

Every file must come with a record count and an amount total, in a trailer row, a control file or a control sheet. The amount is face amount for inforce, premium for billing and claim amount for claims. If either the count or the total does not agree with the file, the whole file is held, because a mismatch usually means the file was cut off or built from the wrong extract. A file with no control totals loads with a warning and the client is asked to add them.

## Validation rules

Rules are kept in one catalogue, each written as a SQL query that returns the failing records. Critical rules hold the failing records back; warning rules load the record and list it for review. Completeness rules check required fields; validity rules check values and codes; uniqueness rules catch duplicates; consistency rules compare fields with each other; reconciliation rules compare billing and claims with inforce and premium with the treaty rate; roll-forward rules compare this month with last month.

## The gate: when a file is held

A file is held, and nothing from it loads, if a file-level control fails (count, total or layout), or if more than 2% of its rows fail a critical rule and it has at least 100 rows. Otherwise the file loads and only the failing records are held back. Small files such as claims are never held for the rate alone, so one bad claim does not stop the others. The 2% limit is a setting agreed with the business and can differ by client.

## Resubmissions

A corrected file replaces the earlier one for the same client and month; it is never added on top. The corrected file goes through intake and every check again. Held-back records from a loaded file can be resent as a correction file containing only those records.

## Client follow-up

Each client receives one follow-up per month listing missing files, held files with the reason, held-back records by issue with the policy numbers, and warnings for review. The fix-by date is five business days after the control run. The Excel exception report goes with the email. Keep the tone factual and specific; name the file and the field, not the person.

## Escalation

Escalate to the Client Administration manager when a client misses the fix-by date, when the same issue appears three months in a row, when a file is held two months in a row, or when a problem could change billing or reserves materially. Escalations are logged with the client, issue, months seen and the agreed action.

## Recurring issues and root cause

When the same check fails for the same client month after month, look for the cause at the source rather than fixing records each month. Typical causes are an extract that joins to a one-to-many table (duplicates), a field defaulted for one product (wrong currency), a code introduced without notice, or a timing cutoff between billing and policy systems. If a value is valid but unusual, such as a known jumbo policy, record an agreed exception so it is not flagged every month.

## Rule changes and user acceptance testing

A new or changed rule is tested on the last three months of data before it goes live. Compare the exceptions before and after the change: every new exception must be explained, and every exception that disappears must be intended. The regression suite must pass, including the check that records loaded plus records held back equal records received. Changes go live at the start of a month, never during a control run.

## Data security and retention

Client files hold personal information such as dates of birth. Store them encrypted, give access only to the people who need it, and mask personal fields outside production. Keep archived client files for the period set in the retention schedule, then delete them with a record of the deletion. Never send client data by personal email or store it on a laptop.

## Using AI tools safely

AI tools may suggest a mapping for a new layout, draft a client email, summarise exceptions or write a read-only query. A person reviews every suggestion before it is used. AI tools never decide what loads to production, never change data, and never receive client personal data unless the tool is approved for it. Queries written by AI run on a read-only connection with no file or network access and a row limit.

## Glossary

Cedant or ceding company: the insurer or reinsurer passing risk on. Retrocession: reinsurance bought by a reinsurer. Treaty: the contract that sets which policies are covered and on what terms. Seriatim or inforce file: a list of every policy or life currently covered. Retro share: the part of the risk ceded to us. Face amount: the policy's benefit amount. Jumbo limit: the largest total cover the treaty accepts on one life. Roll-forward: last month's active policies, less terminations, plus new business, should equal this month's.
