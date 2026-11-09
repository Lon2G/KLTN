"""Editable landscape graphical abstract, drawn from project facts, not sample data.

Outputs SVG, diagrams.net XML, layout/provenance JSON. All icons are original
vector line art. Use rasterize.mjs for PNG exports. No pipeline or API is run.
"""
from pathlib import Path
from html import escape
from urllib.parse import quote
from xml.etree import ElementTree as ET
from PIL import ImageFont
import hashlib
import json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
W, H = 2700, 1460
INK, MUTED, BLUE = '#183042', '#526577', '#1387CB'
PALE, LIME, BORDER = '#EAF5FC', '#DEFA18', '#668094'
FONT = Path('/System/Library/Fonts/Supplemental')
ELEMENTS, TEXT_CHECKS, NODE_GROUPS = [], [], []


def rect(x, y, w, h, fill='white', stroke=BORDER, radius=12, sw=1.7):
    ELEMENTS.append(dict(kind='rect', x=x, y=y, w=w, h=h, fill=fill,
                         stroke=stroke, radius=radius, sw=sw))


def txt(x, y, value, size=24, bold=False, anchor='start', color=INK, width=None):
    # y is the baseline; explicit lines avoid renderer-dependent wrapping.
    font = ImageFont.truetype(str(FONT / ('Arial Bold.ttf' if bold else 'Arial.ttf')), size)
    measured = font.getlength(value)
    if width is not None and measured > width:
        raise ValueError(f'Text too wide ({measured:.1f}>{width}): {value}')
    TEXT_CHECKS.append({'text': value, 'width': measured, 'limit': width})
    ELEMENTS.append(dict(kind='text', x=x, y=y, text=value, size=size,
                         bold=bold, anchor=anchor, color=color, measured=measured))


def arrow(points, color=INK, dashed=False, width=2.8, label=None):
    ELEMENTS.append(dict(kind='edge', points=points, color=color, dashed=dashed, sw=width))
    if label:
        x, y, value = label
        txt(x, y, value, 20, anchor='middle', color=color)


def icon_body(name):
    # Icons use a consistent 72 x 72 coordinate system and no external assets.
    def p(d, fill='none', stroke=BLUE, sw=2.5):
        return f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
    def r(x,y,w,h,fill='none',rx=3):
        return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{BLUE}" stroke-width="2.5"/>'
    def c(x,y,rad,fill='white'):
        return f'<circle cx="{x}" cy="{y}" r="{rad}" fill="{fill}" stroke="{BLUE}" stroke-width="2.5"/>'
    def clock(x=38,y=36,rad=23):
        return c(x,y,rad,PALE)+p(f'M {x} {y-rad+8} V {y} L {x+10} {y+7}')
    def check(x,y):
        return p(f'M {x} {y} l 6 6 l 12 -14',stroke=INK,sw=3)
    def database():
        return p('M 13 17 C 13 7 59 7 59 17 V 54 C 59 65 13 65 13 54 Z',PALE)+p('M13 17 C13 28 59 28 59 17 M13 35 C13 46 59 46 59 35 M13 52 C13 63 59 63 59 52')
    if name=='database':
        return database()+p('M4 43 H10 L15 54 H29 L33 44 H12',fill='white',stroke=INK)+c(18,61,2,INK)+c(28,61,2,INK)
    if name=='quality':
        return r(7,11,39,49,PALE)+p('M15 23 H34 M15 33 H29 M15 43 H26')+p('M49 29 L65 35 V47 Q65 59 49 66 Q33 59 33 47 V35 Z','white')+check(40,47)
    if name=='eventlog':
        return r(5,12,51,43,PALE)+p('M5 24 H56 M20 24 V55 M37 24 V55 M5 35 H56 M5 45 H56')+clock(53,53,15)
    if name=='discovery':
        return p('M14 36 H29 M29 36 V17 H49 M29 36 V55 H49')+c(10,36,6,PALE)+r(49,9,17,16,PALE)+r(49,47,17,16,PALE)+r(22,28,15,16,'white')
    if name=='variants':
        return p('M12 12 V57 M12 24 H34 L48 11 M12 43 H34 L49 57')+c(12,12,5,PALE)+c(12,57,5,PALE)+r(43,5,23,15,PALE)+r(44,49,23,15,PALE)+check(35,31)
    if name=='stages':
        return clock(36,27,22)+p('M11 60 H62')+r(4,51,15,15,PALE)+r(29,51,15,15,PALE)+r(54,51,15,15,PALE)
    if name=='split':
        return r(5,12,62,49,PALE)+p('M5 26 H67 M23 26 V61 M45 26 V61 M20 6 V18 M53 6 V18')+p('M10 34 H18 M10 43 H18 M29 34 H39 M29 43 H39 M51 34 H61 M51 43 H61')
    if name=='reference':
        return c(13,16,7,PALE)+c(33,16,7,PALE)+c(23,36,7,PALE)+p('M13 23 L20 30 M33 23 L26 30')+p('M43 58 H67 M48 57 V37 M57 57 V25 M65 57 V12',sw=4)+p('M42 31 H68',stroke=INK)
    if name=='freeze':
        return p('M28 7 V53 M8 18 L48 42 M8 42 L48 18 M22 12 L28 18 L34 12 M22 48 L28 42 L34 48 M11 25 L19 25 L17 17 M39 43 L37 35 L45 35')+c(53,52,16,PALE)+check(43,52)
    if name=='store':
        return database()+r(42,41,26,24,'white')+p('M48 41 V35 C48 24 62 24 62 35 V41')+p('M55 49 V57',stroke=INK,sw=3)
    if name=='replay':
        return p('M58 26 A25 25 0 1 0 59 47 M58 26 V10 M58 26 H43',sw=3)+p('M29 22 L47 35 L29 48 Z',PALE)+p('M4 63 H67')+c(13,63,3,PALE)+c(35,63,3,PALE)+c(58,63,3,PALE)
    if name=='apply':
        return r(10,6,38,53,PALE)+p('M19 18 H39 M19 28 H39 M19 38 H32')+c(51,48,17,'white')+check(41,48)
    if name=='alert':
        return clock(27,27,22)+p('M48 28 L69 64 H27 Z','white',stroke=INK)+p('M48 40 V51 M48 57 V58',stroke=INK,sw=3)
    if name=='coordinator':
        return r(23,5,26,19,PALE)+p('M36 24 V37 M10 37 H62 M10 37 V46 M36 37 V46 M62 37 V46')+r(2,46,17,17,PALE)+r(27,46,18,17,PALE)+r(54,46,16,17,PALE)
    if name=='process':
        return r(5,10,46,44,PALE)+p('M13 43 V30 M24 43 V22 M35 43 V34',sw=4)+c(48,44,15,'white')+p('M59 55 L68 65',stroke=INK,sw=4)
    if name=='context':
        return c(15,18,7,PALE)+c(36,18,7,PALE)+p('M5 39 Q15 26 25 39 M26 39 Q36 26 46 39 M15 43 L33 56 L51 43')+p('M56 22 C40 22 42 42 56 54 C70 42 72 22 56 22 Z','white')+c(56,32,4,PALE)
    if name=='verifier':
        return p('M36 6 L61 16 V36 Q61 55 36 67 Q11 55 11 36 V16 Z',PALE)+check(24,35)+p('M25 52 H47')
    if name=='bottleneck':
        return p('M9 17 H63 M9 55 H63 M23 17 L33 35 L23 55 M49 17 L39 35 L49 55')+c(36,36,14,PALE)+p('M36 26 V36 L43 40',stroke=INK)
    if name=='explanation':
        return p('M7 10 H64 V49 H30 L16 62 V49 H7 Z',PALE)+p('M17 23 H52 M17 33 H39')+check(40,41)
    if name=='dashboard':
        return r(4,8,64,43,PALE)+p('M29 51 V61 M44 51 V61 M19 63 H54')+r(11,16,17,11,'white')+p('M36 39 V30 M46 39 V24 M57 39 V17',sw=4)+p('M11 35 H27 M11 41 H23')
    if name=='manager':
        return c(29,17,11,PALE)+p('M8 62 V51 Q8 34 29 34 Q50 34 50 51 V62',PALE)+p('M24 36 L29 45 L34 36 M29 45 V58',stroke=INK)+r(43,44,25,19,'white')+p('M50 44 V39 H61 V44')
    raise ValueError(name)


def icon(x,y,name,size=72):
    ELEMENTS.append(dict(kind='icon',x=x,y=y,w=size,h=size,name=name,body=icon_body(name)))


PANELS = [
 ('DATA PREPARATION','Real historical records'),
 ('PROCESS ANALYSIS','Process mining + time features'),
 ('DETECTOR DEVELOPMENT','Offline fitting and evaluation'),
 ('EVENT REPLAY & SCORING','Near real-time simulation'),
 ('MULTI-AGENT AI','Evidence-constrained analysis'),
 ('MANAGEMENT OUTPUT','Local decision support'),
]

# Number, panel, y, icon, title lines, detail lines.
NODES = [
 (1,0,330,'database',['Olist Source Data'],['Orders, items and context','bed_bath_table category']),
 (2,0,600,'quality',['Clean & Standardize'],['Quality checks; retain raw data','Flag missing / conflicting values']),
 (3,0,870,'eventlog',['Create Event Log'],['Order ID · activity · timestamp','Link contextual attributes']),
 (4,1,330,'discovery',['Process Discovery'],['Observed control-flow','Directly-follows relations']),
 (5,1,600,'variants',['Variants &','Conformance'],['Compare event sequences','Check process fitness']),
 (6,1,870,'stages',['Three-Stage Durations'],['Purchase → approval','Approval → carrier handover','Carrier handover → delivery']),
 (7,2,330,'split',['Temporal Data Split'],['Train → validation → test','Control information leakage']),
 (8,2,550,'reference',['References &','Baseline Methods'],['Fit on train; select on validation','Product / contextual references']),
 (9,2,770,'freeze',['Freeze & Evaluate'],['Lock the selected configuration','Evaluate on the test set']),
 (10,2,990,'store',['Store Detector'],['References · rules · thresholds','Versioned configuration']),
 (11,3,330,'replay',['Replay Events'],['Historical events in time order','Update observed order state']),
 (12,3,600,'apply',['Apply Frozen Detector'],['Score completed stages','Check data / reference support']),
 (13,3,870,'alert',['Stage Assessment'],['0–100 score · stage warnings','Keep “not assessable” explicit']),
 (14,4,330,'coordinator',['Code-Based','Coordinator'],['Schedules the three agents','Not an additional AI agent']),
 (15,4,550,'process',['Process Analyst'],['Inspect stage assessments','Select supported observations']),
 (16,4,770,'context',['Context Analyst'],['Compare relevant peer groups','Retain context and uncertainty']),
 (17,4,990,'verifier',['Evidence Verifier'],['Accept / revise / reject claims','Code checks + bounded revision']),
 (18,5,330,'bottleneck',['Potential Bottlenecks'],['Stage and group-level signals','Contextual evidence']),
 (19,5,550,'explanation',['Verified Explanations'],['Source-linked statements','Recommended further checks']),
 (20,5,770,'dashboard',['Local BI Dashboard'],['Overview → groups → orders','Inspect alerts and evidence']),
 (21,5,990,'manager',['Manager Review'],['Prioritize investigation','Decide follow-up actions']),
]


def build():
    rect(0,0,W,H,'white',stroke='none',radius=0)
    txt(45,65,'PROCESS-AWARE ANOMALY DETECTION WITH MULTI-AGENT AI',40,True,width=2540)
    txt(45,108,'Olist · bed_bath_table · Stage-level analysis · Historical event replay · Local BI',25,color=MUTED,width=2540)
    rect(45,137,2610,4,LIME,stroke='none',radius=0)
    # Section panels mirror the user's original white, rounded, numbered format.
    for i,(title,sub) in enumerate(PANELS):
        x=45+i*440
        rect(x,190,410,1000,'#FCFDFE',stroke=BORDER,radius=17,sw=1.6)
        rect(x+1,191,408,106,'#F0F5F8',stroke='none',radius=16)
        txt(x+205,235,title,24,True,'middle',width=389)
        txt(x+205,273,sub,20,False,'middle',MUTED,width=385)
    # Within-panel progression.
    for panel in range(6):
        rows=[n for n in NODES if n[1]==panel]
        cx=45+panel*440+205
        for a,b in zip(rows,rows[1:]):
            arrow([(cx,a[2]+155),(cx,b[2]-5)])
    # Explicit data and evidence routes; no implied live API connection.
    arrow([(430,947.5),(470,947.5),(470,407.5),(505,407.5)])
    arrow([(870,947.5),(910,947.5),(910,407.5),(945,407.5)])
    arrow([(1310,1067.5),(1358,1067.5),(1358,677.5),(1385,677.5)])
    arrow([(1750,947.5),(1790,947.5),(1790,407.5),(1825,407.5)])
    arrow([(2190,1067.5),(2215,1067.5),(2215,407.5),(2265,407.5)])
    # Separate routes from the event log and directly from detector to dashboard.
    arrow([(470,947.5),(470,1250),(1345,1250),(1345,407.5),(1385,407.5)],BLUE,True,2.5,
          (905,1231,'Historical event log + contextual attributes'))
    arrow([(1790,947.5),(1790,1310),(2230,1310),(2230,847.5),(2265,847.5)],BLUE,True,2.5,
          (2008,1288,'Alerts remain available if AI is unavailable'))
    # All node text and icons are separately editable in SVG and diagrams.net.
    for num,panel,y,name,title,details in NODES:
        x=70+panel*440
        first_element=len(ELEMENTS)
        rect(x,y,360,155,'white',stroke=BORDER,radius=12,sw=1.6)
        icon(x+15,y+44,name,67)
        tx=x+96
        title_y=y+42 if len(title)>1 else y+48
        for j,line in enumerate(title):
            txt(tx,title_y+28*j,line,23,True,width=251)
        detail_y=y+102 if len(title)>1 else y+84 if len(details)>2 else y+94
        for j,line in enumerate(details):
            txt(tx,detail_y+25*j,line,17,color=MUTED,width=251)
        rect(x-9,y-13,40,40,LIME,stroke=INK,radius=7,sw=1.6)
        txt(x+11,y+16,str(num),26,True,'middle',width=37)
        NODE_GROUPS.append(dict(id=f'node-{num}',x=x-9,y=y-13,w=369,h=168,
                                first=first_element,last=len(ELEMENTS)))
    # Short scientific qualifications, not an additional processing stage.
    for panel,lines in [
        (0,['Real observations; no fabricated','business records or evaluation labels.']),
        (1,['Estimated delivery date is assessed','separately from stage durations.']),
        (2,['Selection uses validation; no test tuning.']),
        (3,['Product → contextual category fallback.','Scores are not probabilities.']),
        (4,['Evidence tools · verification · bounded calls']),
        (5,['Signals support review; causes unconfirmed.']),
    ]:
        cx=250+440*panel
        first=1120 if len(lines)>1 else 1172
        for j,line in enumerate(lines):
            txt(cx,first+26*j,line,18,anchor='middle',color=MUTED,width=387)
    rect(45,1353,2610,1,'#D9E3EB',stroke='none',radius=0)
    arrow([(50,1390),(110,1390)],INK,width=2.5)
    txt(125,1397,'Analysis / processing flow',20,color=MUTED)
    arrow([(470,1390),(530,1390)],BLUE,True,2.5)
    txt(545,1397,'Data / result reuse',20,color=MUTED)
    txt(2655,1397,'Historical replay with context-availability assumptions; no live deployment validation.',20,anchor='end',color=MUTED)
    txt(2655,1430,'KLTN-Lon2G · Implementation-aligned overview · 9 November 2026',18,anchor='end',color=MUTED)


def svg_output():
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">',
           '<title id="title">Process-aware anomaly detection with multi-agent AI</title>',
           '<desc id="desc">Six landscape panels show Olist data preparation, process analysis, detector development, historical event replay, three evidence-constrained AI agents, and local BI decision support. Scores and explanations are separate. Causes remain unconfirmed.</desc>',
           '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 Z" fill="context-stroke"/></marker></defs>']
    for i,e in enumerate(ELEMENTS):
        kind=e['kind']
        if kind=='rect':
            parts.append(f'<rect id="shape-{i}" x="{e["x"]}" y="{e["y"]}" width="{e["w"]}" height="{e["h"]}" rx="{e["radius"]}" fill="{e["fill"]}" stroke="{e["stroke"]}" stroke-width="{e["sw"]}"/>')
        elif kind=='text':
            parts.append(f'<text id="label-{i}" x="{e["x"]}" y="{e["y"]}" font-family="Arial, Helvetica, sans-serif" font-size="{e["size"]}" font-weight="{700 if e["bold"] else 400}" text-anchor="{e["anchor"]}" fill="{e["color"]}">{escape(e["text"])}</text>')
        elif kind=='icon':
            parts.append(f'<g id="icon-{i}-{e["name"]}" transform="translate({e["x"]},{e["y"]}) scale({e["w"]/72})" stroke-linecap="round" stroke-linejoin="round">{e["body"]}</g>')
        elif kind=='edge':
            points=' '.join(f'{x},{y}' for x,y in e['points'])
            # Explicit arrowhead avoids inconsistent SVG context-stroke support.
            import math
            a,b=e['points'][-2:]; angle=math.atan2(b[1]-a[1],b[0]-a[0])
            base=(b[0]-12*math.cos(angle),b[1]-12*math.sin(angle))
            head=[b,(base[0]-5*math.sin(angle),base[1]+5*math.cos(angle)),(base[0]+5*math.sin(angle),base[1]-5*math.cos(angle))]
            dash=' stroke-dasharray="9 7"' if e['dashed'] else ''
            parts.append(f'<polyline id="flow-{i}" points="{points}" fill="none" stroke="{e["color"]}" stroke-width="{e["sw"]}" stroke-linejoin="round"{dash}/><polygon points="'+ ' '.join(f'{x},{y}' for x,y in head)+f'" fill="{e["color"]}"/>')
    parts.append('</svg>')
    (HERE/'graphical_abstract_landscape.en.svg').write_text('\n'.join(parts)+'\n')


def drawio_output():
    root=ET.Element('mxfile',host='app.diagrams.net',agent='KLTN-Lon2G vector renderer')
    diagram=ET.SubElement(root,'diagram',id='graphical-abstract-landscape-en',name='Graphical abstract — landscape')
    model=ET.SubElement(diagram,'mxGraphModel',dx='0',dy='0',grid='1',gridSize='10',guides='1',tooltips='1',connect='1',arrows='1',fold='1',page='1',pageScale='1',pageWidth=str(W),pageHeight=str(H),background='#FFFFFF')
    graph=ET.SubElement(model,'root')
    ET.SubElement(graph,'mxCell',id='0');ET.SubElement(graph,'mxCell',id='1',parent='0')
    group_members={i:g for g in NODE_GROUPS for i in range(g['first'],g['last'])}
    for i,e in enumerate(ELEMENTS):
        attrs={'id':f'e-{i}','parent':'1'}
        kind=e['kind']
        group=group_members.get(i)
        if group:
            if i==group['first']:
                parent=ET.SubElement(graph,'mxCell',id=group['id'],value='',vertex='1',parent='1',style='group;')
                ET.SubElement(parent,'mxGeometry',{'x':str(group['x']),'y':str(group['y']),
                                                   'width':str(group['w']),'height':str(group['h']),'as':'geometry'})
            attrs['parent']=group['id']
        if kind=='edge':
            attrs.update(edge='1',value='',style=f'edgeStyle=none;rounded=0;html=1;endArrow=block;endFill=1;endSize=10;strokeColor={e["color"]};strokeWidth={e["sw"]};dashed={int(e["dashed"])};dashPattern=9 7;')
            cell=ET.SubElement(graph,'mxCell',attrs)
            geom=ET.SubElement(cell,'mxGeometry',{'relative':'1','as':'geometry'})
            a,b=e['points'][0],e['points'][-1]
            ET.SubElement(geom,'mxPoint',{'x':str(a[0]),'y':str(a[1]),'as':'sourcePoint'})
            ET.SubElement(geom,'mxPoint',{'x':str(b[0]),'y':str(b[1]),'as':'targetPoint'})
            if len(e['points'])>2:
                arr=ET.SubElement(geom,'Array',{'as':'points'})
                for x,y in e['points'][1:-1]:ET.SubElement(arr,'mxPoint',x=str(x),y=str(y))
            continue
        x,y=e['x'],e['y'];attrs['vertex']='1';attrs['value']=''
        if kind=='rect':
            w,h=e['w'],e['h']
            style=f'rounded={int(e["radius"]>0)};absoluteArcSize=1;arcSize={e["radius"]*2};fillColor={e["fill"]};strokeColor={e["stroke"]};strokeWidth={e["sw"]};'
        elif kind=='text':
            w=e['measured']+6;h=e['size']*1.35
            x=x-w/2 if e['anchor']=='middle' else x-w if e['anchor']=='end' else x
            y-=e['size']*1.02
            attrs['value']=e['text']
            style=f'text;html=0;whiteSpace=wrap;align={"center" if e["anchor"]=="middle" else "right" if e["anchor"]=="end" else "left"};verticalAlign=middle;fontFamily=Arial;fontSize={e["size"]};fontColor={e["color"]};fontStyle={int(e["bold"])};spacing=0;'
        elif kind=='icon':
            w,h=e['w'],e['h']
            mini='<svg xmlns="http://www.w3.org/2000/svg" width="72" height="72" viewBox="0 0 72 72"><g stroke-linecap="round" stroke-linejoin="round">'+e['body']+'</g></svg>'
            style='shape=image;imageAspect=0;aspect=fixed;image=data:image/svg+xml,'+quote(mini,safe='')+';'
        attrs['style']=style
        if group:
            x-=group['x'];y-=group['y']
        cell=ET.SubElement(graph,'mxCell',attrs)
        ET.SubElement(cell,'mxGeometry',{'x':str(x),'y':str(y),'width':str(w),'height':str(h),'as':'geometry'})
    ET.indent(root)
    ET.ElementTree(root).write(HERE/'graphical_abstract_landscape.en.drawio',encoding='utf-8',xml_declaration=True)


def manifest():
    sources=['scripts/05_multi_agent/coordinator.py','scripts/06_agent_evaluation/review_v3/runner_v3.py',
             'docs/event_stage_scoring.md','scripts/06_dashboard/build_dashboard.py',
             'docs/remaining_work_2026-11-09.md','scripts/02_process_mining/process_mining_analysis.py']
    provenance={'created_on':'2026-11-09','language':'en','status':'draft_for_user_review',
                'purpose':'Landscape graphical abstract based on the user-provided style and verified project implementation.',
                'canvas':[W,H],'diagram_kind':'conceptual_integration_not_production_execution_trace',
                'icons':'Original vector line art; no third-party icon assets or generated business data.',
                'source_files':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sources},
                'checks':{'text_widths':len(TEXT_CHECKS),'all_within_limits':True,'node_count':len(NODES),'agent_count':3},
                'limitations':['Historical event replay, not validated live deployment.','AI does not modify detector scores or thresholds.','Bottleneck causes remain unconfirmed.','The dashboard uses saved outputs.'],
                'editable_outputs':['graphical_abstract_landscape.en.svg','graphical_abstract_landscape.en.drawio','render_graphical_abstract.py']}
    (HERE/'graphical_abstract_landscape.en.sources.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n')
    (HERE/'graphical_abstract_landscape.en.layout.json').write_text(json.dumps({'canvas':[W,H],'elements':ELEMENTS,'node_groups':NODE_GROUPS},ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    build();svg_output();drawio_output();manifest()
    print(json.dumps({'nodes':len(NODES),'checked_labels':len(TEXT_CHECKS),'svg_canvas':[W,H],'outputs':str(HERE)}))
