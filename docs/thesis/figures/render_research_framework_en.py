"""Render the English translation of the approved Chapter 1 research diagram.

Preserves the Vietnamese diagram's structure and status as of 2026-11-08.
Writes SVG, PNG (300 dpi), Mermaid, and translation provenance only.
Uses Pillow; run with the bundled document Python runtime or another environment
containing Pillow. No experimental data are generated or modified.
"""
from pathlib import Path
from html import escape
from PIL import Image,ImageDraw,ImageFont
import hashlib,json,math

HERE=Path(__file__).resolve().parent
W,H=1480,2240
SCALE=1.5
INK='#172F45'; BLUE='#F0F5F9'; AMBER='#FFF8EB'; EDGE='#8B6327'
NODES=[
 ('scope',7,128,86,12,False,'1. Problem Definition and Research Foundations','Unusual processing times in order fulfillment\nLiterature review → objectives → research questions'),
 ('data',7,111,86,12,False,'2. Data Preparation and Exploratory Analysis','Real Olist data · bed_bath_table category\nQuality review → standardization → EDA; retain raw data'),
 ('split',7,94,86,12,False,'3. Temporal Data Split','Train: model fitting and references · Validation: comparison and selection\nTest: evaluate a fixed candidate; no tuning on the test set'),
 ('detection',4,68,44,20,False,'4. Anomaly Detection','Features of three stages\nProduct and contextual groups\nCompare → freeze the candidate'),
 ('framework',52,68,44,20,False,'5. Multi-Agent Framework','Event replay and state management\nProcess analysis → context\n→ evidence verification\nSmall pilot; refinement ongoing'),
 ('quantitative',7,46,86,17,False,'6. Experiments and Quantitative Evaluation','Detection: coverage, stability, comparison with late delivery\nExplanations: rules / one agent / three agents; sources, time, cost\nResults available; AI comparison remains incomplete'),
 ('qualitative',7,24,86,17,True,'7. Qualitative Evaluation and System Demonstration','Rubric → independent ratings → Kappa suited to the design\nComplete the local interface and assess usefulness\nNot yet complete'),
 ('discussion',7,8,86,11,True,'8. Synthesis, Discussion, and Conclusions','Address research questions · limitations · future work'),
]
EDGES=[('scope','data'),('data','split'),('split','detection'),('split','framework'),('detection','framework'),('detection','quantitative'),('framework','quantitative'),('quantitative','qualitative'),('qualitative','discussion')]

def main():
 fontroot=Path('/System/Library/Fonts/Supplemental')
 regular=fontroot/'Times New Roman.ttf';bold=fontroot/'Times New Roman Bold.ttf'
 if not regular.exists() or not bold.exists():raise FileNotFoundError('Times New Roman fonts required for the thesis figure.')
 im=Image.new('RGB',(int(W*SCALE),int(H*SCALE)),'white');draw=ImageDraw.Draw(im)
 svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">','<rect width="100%" height="100%" fill="white"/>']
 sx=lambda x:40+x*14
 sy=lambda y:30+(152-y)*13.8
 def line(a,b,color=INK,width=2.4,dash=False):
  svg.append(f'<line x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}" stroke="{color}" stroke-width="{width}"'+(' stroke-dasharray="12 8"' if dash else '')+'/>')
  if not dash:draw.line(tuple(v*SCALE for pt in (a,b) for v in pt),fill=color,width=round(width*SCALE));return
  length=math.dist(a,b)
  for start in range(0,int(length),20):
   end=min(start+12,length)
   pts=[(a[j]+(b[j]-a[j])*t/length)*SCALE for t in (start,end) for j in (0,1)]
   draw.line(pts,fill=color,width=round(width*SCALE))
 def arrow(points,dashed=False):
  pts=[(sx(x),sy(y)) for x,y in points]
  for a,b in zip(pts[:-1],pts[1:]):line(a,b,dash=dashed)
  a,b=pts[-2:];angle=math.atan2(b[1]-a[1],b[0]-a[0]);length=13;spread=6
  base=(b[0]-length*math.cos(angle),b[1]-length*math.sin(angle))
  corners=[b,(base[0]-spread*math.sin(angle),base[1]+spread*math.cos(angle)),(base[0]+spread*math.sin(angle),base[1]-spread*math.cos(angle))]
  draw.polygon([(x*SCALE,y*SCALE) for x,y in corners],fill=INK)
  svg.append('<polygon points="'+' '.join(f'{x},{y}' for x,y in corners)+f'" fill="{INK}"/>')
 def text(cx,cy,value,size=30,bolded=False,max_width=None):
  font=ImageFont.truetype(str(bold if bolded else regular),round(size*SCALE))
  lines=value.split('\n');step=size*1.32
  for i,t in enumerate(lines):
   y=cy+(i-(len(lines)-1)/2)*step
   bbox=draw.textbbox((0,0),t,font=font)
   if max_width and (bbox[2]-bbox[0])/SCALE>max_width:raise ValueError(f'Text exceeds available width: {t}')
   draw.text((cx*SCALE,y*SCALE),t,font=font,fill=INK,anchor='mm')
   svg.append(f'<text x="{cx}" y="{y}" text-anchor="middle" dominant-baseline="central" font-family="Times New Roman, serif" font-size="{size}" font-weight="{"bold" if bolded else "normal"}" fill="{INK}">{escape(t)}</text>')
 # Edges beneath the nodes, following the same topology as the source diagram.
 arrow([(50,128),(50,123.3)]);arrow([(50,111),(50,106.3)])
 arrow([(50,94),(50,91),(26,91),(26,88.3)]);arrow([(50,94),(50,91),(74,91),(74,88.3)])
 arrow([(48.2,77.5),(51.8,77.5)])
 arrow([(26,68),(26,65.5),(50,65.5),(50,63.3)]);arrow([(74,68),(74,65.5),(50,65.5),(50,63.3)])
 arrow([(50,46),(50,41.3)],True);arrow([(50,24),(50,19.3)],True)
 text(sx(50),sy(148.5),'RESEARCH PROCESS',44,True)
 text(sx(50),sy(143.8),'KLTN-Lon2G · Olist / bed_bath_table · Status as of 8 November 2026',27)
 for key,x,y,w,h,pending,title,body in NODES:
  left,top,width,height=sx(x),sy(y+h),w*14,h*13.8
  fill=AMBER if pending else BLUE;edge=EDGE if pending else INK
  box=(left*SCALE,top*SCALE,(left+width)*SCALE,(top+height)*SCALE)
  draw.rounded_rectangle(box,radius=12*SCALE,fill=fill,outline=edge if not pending else None,width=round(2.4*SCALE))
  svg.append(f'<rect x="{left}" y="{top}" width="{width}" height="{height}" rx="12" fill="{fill}" stroke="{edge}" stroke-width="2.4"'+(' stroke-dasharray="12 8"' if pending else '')+'/>')
  if pending:
   for a,b in [((left,top),(left+width,top)),((left+width,top),(left+width,top+height)),((left+width,top+height),(left,top+height)),((left,top+height),(left,top))]:
    # PNG dashed boundaries; SVG uses its rounded rectangle above.
    length=math.dist(a,b)
    for start in range(0,int(length),20):
     end=min(start+12,length)
     pts=[(a[j]+(b[j]-a[j])*t/length)*SCALE for t in (start,end) for j in (0,1)]
     draw.line(pts,fill=edge,width=4)
  title_y=sy(y+h-2.2);body_y=sy(y+(h-4.5)/2)
  title_size=31 if w<50 else 32
  body_size=28
  if body_y-(len(body.split('\n'))-1)*body_size*1.32/2-body_size/2 < title_y+title_size/2+7:
   raise ValueError(f'Heading/body overlap: {key}')
  if body_y+(len(body.split('\n'))-1)*body_size*1.32/2+body_size/2>top+height-8:
   raise ValueError(f'Body outside node: {key}')
  text(sx(x+w/2),title_y,title,title_size,True,width-28)
  text(sx(x+w/2),body_y,body,body_size,False,width-28)
 line((sx(6),sy(4.1)),(sx(11),sy(4.1)))
 text(sx(31),sy(4.1),'Implementation / results available',25)
 line((sx(56),sy(4.1)),(sx(61),sy(4.1)),EDGE,dash=True)
 text(sx(79),sy(4.1),'Planned / incomplete',25)
 text(sx(50),sy(-.3),'Near real-time: historical event replay with contextual assumptions.',26)
 text(sx(50),sy(-3.1),'Alerts indicate anomalies; bottleneck causes remain unconfirmed.',26)
 svg.append('</svg>')
 stem=HERE/'research_framework.en'
 stem.with_suffix('.en.svg').write_text('\n'.join(svg)+'\n')
 im.save(HERE/'research_framework.en.png',dpi=(300,300))
 m=['%% English translation of the approved Chapter 1 diagram; status as of 2026-11-08.','flowchart TB']
 for key,x,y,w,h,pending,title,body in NODES:
  m.append(f'    {key}["{title}<br/>{body.replace(chr(10),"<br/>")}"]:::{"pending" if pending else "added"}')
 for a,b in EDGES:
  op='-.->' if b in ('qualitative','discussion') else '-->'
  label='|Fixed candidate|' if (a,b)==('detection','framework') else ''
  m.append(f'    {a} {op}{label} {b}')
 m.extend(['    classDef added fill:#F0F5F9,stroke:#172F45,color:#172F45;','    classDef pending fill:#FFF8EB,stroke:#8B6327,color:#172F45,stroke-dasharray:6 4;'])
 (HERE/'research_framework.en.mmd').write_text('\n'.join(m)+'\n')
 source=HERE/'research_framework.vi.sources.json'
 manifest={'language':'en','translated_on':'2026-11-09','evidence_status_as_of':'2026-11-08','approval_status':'translated_from_user_approved_chapter1','new_experiment':False,'source_figure':'research_framework.vi.png','source_png_sha256':hashlib.sha256((HERE/'research_framework.vi.png').read_bytes()).hexdigest(),'source_provenance':json.loads(source.read_text()),'font':'Times New Roman','image_dpi':300,'image_size':list(im.size),'editable_source':'render_research_framework_en.py','outputs':['research_framework.en.svg','research_framework.en.png','research_framework.en.mmd'],'validation':'All node text widths and heading/body spacing checked.'}
 (HERE/'research_framework.en.sources.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
 print('English diagram generated; text bounds checked:',im.size)
if __name__=='__main__':main()
