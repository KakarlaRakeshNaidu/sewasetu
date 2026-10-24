"""Server-owned field schemas shared by saved forms, final review and corrections."""
import re
import unicodedata
from datetime import datetime

FIELDS={1:('applicant_name','dob','gender','marital_status','husband_name','husband_employer'),
        2:('village','block'),3:('bank_account','ifsc')}
LABELS={'applicant_name':'Full name','dob':'Date of birth','gender':'Gender','marital_status':'Marital status',
'husband_name':'Spouse name','husband_employer':'Spouse employer','village':'Village or town','block':'Block',
'bank_account':'Bank account number','ifsc':'IFSC','doc_path':'Age-proof document'}
BLOCKS=('Sonari','Rajapara','Dhemaji Pathar','Borgaon','Namti','Khelua')


def parse_dob(value):
    if not re.fullmatch(r'[0-9]{2}/[0-9]{2}/[0-9]{4}',value):
        raise ValueError('Enter DOB as DD/MM/YYYY, for example 04/12/1957.')
    try:return datetime.strptime(value,'%d/%m/%Y').date()
    except ValueError:raise ValueError('Enter a real calendar date as DD/MM/YYYY.') from None


def validate(data,day,step=None):
    errors={}
    selected=FIELDS[step] if step else tuple(key for fields in FIELDS.values() for key in fields)
    for key in selected:
        value=data.get(key,'')
        if not isinstance(value,str):
            errors[key]='Enter text for '+LABELS[key].lower()+'.';continue
        if key in ('applicant_name','village','husband_name','husband_employer'):
            optional=key.startswith('husband_')
            if not optional and not value.strip():errors[key]='Enter '+LABELS[key].lower()+'.'
            elif len(value)>100:errors[key]='Use at most 100 characters; nothing is cut automatically.'
            elif any(unicodedata.category(c).startswith('C') and c not in ('\u200c','\u200d') for c in value):
                errors[key]='Remove control characters; letters and combining marks are supported.'
        elif key=='dob':
            try:
                born=parse_dob(value)
                age=day.year-born.year-((day.month,day.day)<(born.month,born.day))
                if born>day:errors[key]='Date of birth cannot be in the future.'
                elif age<60:errors[key]='Applicant must be 60 years or above on the application date.'
            except ValueError as error:errors[key]=str(error)
        elif key=='gender' and value not in ('Male','Female','Other'):
            errors[key]='Choose a gender option.'
        elif key=='marital_status' and value not in ('Married','Unmarried','Widowed'):
            errors[key]='Choose a marital-status option.'
        elif key=='block' and value not in BLOCKS:errors[key]='Choose a block from the list.'
        elif key=='bank_account' and (not re.fullmatch(r'[0-9]{6,30}',value) or set(value)=={'0'}):
            errors[key]='Use 6 to 30 account digits, keeping any leading zeros; do not include spaces.'
        elif key=='ifsc' and not re.fullmatch(r'[A-Z]{4}0[A-Z0-9]{6}',value):
            errors[key]='Enter an 11-character IFSC: four capital letters, 0, then six letters or digits.'
    return errors
