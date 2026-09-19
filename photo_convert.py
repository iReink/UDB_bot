"""Single image conversion subprocess, intentionally isolated from bot RSS."""
import io
import json
import sys
from pathlib import Path


def convert(source, target, thumb, original=False):
    from PIL import Image, ImageOps
    from pillow_heif import register_heif_opener
    register_heif_opener(thumbnails=False, decode_threads=1)
    Image.MAX_IMAGE_PIXELS = 50_000_000
    import warnings
    warnings.simplefilter('error', Image.DecompressionBombWarning)
    with Image.open(source) as image:
        if image.format not in {'JPEG','PNG','WEBP','HEIF','AVIF','GIF','BMP','TIFF'}:
            raise ValueError('Неподдерживаемый формат изображения')
        image.seek(0)
        image = ImageOps.exif_transpose(image)
        image.thumbnail((4096,4096)) if not original else None
        rgba = image.convert('RGBA')
        rgb = Image.new('RGB', rgba.size, 'white')
        rgb.paste(rgba, mask=rgba.getchannel('A'))
        if original:
            import shutil
            shutil.copyfile(source, target)
        else:
            while True:
                result = None
                lo, hi = 25, 90
                while lo <= hi:
                    quality = (lo+hi)//2
                    out = io.BytesIO()
                    rgb.save(out, 'JPEG', quality=quality, optimize=True)
                    if out.tell() <= 300_000:
                        result = out.getvalue()
                        lo = quality+1
                    else:
                        hi = quality-1
                if result is not None:
                    Path(target).write_bytes(result)
                    break
                rgb = rgb.resize((max(1,int(rgb.width*.8)),max(1,int(rgb.height*.8))))
        width, height = rgb.size
        rgb.thumbnail((256,256))
        while True:
            out = io.BytesIO()
            rgb.save(out,'WEBP',quality=55,method=2)
            if out.tell() <= 15_000:
                Path(thumb).write_bytes(out.getvalue())
                break
            rgb = rgb.resize((max(1,int(rgb.width*.8)),max(1,int(rgb.height*.8))))
        return {'width':width,'height':height,'size':Path(target).stat().st_size}


if __name__ == '__main__':
    if sys.platform != 'win32':
        import resource
        import os
        os.nice(10)  # favor interactive bot/web work on a one-vCPU VPS
        resource.setrlimit(resource.RLIMIT_AS, (512*1024**2,512*1024**2))
        resource.setrlimit(resource.RLIMIT_CPU, (25,25))
    try:
        print(json.dumps(convert(*sys.argv[1:4], original=sys.argv[4]=='photo')))
    except Exception:
        print('Не удалось прочитать изображение: повреждённый, слишком большой или неподдерживаемый файл.', file=sys.stderr)
        sys.exit(1)
