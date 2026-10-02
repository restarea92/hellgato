"""Render the README communication diagram as a seamless, looping GIF."""

from bisect import bisect_left
from math import hypot
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


OUTPUT = Path(__file__).resolve().parent
WIDTH, HEIGHT, SCALE = 1200, 610, 2
BACKGROUND = '#101827'
SURFACE = '#172337'
BORDER = '#334259'
TEXT = '#edf5fb'
MUTED = '#a3b3c8'
INPUT = '#65e3bd'
DISPLAY = '#f4bd70'
FONT = Path('C:/Windows/Fonts/segoeui.ttf')
SEMIBOLD = Path('C:/Windows/Fonts/seguisb.ttf')
FRAMES, FRAME_MS = 160, 50
CENTERS = [135, 445, 755, 1065]
NODE_WIDTH, NODE_HEIGHT, NODE_Y = 180, 108, 286


def font(size, bold=False):
    return ImageFont.truetype(str(SEMIBOLD if bold else FONT), int(size * SCALE))


def text(draw, xy, value, size=16, color=TEXT, bold=False, anchor='mm'):
    draw.text(tuple(v * SCALE for v in xy), value, font=font(size, bold), fill=color, anchor=anchor)


def line(draw, points, color, width=2):
    draw.line([(round(x*SCALE), round(y*SCALE)) for x,y in points], fill=color, width=round(width*SCALE), joint='curve')


def rect(draw, bounds, fill, outline=None, radius=12):
    draw.rounded_rectangle(tuple(round(v*SCALE) for v in bounds), radius=radius*SCALE, fill=fill, outline=outline, width=SCALE)


def rounded_path(vertices, radius=16):
    result = [vertices[0]]
    for i in range(1, len(vertices)-1):
        previous, corner, following = vertices[i-1:i+2]
        before = hypot(corner[0]-previous[0], corner[1]-previous[1])
        after = hypot(following[0]-corner[0], following[1]-corner[1])
        distance = min(radius, before/2, after/2)
        start = tuple(corner[j]+(previous[j]-corner[j])*distance/before for j in (0,1))
        end = tuple(corner[j]+(following[j]-corner[j])*distance/after for j in (0,1))
        result.append(start)
        for step in range(1, 13):
            t = step/12
            result.append(tuple((1-t)**2*start[j]+2*(1-t)*t*corner[j]+t*t*end[j] for j in (0,1)))
    result.append(vertices[-1])
    return result


def route(reverse=False):
    centers = CENTERS[::-1] if reverse else CENTERS
    port_y, lane_y = (310, 410) if reverse else (262, 160)
    sign = -1 if reverse else 1
    points = [(centers[0], port_y)]
    for first, second in zip(centers, centers[1:]):
        exit_x = first + sign*NODE_WIDTH/2
        entry_x = second - sign*NODE_WIDTH/2
        points.extend([(exit_x,port_y),(exit_x,lane_y),(entry_x,lane_y),(entry_x,port_y),(second,port_y)])
    path = rounded_path(points)
    lengths = [0.0]
    for first, second in zip(path,path[1:]):
        lengths.append(lengths[-1]+hypot(second[0]-first[0],second[1]-first[1]))
    return path,lengths


def point_at(route_data, distance):
    points,lengths = route_data
    distance = max(0, min(lengths[-1], distance))
    index = min(len(points)-1, max(1, bisect_left(lengths,distance)))
    a,b = points[index-1:index+1]
    t = (distance-lengths[index-1])/(lengths[index]-lengths[index-1])
    return tuple(a[j]+t*(b[j]-a[j]) for j in (0,1))


INPUT_ROUTE, DISPLAY_ROUTE = route(), route(True)


def mix(color, amount):
    rgb = tuple(int(color[i:i+2],16) for i in (1,3,5))
    bg = tuple(int(BACKGROUND[i:i+2],16) for i in (1,3,5))
    return tuple(round(a*amount+b*(1-amount)) for a,b in zip(rgb,bg))


def pipes(draw):
    for data,color in [(INPUT_ROUTE,INPUT),(DISPLAY_ROUTE,DISPLAY)]:
        line(draw,data[0],mix(color,.22),2)
    for i in range(3):
        x = (CENTERS[i]+CENTERS[i+1])/2
        line(draw,[(x-5,155),(x+1,160),(x-5,165)],INPUT,2)
        line(draw,[(x+5,405),(x-1,410),(x+5,415)],DISPLAY,2)


def particles(draw, phase):
    for data,color,square,offset in [(INPUT_ROUTE,INPUT,False,0),(DISPLAY_ROUTE,DISPLAY,True,.125)]:
        length = data[1][-1]
        for packet in range(4):
            distance = ((phase+packet/4+offset)%1)*length
            for trail in range(7,-1,-1):
                position = distance-trail*7
                if position < 0:
                    continue
                x,y = point_at(data,position)
                opacity = (1-trail/9)*min(1,distance/24,(length-distance)/24)
                radius = 4.5 if trail==0 else 2.5
                bounds = tuple(round(v*SCALE) for v in (x-radius,y-radius,x+radius,y+radius))
                fill = mix(color,opacity)
                if square:
                    draw.rounded_rectangle(bounds,radius=SCALE,fill=fill)
                else:
                    draw.ellipse(bounds,fill=fill)


def nodes(draw):
    titles = ['N4 Pro','USB adapter','CORA bridge','Stream Deck']
    subtitles = ['10 LCD keys · 4 dials','Mirabox SDK · Python','Protocol server · Node.js','Official app']
    roles = ['Keys / dials / touch','Decode input · write images','Input reports · image receive','Actions · plugins · rendering']
    for x,title,subtitle,role in zip(CENTERS,titles,subtitles,roles):
        rect(draw,(x-90,232,x+90,340),SURFACE,BORDER)
        text(draw,(x,258),title,21,bold=True)
        text(draw,(x,285),subtitle,14,MUTED)
        text(draw,(x,315),role,12,MUTED)
    for index,x in enumerate(CENTERS):
        text(draw,(x,359),['Hardware','Hellgato','Hellgato','Action engine'][index],12,MUTED)


def render(phase):
    image = Image.new('RGB',(WIDTH*SCALE,HEIGHT*SCALE),BACKGROUND)
    draw = ImageDraw.Draw(image)
    text(draw,(45,43),'HELLGATO',15,INPUT,bold=True,anchor='lm')
    text(draw,(1155,43),'0.0.5-beta',13,MUTED,anchor='rm')
    text(draw,(45,81),'One device. Two directions.',30,bold=True,anchor='lm')
    text(draw,(45,118),'Input to Stream Deck. Display feedback to N4 Pro.',16,MUTED,anchor='lm')
    pipes(draw)
    particles(draw,phase)
    nodes(draw)
    for x,title in zip([290,600,910],['USB input','stdin pipe','CORA TCP :5344']):
        text(draw,(x,184),title,14,MUTED)
    for x,title in zip([290,600,910],['USB display output','Image files + JSONL','CORA TCP :5344']):
        text(draw,(x,444),title,14,MUTED)
    draw.ellipse((46*SCALE,486*SCALE,54*SCALE,494*SCALE),fill=INPUT)
    text(draw,(65,490),'INPUT  →',14,INPUT,bold=True,anchor='lm')
    draw.rectangle((202*SCALE,486*SCALE,210*SCALE,494*SCALE),fill=DISPLAY)
    text(draw,(221,490),'←  DISPLAY',14,DISPLAY,bold=True,anchor='lm')
    text(draw,(1155,490),'Local TCP · 127.0.0.1  |  First pairing :5343',14,MUTED,anchor='rm')
    line(draw,[(45,520),(1155,520)],BORDER,1)
    text(draw,(45,548),'Stream Deck runs the actions and plugins.',15,anchor='lm')
    text(draw,(45,576),'Display path: latest updates per batch · partial touch-strip frames after initialization',14,MUTED,anchor='lm')
    text(draw,(1155,576),'Illustrative motion',12,MUTED,anchor='rm')
    return image.resize((WIDTH,HEIGHT),Image.Resampling.LANCZOS)


def main():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    first = render(0)
    first.save(OUTPUT/'hellgato-flow.png')
    palette = first.quantize(colors=128,method=Image.Quantize.MEDIANCUT)
    frames = [render(i/FRAMES).quantize(palette=palette,dither=Image.Dither.NONE) for i in range(FRAMES)]
    frames[0].save(OUTPUT/'hellgato-flow.gif',save_all=True,append_images=frames[1:],duration=FRAME_MS,loop=0,optimize=True,disposal=1)
    with Image.open(OUTPUT/'hellgato-flow.gif') as gif:
        assert gif.info['loop']==0 and gif.n_frames==FRAMES
        print(f'{gif.size[0]}x{gif.size[1]} · {gif.n_frames} frames · {FRAMES*FRAME_MS/1000:g}s · infinite loop')
    print(f'{OUTPUT / "hellgato-flow.gif"} · {(OUTPUT / "hellgato-flow.gif").stat().st_size:,} bytes')


if __name__=='__main__':
    main()
