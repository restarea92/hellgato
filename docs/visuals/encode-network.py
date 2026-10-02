import json
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageSequence


frames_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / 'work/hellgato-network-frames'
output = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).with_name('hellgato-network.gif')
settings = json.loads((frames_dir / 'capture.json').read_text(encoding='utf-8'))
durations = settings['frameDurations']
if len(durations) != settings['frames'] or sum(durations) != settings['period']:
    raise ValueError('Frame durations do not match the captured loop')
if any(duration < 20 or duration % 10 for duration in durations):
    raise ValueError('GIF durations must be at least 20 ms and use 10 ms units')
images = [Image.open(frames_dir / f'frame-{index:03d}.png').convert('RGB') for index in range(settings['frames'])]
width, height = images[0].size
atlas = Image.new('RGB', (width, height * len(images)))
for index, image in enumerate(images):
    atlas.paste(image, (0, height * index))
palette = atlas.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
colors = palette.getpalette()
white_index = min(range(256), key=lambda index: sum((255 - value) ** 2 for value in colors[index * 3:index * 3 + 3]))
colors[white_index * 3:white_index * 3 + 3] = [255, 255, 255]
palette.putpalette(colors)
frames = []
for image in images:
    frame = image.quantize(palette=palette, dither=Image.Dither.NONE)
    white_pixels = image.convert('L').point(lambda value: 255 if value == 255 else 0)
    frame.paste(white_index, mask=white_pixels)
    frames.append(frame)
output.parent.mkdir(parents=True, exist_ok=True)
frames[0].save(output, save_all=True, append_images=frames[1:], duration=durations, loop=0, disposal=1, optimize=False)
images[0].save(output.with_suffix('.png'))
with Image.open(output) as gif:
    decoded = []
    decoded_durations = []
    for frame in ImageSequence.Iterator(gif):
        decoded.append(frame.convert('RGB'))
        decoded_durations.append(frame.info['duration'])
    if gif.info.get('loop') != 0 or len(decoded) != settings['frames']:
        raise ValueError('GIF loop or frame count differs from the capture')
    if decoded_durations != durations:
        raise ValueError('Encoded GIF timing differs from the capture')
    for index, actual in enumerate(decoded):
        if ImageChops.difference(actual, frames[index].convert('RGB')).getbbox():
            raise ValueError(f'GIF frame {index} differs after decoding')
        if actual.getpixel((0, 0)) != (255, 255, 255):
            raise ValueError('GIF background is not white')
result = {**settings, 'gif': output.name, 'bytes': output.stat().st_size, 'loop': 0, 'palette': 'shared 256 colors', 'dither': 'none'}
output.with_suffix('.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result))
