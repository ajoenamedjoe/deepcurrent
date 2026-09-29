"""
A minimal .docx writer. Stdlib only (zipfile + string XML) -- the desk makes
the same no-pip-install promise as every other desk on this PC, so
python-docx is not available on the target machine.

Produces WordprocessingML that Word (Windows + Mac), LibreOffice and Google
Docs all open: real heading styles (so the navigation pane works), real
tables with header rows that repeat across pages, a footer with page numbers.
"""

import re
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape

FONT = "Calibri"
INK = "1A1A19"
INK2 = "52514E"
INK3 = "898781"
GRID = "D9D8D0"
HEAD_FILL = "F1F0EB"


_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _x(text):
    # characters XML forbids (they arrive in UW text now and then) would make Word refuse the file
    return escape(_XML_BAD.sub("", str(text)), {'"': "&quot;"})


class Xml(str):
    """Already-built WordprocessingML. Plain str is always treated as TEXT.

    The first draft used plain str for both, so every heading built with
    run() was escaped a second time and printed its own XML into the
    document -- caught only by converting the .docx to PDF and looking."""


def run(text, bold=False, italic=False, color=None, size=None, font=None):
    """size in points."""
    props = []
    if font:
        props.append('<w:rFonts w:ascii="%s" w:hAnsi="%s" w:cs="%s"/>' % (font, font, font))
    if bold:
        props.append("<w:b/>")
    if italic:
        props.append("<w:i/>")
    if color:
        props.append('<w:color w:val="%s"/>' % color)
    if size:
        props.append('<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (size * 2, size * 2))
    rpr = "<w:rPr>%s</w:rPr>" % "".join(props) if props else ""
    parts = str(text).split("\n")
    body = '<w:br/>'.join('<w:t xml:space="preserve">%s</w:t>' % _x(p) for p in parts)
    return Xml("<w:r>%s%s</w:r>" % (rpr, body))


class Doc:
    def __init__(self, title="Report", author="Valuation Desk", footer=""):
        self.body = []
        self.title_text = title
        self.author = author
        self.footer_text = footer

    # ---------------------------------------------------------- blocks
    def _para(self, runs, style=None, align=None, space_after=None, space_before=None,
              keep_next=False, indent=None, hanging=None, shade=None, border_left=None):
        ppr = []
        if style:
            ppr.append('<w:pStyle w:val="%s"/>' % style)
        if keep_next:
            ppr.append("<w:keepNext/>")
        if border_left:
            ppr.append('<w:pBdr><w:left w:val="single" w:sz="24" w:space="8" w:color="%s"/></w:pBdr>'
                       % border_left)
        if shade:
            ppr.append('<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % shade)
        if space_after is not None or space_before is not None:
            ppr.append('<w:spacing%s%s/>' % (
                ' w:before="%d"' % space_before if space_before is not None else "",
                ' w:after="%d"' % space_after if space_after is not None else ""))
        if indent is not None or hanging is not None:
            ppr.append('<w:ind w:left="%d"%s/>' % (indent or 0,
                       ' w:hanging="%d"' % hanging if hanging else ""))
        if align:
            ppr.append('<w:jc w:val="%s"/>' % align)
        if isinstance(runs, (list, tuple)):
            runs = "".join(r if isinstance(r, Xml) else run(r) for r in runs)
        elif not isinstance(runs, Xml):
            runs = run(runs)
        self.body.append("<w:p>%s%s</w:p>" % ("<w:pPr>%s</w:pPr>" % "".join(ppr) if ppr else "", runs))

    def title(self, text, sub=None):
        self._para(run(text), style="Title")
        if sub:
            self._para(run(sub, color=INK2, size=12), space_after=200)

    def h1(self, text):
        self._para(run(text), style="Heading1", keep_next=True)

    def h2(self, text):
        self._para(run(text), style="Heading2", keep_next=True)

    def p(self, content, **kw):
        self._para(content, **kw)

    def bullet(self, content, mark="•", color=None):
        if not isinstance(content, Xml):
            content = run(content)
        self._para([run(mark + "\t", color=color, bold=bool(color)), content],
                   indent=360, hanging=360, space_after=60)

    def callout(self, runs, color):
        self._para(runs, border_left=color, shade="F7F7F4", indent=180, space_after=160,
                   space_before=80)

    def table(self, rows, widths, header=True, align=None, bold_first_col=False,
              colors=None):
        """
        rows: list of lists of str (or pre-built run XML via ("xml", s)).
        widths: twips per column. align: per-column 'left'/'right'/'center'.
        colors: optional {(r,c): hex} text colours.
        """
        align = align or ["left"] * len(widths)
        colors = colors or {}
        total = sum(widths)
        out = ['<w:tbl><w:tblPr><w:tblW w:w="%d" w:type="dxa"/>'
               '<w:tblBorders>'
               '<w:top w:val="single" w:sz="4" w:color="%s"/>'
               '<w:bottom w:val="single" w:sz="4" w:color="%s"/>'
               '<w:insideH w:val="single" w:sz="4" w:color="%s"/>'
               '</w:tblBorders>'
               '<w:tblLayout w:type="fixed"/>'
               '<w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
               '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar>'
               '</w:tblPr><w:tblGrid>%s</w:tblGrid>'
               % (total, GRID, GRID, GRID, "".join('<w:gridCol w:w="%d"/>' % w for w in widths))]
        keep = len(rows) <= 14
        for ri, row in enumerate(rows):
            is_head = header and ri == 0
            kn = "<w:keepNext/>" if (keep and ri < len(rows) - 1) else ""
            trpr = "<w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>" if is_head else "<w:trPr><w:cantSplit/></w:trPr>"
            cells = []
            for ci, cell in enumerate(row):
                shade = '<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % HEAD_FILL if is_head else ""
                if isinstance(cell, tuple) and cell[0] == "xml":
                    content = cell[1]
                else:
                    content = run(cell, bold=is_head or (bold_first_col and ci == 0),
                                  color=colors.get((ri, ci)) or (INK2 if is_head else None),
                                  size=9 if is_head else 10)
                cells.append('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>%s</w:tcPr>'
                             '<w:p><w:pPr>%s<w:spacing w:before="0" w:after="0"/><w:jc w:val="%s"/></w:pPr>%s</w:p></w:tc>'
                             % (widths[ci], shade, kn, {"right": "right", "center": "center"}.get(align[ci], "left"), content))
            out.append("<w:tr>%s%s</w:tr>" % (trpr, "".join(cells)))
        out.append("</w:tbl>")
        self.body.append("".join(out))
        self._para("", space_after=120)

    def pagebreak(self):
        self.body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    # ---------------------------------------------------------- package
    def _document(self):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<w:body>%s<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter"/>'
                '<w:pgSz w:w="12240" w:h="15840"/>'
                '<w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" '
                'w:header="600" w:footer="500" w:gutter="0"/></w:sectPr></w:body></w:document>'
                % "".join(self.body))

    def _styles(self):
        def pstyle(sid, name, size, bold=False, color=INK, before=0, after=120, based="Normal",
                   outline=None):
            return ('<w:style w:type="paragraph" w:styleId="%s"><w:name w:val="%s"/>'
                    '<w:basedOn w:val="%s"/><w:next w:val="Normal"/><w:qFormat/>'
                    '<w:pPr><w:keepNext/><w:spacing w:before="%d" w:after="%d"/>%s</w:pPr>'
                    '<w:rPr>%s<w:color w:val="%s"/><w:sz w:val="%d"/><w:szCs w:val="%d"/></w:rPr></w:style>'
                    % (sid, name, based, before, after,
                       '<w:outlineLvl w:val="%d"/>' % outline if outline is not None else "",
                       "<w:b/>" if bold else "", color, size * 2, size * 2))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="%s" w:hAnsi="%s" w:cs="%s" w:eastAsia="%s"/>'
                '<w:color w:val="%s"/><w:sz w:val="21"/><w:szCs w:val="21"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>'
                '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
                '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
                '%s%s%s'
                '<w:style w:type="paragraph" w:styleId="Footer"><w:name w:val="footer"/><w:basedOn w:val="Normal"/>'
                '<w:rPr><w:color w:val="%s"/><w:sz w:val="16"/></w:rPr></w:style>'
                '</w:styles>'
                % (FONT, FONT, FONT, FONT, INK,
                   pstyle("Title", "Title", 26, bold=True, after=60),
                   pstyle("Heading1", "heading 1", 15, bold=True, before=320, after=100, outline=0),
                   pstyle("Heading2", "heading 2", 12, bold=True, color=INK2, before=200, after=80, outline=1),
                   INK3))

    def _footer(self):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:p><w:pPr><w:pStyle w:val="Footer"/><w:tabs><w:tab w:val="right" w:pos="10080"/></w:tabs></w:pPr>'
                '%s<w:r><w:rPr><w:color w:val="%s"/><w:sz w:val="16"/></w:rPr><w:tab/><w:t xml:space="preserve">Page </w:t></w:r>'
                '<w:r><w:rPr><w:color w:val="%s"/><w:sz w:val="16"/></w:rPr><w:fldChar w:fldCharType="begin"/></w:r>'
                '<w:r><w:rPr><w:color w:val="%s"/><w:sz w:val="16"/></w:rPr><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
                '<w:r><w:rPr><w:color w:val="%s"/><w:sz w:val="16"/></w:rPr><w:fldChar w:fldCharType="separate"/></w:r>'
                '<w:r><w:rPr><w:color w:val="%s"/><w:sz w:val="16"/></w:rPr><w:t>1</w:t></w:r>'
                '<w:r><w:rPr><w:color w:val="%s"/><w:sz w:val="16"/></w:rPr><w:fldChar w:fldCharType="end"/></w:r>'
                '</w:p></w:ftr>' % (run(self.footer_text, color=INK3, size=8), INK3, INK3, INK3, INK3, INK3, INK3))

    def save(self, path):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        files = {
            "[Content_Types].xml":
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
                '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                '</Types>',
            "_rels/.rels":
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                '</Relationships>',
            "word/_rels/document.xml.rels":
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
                '</Relationships>',
            "word/document.xml": self._document(),
            "word/styles.xml": self._styles(),
            "word/footer1.xml": self._footer(),
            "docProps/core.xml":
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                '<dc:title>%s</dc:title><dc:creator>%s</dc:creator>'
                '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
                '<dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified>'
                '</cp:coreProperties>' % (_x(self.title_text), _x(self.author), now, now),
            "docProps/app.xml":
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                '<Application>%s</Application></Properties>' % _x(self.author),
        }
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                z.writestr(name, data.encode("utf-8"))
        return path
