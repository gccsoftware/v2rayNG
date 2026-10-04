#!/usr/bin/env python3
"""
customize.py — 在构建前对 v2rayNG 源码树进行「换壳」定制。

功能（全部在构建机上、对上游源码的临时副本执行，不影响上游仓库）：

1. 修改包名：若指定 ``--package``，将 ``V2rayNG/app/build.gradle.kts`` 中的
   ``applicationId`` 替换为新包名。Kotlin namespace（``com.v2ray.ang``）保持
   不变，因此 BuildConfig / JNI（``-DPKGNAME=com/v2ray/ang/service``）等绑定
   均不受影响；fdroid flavor 的 ``applicationIdSuffix`` 也会自动叠加在新包名上。
   同时将 ``Utils.isXray()`` 中 ``startsWith("com.v2ray.ang")`` 的判断改为新包名，
   保证运行时行为与官方包一致。

2. 修改图标：用 ``--icon`` 指定的 PNG 重新生成全部启动器图标：
   * 旧版方图标      mipmap-*/ic_launcher.png          （48 ~ 192 px）
   * 旧版圆图标      mipmap-*/ic_launcher_round.png    （圆角蒙版裁切）
   * 自适应前景      mipmap-*/ic_launcher_foreground.png（108dp 画布，安全区内）
   * 自适应背景色    values/ic_launcher_background.xml  （取样图标边缘像素）

3. 可选注入 ``--ndk-version``：在 ``compileSdk`` 行之后写入 ``ndkVersion``，
   与上游官方 workflow 的做法一致（其 sed 在第 10 行插入）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ORIGINAL_PACKAGE = "com.v2ray.ang"

# 各密度下图标边长（px）。launcher 图标为 48dp，自适应图标画布为 108dp。
LEGACY_SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
FOREGROUND_SIZES = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}
# 自适应前景画布中图案（含自带留白）所占比例，保证主体落在 66% 安全区内
FOREGROUND_CONTENT_RATIO = 0.78

PACKAGE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def fail(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)
    sys.exit(1)


def warn(message: str) -> None:
    print(f"::warning::{message}", file=sys.stderr)


def info(message: str) -> None:
    print(message)


# --------------------------------------------------------------------------- #
# 包名
# --------------------------------------------------------------------------- #
def patch_package_name(source: Path, package: str) -> bool:
    """重写 applicationId，必要时同步 isXray() 判断。"""
    if not PACKAGE_RE.match(package):
        fail(
            f"非法包名 {package!r}：应为小写 Java 形式（如 com.example.vpn），"
            "至少两段，只能包含小写字母 / 数字 / 下划线，段首必须是字母。"
        )

    gradle_file = source / "V2rayNG/app/build.gradle.kts"
    if not gradle_file.is_file():
        fail(f"未找到 {gradle_file}")

    text = gradle_file.read_text(encoding="utf-8")
    pattern = re.compile(r'(applicationId\s*=\s*")([^"]+)(")')
    matches = pattern.findall(text)
    if len(matches) != 1:
        fail(f"{gradle_file} 中找到 {len(matches)} 处 applicationId，预期 1 处，上游结构可能已变更。")

    old_package = matches[0][1]
    new_text, count = pattern.subn(rf"\g<1>{package}\g<3>", text, count=1)
    if count != 1:
        fail("applicationId 替换失败")
    gradle_file.write_text(new_text, encoding="utf-8")
    info(f"[package] applicationId: {old_package} -> {package}")

    if package == old_package:
        return old_package == package

    # 保持 isXray() 行为与官方一致（该函数按包名前缀判断，影响端口/入站配置）
    utils_file = source / "V2rayNG/app/src/main/java/com/v2ray/ang/util/Utils.kt"
    if utils_file.is_file():
        utils_text = utils_file.read_text(encoding="utf-8")
        needle = f'startsWith("{ORIGINAL_PACKAGE}")'
        if needle in utils_text:
            utils_text = utils_text.replace(needle, f'startsWith("{package}")')
            utils_file.write_text(utils_text, encoding="utf-8")
            info(f"[package] Utils.isXray() 判断前缀已改为 {package}")
        else:
            warn("未在 Utils.kt 找到 isXray() 包名判断，上游可能已重构，跳过该补丁。")
    return True


# --------------------------------------------------------------------------- #
# 图标
# --------------------------------------------------------------------------- #
def _load_pillow():
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        fail("缺少 Pillow 依赖，请先执行: python3 -m pip install pillow")
    return Image


def _sample_background_color(img) -> str:
    """取图标四角像素的平均色作为自适应图标背景色。"""
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = [rgb.getpixel(p) for p in [(4, 4), (w - 5, 4), (4, h - 5), (w - 5, h - 5)]]
    r = sum(p[0] for p in px) // 4
    g = sum(p[1] for p in px) // 4
    b = sum(p[2] for p in px) // 4
    return f"#{r:02X}{g:02X}{b:02X}"


def _square_fit(art, size: int, Image):
    """整图缩放到 size×size（源图本身的留白保持不变）。"""
    return art.resize((size, size), Image.LANCZOS)


def _round_icon(art, size: int, Image) -> None:
    """圆形蒙版裁切（4x 超采样抗锯齿）。"""
    from PIL import ImageDraw

    target = size * 4
    base = _square_fit(art, target, Image).convert("RGBA")
    mask = Image.new("L", (target, target), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, target, target), fill=255)
    base.putalpha(mask)
    return base.resize((size, size), Image.LANCZOS)


def _adaptive_foreground(art, canvas: int, Image):
    """108dp 透明画布，图标整体缩放到中央安全区。"""
    from PIL import ImageOps  # noqa: F401 —— 仅确保子模块可用

    content = int(canvas * FOREGROUND_CONTENT_RATIO)
    fg = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    scaled = art.convert("RGBA").resize((content, content), Image.LANCZOS)
    offset = (canvas - content) // 2
    fg.paste(scaled, (offset, offset), scaled)
    return fg


def patch_icons(source: Path, icon_path: Path) -> str:
    """用自定义图标替换所有启动器图标，返回写入的背景色。"""
    Image = _load_pillow()

    if not icon_path.is_file():
        fail(f"找不到图标文件 {icon_path}")
    art = Image.open(icon_path)
    if art.width != art.height:
        warn(f"图标非正方形（{art.width}x{art.height}），将被强制拉伸为方形。")
        side = min(art.width, art.height)
        left = (art.width - side) // 2
        top = (art.height - side) // 2
        art = art.crop((left, top, left + side, top + side))
    art = art.convert("RGB")

    bg_color = _sample_background_color(art)
    info(f"[icon] 自适应背景色（取样自图标四角）: {bg_color}")

    res_dir = source / "V2rayNG/app/src/main/res"
    if not res_dir.is_dir():
        fail(f"未找到资源目录 {res_dir}")

    for density, size in LEGACY_SIZES.items():
        out_dir = res_dir / f"mipmap-{density}"
        out_dir.mkdir(parents=True, exist_ok=True)
        _square_fit(art, size, Image).save(out_dir / "ic_launcher.png", optimize=True)
        _round_icon(art, size, Image).save(out_dir / "ic_launcher_round.png", optimize=True)

    for density, size in FOREGROUND_SIZES.items():
        out_dir = res_dir / f"mipmap-{density}"
        out_dir.mkdir(parents=True, exist_ok=True)
        _adaptive_foreground(art, size, Image).save(out_dir / "ic_launcher_foreground.png", optimize=True)

    bg_xml = res_dir / "values/ic_launcher_background.xml"
    if bg_xml.is_file():
        xml_text = bg_xml.read_text(encoding="utf-8")
        xml_text = re.sub(
            r'(<color\s+name="ic_launcher_background">)([^<]+)(</color>)',
            rf"\g<1>{bg_color}\g<3>",
            xml_text,
        )
        bg_xml.write_text(xml_text, encoding="utf-8")
    else:
        bg_xml.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f'<resources>\n    <color name="ic_launcher_background">{bg_color}</color>\n</resources>\n',
            encoding="utf-8",
        )
    info(f"[icon] 已写入 {len(LEGACY_SIZES) * 2 + len(FOREGROUND_SIZES)} 张图标 + 背景色 {bg_xml}")

    return bg_color


# --------------------------------------------------------------------------- #
# NDK 版本（与上游 workflow 行为一致）
# --------------------------------------------------------------------------- #
def patch_ndk_version(source: Path, ndk_version: str | None) -> None:
    if not ndk_version:
        return
    gradle_file = source / "V2rayNG/app/build.gradle.kts"
    text = gradle_file.read_text(encoding="utf-8")
    if re.search(r"ndkVersion\s*=", text):
        info("[ndk] build.gradle.kts 已存在 ndkVersion，跳过注入。")
        return
    pattern = re.compile(r"^([ \t]*)(compileSdk\s*=.*)$", re.MULTILINE)
    if not pattern.search(text):
        fail("未找到 compileSdk 行，无法注入 ndkVersion")
    new_text = pattern.sub(rf'\g<0>\n\g<1>ndkVersion = "{ndk_version}"', text, count=1)
    gradle_file.write_text(new_text, encoding="utf-8")
    info(f"[ndk] 已注入 ndkVersion = {ndk_version}")


# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(
        description="在构建前修改 v2rayNG 的包名 / 图标（对上游源码的本地副本执行）。"
    )
    parser.add_argument("--source", required=True, type=Path, help="上游 v2rayNG 源码树路径")
    parser.add_argument("--package", default="", help=f'新包名；默认保持 "{ORIGINAL_PACKAGE}" 不变')
    parser.add_argument("--icon", type=Path, default=None, help="自定义图标 PNG 路径（正方形）")
    parser.add_argument("--ndk-version", default="", help="可选：注入 android.ndkVersion")
    args = parser.parse_args()

    source: Path = args.source.resolve()
    if not (source / "V2rayNG").is_dir():
        fail(f"{source} 下没有 V2rayNG 目录，这不是一个有效的 v2rayNG 源码树")

    info("=== v2rayNG customization ===")

    if args.package:
        patch_package_name(source, args.package)
    else:
        info(f"[package] 未指定 --package，保持默认 {ORIGINAL_PACKAGE}")

    if args.icon:
        patch_icons(source, args.icon.resolve())
    else:
        info("[icon] 未指定 --icon，保持官方图标")

    patch_ndk_version(source, args.ndk_version or None)

    info("=== customization done ===")


if __name__ == "__main__":
    main()
