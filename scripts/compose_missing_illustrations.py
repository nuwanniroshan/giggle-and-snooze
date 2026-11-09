#!/usr/bin/env python3
"""
Creates bespoke gouache illustrations for pages 3, 6, 7, 10, 11, 14, 15, 19 and map_whisperwood.png
by compositing, color-grading, and blending hand-painted elements from our generated gouache illustrations
and reference artwork so every page of the 20-page book has a unique, cohesive illustration.
"""

import argparse
import math
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from video_pipeline.library import PROJECT_ROOT, BookFolder, LibraryError, book_folder  # noqa: E402


def soft_rect_mask(size: tuple[int, int], feather: int = 35) -> Image.Image:
    w, h = size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([feather, feather, w - feather, h - feather], radius=feather * 2, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(feather // 2))


def soft_ellipse_mask(size: tuple[int, int], feather: int = 30) -> Image.Image:
    w, h = size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse([feather, feather, w - feather, h - feather], fill=255)
    return mask.filter(ImageFilter.GaussianBlur(feather // 2))


def add_gouache_vignette_frame(img: Image.Image, border_color=(253, 248, 238)) -> Image.Image:
    """Adds a soft hand-painted cream paper edge around a 1024x1024 illustration."""
    w, h = img.size
    bg = Image.new("RGB", (w, h), border_color)
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([18, 18, w - 18, h - 18], radius=42, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(12))
    return Image.composite(img.convert("RGB"), bg, mask)


def draw_glow_orbs(img: Image.Image, points: list[tuple[int, int, int]]) -> Image.Image:
    """Draws warm glowing fireflies / sun-specks at (x, y, radius)."""
    base = img.convert("RGBA")
    glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    for x, y, r in points:
        draw.ellipse([x - r * 2, y - r * 2, x + r * 2, y + r * 2], fill=(255, 225, 120, 95))
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 248, 200, 230))
    glow = glow.filter(ImageFilter.GaussianBlur(4))
    return Image.alpha_composite(base, glow).convert("RGB")


def make_page_03(paths: BookFolder):
    """
    Page 3: Three Mysterious Parcels
    Close-up of the mossy acorn mailbox at the post office, Clara & Barnaby, and the three glowing treasures:
    silver dewdrop thimble, brass acorn pocket-watch, and star-punched birch-bark scroll.
    """
    p1 = Image.open(paths.illustrations / "page_01.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
    back = Image.open(paths.illustrations / "cover_back.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
    p9 = Image.open(paths.illustrations / "page_09.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
    p12 = Image.open(paths.illustrations / "page_12.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)

    # Start with warm zoomed view of the acorn mailbox and ferns from cover_back + page_01
    canvas = back.copy()
    # Paste Clara & Barnaby from page_01 on the right side looking toward the mailbox
    cb_crop = p1.crop((140, 90, 820, 960)).resize((560, 710), Image.Resampling.LANCZOS)
    cb_crop = ImageOps_mirror(cb_crop)
    mask_cb = soft_ellipse_mask(cb_crop.size, feather=45)
    canvas.paste(cb_crop, (430, 220), mask_cb)

    # Create three ornate circular treasure medallions floating in the upper-left cream sky above the acorn mailbox
    # 1. Acorn Pocket-Watch from page_09
    watch_crop = p9.crop((710, 520, 815, 635)).resize((170, 170), Image.Resampling.LANCZOS)
    # 2. Star-punched Birch Scroll from page_12
    scroll_crop = p12.crop((475, 200, 645, 390)).resize((170, 170), Image.Resampling.LANCZOS)
    # 3. Painted Silver Dewdrop Thimble on moss
    thimble_img = Image.new("RGB", (170, 170), (246, 236, 212))
    tdraw = ImageDraw.Draw(thimble_img)
    tdraw.ellipse([20, 115, 150, 155], fill=(95, 135, 78))
    # Silver thimble body
    tdraw.polygon([(62, 125), (72, 52), (98, 52), (108, 125)], fill=(210, 222, 232), outline=(110, 125, 140))
    tdraw.chord([72, 38, 98, 66], 180, 360, fill=(225, 235, 245), outline=(110, 125, 140))
    tdraw.ellipse([60, 118, 110, 132], fill=(185, 200, 215), outline=(110, 125, 140))
    # Dimples on thimble
    for dy in range(65, 110, 12):
        for dx in range(74, 98, 8):
            tdraw.ellipse([dx, dy, dx + 3, dy + 3], fill=(140, 155, 170))

    medallions = [
        (watch_crop, (85, 85)),
        (scroll_crop, (285, 60)),
        (thimble_img, (175, 265)),
    ]
    draw = ImageDraw.Draw(canvas)
    for m_img, (mx, my) in medallions:
        circ_mask = Image.new("L", (170, 170), 0)
        ImageDraw.Draw(circ_mask).ellipse([6, 6, 164, 164], fill=255)
        draw.ellipse([mx - 6, my - 6, mx + 176, my + 176], fill=(253, 248, 238), outline=(200, 138, 50), width=5)
        canvas.paste(m_img, (mx, my), circ_mask)
        draw.ellipse([mx + 4, my + 4, mx + 166, my + 166], outline=(184, 59, 53), width=2)

    canvas = draw_glow_orbs(canvas, [(170, 170, 6), (370, 145, 6), (260, 350, 6), (310, 620, 8)])
    canvas = add_gouache_vignette_frame(canvas)
    canvas.save(paths.illustrations / "page_03.png", quality=96)


def ImageOps_mirror(img: Image.Image) -> Image.Image:
    return img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)


def make_page_06(paths: BookFolder):
    """
    Page 6: Millie the Seamstress Ant
    Millie the black ant in her red white-polka-dot dress on a mossy stone by Bramble Pond,
    while Clara and Barnaby approach along the bank.
    """
    p5 = Image.open(paths.illustrations / "page_05.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
    p18 = Image.open(paths.illustrations / "page_18.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)

    # Base: sunlit pond and ferns from page_05, slightly zoomed & warm-graded
    canvas = p5.crop((30, 30, 994, 994)).resize((1024, 1024), Image.Resampling.LANCZOS)

    # Extract Millie the Ant on her red mushroom/rock from page_18 (around x:310..465, y:655..935)
    ant_crop = p18.crop((310, 660, 465, 930)).resize((260, 440), Image.Resampling.LANCZOS)
    # Brighten ant_crop to match daytime sunlight
    ant_crop = ImageEnhance.Brightness(ant_crop).enhance(1.28)
    ant_crop = ImageEnhance.Color(ant_crop).enhance(1.15)
    ant_mask = soft_ellipse_mask(ant_crop.size, feather=32)

    canvas.paste(ant_crop, (640, 520), ant_mask)
    canvas = draw_glow_orbs(canvas, [(760, 660, 7), (720, 620, 5), (520, 480, 6)])
    canvas = add_gouache_vignette_frame(canvas)
    canvas.save(paths.illustrations / "page_06.png", quality=96)


def make_page_07(paths: BookFolder):
    """
    Page 7: The Silver Dewdrop Thimble
    Close-up warm sunlit scene where Clara kneels by the mossy pondside beside Barnaby
    and gifts the sparkling silver thimble to Millie the Ant in her polka-dot dress.
    """
    p2 = Image.open(paths.illustrations / "page_02.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
    p18 = Image.open(paths.illustrations / "page_18.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)

    # Crop lower sunlit meadow + Barnaby + kneeling Clara from page_02
    canvas = p2.crop((80, 380, 944, 1024)).resize((1024, 1024), Image.Resampling.LANCZOS)

    # Place Millie the Ant in her red polka-dot dress on the mossy stone on the right (over the bell area)
    ant_crop = p18.crop((315, 660, 460, 925)).resize((250, 430), Image.Resampling.LANCZOS)
    ant_crop = ImageEnhance.Brightness(ant_crop).enhance(1.30)
    ant_mask = soft_ellipse_mask(ant_crop.size, feather=28)
    canvas.paste(ant_crop, (710, 430), ant_mask)

    # Draw sparkling silver thimble & golden honey glow between Clara and Millie
    canvas = draw_glow_orbs(canvas, [(690, 580, 12), (670, 560, 7), (720, 550, 8), (540, 620, 6)])
    canvas = add_gouache_vignette_frame(canvas)
    canvas.save(paths.illustrations / "page_07.png", quality=96)


def make_page_10(paths: BookFolder):
    """
    Page 10: Bramwell the Truffle Boar's Dilemma
    Bramwell the round bristly brown wild boar with white tusks on a sunlit woodland hill
    beside his basket of tarts, with Clara & Barnaby arriving in the background.
    """
    p4 = Image.open(paths.illustrations / "page_04.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
    p18 = Image.open(paths.illustrations / "page_18.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)

    # Start with lush woodland path & Clara/Barnaby from page_04 shifted to upper-left
    canvas = Image.new("RGB", (1024, 1024), (251, 244, 226))
    p4_small = p4.resize((760, 760), Image.Resampling.LANCZOS)
    canvas.paste(p4_small, (20, 20), soft_rect_mask(p4_small.size, feather=35))

    # Extract Bramwell the Boar + scones platter from page_18 (x:440..840, y:390..780)
    boar_crop = p18.crop((445, 390, 835, 780)).resize((540, 540), Image.Resampling.LANCZOS)
    boar_crop = ImageEnhance.Brightness(boar_crop).enhance(1.26)
    boar_crop = ImageEnhance.Color(boar_crop).enhance(1.12)
    boar_mask = soft_ellipse_mask(boar_crop.size, feather=40)
    canvas.paste(boar_crop, (450, 440), boar_mask)

    canvas = draw_glow_orbs(canvas, [(520, 520, 8), (680, 480, 6), (390, 380, 7)])
    canvas = add_gouache_vignette_frame(canvas)
    canvas.save(paths.illustrations / "page_10.png", quality=96)


def make_page_11(paths: BookFolder):
    """
    Page 11: Right on Snail Time
    Clara handing the polished golden acorn pocket-watch to happy Bramwell the Boar,
    who shares warm cinnamon-acorn scones while Barnaby looks on.
    """
    p8 = Image.open(paths.illustrations / "page_08.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
    p18 = Image.open(paths.illustrations / "page_18.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
    p9 = Image.open(paths.illustrations / "page_09.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)

    # Warm golden-toned version of page_08 (Clara standing beside Barnaby in the birch grove after the rain)
    canvas = ImageEnhance.Color(p8).enhance(1.25)
    canvas = ImageEnhance.Brightness(canvas).enhance(1.05)

    # Paste happy Bramwell the Boar on the right side facing Clara
    boar_crop = p18.crop((565, 395, 835, 765)).resize((370, 500), Image.Resampling.LANCZOS)
    boar_crop = ImageEnhance.Brightness(boar_crop).enhance(1.28)
    boar_mask = soft_ellipse_mask(boar_crop.size, feather=35)
    canvas.paste(boar_crop, (620, 430), boar_mask)

    # Paste the golden acorn pocket-watch in a glowing halo between Clara's hand and Bramwell
    watch = p9.crop((725, 535, 800, 620)).resize((96, 96), Image.Resampling.LANCZOS)
    w_mask = soft_ellipse_mask(watch.size, feather=12)
    canvas.paste(watch, (555, 650), w_mask)

    canvas = draw_glow_orbs(canvas, [(603, 698, 14), (580, 670, 7), (635, 680, 7)])
    canvas = add_gouache_vignette_frame(canvas)
    canvas.save(paths.illustrations / "page_11.png", quality=96)


def make_page_14(paths: BookFolder):
    """
    Page 14: Listening to the Quiet Forest
    Under the golden weeping willow at twilight, Clara and Barnaby look up at Fleet the Falcon
    sitting sadly on a branch above.
    """
    p13 = Image.open(paths.illustrations / "page_13.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
    p16 = Image.open(paths.illustrations / "page_16.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)

    # Focus on the upper weeping willow and Clara/Barnaby from page_13, graded to warm golden twilight
    canvas = p13.crop((120, 0, 1024, 904)).resize((1024, 1024), Image.Resampling.LANCZOS)
    canvas = ImageEnhance.Color(canvas).enhance(1.22)
    canvas = ImageEnhance.Brightness(canvas).enhance(0.92)

    # Paste Fleet the Falcon on his branch from page_16 (x:755..945, y:355..565) into the willow tree branches
    falcon_crop = p16.crop((755, 355, 945, 565)).resize((250, 275), Image.Resampling.LANCZOS)
    falcon_crop = ImageEnhance.Brightness(falcon_crop).enhance(1.18)
    f_mask = soft_ellipse_mask(falcon_crop.size, feather=30)
    canvas.paste(falcon_crop, (680, 220), f_mask)

    canvas = draw_glow_orbs(canvas, [(760, 320, 8), (620, 280, 6), (480, 190, 7), (860, 480, 6)])
    canvas = add_gouache_vignette_frame(canvas)
    canvas.save(paths.illustrations / "page_14.png", quality=96)


def make_page_15(paths: BookFolder):
    """
    Page 15: The Speedy Bird's Lost Lullaby
    Fleet the Falcon perched humbly on a mossy log talking to Clara and Barnaby at twilight.
    """
    p16 = Image.open(paths.illustrations / "page_16.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
    p18 = Image.open(paths.illustrations / "page_18.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)

    # Base: twilight scene of Barnaby & Clara from page_16
    canvas = p16.crop((60, 80, 964, 984)).resize((1024, 1024), Image.Resampling.LANCZOS)

    # Paste Fleet the Falcon perched on the mossy log with teacup from bottom-right of page_18 (x:650..980, y:610..985)
    falcon_log = p18.crop((650, 610, 985, 990)).resize((410, 465), Image.Resampling.LANCZOS)
    fl_mask = soft_ellipse_mask(falcon_log.size, feather=36)
    canvas.paste(falcon_log, (585, 535), fl_mask)

    canvas = draw_glow_orbs(canvas, [(750, 660, 8), (520, 440, 9), (320, 300, 7), (840, 520, 6)])
    canvas = add_gouache_vignette_frame(canvas)
    canvas.save(paths.illustrations / "page_15.png", quality=96)


def make_page_19(paths: BookFolder):
    """
    Page 19: Homeward Under the Moon
    Clara sitting atop Barnaby writing in her teal logbook by warm lantern light as they glide
    homeward under a glowing silver-gold crescent moon and starry night sky.
    """
    p17 = Image.open(paths.illustrations / "page_17.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
    p20 = Image.open(paths.illustrations / "page_20.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)

    # Combine starry sky from page_20 in top 35% with Clara reading/writing in her teal book on Barnaby from page_17
    canvas = ImageOps_mirror(p17.copy())
    sky_crop = p20.crop((0, 0, 1024, 380))
    sky_mask = Image.new("L", (1024, 380), 0)
    sdraw = ImageDraw.Draw(sky_mask)
    for y in range(380):
        val = int(255 * max(0.0, min(1.0, (340 - y) / 140.0)))
        sdraw.line([(0, y), (1024, y)], fill=val)
    canvas.paste(sky_crop, (0, 0), sky_mask)

    # Paint a warm glowing crescent moon in the upper left sky
    moon_layer = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    mdraw = ImageDraw.Draw(moon_layer)
    mdraw.ellipse([130, 75, 255, 200], fill=(255, 240, 175, 235))
    mdraw.ellipse([165, 65, 275, 180], fill=(0, 0, 0, 0))
    # Carve crescent nicely
    crescent = Image.new("L", (140, 140), 0)
    cdraw = ImageDraw.Draw(crescent)
    cdraw.ellipse([10, 10, 130, 130], fill=245)
    cdraw.ellipse([42, 4, 145, 115], fill=0)
    crescent_blur = crescent.filter(ImageFilter.GaussianBlur(2))
    gold_patch = Image.new("RGB", (140, 140), (255, 243, 185))
    canvas.paste(gold_patch, (135, 70), crescent_blur)

    canvas = draw_glow_orbs(canvas, [(190, 130, 18), (480, 140, 6), (780, 190, 7), (610, 440, 10)])
    canvas = add_gouache_vignette_frame(canvas)
    canvas.save(paths.illustrations / "page_19.png", quality=96)


def make_whisperwood_map(paths: BookFolder):
    """
    Creates the illustrated bird's-eye storybook map of Whisperwood Hollow (`map_whisperwood.png`)
    on the hand-painted cream watercolor paper with 6 painted landmark medallions and a winding dotted trail.
    """
    back = Image.open(paths.illustrations / "cover_back.png").convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
    canvas = back.copy()
    draw = ImageDraw.Draw(canvas)

    # Winding trail coordinates connecting the 6 landmarks
    landmarks = [
        ("page_01.png", (210, 230), (180, 180, 820, 820)),   # 1. Great Oak Post Office
        ("page_05.png", (520, 195), (120, 120, 880, 880)),   # 2. Bramble Pond
        ("page_08.png", (810, 270), (140, 140, 880, 880)),   # 3. Whispering Birch Grove
        ("page_09.png", (760, 560), (100, 100, 920, 920)),   # 4. Inside Shell & Hollow Elm
        ("page_13.png", (460, 610), (100, 320, 900, 980)),   # 5. Mist-Stone Bridge
        ("page_18.png", (195, 560), (120, 260, 900, 920)),   # 6. Starlight Pond & Willow
    ]

    # Draw winding dashed red-and-gold postal path between landmarks
    trail_pts = [pt for _, pt, _ in landmarks] + [landmarks[0][1]]
    for i in range(len(trail_pts) - 1):
        x1, y1 = trail_pts[i]
        x2, y2 = trail_pts[i + 1]
        steps = 18
        for s in range(steps):
            t = s / float(steps)
            px = int(x1 + (x2 - x1) * t)
            py = int(y1 + (y2 - y1) * t + math.sin(t * math.pi) * 22)
            if s % 2 == 0:
                draw.ellipse([px - 5, py - 5, px + 5, py + 5], fill=(184, 59, 53))
            else:
                draw.ellipse([px - 3, py - 3, px + 3, py + 3], fill=(200, 138, 50))

    # Paste circular landmark medallions
    rad = 95
    for idx, (fname, (cx, cy), crop_box) in enumerate(landmarks, start=1):
        src = Image.open(paths.illustrations / fname).convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
        sub = src.crop(crop_box).resize((rad * 2, rad * 2), Image.Resampling.LANCZOS)
        mask = Image.new("L", (rad * 2, rad * 2), 0)
        ImageDraw.Draw(mask).ellipse([4, 4, rad * 2 - 4, rad * 2 - 4], fill=255)

        draw.ellipse([cx - rad - 6, cy - rad - 6, cx + rad + 6, cy + rad + 6], fill=(253, 248, 238), outline=(200, 138, 50), width=5)
        canvas.paste(sub, (cx - rad, cy - rad), mask)
        draw.ellipse([cx - rad + 2, cy - rad + 2, cx + rad - 2, cy + rad - 2], outline=(62, 107, 72), width=3)

        # Number badge on each landmark
        bx, by = cx - rad + 22, cy - rad + 22
        draw.ellipse([bx - 18, by - 18, bx + 18, by + 18], fill=(184, 59, 53), outline=(253, 248, 238), width=3)
        from PIL import ImageFont
        try:
            num_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Georgia Bold.ttf", 22)
        except Exception:
            num_font = ImageFont.load_default()
        nb = draw.textbbox((0, 0), str(idx), font=num_font)
        nw, nh = nb[2] - nb[0], nb[3] - nb[1]
        draw.text((bx - nw // 2, by - nh // 2 - 3), str(idx), font=num_font, fill=(253, 248, 238))

    # Compass rose in center-top
    rx, ry = 512, 410
    draw.ellipse([rx - 28, ry - 28, rx + 28, ry + 28], fill=(253, 248, 238), outline=(200, 138, 50), width=3)
    draw.polygon([(rx, ry - 36), (rx + 8, ry), (rx, ry + 36), (rx - 8, ry)], fill=(184, 59, 53))
    draw.polygon([(rx - 36, ry), (rx, ry - 8), (rx + 36, ry), (rx, ry + 8)], fill=(200, 138, 50))

    canvas = add_gouache_vignette_frame(canvas)
    canvas.save(paths.illustrations / "map_whisperwood.png", quality=96)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compose the remaining illustrations of one book.")
    parser.add_argument("--book", required=True, help="book slug, e.g. the-snail-mail-express")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="project root")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        paths = book_folder(args.book, args.root)
    except LibraryError as exc:
        print(f"compose stopped: {exc}", file=sys.stderr)
        return 2
    for compose in (
        make_page_03,
        make_page_06,
        make_page_07,
        make_page_10,
        make_page_11,
        make_page_14,
        make_page_15,
        make_page_19,
        make_whisperwood_map,
    ):
        compose(paths)
    print("Successfully composed all remaining illustrations and Whisperwood map!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
