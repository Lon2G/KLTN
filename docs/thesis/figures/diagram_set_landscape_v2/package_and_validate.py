"""Validate and package only the generated thesis figure set (offline)."""
from pathlib import Path
from xml.etree import ElementTree as ET
from html import escape
from PIL import Image,ImageOps,ImageDraw,ImageFont
import hashlib,json,zipfile

HERE=Path(__file__).resolve().parent
manifest=json.loads((HERE/'manifest.json').read_text())
pages=ET.parse(HERE/'diagram_set_landscape_v2.vi.drawio').findall('diagram')
assert len(pages)==11
checks=[]
for fig,page in zip(manifest['figures'],pages):
    d=json.loads((HERE/(fig['stem']+'.layout.json')).read_text())
    src=HERE.parent/'diagram_set_v1'/(fig['stem']+'.layout.json')
    assert hashlib.sha256(src.read_bytes()).hexdigest()==fig['source_sha256']
    assert d['width']>d['height']
    cells={c.get('id'):c for c in page.findall('.//mxCell')}
    for n in d['nodes']:
        assert n['id'] in cells and cells[n['id']].get('vertex')=='1'
        assert any(c.get('parent')==n['id'] and c.get('value') for c in cells.values())
        assert n['h']>=n['head_h']+36+(len(n['body_lines'])-1)*31
    edges=[c for c in cells.values() if c.get('edge')=='1']
    assert len(edges)==len(d['edges'])
    for e,old in zip(edges,d['edges']):assert e.get('source')==old['start'] and e.get('target')==old['end']
    ET.parse(HERE/(fig['stem']+'.svg'));ET.parse(HERE/'drawio'/(fig['stem']+'.drawio'))
    png=Image.open(HERE/(fig['stem']+'.png'))
    assert png.size==(d['width']*2,d['height']*2)
    assert abs(png.info['dpi'][0]-300)<.1
    checks.append({'stem':fig['stem'],'nodes':len(d['nodes']),'connected_edges':len(edges),'png_dimensions':png.size,'passed':True})

html=['<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Bộ 11 sơ đồ nằm ngang</title><style>body{font-family:Arial,sans-serif;background:#f3f6f8;color:#183042;margin:0;padding:32px}main{max-width:1500px;margin:auto}h1{margin-bottom:10px}a{color:#006da8}header{padding:20px 0 28px}section{margin:0 0 28px;padding:22px;background:white;border:1px solid #c8d5df;border-radius:16px}img{display:block;width:100%;height:auto;margin:14px 0}nav{display:flex;gap:16px;flex-wrap:wrap}p{line-height:1.6}</style><main><header><h1>Bộ 11 sơ đồ nằm ngang</h1><p>Tiếng Việt · Khung bo góc · Icon nghiệp vụ · Bản chỉnh sửa có 11 trang.</p><nav><a href="diagram_set_landscape_v2.vi.drawio" download>Tải file draw.io chung</a><a href="diagram_set_landscape_v2.zip" download>Tải cả bộ ZIP</a><a href="README.md">Hướng dẫn</a></nav></header>']
for f in manifest['figures']:
    s=f['stem'];title=escape(f['title'])
    html.append(f'<section><h2>{s[:2]}. {title}</h2><p>Vị trí đề xuất: Chương {f["chapter"]}. Mã file chưa là số hình chính thức.</p><a href="{s}.svg"><img src="{s}.preview.png" alt="{title}"></a><nav><a href="drawio/{s}.drawio" download>Chỉnh sửa draw.io</a><a href="{s}.png" download>PNG 300 dpi</a><a href="{s}.svg" download>SVG vector</a></nav></section>')
html.append('</main></html>');(HERE/'index.html').write_text('\n'.join(html))

font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Bold.ttf',23)
cw,ch=790,610;sheet=Image.new('RGB',(cw*3,ch*4),'#eef3f7');draw=ImageDraw.Draw(sheet)
for i,f in enumerate(manifest['figures']):
    x=(i%3)*cw;y=(i//3)*ch
    draw.rounded_rectangle((x+12,y+12,x+cw-12,y+ch-12),14,fill='white',outline='#c5d2db')
    draw.text((x+28,y+26),f'{i+1:02d}  '+f['title'],font=font,fill='#183042') if font.getlength(f'{i+1:02d}  '+f['title'])<cw-55 else draw.text((x+28,y+26),f'{i+1:02d}  '+f['title'][:43]+'…',font=font,fill='#183042')
    image=Image.open(HERE/(f['stem']+'.preview.png')).convert('RGB')
    thumb=ImageOps.contain(image,(cw-40,ch-100),Image.Resampling.LANCZOS)
    sheet.paste(thumb,(x+(cw-thumb.width)//2,y+75+(ch-100-thumb.height)//2))
sheet.save(HERE/'contact_sheet.vi.png')
report={'figures':11,'combined_drawio_pages':11,'all_source_layouts_unchanged':True,'checks':checks,'visual_review':'All 11 rendered previews inspected; final connector review recorded in work session.'}
(HERE/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
with zipfile.ZipFile(HERE/'diagram_set_landscape_v2.zip','w',zipfile.ZIP_DEFLATED) as z:
    for f in sorted(HERE.rglob('*')):
        if f.is_file() and f.suffix not in ('.zip','.pyc') and '__pycache__' not in f.parts:
            z.write(f,Path('diagram_set_landscape_v2')/f.relative_to(HERE))
print(json.dumps({'figures':11,'pages':11,'source_preserved':True,'zip_bytes':(HERE/'diagram_set_landscape_v2.zip').stat().st_size}))
