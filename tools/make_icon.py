"""Рисует иконку RouteDeck (как static/favicon.svg) в static/icon.ico и static/icon.png."""

import os
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = 1024          # рисуем крупно и уменьшаем - так края сглаживаются
K = S / 64        # масштаб относительно viewBox 64x64 из SVG


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw() -> Image.Image:
    c1, c2 = (0x3b, 0x82, 0xf6), (0x8b, 0x5c, 0xf6)
    grad = Image.new('RGB', (S, S))
    px = grad.load()
    for y in range(S):
        for x in range(S):
            px[x, y] = lerp(c1, c2, (x + y) / (2 * S))

    mask = Image.new('L', (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=15 * K, fill=255)
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)

    def line(points, width):
        pts = [(x * K, y * K) for x, y in points]
        d.line(pts, fill=white, width=round(width * K), joint='curve')
        r = width * K / 2
        for x, y in (pts[0], pts[-1]):
            d.ellipse([x - r, y - r, x + r, y + r], fill=white)

    # Арка туннеля: M14 51 V33 a18 18 0 0 1 36 0 v18
    w = 5.5
    line([(14, 51), (14, 33)], w)
    line([(50, 33), (50, 51)], w)
    # PIL рисует толщину дуги внутрь рамки, поэтому рамка = радиус + полтолщины.
    r = 18 + w / 2
    d.arc([(32 - r) * K, (33 - r) * K, (32 + r) * K, (33 + r) * K], 180, 360, fill=white, width=round(w * K))
    # Стрелка маршрута
    line([(32, 51), (32, 30)], 5)
    line([(24.5, 37.5), (32, 30), (39.5, 37.5)], 5)
    return img


if __name__ == '__main__':
    img = draw()
    out = os.path.join(ROOT, 'static')
    img.resize((256, 256), Image.LANCZOS).save(os.path.join(out, 'icon.png'))
    img.save(os.path.join(out, 'icon.ico'), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print('icon.ico / icon.png готовы')
