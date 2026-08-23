from pathlib import Path

from PIL import Image, ImageDraw


image = Image.new("RGBA", (256, 256), (52, 120, 246, 255))
draw = ImageDraw.Draw(image)
draw.ellipse((34, 34, 222, 222), fill=(255, 255, 255, 255))
draw.polygon(
    [(128, 62), (143, 111), (194, 128), (143, 145), (128, 194), (111, 145), (62, 128), (111, 111)],
    fill=(52, 120, 246, 255),
)
Path("icons").mkdir(exist_ok=True)
image.save("icons/icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
