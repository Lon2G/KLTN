"""Restyle the 11 Vietnamese thesis diagrams as editable landscape drawings.

Read-only input: diagram_set_v1 layout snapshots and selected current project docs.
No experiments, model calls, raw data writes, or edits to the previous diagram set.
SVG and diagrams.net share geometry/text; rasterize.mjs exports the PNG previews.
"""
from pathlib import Path
from xml.etree import ElementTree as ET
from urllib.parse import quote
from html import escape
from copy import deepcopy
from PIL import ImageFont
import hashlib, heapq, importlib.util, json, math

HERE=Path(__file__).resolve().parent
SOURCE=HERE.parent/'diagram_set_v1'
ROOT=HERE.parents[3]
FONT=Path('/System/Library/Fonts/Supplemental')
INK='#183042';MUTED='#526577';BLUE='#1387CB';LIME='#DEFA18'
STYLES={'done':('#FFFFFF','#668094','#EEF5FA'),
        'output':('#F6FBF8','#447964','#EAF5EF'),
        'pending':('#FFF9EE','#A0762A','#FFF0D6'),
        'alert':('#FFF5F3','#AE5E4B','#FCE6E0'),
        'note':('#F6F8FA','#7B8791','#EAF0F3')}
spec=importlib.util.spec_from_file_location('abstract_icons',HERE.parent/'graphical_abstract_landscape_v1/render_graphical_abstract.py')
icons=importlib.util.module_from_spec(spec);spec.loader.exec_module(icons)
REG=ImageFont.truetype(str(FONT/'Arial.ttf'),23)
BOLD=ImageFont.truetype(str(FONT/'Arial Bold.ttf'),28)
SMALL=ImageFont.truetype(str(FONT/'Arial.ttf'),20)

# Grid coordinates; content stays upright. Empty cells provide space for branches.
POSITIONS={
 1:{'question':(0,0),'data':(1,0),'split':(2,0),'detector':(3,0),'agents':(3,1),'evaluation':(2,1),'planned':(1,1)},
 2:{'events':(0,0),'process':(1,0),'anomaly':(1,1),'evidence':(2,0),'agents':(2,1),'bi':(3,1)},
 3:{'customers':(0,0),'orders':(1,0),'reviews':(2,0),'payments':(3,0),'products':(1,1),'items':(2,1),'sellers':(3,1),'translation':(0,1),'geo':(0,2),'zip':(2,2)},
 4:{'purchase':(0,0),'approval':(1,0),'handoff':(2,0),'delivery':(3,0),'promise':(2,1),'calendar':(3,1),'basis':(0,1)},
 5:{'raw':(0,0),'normalize':(1,0),'quality':(2,0),'snapshot':(3,0),'audit':(1,1),'cohort':(3,1),'train':(1,2),'validation':(2,2),'test':(3,2)},
 6:{'input':(0,0),'eligible':(1,0),'product':(2,0),'fallback':(3,0),'unavailable':(0,1),'missing':(0,2),'score':(2,1),'output':(2,2)},
 7:{'data':(0,0),'training':(1,0),'frozen':(2,0),'replay':(1,1),'evidence':(2,1),'agents':(3,1),'budget':(3,0),'results':(2,2),'bi':(3,2)},
 8:{'contract':(0,0),'process':(1,0),'context':(2,0),'verifier':(3,0),'errors':(0,1),'revision':(3,1),'publication':(2,1),'coverage':(1,1),'coverage_repair':(1,2),'final':(2,2)},
 9:{'events':(0,0),'dedup':(1,0),'state':(2,0),'strict':(3,0),'assumed':(3,1),'strict_result':(2,1),'assumed_result':(2,2),'storage':(1,2)},
 10:{'data':(0,0),'overview':(1,0),'group':(2,0),'order':(2,1),'evidence':(1,1),'followup':(0,1)},
 11:{'fixed':(0,1),'detectors':(1,0),'agents':(1,2),'metrics':(2,0),'technical':(2,2),'limits':(3,0),'ratings':(3,2),'synthesis':(4,1)},
}
ICON_MAP={
 1:dict(question='research',data='quality',split='split',detector='alert',agents='coordinator',evaluation='process',planned='explanation'),
 2:dict(events='eventlog',process='discovery',anomaly='alert',evidence='quality',agents='coordinator',bi='dashboard'),
 3:dict(customers='manager',orders='database',reviews='review',products='package',items='package',payments='payment',translation='translation',sellers='shop',geo='map',zip='map'),
 4:dict(purchase='cart',approval='quality',handoff='truck',delivery='home',promise='split',calendar='stages',basis='research'),
 5:dict(raw='database',normalize='quality',quality='verifier',snapshot='process',audit='quality',cohort='package',train='reference',validation='process',test='verifier'),
 6:dict(input='eventlog',eligible='quality',unavailable='alert',product='package',fallback='reference',score='calculator',missing='alert',output='dashboard'),
 7:dict(data='database',training='reference',replay='replay',frozen='store',evidence='quality',agents='coordinator',budget='payment',results='verifier',bi='dashboard'),
 8:dict(contract='quality',process='process',context='context',verifier='verifier',publication='explanation',coverage='quality',errors='alert',revision='replay',coverage_repair='apply',final='explanation'),
 9:dict(events='eventlog',dedup='quality',state='eventlog',strict='verifier',assumed='research',strict_result='alert',assumed_result='stages',storage='store'),
 10:dict(data='database',overview='dashboard',group='context',order='eventlog',evidence='verifier',followup='manager'),
 11:dict(fixed='store',detectors='reference',agents='coordinator',metrics='process',technical='calculator',limits='alert',ratings='review',synthesis='research'),
}

# Distinct entry/exit sides prevent a returning edge from looking like a
# two-way relation or falsely joining an unrelated branch at a shared port.
PORT_OVERRIDES={
 1:{('split','agents'):('B','T'),('detector','evaluation'):('B','T')},
 3:{('zip','customers'):('T','B'),('zip','sellers'):('R','B')},
 5:{('quality','audit'):('B','T'),('cohort','train'):('B','T'),('cohort','validation'):('B','T')},
 6:{('eligible','unavailable'):('B','T'),('fallback','unavailable'):('B','R')},
 7:{('frozen','replay'):('B','T'),('agents','results'):('B','T')},
 8:{('verifier','publication'):('B','R'),('verifier','errors'):('B','R'),('coverage','final'):('B','L')},
 9:{('strict','strict_result'):('B','R'),('state','assumed'):('B','L'),('assumed','assumed_result'):('B','R')},
}


def icon_body(name):
    extras={
      'package':'<path d="M9 20 L36 7 L63 20 V52 L36 66 L9 52 Z M9 20 L36 34 L63 20 M36 34 V66 M23 13 L50 27 V40"/>',
      'payment':'<rect x="5" y="15" width="62" height="43" rx="6"/><path d="M5 29 H67 M14 45 H29 M43 45 H58"/>',
      'map':'<path d="M5 19 L24 10 L48 19 L67 10 V56 L48 65 L24 56 L5 65 Z M24 10 V56 M48 19 V65"/><path d="M37 17 C20 17 20 38 37 51 C54 38 54 17 37 17 Z" fill="white"/><circle cx="37" cy="28" r="5"/>',
      'translation':'<path d="M6 13 H40 V43 H6 Z M32 32 H66 V62 H32 Z" fill="white"/><text x="14" y="36" font-family="Arial" font-size="24" fill="#1387CB" stroke="none">A</text><text x="40" y="55" font-family="Arial" font-size="24" fill="#1387CB" stroke="none">V</text>',
      'shop':'<path d="M12 27 V63 H61 V27 M6 27 L14 10 H58 L67 27 M6 27 Q14 38 22 27 Q30 38 38 27 Q46 38 54 27 Q62 38 67 27 M22 63 V44 H37 V63 M47 45 H56 V54 H47 Z"/>',
      'cart':'<path d="M5 11 H15 L24 50 H56 L65 23 H19 M27 32 H59 M31 23 L34 49 M48 23 L47 49"/><circle cx="29" cy="61" r="5"/><circle cx="54" cy="61" r="5"/>',
      'truck':'<path d="M4 17 H43 V52 H4 Z M43 29 H56 L68 42 V52 H43 M53 30 V42 H66"/><circle cx="17" cy="55" r="8" fill="white"/><circle cx="55" cy="55" r="8" fill="white"/>',
      'home':'<path d="M5 32 L35 8 L66 32 M14 28 V62 H57 V28 M27 62 V43 H43 V62"/><path d="M43 25 L49 31 L61 16" stroke="#183042"/>',
      'review':'<path d="M6 8 H66 V48 H29 L14 62 V48 H6 Z"/><path d="M36 17 L40 26 L50 27 L42 34 L44 44 L36 39 L27 44 L29 34 L21 27 L31 26 Z" fill="#EAF5FC"/>',
      'calculator':'<rect x="12" y="5" width="48" height="62" rx="5"/><path d="M20 14 H52 V28 H20 Z M20 40 H28 M24 36 V44 M41 40 H51 M20 55 H28 M41 51 L51 60 M51 51 L41 60"/>',
      'research':'<path d="M6 13 Q20 5 35 13 Q50 5 66 13 V55 Q50 47 35 55 Q20 47 6 55 Z M35 13 V55 M13 23 H27 M13 32 H27 M43 23 H57"/><circle cx="50" cy="46" r="12" fill="white"/><path d="M59 55 L68 65" stroke="#183042"/>',
    }
    if name in extras:
        return f'<g fill="none" stroke="{BLUE}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">{extras[name]}</g>'
    return icons.icon_body(name)


def wrap(text,font,width):
    result=[]
    for paragraph in text.split('\n'):
        if not paragraph: result.append('');continue
        words=paragraph.split();line=''
        for word in words:
            if font.getlength(word)>width:
                raise ValueError(f'Unbreakable word exceeds width: {word}')
            candidate=(line+' '+word).strip()
            if line and font.getlength(candidate)>width:
                result.append(line);line=word
            else:line=candidate
        result.append(line)
    return result


def update_status(d):
    """Only factual status edits justified by 09/11 code/results; log every change."""
    num=int(d['stem'][:2]);n={x['id']:x for x in d['nodes']};changes=[]
    def change(key,**kw):
        for k,v in kw.items():
            if n[key].get(k)!=v:
                changes.append({'node':key,'field':k,'old':n[key].get(k),'new':v});n[key][k]=v
    if num==1:
        change('agents',body='Quy trình → bối cảnh → kiểm chứng\nCông cụ tính toán có nguồn\nĐối chứng trên 20 case phát triển')
        change('evaluation',body='Độ bao phủ, ổn định, đối chiếu trễ hẹn\nĐã chạy 80/80 lượt đối chứng AI; giữ cả lượt lỗi')
        change('planned',body='Chấm định tính / Kappa · hoàn thiện và đánh giá BI\nTổng hợp kết luận; chưa chứng minh ưu thế Multi-Agent')
    if num==2:
        change('bi',title='Business Intelligence — bản local',body='Tổng hợp nhóm cần chú ý → xem đơn → xem bằng chứng\nTrình bày kết quả đã lưu; chưa kiểm chứng vận hành thực tế',kind='output')
        d['footer'][1]='Khung khái niệm; quan hệ trong hình không chứng minh quan hệ nhân quả.'
    if num==7:
        change('bi',title='BI local — đã có bản chạy',body='Tổng quan → nhóm → đơn → bằng chứng\nHTML tĩnh từ kết quả đã lưu',kind='output')
        d['caption']='Kiến trúc thành phần phân tích, lớp giải thích và BI local từ kết quả đã lưu.'
        d['footer'][1]='Đã có thực nghiệm thành phần và đối chứng AI; chưa kiểm chứng đầu-cuối trong sản xuất.'
    if num==10:
        d['title']='LUỒNG KHAI THÁC BI LOCAL'
        d['subtitle']='Bản HTML từ kết quả đã lưu · Không phải dashboard trực tuyến trong sản xuất'
        change('overview',title='Tổng quan quản lý',body='Bộ lọc tập dữ liệu / tháng / công đoạn / bang / trạng thái giao\nThống kê cảnh báo và đơn chưa đánh giá được',kind='done')
        change('group',body='Theo công đoạn, bang khách hàng, seller của đơn một seller\nSố lượng / tỷ lệ kèm mẫu số\nNhóm nhỏ được ghi chỉ tham khảo',kind='done')
        change('order',body='Giữ đơn giao sớm có cảnh báo\nTách điểm công đoạn với giao trễ hẹn\nHiển thị lý do chưa đánh giá',kind='done')
        change('evidence',title='Xem bằng chứng của đơn',body='Mốc thực tế · thời lượng · trung bình / số mẫu / cấp tham chiếu · điểm\nHiển thị nhận định AI đã kiểm chứng khi có đầu ra lưu sẵn',kind='done')
        change('followup',title='Đề xuất kiểm tra và hỗ trợ quyết định',body='Đề xuất từ phản hồi AI đã kiểm chứng khi có\nNgười quản lý quyết định; chưa đánh giá hiệu quả sử dụng',kind='output')
        d['footer'][0]='Đã có bản local; chỉ hiển thị giải thích AI với đầu ra đã qua kiểm tra. Kết quả lấy từ hồ sơ đã lưu.'
        d['footer'].append('Không tuyên bố mọi chức năng phân tích sâu hoặc hiệu quả quản trị đã được hoàn thiện / kiểm chứng.')
    if num==11:
        change('agents',body='Quy tắc / một agent / ba agent\nCùng đơn, detector và công cụ\nHai cấu hình trần tài nguyên\n80 lượt AI trên 20 case phát triển')
        change('technical',title='Kiểm chứng kỹ thuật — đối chứng',body='Khớp nguồn · bao phủ cảnh báo\nHoàn thành / lỗi · thời gian · token · chi phí\n80 lượt đã chạy; không phải 80 lượt đều thành công')
        d['footer'][2]='Giữ cả lượt lỗi/thiếu; 20 case phát triển đã được xem trước, chưa là kiểm chứng độc lập.'
    for e in d['edges']:
        if e['end']=='bi' and num in (2,7): e['pending']=False
        if num==10: e['pending']=False
    return changes


def prepare(d):
    num=int(d['stem'][:2]);positions=POSITIONS[num]
    pitch_x=740 if num==4 else 720
    for i,n in enumerate(d['nodes']):
        col,row=positions[n['id']]
        w=520
        if (num,n['id']) in [(4,'basis'),(6,'score'),(6,'output'),(8,'final')]:w=1240 if num!=4 else 1260
        if (num,n['id'])==(3,'zip'):w=800
        n.update(x=110+col*pitch_x,y=260+row*500,w=w,num=i+1,icon=ICON_MAP[num][n['id']])
        n['title_lines']=wrap(n['title'],BOLD,w-140)
        n['body_lines']=wrap(n['body'],REG,w-48)
        n['head_h']=max(96,44+len(n['title_lines'])*34)
        n['h']=max(230,n['head_h']+36+len(n['body_lines'])*31)
        if n['h']>410:raise ValueError((d['stem'],n['id'],'node too tall',n['h']))
    d['width']=max(n['x']+n['w'] for n in d['nodes'])+110
    d['graph_bottom']=max(n['y']+n['h'] for n in d['nodes'])+100
    d['height']=max(1050,d['graph_bottom']+210+len(d['footer'])*31)
    assert d['width']>d['height'],(d['stem'],d['width'],d['height'])
    d['labels']=[]


def segment_hits(a,b,box,pad=0):
    l,t,r,bt=box; l-=pad;t-=pad;r+=pad;bt+=pad
    if abs(a[0]-b[0])<1e-6:
        return l<a[0]<r and max(min(a[1],b[1]),t)<min(max(a[1],b[1]),bt)
    if abs(a[1]-b[1])<1e-6:
        return t<a[1]<bt and max(min(a[0],b[0]),l)<min(max(a[0],b[0]),r)
    raise ValueError('Nonorthogonal segment')


def compact(points):
    result=[]
    for p in points:
        if result and result[-1]==p:continue
        while len(result)>1 and ((result[-2][0]==result[-1][0]==p[0]) or (result[-2][1]==result[-1][1]==p[1])):
            result.pop()
        result.append(p)
    return result


def route_all(d):
    nodes={n['id']:n for n in d['nodes']}
    obstacles={k:(n['x']-26,n['y']-26,n['x']+n['w']+26,n['y']+n['h']+26) for k,n in nodes.items()}
    all_segments=[]
    def port(n,side):
        x,y,w,h=n['x'],n['y'],n['w'],n['h']
        p={'R':(x+w,y+h/2),'L':(x,y+h/2),'T':(x+w/2,y),'B':(x+w/2,y+h)}[side]
        dx,dy={'R':(48,0),'L':(-48,0),'T':(0,-48),'B':(0,48)}[side]
        return p,(p[0]+dx,p[1]+dy)
    xs={65,d['width']-65};ys={205,d['graph_bottom']-35}
    for n in nodes.values():
        for side in 'RLTB':
            _,p=port(n,side);xs.add(p[0]);ys.add(p[1])
        for delta in (48,95):
            xs.update((n['x']-delta,n['x']+n['w']+delta))
            ys.update((n['y']-delta,n['y']+n['h']+delta))
    xs=sorted(x for x in xs if 50<=x<=d['width']-50)
    ys=sorted(y for y in ys if 195<=y<=d['graph_bottom'])
    valid={(ix,iy) for ix,x in enumerate(xs) for iy,y in enumerate(ys)
           if not any(l<x<r and t<y<b for l,t,r,b in obstacles.values())}
    adjacency={}
    for ix,iy in valid:
        neighbors=[]
        for jx,jy,axis in [(ix-1,iy,0),(ix+1,iy,0),(ix,iy-1,1),(ix,iy+1,1)]:
            if (jx,jy) not in valid:continue
            a,b=(xs[ix],ys[iy]),(xs[jx],ys[jy])
            if any(segment_hits(a,b,box) for box in obstacles.values()):continue
            neighbors.append((jx,jy,axis,math.dist(a,b)))
        adjacency[(ix,iy)]=neighbors
    def find(a,b):
        s=(xs.index(a[0]),ys.index(a[1]),-1);goal=(xs.index(b[0]),ys.index(b[1]))
        cost={s:0};prev={};heap=[(0,s)]
        while heap:
            _,state=heapq.heappop(heap)
            if state[:2]==goal:
                route=[];cur=state
                while True:
                    route.append((xs[cur[0]],ys[cur[1]]))
                    if cur==s:break
                    cur=prev[cur]
                return list(reversed(route)),cost[state]
            for nx,ny,axis,dist in adjacency.get(state[:2],[]):
                p,q=(xs[state[0]],ys[state[1]]),(xs[nx],ys[ny])
                reuse=0
                for a0,b0 in all_segments:
                    if axis==0 and a0[1]==b0[1]==p[1]:reuse+=max(0,min(max(p[0],q[0]),max(a0[0],b0[0]))-max(min(p[0],q[0]),min(a0[0],b0[0])))
                    if axis==1 and a0[0]==b0[0]==p[0]:reuse+=max(0,min(max(p[1],q[1]),max(a0[1],b0[1]))-max(min(p[1],q[1]),min(a0[1],b0[1])))
                nxt=(nx,ny,axis);score=cost[state]+dist+(45 if state[2] not in (-1,axis) else 0)+reuse*2.0
                if score<cost.get(nxt,math.inf):
                    cost[nxt]=score;prev[nxt]=state
                    heuristic=abs(xs[nx]-b[0])+abs(ys[ny]-b[1])
                    heapq.heappush(heap,(score+heuristic,nxt))
        return None,math.inf
    for e in d['edges']:
        if int(d['stem'][:2])==3 and (e['start'],e['end'])==('zip','customers'):
            pts=[(1950,1260),(1950,1180),(65,1180),(65,600),(370,600),(370,490)]
            e.update(points=pts,start_side='T',end_side='B')
            all_segments.extend(zip(pts[:-1],pts[1:]));continue
        sn,tn=nodes[e['start']],nodes[e['end']]
        dx=tn['x']+tn['w']/2-sn['x']-sn['w']/2;dy=tn['y']+tn['h']/2-sn['y']-sn['h']/2
        hpair=('R','L') if dx>=0 else ('L','R')
        vpair=('B','T') if dy>=0 else ('T','B')
        primary=hpair if abs(dx)>abs(dy) else vpair
        options=[primary,vpair if primary==hpair else hpair]
        options.extend([(primary[0],vpair[1]),(vpair[0],primary[1])])
        override=PORT_OVERRIDES.get(int(d['stem'][:2]),{}).get((e['start'],e['end']))
        if override:options=[override]
        best=None
        for rank,(ss,ts) in enumerate(dict.fromkeys(options)):
            sp,sa=port(sn,ss);tp,ta=port(tn,ts)
            if any(segment_hits(sp,sa,box) for k,box in obstacles.items() if k!=e['start']):continue
            if any(segment_hits(tp,ta,box) for k,box in obstacles.items() if k!=e['end']):continue
            pts,cost=find(sa,ta)
            if pts is None:continue
            cost+=rank*110
            if best is None or cost<best[0]:best=(cost,compact([sp,*pts,tp]),ss,ts)
        if best is None:raise ValueError(f'No route {d["stem"]}: {e["start"]}->{e["end"]}')
        _,pts,ss,ts=best;e.update(points=pts,start_side=ss,end_side=ts)
        all_segments.extend(zip(pts[:-1],pts[1:]))
    # Labels placed after routing, outside nodes and other labels.
    used=[]
    for e in d['edges']:
        e['at']=None
        if not e.get('label'):continue
        lines=wrap(e['label'],SMALL,155);width=max(SMALL.getlength(s) for s in lines)+20;height=len(lines)*25+16
        candidates=[]
        for a,b in zip(e['points'][:-1],e['points'][1:]):
            length=math.dist(a,b)
            if length<45:continue
            for f in (.5,.3,.7):
                x=a[0]+(b[0]-a[0])*f;y=a[1]+(b[1]-a[1])*f
                if length>(width+25 if a[1]==b[1] else height+25):candidates.append((x,y))
                if a[1]==b[1]:
                    candidates.extend([(x,y-height/2-12),(x,y+height/2+12)])
                else:candidates.extend([(x+width/2+12,y),(x-width/2-12,y)])
        preferred_labels={(6,'fallback','score'):(2350,538),(8,'verifier','publication'):(2300,585)}
        preferred=preferred_labels.get((int(d['stem'][:2]),e['start'],e['end']))
        if preferred:candidates.insert(0,preferred)
        chosen=None
        for x,y in candidates:
            box=(x-width/2,y-height/2,x+width/2,y+height/2)
            def overlaps(a,b):return a[0]<b[2] and a[2]>b[0] and a[1]<b[3] and a[3]>b[1]
            if box[0]<45 or box[2]>d['width']-45 or box[1]<185 or box[3]>d['graph_bottom']:continue
            if any(overlaps(box,(n['x']-8,n['y']-18,n['x']+n['w']+8,n['y']+n['h']+8)) for n in nodes.values()):continue
            if any(overlaps(box,b) for b in used):continue
            other_segments=[(a,b) for other in d['edges'] if other is not e for a,b in zip(other['points'][:-1],other['points'][1:])]
            if any(segment_hits(a,b,box) for a,b in other_segments):continue
            chosen=(x,y,box);break
        if chosen is None:raise ValueError(f'No label space {d["stem"]}: {e["label"]}')
        e.update(at=chosen[:2],label_lines=lines,label_box=chosen[2]);used.append(chosen[2])
    # Verify no unrelated card is crossed by any connection.
    for e in d['edges']:
        for k,n in nodes.items():
            if k in (e['start'],e['end']):continue
            box=(n['x'],n['y'],n['x']+n['w'],n['y']+n['h'])
            assert not any(segment_hits(a,b,box) for a,b in zip(e['points'][:-1],e['points'][1:])),(d['stem'],e,k)


def svgtxt(x,y,lines,size=23,bold=False,color=INK,anchor='start',line_h=None):
    line_h=line_h or size*1.3
    return ''.join(f'<text x="{x}" y="{y+i*line_h}" font-family="Arial, Helvetica, sans-serif" font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}" text-anchor="{anchor}">{escape(s)}</text>' for i,s in enumerate(lines))


def render_svg(d):
    arrowheads=[]
    W,H=d['width'],d['height'];parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',f'<title>{escape(d["title"])}</title>',f'<rect width="{W}" height="{H}" fill="white"/>']
    parts.append(svgtxt(65,68,[d['title']],40,True))
    parts.append(svgtxt(65,112,wrap(d['subtitle'],REG,W-130),23,color=MUTED))
    parts.append(f'<rect x="65" y="147" width="{W-130}" height="4" fill="{LIME}"/><rect x="35" y="180" width="{W-70}" height="{d["graph_bottom"]-165}" rx="22" fill="#FCFDFE" stroke="#C5D2DB" stroke-width="1.5"/>')
    for e in d['edges']:
        pts=e['points'];coords=' '.join(f'{x},{y}' for x,y in pts);color='#A0762A' if e.get('pending') else INK
        # White halo makes crossing connections distinguishable.
        parts.append(f'<polyline points="{coords}" fill="none" stroke="white" stroke-width="10" stroke-linejoin="round"/>')
        dash=' stroke-dasharray="10 7"' if e.get('pending') else ''
        parts.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round"{dash}/>')
        a,b=pts[-2:];angle=math.atan2(b[1]-a[1],b[0]-a[0]);base=(b[0]-15*math.cos(angle),b[1]-15*math.sin(angle))
        head=[b,(base[0]-6*math.sin(angle),base[1]+6*math.cos(angle)),(base[0]+6*math.sin(angle),base[1]-6*math.cos(angle))]
        arrowheads.append('<polygon points="'+' '.join(f'{x},{y}' for x,y in head)+f'" fill="{color}"/>')
    parts.extend(arrowheads)
    for n in d['nodes']:
        x,y,w,h=n['x'],n['y'],n['w'],n['h'];fill,stroke,header=STYLES[n['kind']]
        dash=' stroke-dasharray="10 7"' if n['kind']=='pending' else ''
        parts.append(f'<g id="{n["id"]}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="15" fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}/><rect x="{x+1}" y="{y+1}" width="{w-2}" height="{n["head_h"]}" rx="14" fill="{header}"/><line x1="{x}" y1="{y+n["head_h"]}" x2="{x+w}" y2="{y+n["head_h"]}" stroke="{stroke}" stroke-opacity=".3"/>')
        parts.append(f'<g transform="translate({x+22},{y+(n["head_h"]-68)/2}) scale({68/72})" stroke-linecap="round" stroke-linejoin="round">{icon_body(n["icon"])}</g>')
        parts.append(svgtxt(x+114,y+42,n['title_lines'],28,True,line_h=34))
        parts.append(svgtxt(x+24,y+n['head_h']+36,n['body_lines'],23,line_h=31))
        parts.append(f'<rect x="{x-13}" y="{y-17}" width="45" height="45" rx="8" fill="{LIME}" stroke="{INK}" stroke-width="1.8"/>')
        parts.append(svgtxt(x+9.5,y+14,[str(n['num'])],28,True,anchor='middle'))
        parts.append('</g>')
    for e in d['edges']:
        if not e.get('label'):continue
        x,y=e['at'];l,t,r,b=e['label_box']
        parts.append(f'<rect x="{l}" y="{t}" width="{r-l}" height="{b-t}" rx="5" fill="white" stroke="#D8E1E7"/>')
        parts.append(svgtxt(x,t+27,e['label_lines'],20,anchor='middle',line_h=25))
    fy=d['graph_bottom']+59
    for i,(label,kind) in enumerate([('Có triển khai / kết quả','done'),('Đầu ra','output'),('Chưa hoàn tất','pending'),('Chưa đủ điều kiện','alert')]):
        x=65+i*420;fill,stroke,_=STYLES[kind];dash=' stroke-dasharray="7 5"' if kind=='pending' else ''
        parts.append(f'<rect x="{x}" y="{fy-18}" width="33" height="22" rx="4" fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}/>')
        parts.append(svgtxt(x+45,fy,[label],20,color=MUTED))
    for i,line in enumerate(d['footer']):parts.append(svgtxt(65,fy+48+i*31,[line],22,color=MUTED))
    parts.append(svgtxt(W-65,H-25,['KLTN-Lon2G · 09/11/2026 · Số vàng là mã khối trong hình, không phải số hình của báo cáo.'],18,color=MUTED,anchor='end'))
    parts.append('</svg>')
    (HERE/(d['stem']+'.svg')).write_text('\n'.join(parts)+'\n')


def drawio_page(d):
    page=ET.Element('diagram',id=d['stem'],name=d['stem'][:2]+' · '+d['title'])
    model=ET.SubElement(page,'mxGraphModel',grid='1',gridSize='10',guides='1',tooltips='1',connect='1',arrows='1',fold='1',page='1',pageScale='1',pageWidth=str(d['width']),pageHeight=str(d['height']),background='#FFFFFF')
    root=ET.SubElement(model,'root');ET.SubElement(root,'mxCell',id='0');ET.SubElement(root,'mxCell',id='1',parent='0')
    serial=[0]
    def cell(x,y,w,h,value='',style='',parent='1',cid=None):
        serial[0]+=1;c=ET.SubElement(root,'mxCell',id=cid or f'part-{serial[0]}',value=value,vertex='1',parent=parent,style=style)
        ET.SubElement(c,'mxGeometry',{'x':str(x),'y':str(y),'width':str(w),'height':str(h),'as':'geometry'});return c
    def text(x,y,w,lines,size=23,bold=False,color=INK,parent='1',align='left',cid=None):
        value='<div style="line-height:130%">'+'<br>'.join(escape(s) for s in lines)+'</div>'
        return cell(x,y,w,len(lines)*size*1.4+4,value,f'text;html=1;whiteSpace=wrap;align={align};verticalAlign=top;spacing=0;fontFamily=Arial;fontSize={size};fontStyle={int(bold)};fontColor={color};',parent,cid)
    text(65,28,d['width']-130,[d['title']],40,True,cid='figure-title')
    text(65,86,d['width']-130,wrap(d['subtitle'],REG,d['width']-130),23,color=MUTED)
    cell(65,147,d['width']-130,4,style=f'fillColor={LIME};strokeColor=none;')
    cell(35,180,d['width']-70,d['graph_bottom']-165,style='rounded=1;absoluteArcSize=1;arcSize=44;fillColor=#FCFDFE;strokeColor=#C5D2DB;strokeWidth=1.5;locked=1;',cid='background')
    for n in d['nodes']:
        fill,stroke,header=STYLES[n['kind']]
        dash='dashed=1;dashPattern=10 7;' if n['kind']=='pending' else ''
        cell(n['x'],n['y'],n['w'],n['h'],style=f'rounded=1;absoluteArcSize=1;arcSize=30;fillColor={fill};strokeColor={stroke};strokeWidth=2;container=1;collapsible=0;{dash}',cid=n['id'])
        # Child labels/icons move with the parent card; all text remains native.
        cell(1,1,n['w']-2,n['head_h'],style=f'rounded=1;absoluteArcSize=1;arcSize=28;fillColor={header};strokeColor=none;connectable=0;',parent=n['id'])
        mini='<svg xmlns="http://www.w3.org/2000/svg" width="72" height="72" viewBox="0 0 72 72"><g stroke-linecap="round" stroke-linejoin="round">'+icon_body(n['icon'])+'</g></svg>'
        cell(22,(n['head_h']-68)/2,68,68,style='shape=image;imageAspect=0;aspect=fixed;connectable=0;image=data:image/svg+xml,'+quote(mini,safe='')+';',parent=n['id'])
        text(114,14,n['w']-140,n['title_lines'],28,True,parent=n['id'])
        text(24,n['head_h']+12,n['w']-48,n['body_lines'],23,parent=n['id'])
        cell(-13,-17,45,45,str(n['num']),f'rounded=1;absoluteArcSize=1;arcSize=16;fillColor={LIME};strokeColor={INK};strokeWidth=1.8;fontFamily=Arial;fontSize=28;fontStyle=1;fontColor={INK};connectable=0;',n['id'])
    nodes={n['id']:n for n in d['nodes']}
    for i,e in enumerate(d['edges']):
        s,t=nodes[e['start']],nodes[e['end']];a,b=e['points'][0],e['points'][-1]
        rel=lambda p,n:((p[0]-n['x'])/n['w'],(p[1]-n['y'])/n['h'])
        ex,ey=rel(a,s);nx,ny=rel(b,t)
        color='#A0762A' if e.get('pending') else INK
        st=f'edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;endFill=1;endSize=10;strokeColor={color};strokeWidth=3;jumpStyle=arc;jumpSize=10;exitX={ex};exitY={ey};exitDx=0;exitDy=0;exitPerimeter=0;entryX={nx};entryY={ny};entryDx=0;entryDy=0;entryPerimeter=0;'
        if e.get('pending'):st+='dashed=1;dashPattern=10 7;'
        c=ET.SubElement(root,'mxCell',id=f'edge-{i}',value='',edge='1',parent='1',source=e['start'],target=e['end'],style=st)
        g=ET.SubElement(c,'mxGeometry',{'relative':'1','as':'geometry'})
        if len(e['points'])>2:
            arr=ET.SubElement(g,'Array',{'as':'points'})
            for x,y in e['points'][1:-1]:ET.SubElement(arr,'mxPoint',x=str(x),y=str(y))
        if e.get('label'):
            # Native edge label with offset from route midpoint; moves with its edge.
            pts=e['points'];total=sum(math.dist(a,b) for a,b in zip(pts[:-1],pts[1:]));target=total/2;run=0
            mx,my=pts[0]
            for a,b in zip(pts[:-1],pts[1:]):
                length=math.dist(a,b)
                if run+length>=target:
                    frac=(target-run)/length;mx=a[0]+frac*(b[0]-a[0]);my=a[1]+frac*(b[1]-a[1]);break
                run+=length
            l,top,r,bt=e['label_box'];x,y=e['at']
            label=ET.SubElement(root,'mxCell',id=f'edge-label-{i}',value='<br>'.join(escape(s) for s in e['label_lines']),vertex='1',connectable='0',parent=f'edge-{i}',style=f'edgeLabel;html=1;align=center;verticalAlign=middle;fontFamily=Arial;fontSize=20;fontColor={INK};fillColor=#FFFFFF;strokeColor=#D8E1E7;rounded=1;spacing=4;')
            lg=ET.SubElement(label,'mxGeometry',{'x':'0','y':'0','width':str(r-l),'height':str(bt-top),'relative':'1','as':'geometry'})
            ET.SubElement(lg,'mxPoint',{'x':str(x-mx-(r-l)/2),'y':str(y-my-(bt-top)/2),'as':'offset'})
    fy=d['graph_bottom']+59
    for i,(label,kind) in enumerate([('Có triển khai / kết quả','done'),('Đầu ra','output'),('Chưa hoàn tất','pending'),('Chưa đủ điều kiện','alert')]):
        x=65+i*420;fill,stroke,_=STYLES[kind];dash='dashed=1;' if kind=='pending' else ''
        cell(x,fy-18,33,22,style=f'rounded=1;fillColor={fill};strokeColor={stroke};strokeWidth=2;{dash}')
        text(x+45,fy-21,360,[label],20,color=MUTED)
    for i,line in enumerate(d['footer']):text(65,fy+25+i*31,d['width']-130,[line],22,color=MUTED)
    text(65,d['height']-45,d['width']-130,['KLTN-Lon2G · 09/11/2026 · Số vàng là mã khối trong hình, không phải số hình của báo cáo.'],18,color=MUTED,align='right')
    return page


def main():
    HERE.mkdir(exist_ok=True);(HERE/'drawio').mkdir(exist_ok=True)
    all_pages=ET.Element('mxfile',host='app.diagrams.net',agent='KLTN-Lon2G landscape v2',version='24.0.0')
    manifest={'created_on':'2026-11-09','language':'vi','source':'diagram_set_v1','status':'draft_for_user_review','new_experiments':False,'api_calls':0,'figures':[]}
    for src in sorted(SOURCE.glob('*.layout.json')):
        d=json.loads(src.read_text());original=deepcopy(d)
        changes=update_status(d);prepare(d);route_all(d);render_svg(d)
        page=drawio_page(d);all_pages.append(deepcopy(page))
        single=ET.Element('mxfile',host='app.diagrams.net',agent='KLTN-Lon2G landscape v2',version='24.0.0');single.append(page);ET.indent(single)
        ET.ElementTree(single).write(HERE/'drawio'/(d['stem']+'.drawio'),encoding='utf-8',xml_declaration=True)
        # Same graph topology and core formulas as the original, with logged status edits.
        assert [(e['start'],e['end'],e['label']) for e in original['edges']]==[(e['start'],e['end'],e['label']) for e in d['edges']]
        for old,new in zip(original['nodes'],d['nodes']):
            assert old['id']==new['id']
            if not any(c['node']==old['id'] for c in changes):assert old['title']==new['title'] and old['body']==new['body']
        (HERE/(d['stem']+'.layout.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
        manifest['figures'].append({'stem':d['stem'],'title':d['title'],'chapter':d['chapter'],'size':[d['width'],d['height']],'nodes':len(d['nodes']),'edges':len(d['edges']),'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'status_updates':changes,'checks':'Text wrapped; paths do not cross unrelated cards; edge labels do not overlap cards/edges; topology preserved.'})
        print(d['stem'],d['width'],d['height'],'OK',flush=True)
    ET.indent(all_pages);ET.ElementTree(all_pages).write(HERE/'diagram_set_landscape_v2.vi.drawio',encoding='utf-8',xml_declaration=True)
    refs=['docs/remaining_work_2026-11-09.md','docs/agent_comparison_v3_results_2026-11-09.md','scripts/06_dashboard/build_dashboard.py','scripts/06_dashboard/template.html']
    manifest['status_sources']={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in refs}
    (HERE/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print('Combined diagrams.net file: 11 pages; all source figures preserved.')


if __name__=='__main__':main()
