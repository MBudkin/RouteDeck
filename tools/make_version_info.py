"""Создаёт build/version_info.txt - ресурс версии для RouteDeck.exe (Свойства → Подробно)."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from version import APP_NAME, APP_VERSION  # noqa: E402

parts = [int(p) for p in APP_VERSION.split('.')] + [0] * 4
nums = tuple(parts[:4])

TEMPLATE = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041904B0', [
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('FileDescription', '{APP_NAME} — маршрутизация сайтов через VPN на Keenetic'),
      StringStruct('FileVersion', '{APP_VERSION}'),
      StringStruct('ProductVersion', '{APP_VERSION}'),
      StringStruct('InternalName', '{APP_NAME}'),
      StringStruct('OriginalFilename', '{APP_NAME}.exe')])]),
    VarFileInfo([VarStruct('Translation', [0x0419, 1200])])
  ]
)
"""

if __name__ == '__main__':
    os.makedirs(os.path.join(ROOT, 'build'), exist_ok=True)
    path = os.path.join(ROOT, 'build', 'version_info.txt')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(TEMPLATE)
    print(f'{APP_NAME} {APP_VERSION}: {path}')
