"""用 Pillow 绘制 GitHub 社交预览卡片 (1280x640) — 精确版"""
from PIL import Image, ImageDraw, ImageFont
import os

W, H = 1280, 640

# ========== 颜色定义 ==========
BG_TOP = (10, 14, 26)       # #0a0e1a
BG_BOTTOM = (17, 24, 39)    # #111827
GRID_COLOR = (20, 35, 50)   # 暗色网格，不用透明度
CYAN = (6, 182, 212)        # #06b6d4
CYAN_BRIGHT = (34, 211, 238) # #22d3ee
WHITE = (255, 255, 255)
GRAY_SUBTITLE = (148, 163, 184)  # #94a3b8
GRAY_FOOTER = (100, 116, 139)    # #64748b
TAG_BG = (8, 32, 42)         # 标签背景（深色）
TAG_BG_HL = (6, 50, 60)      # 高亮标签背景
STAR_COLOR = (34, 211, 238, 120)

# ========== 创建画布 ==========
img = Image.new("RGBA", (W, H), BG_TOP)
draw = ImageDraw.Draw(img, "RGBA")

# 渐变背景
for y in range(H):
    ratio = y / H
    r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * ratio)
    g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * ratio)
    b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * ratio)
    draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

# 网格（非常淡）
for x in range(0, W, 40):
    draw.line([(x, 0), (x, H)], fill=(*GRID_COLOR, 40), width=1)
for y in range(0, H, 40):
    draw.line([(0, y), (W, y)], fill=(*GRID_COLOR, 40), width=1)

# ========== 加载字体 ==========
def find_font(names):
    """在 Windows Fonts 目录中查找字体"""
    font_dir = "C:/Windows/Fonts"
    for name in names:
        path = os.path.join(font_dir, name)
        if os.path.exists(path):
            return path
    return None

# 优先使用 Segoe UI（Windows 10/11 自带）
font_bold_path = find_font(["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"])
font_regular_path = find_font(["segoeui.ttf", "arial.ttf"])
font_light_path = find_font(["segoeuil.ttf", "segoeui.ttf", "arial.ttf"])

def make_font(size, bold=False, light=False):
    if bold and font_bold_path:
        return ImageFont.truetype(font_bold_path, size)
    if light and font_light_path:
        return ImageFont.truetype(font_light_path, size)
    if font_regular_path:
        return ImageFont.truetype(font_regular_path, size)
    return ImageFont.load_default()

font_title = make_font(76, bold=True)
font_subtitle = make_font(28, light=True)
font_tag = make_font(19, bold=True)
font_tag_small = make_font(17, bold=True)
font_footer = make_font(21)

# ========== 星点 ==========
import random
random.seed(42)
for _ in range(40):
    sx = random.randint(20, W-20)
    sy = random.randint(20, H-20)
    size = random.choice([1, 1, 1, 2])
    alpha = random.randint(40, 120)
    draw.ellipse([sx-size, sy-size, sx+size, sy+size], fill=(*CYAN_BRIGHT, alpha))

# ========== 闪电图标 ==========
bolt_pts = [
    (88, 55),
    (60, 100),
    (80, 100),
    (70, 130),
    (105, 78),
    (85, 78),
]
draw.polygon(bolt_pts, fill=(*CYAN, 240))

# ========== 绘制标签（自动宽度计算，均匀弧形分布）==========
def tag_metrics(text, small=False):
    fnt = font_tag_small if small else font_tag
    bbox = draw.textbbox((0, 0), text, font=fnt)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pad_x = 20
    pad_y = 13
    w = tw + pad_x * 2
    h = th + pad_y * 2
    return fnt, tw, th, w, h

def draw_tag_at(text, cx, cy, highlight=False, small=False):
    fnt, tw, th, w, h = tag_metrics(text, small)
    rx = cx - w//2
    ry = cy - h//2 - 1
    radius = h // 2
    if highlight:
        bg = (*CYAN, 40)
        border = CYAN_BRIGHT
        txt_color = CYAN_BRIGHT
    else:
        bg = (*CYAN, 18)
        border = CYAN
        txt_color = WHITE
    draw.rounded_rectangle([rx, ry, rx+w, ry+h], radius=radius, fill=bg, outline=border, width=2)
    draw.text((cx - tw//2, cy - th//2 - 1), text, fill=txt_color, font=fnt)

# 8 个标签：两行居中布局
# 第一行（上弧形）：5 个核心 AI + 业务能力
row1 = [
    ("LLM Gateway",   False),
    ("MCP Tools",     False),
    ("SSE Streaming", False),
    ("JWT Auth",      False),
    ("CRUD Template", False),
]
# 第二行（下弧形）：3 个 DevOps 能力
row2 = [
    ("Docker Ready",  False),
    ("Kafka",         True),
    ("Prometheus",    False),
]

def layout_row(items, center_y, rise, gap):
    """在一行内居中均匀排列标签，返回 positions"""
    total = sum(tag_metrics(t, sm)[3] for t, sm in items) + gap * (len(items) - 1)
    sx = (W - total) // 2
    result = []
    x = sx
    n = len(items)
    for i, (text, hl) in enumerate(items):
        _, _, _, w, _ = tag_metrics(text, False)
        cx = x + w // 2
        # 抛物线：中心高
        t = i / (n - 1) if n > 1 else 0.5
        parabola = 1 - abs(2*t - 1)
        cy = int(center_y - rise * parabola)
        result.append((text, cx, cy, hl, False))
        x += w + gap
    return result

positions = []
positions += layout_row(row1, center_y=185, rise=18, gap=22)
positions += layout_row(row2, center_y=260, rise=10, gap=28)

for text, cx, cy, hl, sm in positions:
    draw_tag_at(text, cx, cy, highlight=hl, small=sm)

# ========== 主标题（带发光效果）==========
title_text = "FastAPI AI Starter"
bbox = draw.textbbox((0, 0), title_text, font=font_title)
tw = bbox[2] - bbox[0]
title_x = (W - tw) // 2
title_y = 345

# glow layers
for offset in [4, 3, 2, 1]:
    alpha = int(15 / offset)
    for dx, dy in [(-offset, 0), (offset, 0), (0, -offset), (0, offset)]:
        draw.text((title_x + dx, title_y + dy), title_text, fill=(*CYAN, alpha), font=font_title)

draw.text((title_x, title_y), title_text, fill=WHITE, font=font_title)

# ========== 副标题 ==========
sub_text = "Production-ready FastAPI backend scaffold for AI applications"
bbox = draw.textbbox((0, 0), sub_text, font=font_subtitle)
tw = bbox[2] - bbox[0]
draw.text(((W - tw) // 2, 432), sub_text, fill=(*GRAY_SUBTITLE, 230), font=font_subtitle)

# ========== 分隔线 ==========
line_w = 80
draw.rounded_rectangle(
    [(W - line_w)//2, 478, (W + line_w)//2, 483],
    radius=2, fill=(*CYAN, 220)
)

# ========== 底部技术栈 ==========
footer_text = "Python 3.12  ·  FastAPI  ·  Pydantic v2  ·  Tortoise-ORM"
bbox = draw.textbbox((0, 0), footer_text, font=font_footer)
tw = bbox[2] - bbox[0]
draw.text(((W - tw) // 2, 530), footer_text, fill=(*GRAY_FOOTER, 200), font=font_footer)

# ========== 保存 ==========
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "social-preview.png")
# RGBA -> RGB（黑色背景填充透明区域）
final = Image.new("RGB", (W, H), BG_TOP)
final.paste(img, mask=img.split()[3])
final.save(out, "PNG", optimize=True)
print(f"Saved: {out}")
print(f"Font bold: {font_bold_path}")
print(f"Font regular: {font_regular_path}")
