"""Read-only aggregate identity audit. A suspicious pattern is never proof of the lost value."""
import json
from backup import dc
SQL="""BEGIN READ ONLY;
SELECT json_build_object('applications',count(*),
 'name_utf8_bytes_at_old_boundary',count(*) FILTER(WHERE octet_length(applicant_name)=20),
 'name_replacement_character',count(*) FILTER(WHERE position(chr(65533) in applicant_name)>0),
 'dob_with_day_and_month_at_most_12',count(*) FILTER(WHERE extract(day from dob)<=12 AND extract(month from dob)<=12),
 'missing_document_path',count(*) FILTER(WHERE doc_path IS NULL OR doc_path='')) FROM applications;
COMMIT;"""
response=dc('exec','-T','db','psql','-U','sewasetu','-d','sewasetu','-Atc',SQL,capture_output=True,text=True).stdout
for line in response.splitlines():
    if line.startswith('{'):print(json.dumps({'read_only_identity_audit':json.loads(line),'historical_corrections':0,'interpretation':'Patterns require independent assisted verification; DOB hashes are not evidence.'}))
