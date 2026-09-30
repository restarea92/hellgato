"""Export Windows icon sizes from the transparent Hellgato artwork."""

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / 'app/assets/icons'
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def export(master, prefix, windows_icon=False):
    with Image.open(ICONS / master) as source:
        artwork = source.convert('RGBA')
    if artwork.getchannel('A').getextrema() != (0, 255):
        raise ValueError('The icon master must have transparent and opaque pixels')
    # Image generation can leave almost invisible alpha pixels far outside the
    # silhouette. They must not determine Windows icon occupancy or centering.
    silhouette = artwork.getchannel('A').point(lambda alpha: 255 if alpha >= 32 else 0)
    artwork = artwork.crop(silhouette.getbbox())
    frames = []
    for size in SIZES:
        inset = 0 if size <= 256 else round(size * .01)
        image = Image.new('RGBA', (size, size))
        resampling = Image.Resampling.LANCZOS if size <= 96 else Image.Resampling.NEAREST
        artwork_size = size - inset * 2
        scale = artwork_size / max(artwork.size)
        resized = artwork.resize(tuple(max(1, round(side * scale)) for side in artwork.size), resampling)
        image.alpha_composite(resized, ((size - resized.width) // 2, (size - resized.height) // 2))
        if size == 64:
            image.save(ICONS / f'{prefix}-64.png')
        frames.append(image)
    if windows_icon:
        frames[-1].save(ICONS / f'{prefix}.ico', sizes=[frame.size for frame in frames],
                        append_images=frames[:-1])
    print(f'Exported {prefix}: application image' + (' and Windows icon' if windows_icon else ''))


def main():
    export('hellgato-master.png', 'hellgato')
    export('hellgato-front-master.png', 'hellgato-front', windows_icon=True)


if __name__ == '__main__':
    main()
