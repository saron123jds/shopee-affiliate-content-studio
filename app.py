import os
import io
import re
import json
import zipfile
import datetime as dt
from dataclasses import dataclass
from typing import List, Tuple

import requests
from slugify import slugify
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy

APP_PORT = int(os.environ.get("PORT", "7000"))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///studio.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


# -----------------------------
# Models
# -----------------------------
class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fixed_hashtags = db.Column(db.Text, default="#shopee #shopeeafiliados")
    max_hashtags = db.Column(db.Integer, default=18)
    cta = db.Column(db.Text, default="Confira no link 👇")
    affiliate_disclaimer = db.Column(db.Text, default="(Link de afiliado — posso receber comissão sem custo extra.)")
    language = db.Column(db.Text, default="pt-br")
    default_prefix = db.Column(db.Text, default="Achado do dia ✨")
    default_suffix = db.Column(db.Text, default="")

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.Text, nullable=False)
    category = db.Column(db.Text, default="")
    price = db.Column(db.Text, default="")
    affiliate_link = db.Column(db.Text, default="")
    image_urls = db.Column(db.Text, default="")  # one per line
    notes = db.Column(db.Text, default="")
    caption = db.Column(db.Text, default="")
    hashtags = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)

class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.Text, default="")
    shopee_url = db.Column(db.Text, nullable=False)
    target_views = db.Column(db.Integer, default=1)
    current_views = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)


# -----------------------------
# Hashtag engine (heurística)
# -----------------------------
STOPWORDS = set("""a o os as de da do das dos e em no na nos nas para por com sem um uma umas uns
manga tecido cor tamanho feminino feminina masculino masculina infantil plus size moda look
""".split())

CATEGORY_TAGS = {
    "moda feminina": ["#modafeminina", "#lookdodia", "#tendencia", "#roupafeminina"],
    "moda evangélica": ["#modaevangelica", "#lookevangelico", "#modacrista"],
    "beleza": ["#beleza", "#skincare", "#maquiagem"],
    "casa": ["#casaedecoracao", "#organização", "#utilidades"],
    "eletronicos": ["#eletronicos", "#tecnologia", "#gadgets"],
    "fitness": ["#fitness", "#treino", "#academia"],
    "acessorios": ["#acessorios", "#estilo", "#detalhes"],
}

MATERIAL_TAGS = {
    "linho": "#linho",
    "algodao": "#algodao",
    "algodão": "#algodao",
    "jeans": "#jeans",
    "chiffon": "#chiffon",
    "laise": "#laise",
    "tricot": "#tricot",
    "tricô": "#tricot",
    "malha": "#malha",
    "tule": "#tule",
    "viscose": "#viscose",
    "sued": "#sued",
    "suede": "#suede",
}

ITEM_TAGS = {
    "vestido": "#vestido",
    "saia": "#saia",
    "blusa": "#blusa",
    "camisa": "#camisa",
    "conjunto": "#conjunto",
    "calça": "#calca",
    "calca": "#calca",
    "short": "#short",
    "jaqueta": "#jaqueta",
    "casaco": "#casaco",
    "bolsa": "#bolsa",
    "sapato": "#sapato",
    "tenis": "#tenis",
    "tênis": "#tenis",
    "sandalia": "#sandalia",
    "sandália": "#sandalia",
}

COLOR_TAGS = {
    "preto": "#preto",
    "branco": "#branco",
    "bege": "#bege",
    "nude": "#nude",
    "azul": "#azul",
    "rosa": "#rosa",
    "verde": "#verde",
    "vermelho": "#vermelho",
    "marrom": "#marrom",
    "cinza": "#cinza",
    "off white": "#offwhite",
    "offwhite": "#offwhite",
}

def tokenize(text: str) -> List[str]:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9áàâãéèêíìîóòôõúùûç\s-]", " ", text)
    parts = re.split(r"\s+", text.strip())
    return [p for p in parts if p and p not in STOPWORDS and len(p) > 2]

def uniq_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for it in items:
        if it not in seen:
            out.append(it)
            seen.add(it)
    return out

def normalize_tag(word: str) -> str:
    # remove accents-ish for common cases
    w = word.lower()
    w = w.replace("ç","c").replace("ã","a").replace("á","a").replace("à","a").replace("â","a")
    w = w.replace("é","e").replace("ê","e").replace("í","i").replace("ó","o").replace("ô","o").replace("õ","o")
    w = w.replace("ú","u")
    w = re.sub(r"[^a-z0-9]", "", w)
    if not w:
        return ""
    return f"#{w}"

def build_hashtags(title: str, category: str, fixed: str, max_n: int) -> str:
    tags = []
    # fixed first
    fixed_parts = re.findall(r"#\w+[\w_]*", fixed or "")
    tags.extend([t.lower() for t in fixed_parts])

    t = (title or "").lower()

    # category tags
    c = (category or "").lower().strip()
    if c in CATEGORY_TAGS:
        tags.extend([x.lower() for x in CATEGORY_TAGS[c]])

    # material/item/color tags
    for k,v in MATERIAL_TAGS.items():
        if k in t:
            tags.append(v)
    for k,v in ITEM_TAGS.items():
        if re.search(rf"\b{re.escape(k)}\b", t):
            tags.append(v)
    for k,v in COLOR_TAGS.items():
        if k in t:
            tags.append(v)

    # keyword-derived tags (top tokens)
    toks = tokenize(title)
    toks = uniq_keep_order(toks)[:12]
    tags.extend([normalize_tag(x) for x in toks if normalize_tag(x)])

    tags = [x for x in tags if x]
    tags = uniq_keep_order([x.lower() for x in tags])

    # cap
    if max_n and len(tags) > max_n:
        tags = tags[:max_n]
    return " ".join(tags)

def build_caption(settings: Settings, p: Product) -> Tuple[str, str]:
    title = (p.title or "").strip()
    price = (p.price or "").strip()
    link = (p.affiliate_link or "").strip()
    prefix = (settings.default_prefix or "").strip()
    suffix = (settings.default_suffix or "").strip()

    line1 = f"{prefix} {title}".strip()

    lines = [line1]
    if price:
        lines.append(f"💰 {price}")
    if p.notes:
        lines.append(p.notes.strip())

    # CTA + link + disclaimer
    cta = (settings.cta or "").strip()
    disc = (settings.affiliate_disclaimer or "").strip()
    if link:
        lines.append(f"{cta} {link}".strip())
    else:
        lines.append(cta)
    if disc:
        lines.append(disc)

    if suffix:
        lines.append(suffix)

    caption = "\n".join([x for x in lines if x])
    hashtags = build_hashtags(title, p.category, settings.fixed_hashtags, settings.max_hashtags)
    return caption, hashtags


# -----------------------------
# Helpers
# -----------------------------
def get_settings() -> Settings:
    s = Settings.query.first()
    if not s:
        s = Settings()
        db.session.add(s)
        db.session.commit()
    return s

with app.app_context():
    db.create_all()
    get_settings()

def parse_image_urls(text: str) -> List[str]:
    urls = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        urls.append(line)
    return urls

def safe_filename(title: str, idx: int) -> str:
    slug = slugify(title)[:60] or "produto"
    return f"{slug}_{idx:03d}"

def download_images(urls: List[str], folder: str) -> List[str]:
    os.makedirs(folder, exist_ok=True)
    saved = []
    for i, url in enumerate(urls, start=1):
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            # guess extension
            ext = ".jpg"
            ctype = (r.headers.get("content-type") or "").lower()
            if "png" in ctype: ext = ".png"
            if "webp" in ctype: ext = ".webp"
            if "jpeg" in ctype or "jpg" in ctype: ext = ".jpg"
            fn = os.path.join(folder, f"img_{i:02d}{ext}")
            with open(fn, "wb") as f:
                f.write(r.content)
            saved.append(fn)
        except Exception:
            # skip on error
            continue
    return saved

def format_price(value) -> str:
    if value is None:
        return ""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return ""
    if amount <= 0:
        return ""
    if amount >= 100000:
        amount = amount / 100000
    elif amount >= 100:
        amount = amount / 100
    formatted = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"

def find_dict_with_keys(data, required_keys: List[str]):
    if isinstance(data, dict):
        if all(key in data for key in required_keys):
            return data
        for value in data.values():
            found = find_dict_with_keys(value, required_keys)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = find_dict_with_keys(value, required_keys)
            if found:
                return found
    return None

def extract_shopee_product(url: str) -> Tuple[str, str, List[str]]:
    headers = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "accept-language": "pt-BR,pt;q=0.9,en;q=0.8",
    }
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    html = resp.text
    match = re.search(r'__NEXT_DATA__"\s*type="application/json"\s*>(\{.*?\})</script>', html, re.DOTALL)
    if not match:
        raise ValueError("Não foi possível localizar os dados do produto.")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError("Não foi possível ler os dados do produto.") from exc

    item = find_dict_with_keys(data, ["name", "images"])
    if not item:
        raise ValueError("Não foi possível encontrar imagens do produto.")

    title = (item.get("name") or item.get("title") or "").strip()
    if not title:
        raise ValueError("Não foi possível identificar o título do produto.")

    images_raw = item.get("images") or []
    image_urls = []
    for img in images_raw:
        if not img:
            continue
        if isinstance(img, str) and img.startswith("http"):
            image_urls.append(img)
        else:
            image_urls.append(f"https://down-br.img.susercontent.com/file/{img}")

    price = format_price(
        item.get("price") or item.get("price_min") or item.get("price_min_before_discount")
    )
    return title, price, image_urls

# -----------------------------
# Routes
# -----------------------------
@app.route("/")
def home():
    s = get_settings()
    total = Product.query.count()
    ready = Product.query.filter(Product.caption != "").count()
    return render_template("home.html", total=total, ready=ready, settings=s)

@app.route("/settings", methods=["GET","POST"])
def settings():
    s = get_settings()
    if request.method == "POST":
        s.fixed_hashtags = request.form.get("fixed_hashtags","").strip()
        s.max_hashtags = int(request.form.get("max_hashtags","18") or 18)
        s.cta = request.form.get("cta","").strip()
        s.affiliate_disclaimer = request.form.get("affiliate_disclaimer","").strip()
        s.default_prefix = request.form.get("default_prefix","").strip()
        s.default_suffix = request.form.get("default_suffix","").strip()
        db.session.commit()
        flash("Configurações salvas ✅", "success")
        return redirect(url_for("settings"))
    return render_template("settings.html", s=s)

@app.route("/products")
def products():
    q = (request.args.get("q") or "").strip()
    if q:
        items = Product.query.filter(Product.title.like(f"%{q}%")).order_by(Product.updated_at.desc()).all()
    else:
        items = Product.query.order_by(Product.updated_at.desc()).all()
    return render_template("products.html", items=items, q=q)

@app.route("/products/new", methods=["GET","POST"])
def product_new():
    if request.method == "POST":
        p = Product(
            title=request.form.get("title","").strip(),
            category=request.form.get("category","").strip(),
            price=request.form.get("price","").strip(),
            affiliate_link=request.form.get("affiliate_link","").strip(),
            image_urls=request.form.get("image_urls","").strip(),
            notes=request.form.get("notes","").strip(),
        )
        db.session.add(p)
        db.session.commit()
        flash("Produto cadastrado ✅", "success")
        return redirect(url_for("products"))
    return render_template("product_form.html", p=None)

@app.route("/products/from_shopee", methods=["POST"])
def product_from_shopee():
    url = (request.form.get("shopee_url") or "").strip()
    if not url:
        flash("Informe o link do produto da Shopee.", "warning")
        return redirect(url_for("product_new"))
    try:
        title, price, image_urls = extract_shopee_product(url)
    except Exception as exc:
        flash(f"Não foi possível importar o produto: {exc}", "danger")
        return redirect(url_for("product_new"))

    p = Product(
        title=title,
        price=price,
        affiliate_link="",
        image_urls="\n".join(image_urls),
    )
    db.session.add(p)
    db.session.commit()
    flash("Produto importado da Shopee ✅", "success")
    return redirect(url_for("product_edit", pid=p.id))

@app.route("/products/<int:pid>/edit", methods=["GET","POST"])
def product_edit(pid):
    p = Product.query.get_or_404(pid)
    if request.method == "POST":
        p.title = request.form.get("title","").strip()
        p.category = request.form.get("category","").strip()
        p.price = request.form.get("price","").strip()
        p.affiliate_link = request.form.get("affiliate_link","").strip()
        p.image_urls = request.form.get("image_urls","").strip()
        p.notes = request.form.get("notes","").strip()
        db.session.commit()
        flash("Produto atualizado ✅", "success")
        return redirect(url_for("products"))
    return render_template("product_form.html", p=p)

@app.route("/videos", methods=["GET", "POST"])
def videos():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        shopee_url = request.form.get("shopee_url", "").strip()
        target_views = int(request.form.get("target_views", "1") or 1)
        if not shopee_url:
            flash("Informe o link do vídeo da Shopee.", "warning")
            return redirect(url_for("videos"))
        if target_views < 1:
            target_views = 1
        v = Video(title=title, shopee_url=shopee_url, target_views=target_views)
        db.session.add(v)
        db.session.commit()
        flash("Vídeo adicionado ✅", "success")
        return redirect(url_for("videos"))

    items = Video.query.order_by(Video.updated_at.desc()).all()
    return render_template("videos.html", items=items)

@app.route("/videos/<int:vid>/increment", methods=["POST"])
def video_increment(vid):
    v = Video.query.get_or_404(vid)
    v.current_views = (v.current_views or 0) + 1
    db.session.commit()
    flash("Visualização registrada ✅", "success")
    return redirect(url_for("videos"))

@app.route("/videos/<int:vid>/reset", methods=["POST"])
def video_reset(vid):
    v = Video.query.get_or_404(vid)
    v.current_views = 0
    db.session.commit()
    flash("Contador reiniciado ✅", "warning")
    return redirect(url_for("videos"))

@app.route("/videos/<int:vid>/delete", methods=["POST"])
def video_delete(vid):
    v = Video.query.get_or_404(vid)
    db.session.delete(v)
    db.session.commit()
    flash("Vídeo removido 🗑️", "warning")
    return redirect(url_for("videos"))

@app.route("/products/<int:pid>/delete", methods=["POST"])
def product_delete(pid):
    p = Product.query.get_or_404(pid)
    db.session.delete(p)
    db.session.commit()
    flash("Produto removido 🗑️", "warning")
    return redirect(url_for("products"))

@app.route("/products/<int:pid>/generate", methods=["POST"])
def product_generate(pid):
    s = get_settings()
    p = Product.query.get_or_404(pid)
    cap, tags = build_caption(s, p)
    p.caption = cap
    p.hashtags = tags
    db.session.commit()
    flash("Legenda e hashtags geradas ✨", "success")
    return redirect(url_for("products"))

@app.route("/generate_all", methods=["POST"])
def generate_all():
    s = get_settings()
    items = Product.query.all()
    for p in items:
        cap, tags = build_caption(s, p)
        p.caption = cap
        p.hashtags = tags
    db.session.commit()
    flash(f"Gerado para {len(items)} produtos ✅", "success")
    return redirect(url_for("products"))

@app.route("/export_zip", methods=["POST"])
def export_zip():
    # Build a ZIP with folders per product: images + caption.txt + meta.json
    s = get_settings()
    items = Product.query.order_by(Product.updated_at.desc()).all()
    ts = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        manifest = []
        for idx, p in enumerate(items, start=1):
            cap, tags = (p.caption, p.hashtags)
            if not cap or not tags:
                cap, tags = build_caption(s, p)

            folder = safe_filename(p.title, idx)
            # caption file
            caption_text = cap + "\n\n" + tags + "\n"
            z.writestr(f"{folder}/caption.txt", caption_text)

            meta = {
                "id": p.id,
                "title": p.title,
                "category": p.category,
                "price": p.price,
                "affiliate_link": p.affiliate_link,
                "image_urls": parse_image_urls(p.image_urls),
                "caption": cap,
                "hashtags": tags,
                "updated_at": (p.updated_at.isoformat() if p.updated_at else None)
            }
            z.writestr(f"{folder}/meta.json", json.dumps(meta, ensure_ascii=False, indent=2))

            # Optionally download images at export time
            urls = meta["image_urls"]
            if urls:
                tmp_dir = os.path.join("tmp_export", folder)
                paths = download_images(urls, tmp_dir)
                for pth in paths:
                    arc = f"{folder}/images/{os.path.basename(pth)}"
                    z.write(pth, arcname=arc)

            manifest.append({"folder": folder, "title": p.title, "affiliate_link": p.affiliate_link})

        z.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    # cleanup temp
    if os.path.exists("tmp_export"):
        import shutil
        shutil.rmtree("tmp_export", ignore_errors=True)

    mem.seek(0)
    return send_file(mem, mimetype="application/zip", as_attachment=True, download_name=f"conteudos_{ts}.zip")

@app.route("/copy/<int:pid>")
def product_copy(pid):
    p = Product.query.get_or_404(pid)
    return render_template("copy.html", p=p)

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        get_settings()
    app.run(host="127.0.0.1", port=APP_PORT, debug=True)
