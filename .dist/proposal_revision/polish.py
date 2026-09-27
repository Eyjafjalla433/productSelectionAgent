from docx import Document
from docx.shared import Pt
from pathlib import Path
p=Path('E:/Code/productSelectionAgent/Shopping_Copilot_Proposal_EN_Completed.docx')
d=Document(p)
active=False
for x in d.paragraphs:
    if x.text.startswith('07  '): active=True
    if x.text.startswith('09  '): active=False
    if active:
        f=x.paragraph_format
        if x.style.name=='Normal':
            f.line_spacing=1.0; f.space_after=Pt(5)
            for r in x.runs: r.font.size=Pt(10)
        elif x.style.name=='Heading 2':
            f.space_before=Pt(8); f.space_after=Pt(5)
            for r in x.runs: r.font.size=Pt(12)
d.save(p)
