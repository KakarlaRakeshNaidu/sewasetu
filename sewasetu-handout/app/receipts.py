"""Shaped Unicode receipts; no substitute characters or transliteration."""
from pathlib import Path
import io
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject
from datetime import date, datetime
from zoneinfo import ZoneInfo
from fpdf import FPDF

FONT_DIR=Path(__file__).parent/'static'/'fonts'


def render_receipt(row):
    pdf=FPDF()
    pdf.set_auto_page_break(auto=True,margin=18)
    for family,file in [('Noto','NotoSans-Regular.ttf'),('Devanagari','NotoSansDevanagari-Regular.ttf'),('Bengali','NotoSansBengali-Regular.ttf')]:
        pdf.add_font(family,fname=str(FONT_DIR/file))
    pdf.set_font('Noto',size=11)
    pdf.set_fallback_fonts(['Devanagari','Bengali'])
    pdf.set_text_shaping(True)
    pdf.add_page()
    pdf.set_font_size(14)
    pdf.multi_cell(0,8,'GOVERNMENT OF PURVANCHAL',align='C',new_x='LMARGIN',new_y='NEXT')
    pdf.set_font_size(11)
    for text in ['Department of Social Welfare','Old Age Pension Scheme - Acknowledgment']:
        pdf.multi_cell(0,7,text,align='C',new_x='LMARGIN',new_y='NEXT')
    pdf.ln(6)
    labels=['Application No','Applicant Name','Mobile','Date of Birth','Village','Block','Bank Account','IFSC','Submitted At','Status']
    for label,value in zip(labels,row):
        if isinstance(value,datetime):
            value=(value.replace(tzinfo=ZoneInfo('Asia/Kolkata')) if value.tzinfo is None else value.astimezone(ZoneInfo('Asia/Kolkata'))).strftime('%d/%m/%Y %H:%M:%S IST')
        elif isinstance(value,date):value=value.strftime('%d/%m/%Y')
        text='' if value is None else str(value)
        pdf.set_text_color(70,80,95)
        pdf.set_font_size(9)
        pdf.multi_cell(0,5,label,new_x='LMARGIN',new_y='NEXT')
        pdf.set_text_color(15,25,40)
        pdf.set_font_size(11)
        pdf.multi_cell(0,7,text or 'Not recorded',new_x='LMARGIN',new_y='NEXT')
        pdf.ln(2)
    pdf.set_font_size(9)
    pdf.multi_cell(0,5,'This is a computer generated acknowledgment. Processing SLA as per the Purvanchal Right to Public Services Act applies.',new_x='LMARGIN',new_y='NEXT')
    # HarfBuzz can emit additional positioned glyphs for one Unicode cluster.
    # fpdf2 omits these from ToUnicode; an unmapped CID is otherwise extracted
    # as a spurious control character. Explicitly map those glyphs to empty text.
    empty={font.i:[cid for glyph,cid in font.subset.items() if glyph and glyph.unicode==()] for font in pdf.fonts.values()}
    reader=PdfReader(io.BytesIO(bytes(pdf.output())))
    for page in reader.pages:
        for name,reference in page['/Resources']['/Font'].items():
            font=reference.get_object()
            missing=empty.get(int(name[2:]),[])
            if missing and '/ToUnicode' in font:
                source=font['/ToUnicode'].get_data()
                block=(str(len(missing))+' beginbfchar\n'+''.join('<%04X> <>\n'%cid for cid in missing)+'endbfchar\n').encode('ascii')
                stream=DecodedStreamObject();stream.set_data(source.replace(b'endcmap',block+b'endcmap'))
                font[NameObject('/ToUnicode')]=stream
    writer=PdfWriter();writer.append(reader)
    output=io.BytesIO();writer.write(output)
    return output.getvalue()
