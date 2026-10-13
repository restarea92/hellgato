"""Draw the Stream Dog client icon on a 64 x 64 pixel grid.

All shapes use integer cells and a fixed palette. Enlargements use nearest
neighbour sampling; the editable drawing below is the source artwork.
"""

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'app/assets/icons'
PALETTE = {
    'outline': '#493126', 'shadow': '#a56840', 'tan': '#e8a75d',
    'light': '#ffd08b', 'cream': '#fff2d5', 'cream_shadow': '#dfc5a5',
    'pink': '#d99191', 'pink_dark': '#b76d78', 'black': '#241e27',
    'eye': '#171920', 'white': '#ffffff', 'nose_light': '#65535a',
    'metal': '#27303e', 'edge': '#526278', 'screen': '#131b28',
    'orange': '#ff893a', 'fire': '#ffd66b', 'knob': '#b4c3cf',
}


def draw_icon():
    image = Image.new('RGBA', (64, 64))
    pen = ImageDraw.Draw(image)

    def box(bounds, color):
        pen.rectangle(bounds, fill=PALETTE[color])

    def shape(points, color):
        pen.polygon(points, fill=PALETTE[color])

    # The uneven upright ears and white blaze follow the reference corgi.
    shape([(8, 21), (8, 5), (10, 5), (10, 3), (14, 3), (14, 5),
           (17, 5), (17, 8), (20, 8), (20, 12), (24, 16)], 'outline')
    shape([(11, 19), (11, 7), (14, 7), (14, 10), (17, 10),
           (17, 14), (21, 17)], 'tan')
    shape([(12, 10), (14, 12), (14, 15), (17, 18), (12, 19)], 'pink')
    shape([(39, 16), (43, 10), (45, 10), (45, 6), (48, 6),
           (48, 2), (52, 2), (52, 4), (55, 4), (55, 24)], 'outline')
    shape([(43, 18), (46, 12), (48, 12), (48, 8), (51, 6),
           (52, 6), (52, 22)], 'light')
    shape([(47, 19), (49, 14), (49, 10), (52, 9), (52, 23)], 'pink')
    box((51, 12, 52, 21), 'pink_dark')

    # Head silhouette, cheek tufts and chest are deliberately stepped pixels.
    shape([(22, 13), (41, 13), (41, 15), (47, 15), (47, 18),
           (53, 18), (53, 23), (56, 23), (56, 32), (54, 32),
           (54, 35), (57, 35), (57, 41), (54, 41), (54, 46),
           (51, 46), (51, 53), (13, 53), (13, 46), (10, 46),
           (10, 42), (7, 42), (7, 37), (10, 37), (10, 32),
           (7, 32), (7, 24), (10, 24), (10, 20), (16, 20),
           (16, 16), (22, 16)], 'outline')
    shape([(22, 16), (40, 16), (40, 18), (46, 18), (46, 21),
           (51, 21), (51, 25), (53, 25), (53, 31), (51, 31),
           (51, 37), (54, 37), (54, 40), (51, 40), (51, 45),
           (48, 48), (16, 48), (13, 44), (13, 39), (10, 39),
           (13, 36), (13, 30), (10, 30), (10, 26), (13, 26),
           (13, 22), (18, 22), (18, 19), (22, 19)], 'tan')
    box((16, 22, 23, 24), 'light')
    box((42, 21, 47, 23), 'light')
    shape([(25, 16), (36, 16), (36, 19), (39, 19), (39, 26),
           (37, 26), (37, 32), (40, 35), (24, 35), (27, 31),
           (27, 25), (25, 25)], 'cream')
    box((26, 17, 28, 23), 'white')
    box((36, 20, 37, 26), 'light')
    shape([(15, 34), (23, 34), (23, 32), (40, 32), (40, 34),
           (49, 34), (49, 40), (52, 40), (49, 43), (48, 49),
           (16, 49), (15, 43), (12, 41), (15, 38)], 'cream')
    box((16, 42, 19, 47), 'cream_shadow')
    box((46, 40, 48, 47), 'cream_shadow')

    # Large glossy eyes: no sub-pixel edges or anti-aliasing.
    for x in (15, 40):
        shape([(x+2, 25), (x+8, 25), (x+8, 27), (x+10, 27),
               (x+10, 33), (x+8, 33), (x+8, 36), (x+2, 36),
               (x+2, 34), (x, 34), (x, 28), (x+2, 28)], 'shadow')
        box((x+1, 27, x+8, 33), 'eye')
        box((x+3, 26, x+6, 35), 'eye')
        box((x+2, 27, x+4, 29), 'white')
        box((x+5, 30, x+6, 31), 'cream_shadow')
    shape([(27, 33), (36, 33), (36, 34), (39, 34), (39, 38),
           (36, 38), (36, 40), (28, 40), (28, 38), (25, 38),
           (25, 35), (27, 35)], 'black')
    box((28, 33, 34, 34), 'nose_light')
    shape([(25, 40), (29, 40), (29, 42), (34, 42), (34, 40),
           (39, 40), (39, 45), (37, 45), (37, 48), (28, 48),
           (28, 46), (25, 46)], 'black')
    box((29, 42, 35, 46), 'pink')
    box((31, 43, 36, 47), 'pink_dark')
    box((30, 43, 31, 46), 'pink')

    # The dog holds the Hellgato controller: fire face, screen, four dials.
    box((17, 47, 47, 61), 'black')
    box((15, 49, 49, 59), 'black')
    box((18, 48, 46, 59), 'metal')
    box((18, 48, 46, 48), 'edge')
    box((20, 50, 44, 56), 'screen')
    shape([(25, 55), (25, 53), (27, 53), (27, 51), (29, 52),
           (30, 50), (32, 52), (35, 50), (36, 53), (38, 52),
           (39, 55)], 'orange')
    box((29, 53, 35, 55), 'fire')
    box((29, 54, 30, 54), 'screen')
    box((34, 54, 35, 54), 'screen')
    for x in (22, 28, 34, 40):
        box((x, 58, x+2, 59), 'knob')
        box((x, 59, x+2, 59), 'edge')
    # Paws wrap in front, with one darker cell separating the toes.
    for x in (11, 46):
        box((x, 47, x+6, 53), 'outline')
        box((x+1, 47, x+5, 51), 'cream')
        box((x+2, 52, x+5, 52), 'cream_shadow')
        box((x+3, 50, x+3, 52), 'cream_shadow')
    return image


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    master = draw_icon()
    master.save(OUT / 'stream-dog-64.png')
    master.resize((512, 512), Image.Resampling.NEAREST).save(OUT / 'stream-dog-preview.png')
    # Supply each ICO frame explicitly; Pillow must not smooth a pixel edge.
    sizes = (16, 24, 32, 48, 64, 128, 256)
    frames = [master.resize((size, size), Image.Resampling.NEAREST) for size in sizes]
    frames[-1].save(OUT / 'stream-dog.ico', sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])


if __name__ == '__main__':
    main()
