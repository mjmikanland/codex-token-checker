from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).with_name('CodexTokenChecker.ico')
SIZES = [16, 24, 32, 48, 64, 128, 256]

base = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
d = ImageDraw.Draw(base)
# Rounded blue tile
pad = 18
r = 48
# Pillow rounded_rectangle is supported in current versions
d.rounded_rectangle((pad, pad, 256-pad, 256-pad), radius=r, fill=(37, 99, 235, 255))
# White token ring
box = (58, 58, 198, 198)
d.ellipse(box, outline=(255,255,255,255), width=24)
# Accent arc suggesting usage
try:
    d.arc(box, start=-90, end=135, fill=(34,197,94,255), width=24)
except TypeError:
    pass
# Inner dot
d.ellipse((104,104,152,152), fill=(255,255,255,255))

base.save(OUT, format='ICO', sizes=[(n,n) for n in SIZES])
print(f'Created: {OUT}')
