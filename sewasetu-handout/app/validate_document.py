"""Isolated content validation: size is bounded by the parent; decoding has CPU/RAM limits."""
import sys
import resource
resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
resource.setrlimit(resource.RLIMIT_CPU,(2,2))
try:
    filename,extension=sys.argv[1:]
    if extension=='.pdf':
        from pypdf import PdfReader
        reader=PdfReader(filename,strict=True)
        if reader.is_encrypted or len(reader.pages)<1:
            raise ValueError('Unreadable PDF')
    else:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS=None  # Subsample JPEG before decoding; subprocess RAM/CPU remains bounded.
        with Image.open(filename) as image:
            if image.format!='JPEG':raise ValueError('Not JPEG')
            image.draft('RGB',(1024,1024))
            image.load()
    print('VALID')
except Exception:
    print('INVALID')
    raise SystemExit(1)
