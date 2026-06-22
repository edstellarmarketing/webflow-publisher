# -*- coding: utf-8 -*-
import streamlit as st
import requests
import re
from bs4 import BeautifulSoup, NavigableString, Tag
import html as html_mod
import json

# ─── CONFIG ───────────────────────────────────────────────────────────────────
WEBFLOW_API_BASE = "https://api.webflow.com/v2"
EMBED_CHAR_LIMIT = 10000
REQUEST_TIMEOUT = 30  # seconds — keep Streamlit worker from hanging on a slow API

# ─── EMBED DETECTION RULES ────────────────────────────────────────────────────
# Top-level CSS classes that mark an element as an EMBED block.
# If any of these classes appear on a tag, the ENTIRE tag (and its children)
# gets wrapped with <div data-rt-embed-type='true'>
EMBED_TOP_CLASSES = {
    # Key Takeaways box
    "takeaway", "key-takeaways",
    # Evaluation Criteria grid
    "criteria",
    # Comparison Table
    "table-scroll",
    # Infographic copy-to-clipboard
    "copy-div",
    # Company Profile card
    "co-card",
    # Testimonial / Expert Quote
    "testimonial",
    # FAQ section (on <section> tag)
    "faq",
    # CTA block
    "cta",
    # Stats grid (from Malaysia template)
    "nl-card",
    # Related reading
    "related-reading",
    # Author block
    "author-block",
    # Infographic placeholder
    "infographic-placeholder",
    # Expert quote (standalone, outside co-card)
    "expert-quote",
    # CTA block (aside variant)
    "cta-block",
    # Steps list
    "steps-list",
}

# Container tags to UNWRAP (strip the tag, process its children individually)
UNWRAP_TAGS = {"article", "main", "header", "nav"}

# Section/aside: unwrap ONLY if they don't have an embed class
# (e.g. <section class="faq"> is embed, but <section> without class is unwrap)

# Tags that are always plain rich text (when no embed class present)
PLAIN_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li",
              "a", "strong", "em", "b", "i", "blockquote", "figure",
              "figcaption", "br", "hr", "img"}


# ─── PREPROCESSING ────────────────────────────────────────────────────────────

def unescape_if_needed(html_content):
    if "&lt;div" in html_content or "&lt;table" in html_content or "&lt;style" in html_content:
        return html_mod.unescape(html_content)
    return html_content


def normalize_html(html_content):
    html_content = unescape_if_needed(html_content)
    # Use BeautifulSoup to extract <body> content reliably
    soup = BeautifulSoup(html_content, "html.parser")
    body = soup.find("body")
    if body:
        # Return the inner HTML of <body>
        return body.decode_contents().strip()
    # If no <body>, check for <article> directly
    article = soup.find("article")
    if article:
        return str(article)
    # Return as-is
    return html_content.strip()


# ─── BLOCK CLASSIFIER ────────────────────────────────────────────────────────

def get_classes(tag):
    """Get classes as a set."""
    classes = tag.get("class", [])
    if isinstance(classes, str):
        classes = classes.split()
    return set(classes)


def is_embed_block(tag):
    """Check if a tag should be treated as an embed block."""
    if not isinstance(tag, Tag):
        return False

    classes = get_classes(tag)

    # Direct match: tag has a known embed class
    if classes & EMBED_TOP_CLASSES:
        return True

    # <div> with ANY class (likely a styled component)
    if tag.name == "div" and classes:
        return True

    # <section> with a class (like <section class="faq">)
    if tag.name == "section" and classes:
        return True

    # <aside> with a class (like <aside class="cta-block">)
    if tag.name == "aside" and classes:
        return True

    # <table> with a class
    if tag.name == "table" and classes:
        return True

    # <details> tags (FAQ items when not inside a faq section)
    if tag.name == "details":
        return True

    return False


def should_unwrap(tag):
    """Check if a container tag should be unwrapped (children processed individually)."""
    if not isinstance(tag, Tag):
        return False

    classes = get_classes(tag)

    # Always unwrap these tags
    if tag.name in UNWRAP_TAGS:
        return True

    # <section> without embed class → unwrap
    if tag.name == "section" and not (classes & EMBED_TOP_CLASSES):
        return True

    # <aside> without embed class → unwrap
    if tag.name == "aside" and not (classes & EMBED_TOP_CLASSES) and not classes:
        return True

    # <div> without ANY class → generic wrapper, unwrap
    if tag.name == "div" and not classes:
        return True

    return False


def is_noise(element):
    """Check if an element is noise (comments, section labels, etc.) to strip."""
    if isinstance(element, NavigableString):
        text = str(element).strip()
        if not text:
            return True
        # Strip section comment labels like "Section 1: Title"
        if re.match(r'^(Section \d+|Company \d+|Mid-Blog|Expert Quote|End)', text, re.IGNORECASE):
            return True
        # Strip bare text that looks like a comment
        if text.startswith("REPLACE:") or text.startswith("PLACEHOLDER:"):
            return True
    return False


def unwrap_containers(soup):
    """
    Unwrap the outermost generic container if there's only one.
    e.g. <article><...content...></article> → process content directly.
    """
    children = list(soup.children)
    real_children = [c for c in children if isinstance(c, Tag) or
                     (isinstance(c, NavigableString) and str(c).strip())]

    if len(real_children) == 1 and isinstance(real_children[0], Tag):
        child = real_children[0]
        if child.name in UNWRAP_TAGS:
            return unwrap_containers(child)
        if child.name == "div" and not get_classes(child):
            return unwrap_containers(child)

    return soup


def process_children(parent, blocks):
    """
    Recursively process children of a container element.
    - Embed blocks → collect as embed
    - Unwrappable containers → recurse into their children
    - Plain tags → collect as plain
    - Noise text → skip
    - <style> → skip (Webflow has its own CSS)
    """
    for element in parent.children:
        # Skip noise (empty text, section labels, placeholders)
        if is_noise(element):
            continue

        # Skip non-tag, non-string
        if not isinstance(element, (Tag, NavigableString)):
            continue

        # NavigableString that's not noise — wrap loose text in <p>
        if isinstance(element, NavigableString):
            text = str(element).strip()
            if text:
                blocks.append(("plain", f"<p>{text}</p>"))
            continue

        # <style> → skip entirely (Webflow uses its own stylesheets)
        if element.name == "style":
            continue

        # <script> → skip
        if element.name == "script":
            continue

        # <h1> → skip (handled by the Name field, not content)
        if element.name == "h1":
            continue

        # Is this an embed block?
        if is_embed_block(element):
            blocks.append(("embed", str(element)))
            continue

        # Should this container be unwrapped?
        if should_unwrap(element):
            process_children(element, blocks)
            continue

        # <p> that might contain embed children (parser quirk)
        if element.name == "p":
            has_inner_embeds = any(
                isinstance(child, Tag) and is_embed_block(child)
                for child in element.children
            )
            if has_inner_embeds:
                current_plain = []
                for child in element.children:
                    if isinstance(child, Tag) and is_embed_block(child):
                        if current_plain:
                            plain_html = "".join(str(c) for c in current_plain).strip()
                            if plain_html and plain_html not in ("<br/>", "<br>", ""):
                                blocks.append(("plain", f"<p>{plain_html}</p>"))
                            current_plain = []
                        blocks.append(("embed", str(child)))
                    else:
                        current_plain.append(child)
                if current_plain:
                    plain_html = "".join(str(c) for c in current_plain).strip()
                    if plain_html and plain_html not in ("<br/>", "<br>", ""):
                        blocks.append(("plain", f"<p>{plain_html}</p>"))
                continue

        # Plain rich text element
        el_html = str(element).strip()
        if el_html:
            blocks.append(("plain", el_html))


def split_into_blocks(html_content):
    html_content = normalize_html(html_content)
    soup = BeautifulSoup(html_content, "html.parser")

    # Unwrap outermost container (e.g. <article>)
    soup = unwrap_containers(soup)

    blocks = []
    process_children(soup, blocks)

    return blocks


# ─── CONVERSION LAYER ─────────────────────────────────────────────────────────
# Transforms HTML file format → Webflow template format before wrapping.

# CSS style block for co-card and related components (injected once)
CO_CARD_STYLE = '''<style>
  .co-card {
    background-color: #fff;
    border: 1px solid #e2e2dd;
    transition: box-shadow .3s;
    border-radius: 8px;
    padding: 30px;
    margin: 14px 0;
  }
  .co-card:hover { box-shadow: 0 8px 30px rgba(0,0,0,.06); }
  .co-hdr { display: flex; align-items: flex-start; gap: 16px; margin-bottom: 16px; }
  .co-hdr h3, .co-hdr p { margin: 0; }
  .co-logo { border-radius: 8px; padding: 4px 10px; border: 1px solid #264cbe; display: flex; align-items: center; justify-content: center; height:70px; }
  .co-logo img { width: 150px; height: 100%; object-fit: contain; }
  .meta-row { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
  .chip { font-size: 12px; padding: 4px 10px; border-radius: 5px; background: #f3f4f6; color: #1a1a2e; font-weight: 500; }
  .co-card p { margin-top: 0px; }
  .co-card p a { text-decoration: none; }
  .co-card ul { padding-left: 20px; margin-bottom: 12px; margin-top: 10px; }
  .co-card li { margin-bottom: 4px; }
  .insight { display: flex; gap: 16px; background: linear-gradient(135deg, #fefce8, #fef9c3); border: 1px solid #fde68a; border-radius: 10px; padding: 24px; margin: 16px 0; }
  .insight-icon { width: 40px; height: 40px; border-radius: 50%; background: #fef3c7; display: flex; align-items: center; justify-content: center; font-size: 20px; flex-shrink: 0; }
  .ename { font-weight: 600; font-size: 14px; margin-top: 8px; }
  .author-del { font-size: 13px; color: #6b7280; margin-top: 4px; }
</style>'''

# CSS style block for criteria grid
CRITERIA_STYLE = '''<style>
  .criteria {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
    gap: 1rem;
    margin: 1.5rem 0 2rem;
  }
  .crit-item {
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: 1.25rem;
    text-align: center;
  }
  .crit-icon { font-size: 1.8rem; margin-bottom: 0.5rem; }
  .crit-item p:first-of-type { font-weight: 700; font-size: 0.95rem; color: #1E40AF; margin-bottom: 0.4rem; }
  .crit-item p:last-of-type { font-size: 0.82rem; color: #6B7280; line-height: 1.5; }
  @media (max-width: 768px) { .criteria { grid-template-columns: 1fr 1fr; } }
  @media (max-width: 480px) { .criteria { grid-template-columns: 1fr; } }
</style>'''


def convert_key_takeaways(soup_tag):
    """key-takeaways + <h3> → takeaway + <p>💡 KEY TAKEAWAYS</p>"""
    soup_tag["class"] = ["takeaway"]
    h3 = soup_tag.find("h3")
    if h3:
        h3.name = "p"
        h3.string = "💡 KEY TAKEAWAYS"
    return str(soup_tag)


def convert_eval_grid(soup_tag):
    """eval-grid/eval-card/icon/factor/desc → criteria/crit-item/crit-icon + <p> pairs.
    Only keeps the first 6 standard criteria, drops region-specific ones."""
    soup_tag["class"] = ["criteria"]
    cards = soup_tag.find_all("div", class_="eval-card")

    # Only keep the first 6 standard criteria
    for i, card in enumerate(cards):
        if i < 6:
            card["class"] = ["crit-item"]
            icon_div = card.find("div", class_="icon")
            if icon_div:
                icon_div["class"] = ["crit-icon"]
            factor = card.find("div", class_="factor")
            if factor:
                factor.name = "p"
                del factor["class"]
            desc = card.find("div", class_="desc")
            if desc:
                desc.name = "p"
                del desc["class"]
        else:
            # Remove region-specific criteria (7th onwards)
            card.decompose()
    return str(soup_tag)


def convert_table_wrap(soup_tag):
    """table-wrap → table-scroll, add comp-table class, add rank spans"""
    soup_tag["class"] = ["table-scroll"]
    table = soup_tag.find("table")
    if table:
        table["class"] = ["comp-table"]
        # Add <span class="rank"> around first <td> content in each row
        for row in table.find_all("tr"):
            tds = row.find_all("td")
            if tds and tds[0].string and tds[0].string.strip().isdigit():
                num = tds[0].string.strip()
                tds[0].clear()
                span = BeautifulSoup(f'<span class="rank">{num}</span>', "html.parser")
                tds[0].append(span)
    return str(soup_tag)


def convert_company_profile(soup_tag):
    """company-profile → co-card with co-hdr, co-logo, meta-row, chip structure"""
    # Change main class
    classes = soup_tag.get("class", [])
    new_classes = ["co-card"]
    if "featured" in classes:
        new_classes.append("featured")  # preserve featured flag if present
    soup_tag["class"] = new_classes

    # Find and restructure the header: h3 + tagline → co-hdr with co-logo
    h3 = soup_tag.find("h3")
    tagline = soup_tag.find("p", class_="tagline")

    if h3:
        # Build co-hdr structure
        co_hdr_html = '<div class="co-hdr"><div class="co-logo"></div><div>'
        co_hdr_html += str(h3)
        if tagline:
            # Remove tagline class
            tagline_copy = str(tagline).replace(' class="tagline"', '')
            co_hdr_html += tagline_copy
        co_hdr_html += '</div></div>'

        # Insert co-hdr before h3
        co_hdr = BeautifulSoup(co_hdr_html, "html.parser")
        h3.insert_before(co_hdr)
        h3.decompose()
        if tagline:
            tagline.decompose()

    # meta-badges → meta-row, badge → chip
    meta = soup_tag.find("div", class_="meta-badges")
    if meta:
        meta["class"] = ["meta-row"]
        for badge in meta.find_all("span", class_="badge"):
            badge["class"] = ["chip"]

    # offerings-title div → <b>Key Offerings:</b>
    off_title = soup_tag.find("div", class_="offerings-title")
    if off_title:
        off_title.replace_with(BeautifulSoup("<b>Key Offerings:</b>", "html.parser"))

    # highlights-title div → <b>Highlights:</b>
    hi_title = soup_tag.find("div", class_="highlights-title")
    if hi_title:
        hi_title.replace_with(BeautifulSoup("<b>Highlights:</b>", "html.parser"))

    # company-location div → <p><b>Location:</b> text</p>
    loc = soup_tag.find("div", class_="company-location")
    if loc:
        loc.name = "p"
        del loc["class"]

    # expert-quote inside card → insight block (simplified — keep as-is if present)
    # The insight block structure in the Webflow format is already inside co-card

    return str(soup_tag)


def convert_expert_quote_to_testimonial(soup_tag):
    """
    expert-quote (simple blockquote + attribution) → testimonial layout.
    Note: The input has placeholder data; the real testimonial data comes from
    the content writers. We convert the structure but keep the content.
    """
    soup_tag["class"] = ["testimonial"]

    # blockquote → plain <p>
    bq = soup_tag.find("blockquote")
    if bq:
        bq.name = "p"

    # attribution div → simplified author section
    attr = soup_tag.find("div", class_="attribution")
    if attr:
        attr["class"] = ["div-flex"]
        # The attribution has <strong>Name</strong><br/>Title<br/>Credentials
        # Convert to the author-name structure
        strong = attr.find("strong")
        name = strong.string if strong else ""
        # Get remaining text
        text_parts = [s.strip() for s in attr.stripped_strings if s.strip() != name]

        new_html = f'<div class="author-name"><div class="name-flex"><b>{name}</b></div>'
        if len(text_parts) >= 1:
            new_html += f'<p class="author-pos">{text_parts[0]}</p>'
        if len(text_parts) >= 2:
            new_html += f'<p class="author-del">{text_parts[1]}</p>'
        new_html += '</div>'

        attr.clear()
        attr.append(BeautifulSoup(new_html, "html.parser"))

    return str(soup_tag)


def convert_steps_to_paragraphs(soup_tag):
    """
    steps-list with step-items (h4 + p) → plain <p> paragraphs with bold lead.
    Returns a LIST of plain blocks, not a single embed.
    """
    paragraphs = []
    for step in soup_tag.find_all("div", class_="step-item"):
        h4 = step.find("h4")
        p = step.find("p")
        if h4 and p:
            # Combine: <p><strong>Step title.</strong> Step content...</p>
            step_html = f"<p><strong>{h4.get_text()}</strong> {p.decode_contents()}</p>"
            paragraphs.append(step_html)
    return paragraphs


def convert_faq_details(details_list):
    """
    <details><summary>Q</summary><div class="faq-answer"><p>A</p></div></details>
    → <section class="faq" itemscope itemtype="https://schema.org/FAQPage">
        <div class="faq-item"><div class="faq-question"><p>Q</p><span class="toggle-icon"></span></div>
        <div class="faq-answer"><p>A</p></div></div>
    """
    faq_html = '<section class="faq" itemscope="" itemtype="https://schema.org/FAQPage">\n'

    for i, detail in enumerate(details_list):
        summary = detail.find("summary")
        answer_div = detail.find("div", class_="faq-answer")

        q_text = summary.get_text() if summary else ""
        a_html = answer_div.decode_contents() if answer_div else ""

        active = ' active' if i == 0 else ''
        faq_html += f'''<div class="faq-item{active}" itemscope="" itemprop="mainEntity" itemtype="https://schema.org/Question">
<div class="faq-question" data-index="{i}">
<p itemprop="name">{q_text}</p>
<span class="toggle-icon"></span>
</div>
<div class="faq-answer" id="answer-{i}" itemprop="acceptedAnswer" itemscope="" itemtype="https://schema.org/Answer">
{a_html}
</div>
</div>\n'''

    faq_html += '</section>'
    return faq_html


def convert_cta_block(soup_tag, is_end_cta=False):
    """aside.cta-block → div.cta (mid) or div.cta.bg-green (end)"""
    new_div = BeautifulSoup(str(soup_tag), "html.parser").find()
    new_div.name = "div"

    if is_end_cta:
        new_div["class"] = ["cta", "bg-green"]
    else:
        new_div["class"] = ["cta"]

    # Add style to h3
    h3 = new_div.find("h3")
    if h3:
        h3["style"] = "color:white;margin-top:0px"

    return str(new_div)


def convert_quotes_to_single(html_str):
    """Convert all double-quoted attributes to single quotes for Webflow.
    Encodes any literal apostrophes inside the value first so the swap doesn't
    produce malformed attrs like title='don't'."""
    def _swap(m):
        name, val = m.group(1), m.group(2)
        return f"{name}='{val.replace(chr(39), '&#39;')}'"
    return re.sub(r'(\w+)="([^"]*)"', _swap, html_str)


def convert_block(block_type, block_html):
    """Apply the appropriate conversion based on the block's content."""
    soup = BeautifulSoup(block_html, "html.parser")
    first_tag = soup.find()
    if not first_tag:
        return block_html

    classes = set(first_tag.get("class", []))

    # Key Takeaways
    if "key-takeaways" in classes:
        return convert_key_takeaways(first_tag)

    # Eval Grid → Criteria
    if "eval-grid" in classes:
        return convert_eval_grid(first_tag)

    # Table Wrap → Table Scroll
    if "table-wrap" in classes:
        return convert_table_wrap(first_tag)

    # Company Profile → Co-Card
    if "company-profile" in classes:
        return convert_company_profile(first_tag)

    # Expert Quote → Testimonial
    if "expert-quote" in classes:
        return convert_expert_quote_to_testimonial(first_tag)

    # CTA Block (aside)
    if "cta-block" in classes:
        return convert_cta_block(first_tag, is_end_cta=False)

    return block_html


def classify_and_wrap(html_content):
    blocks = split_into_blocks(html_content)

    output_parts = []
    embed_count = 0
    plain_count = 0
    warnings = []
    style_injected = False
    criteria_style_injected = False
    cta_count = 0  # Track CTAs to detect end CTA

    for block_type, block_html in blocks:
        if block_type == "embed":
            soup = BeautifulSoup(block_html, "html.parser")
            first_tag = soup.find()
            classes = set(first_tag.get("class", [])) if first_tag else set()

            # ── Special: steps-list → convert to plain paragraphs ──
            if "steps-list" in classes:
                paragraphs = convert_steps_to_paragraphs(first_tag)
                for p in paragraphs:
                    output_parts.append(p)
                    plain_count += 1
                continue

            # ── Special: FAQ details → convert to faq section ──
            if first_tag and first_tag.name == "details":
                # Collect all consecutive details blocks
                # (they come one by one from the parser)
                if not any("section" in p and "faq" in p for p in output_parts[-1:]):
                    # This is a standalone details — collect it
                    # We'll handle FAQ collection below
                    pass

            # ── Special: Inject criteria style before first criteria block ──
            if ("eval-grid" in classes or "criteria" in classes) and not criteria_style_injected:
                crit_style_wrapped = f'<div data-rt-embed-type="true">\n{CRITERIA_STYLE}\n</div>'
                output_parts.append(crit_style_wrapped)
                embed_count += 1
                criteria_style_injected = True

            # ── Special: Inject style block before first co-card ──
            if ("company-profile" in classes or "co-card" in classes) and not style_injected:
                style_wrapped = f'<div data-rt-embed-type="true">\n{CO_CARD_STYLE}\n</div>'
                output_parts.append(style_wrapped)
                embed_count += 1
                style_injected = True

            # ── Special: CTA detection (mid vs end) ──
            if "cta-block" in classes:
                cta_count += 1

            # Apply structural conversion
            block_html = convert_block(block_type, block_html)

            if len(block_html) > EMBED_CHAR_LIMIT:
                soup2 = BeautifulSoup(block_html, "html.parser")
                ft = soup2.find()
                cn = " ".join(ft.get("class", [])) if ft else "unknown"
                warnings.append({
                    "block": f"{ft.name if ft else '?'}.{cn}",
                    "chars": len(block_html),
                    "preview": block_html[:150] + "..."
                })

            wrapped = f'<div data-rt-embed-type="true">\n{block_html}\n</div>'
            output_parts.append(wrapped)
            embed_count += 1
        else:
            stripped = block_html.strip()
            if stripped and stripped not in ("<p></p>", "<p> </p>", "<br/>", "<br>"):
                output_parts.append(stripped)
                plain_count += 1

    # ── Post-processing: collect ALL <details> into one FAQ section ──
    # A stray non-details block between two <details> must NOT split the FAQ,
    # so we gather every detail first, then emit one FAQ section in place of
    # the first detail's slot and drop the rest.
    all_details = []
    for part in output_parts:
        if 'data-rt-embed-type="true"' in part and "<details" in part:
            s = BeautifulSoup(part, "html.parser")
            wrapper = s.find("div", attrs={"data-rt-embed-type": True})
            detail = wrapper.find("details") if wrapper else None
            if detail:
                all_details.append(detail)

    final_parts = []
    faq_emitted = False
    for part in output_parts:
        is_detail_block = (
            'data-rt-embed-type="true"' in part
            and "<details" in part
            and BeautifulSoup(part, "html.parser")
                .find("div", attrs={"data-rt-embed-type": True})
                .find("details") is not None
        )
        if is_detail_block:
            if not faq_emitted and all_details:
                faq_html = convert_faq_details(all_details)
                final_parts.append(f'<div data-rt-embed-type="true">\n{faq_html}\n</div>')
                faq_emitted = True
            # Skip subsequent detail blocks — already folded into the FAQ
            continue
        final_parts.append(part)

    processed_html = "\n".join(final_parts)

    # Process external links: add rel='nofollow' and target='_blank' for non-edstellar links
    link_soup = BeautifulSoup(processed_html, "html.parser")
    for a_tag in link_soup.find_all("a", href=True):
        href = a_tag.get("href", "")
        # Skip internal links (edstellar.com, relative paths, mailto, #anchors)
        if (href.startswith("/") or
            href.startswith("#") or
            href.startswith("mailto:") or
            "edstellar.com" in href):
            # Edstellar links: ensure target='_blank' but NO nofollow
            if href.startswith("http") and "edstellar.com" in href:
                a_tag["target"] = "_blank"
            continue
        # External links: add nofollow + new tab
        if href.startswith("http"):
            a_tag["target"] = "_blank"
            a_tag["rel"] = "nofollow"
    processed_html = str(link_soup)

    # Global: convert double quotes to single quotes on all embed blocks
    processed_html = convert_quotes_to_single(processed_html)
    # Restore the data-rt-embed-type wrapper to double quotes
    processed_html = processed_html.replace("data-rt-embed-type='true'", 'data-rt-embed-type="true"')

    # Recalculate counts after FAQ merging
    final_soup = BeautifulSoup(processed_html, "html.parser")
    embed_count = 0
    plain_count = 0
    for el in final_soup.children:
        if isinstance(el, Tag):
            if el.get("data-rt-embed-type"):
                embed_count += 1
            else:
                plain_count += 1

    stats = {
        "total_blocks": embed_count + plain_count,
        "embed_blocks": embed_count,
        "plain_blocks": plain_count,
        "warnings": warnings,
        "total_chars": len(processed_html),
    }

    return processed_html, stats


# ─── WEBFLOW API ──────────────────────────────────────────────────────────────

def get_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "accept": "application/json",
    }


def test_api_connection(token, collection_id):
    """Test API token by checking collection access and item count."""
    results = {}

    # 1. Test token + collection access — get collection info
    resp = requests.get(f"{WEBFLOW_API_BASE}/collections/{collection_id}",
                        headers=get_headers(token), timeout=REQUEST_TIMEOUT)
    if resp.status_code == 200:
        col = resp.json()
        results["collection"] = {
            "status": "✅ OK",
            "name": col.get("displayName", "?"),
            "slug": col.get("slug", "?"),
            "fields": len(col.get("fields", [])),
        }
    elif resp.status_code in (401, 403):
        results["collection"] = {"status": f"❌ Auth failed ({resp.status_code})", "error": resp.text}
        return results
    else:
        results["collection"] = {"status": f"❌ {resp.status_code}", "error": resp.text}
        return results

    # 2. Test items read — get first page count
    resp = requests.get(f"{WEBFLOW_API_BASE}/collections/{collection_id}/items",
                        headers=get_headers(token), params={"limit": 1},
                        timeout=REQUEST_TIMEOUT)
    if resp.status_code == 200:
        data = resp.json()
        total = data.get("pagination", {}).get("total", 0)
        # Also grab the first item name as proof
        items = data.get("items", [])
        sample = items[0]["fieldData"].get("name", "?") if items else "—"
        results["items"] = {"status": "✅ OK", "total_items": total, "sample": sample}
    else:
        results["items"] = {"status": f"❌ {resp.status_code}", "error": resp.text}

    # 3. Test write scope — use token introspect
    resp = requests.get(f"{WEBFLOW_API_BASE}/token/introspect",
                        headers=get_headers(token), timeout=REQUEST_TIMEOUT)
    if resp.status_code == 200:
        info = resp.json()
        results["token"] = {
            "status": "✅ OK",
            "type": info.get("authorization", {}).get("type", "?"),
        }
    else:
        # Introspect might not work for site tokens — that's fine
        results["token"] = {"status": "ℹ️ Skipped (site token)", "note": "CMS access confirmed above"}

    return results


def search_item_by_slug(token, slug, collection_id):
    url = f"{WEBFLOW_API_BASE}/collections/{collection_id}/items"
    headers = get_headers(token)

    # Fast path: server-side slug filter. Webflow v2 returns just the match.
    resp = requests.get(url, headers=headers,
                        params={"slug": slug, "limit": 1},
                        timeout=REQUEST_TIMEOUT)
    if resp.status_code == 200:
        for item in resp.json().get("items", []):
            if item.get("fieldData", {}).get("slug") == slug:
                return item, None
        # 200 but no match via filter → fall through to full scan in case the
        # endpoint silently ignored the filter param on an older API version.
    elif resp.status_code not in (400, 422):
        return None, f"API Error {resp.status_code}: {resp.text}"

    # Fallback: paginate.
    offset, limit = 0, 100
    while True:
        resp = requests.get(url, headers=headers,
                            params={"offset": offset, "limit": limit},
                            timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None, f"API Error {resp.status_code}: {resp.text}"

        data = resp.json()
        for item in data.get("items", []):
            if item.get("fieldData", {}).get("slug") == slug:
                return item, None

        total = data.get("pagination", {}).get("total", 0)
        if offset + limit >= total:
            break
        offset += limit

    return None, f"No item found with slug: '{slug}'"


def fetch_collection_schema(token, collection_id):
    """Return {'fields': [...], 'field_map': {slug: field_info}} for the collection."""
    resp = requests.get(f"{WEBFLOW_API_BASE}/collections/{collection_id}",
                        headers=get_headers(token), timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}: {resp.text}"
    data = resp.json()
    fields = data.get("fields", [])
    field_map = {f.get("slug"): f for f in fields if f.get("slug")}
    return {"fields": fields, "field_map": field_map, "raw": data}, None


def list_reference_options(token, ref_collection_id):
    """List all items in a referenced collection, paginated."""
    items = []
    offset = 0
    while True:
        resp = requests.get(
            f"{WEBFLOW_API_BASE}/collections/{ref_collection_id}/items",
            headers=get_headers(token),
            params={"offset": offset, "limit": 100},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return items, f"HTTP {resp.status_code}: {resp.text}"
        data = resp.json()
        items.extend(data.get("items", []))
        total = data.get("pagination", {}).get("total", 0)
        offset += 100
        if offset >= total:
            break
    return items, None


REF_TYPE_NAMES = {"Reference", "ItemRef", "ItemReference", "CollectionItem"}
MULTI_REF_TYPE_NAMES = {"MultiReference", "ItemRefSet", "MultiItemRef", "MultiCollectionItem"}


def resolve_field_value(token, field_info, raw_value, ref_cache):
    """
    Convert a raw markdown value into the API-expected shape for the field type.
    Returns (converted_value, warning_or_none).
    ref_cache: dict used to memoise reference-collection lookups across calls.
    """
    ftype = field_info.get("type", "")
    if raw_value is None or raw_value == "":
        return raw_value, None

    validations = field_info.get("validations", {}) or {}
    has_ref_collection = bool(validations.get("collectionId"))

    # Simple Option field: {options: [{id, name, ...}]}
    if ftype == "Option":
        options = validations.get("options", []) or []
        lower_val = str(raw_value).strip().lower()
        for opt in options:
            if str(opt.get("name", "")).strip().lower() == lower_val or \
               str(opt.get("id", "")) == str(raw_value):
                return opt.get("id"), None
            if str(opt.get("name", "")).strip().lower().replace(" ", "-") == lower_val:
                return opt.get("id"), None
        return raw_value, f"Option value '{raw_value}' not found in field options"

    # Reference to another collection.
    # Trust any field type that carries a collectionId validation, since
    # Webflow's API has used several names for this (Reference, ItemRef, etc).
    is_multi = ftype in MULTI_REF_TYPE_NAMES
    is_single = ftype in REF_TYPE_NAMES or (has_ref_collection and not is_multi)

    if is_multi or is_single:
        ref_col = validations.get("collectionId")
        if not ref_col:
            return raw_value, f"Reference-like field (type={ftype}) has no collectionId in schema"

        # Memoise items per referenced collection
        if ref_col not in ref_cache:
            items, err = list_reference_options(token, ref_col)
            if err:
                return raw_value, f"Could not load ref collection: {err}"
            ref_cache[ref_col] = items

        items = ref_cache[ref_col]

        def _norm(s):
            s = str(s).strip().lower()
            return re.sub(r'[^a-z0-9]+', '-', s).strip('-')

        def find_item(lookup):
            want = _norm(lookup)
            for i in items:
                fd = i.get("fieldData", {})
                candidates = {
                    _norm(fd.get("slug", "")),
                    _norm(fd.get("name", "")),
                    str(i.get("id", "")).strip().lower(),
                }
                if want in candidates:
                    return i
            return None

        if is_multi:
            vals = [v.strip() for v in str(raw_value).split(";") if v.strip()]
            ids, missing = [], []
            for v in vals:
                hit = find_item(v)
                if hit:
                    ids.append(hit["id"])
                else:
                    missing.append(v)
            if missing:
                return ids, f"Missing MultiReference values: {', '.join(missing)}"
            return ids, None

        hit = find_item(raw_value)
        if hit:
            return hit["id"], None
        available = ", ".join(
            str(i.get("fieldData", {}).get("slug", "?")) for i in items[:10]
        )
        return None, (
            f"Reference value '{raw_value}' not found in collection {ref_col}. "
            f"Available slugs: {available}{'…' if len(items) > 10 else ''}"
        )

    return raw_value, None


def create_new_item(token, name, slug, content_html, collection_id, extra_fields=None,
                    content_field="content"):
    """Create a new item in the collection."""
    url = f"{WEBFLOW_API_BASE}/collections/{collection_id}/items"
    headers = get_headers(token)

    field_data = {
        "name": name,
        "slug": slug,
        content_field: content_html,
    }

    # Add optional fields if provided
    if extra_fields:
        field_data.update(extra_fields)

    payload = {
        "items": [{
            "fieldData": field_data,
            "isDraft": True,
        }]
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    return resp


# ─── CMS FIELDS MD HELPERS ────────────────────────────────────────────────────

def field_name_to_slug(name):
    slug = name.lower().strip()
    slug = re.sub(r'\s*\(.*?\)', '', slug)       # drop (parenthetical)
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)     # remove special chars
    slug = re.sub(r'\s+', '-', slug.strip())      # spaces → hyphens
    return re.sub(r'-+', '-', slug).strip('-')


def rich_text_bullets_to_html(text):
    text = re.sub(r'^Bullet list[-;:\s]+', '', text, flags=re.IGNORECASE).strip()
    items = [i.strip() for i in text.split(';') if i.strip()]
    if not items:
        return f'<p>{text}</p>'
    return '<ul>' + ''.join(f'<li>{item}</li>' for item in items) + '</ul>'


FAQ_SECTION_OPEN = '<section class="faq" itemscope="" itemtype="https://schema.org/FAQPage">'
FAQ_SECTION_CLOSE = '</section>'


def _norm_field_name(s):
    """Lowercase + strip non-alphanumerics for forgiving display-name matches."""
    return re.sub(r'[^a-z0-9]+', '', str(s).lower())


def is_faq_field(display_name):
    """Forgiving match for the FAQ container field. Catches FAQ / FAQs /
    FAQ's / FAQs Section / FAQ Section etc."""
    n = _norm_field_name(display_name)
    if not n:
        return False
    # Any normalized name that starts with 'faq' AND contains nothing other
    # than 'faq' + optional 's' + optional 'section' / 'container'
    if n in {"faq", "faqs", "faqsection", "faqssection",
             "faqcontainer", "faqscontainer"}:
        return True
    return n.startswith("faq") and ("section" in n or n.endswith("s") or n == "faq")


def is_trainer_paragraph_field(display_name):
    """Trainer Paragraph stays as plain text. Catches 'Trainer Paragraph',
    'Trainers Paragraph', 'Trainer Para', 'Trainers Para', etc."""
    n = _norm_field_name(display_name)
    if not n.startswith("trainer"):
        return False
    return ("paragraph" in n) or n.endswith("para") or n.endswith("paras")


def wrap_faq_section(value):
    """Wrap FAQ markup with the schema.org FAQPage section AND the Webflow
    Rich Text embed wrapper. Webflow strips <section> from a Rich Text field
    unless it sits inside a <div data-rt-embed-type='true'> block, which is
    why a bare <section class='faq'> renders as empty in the Editor.
    Idempotent — won't re-wrap content that's already in the final shape."""
    if not value:
        return value
    stripped = value.strip()
    low = stripped.lower()

    has_embed_wrapper = 'data-rt-embed-type' in low[:120]
    has_faq_section = 'schema.org/faqpage' in low

    if has_embed_wrapper and has_faq_section:
        return stripped  # already in final shape

    if has_faq_section:
        inner = stripped
    else:
        inner = f'{FAQ_SECTION_OPEN}\n{stripped}\n{FAQ_SECTION_CLOSE}'

    return f'<div data-rt-embed-type="true">\n{inner}\n</div>'


def plain_text_only(text):
    """Strip ALL HTML and return bare text — for Webflow plain-text fields
    that would otherwise display the markup as literal characters."""
    if text is None:
        return text
    soup = BeautifulSoup(str(text), "html.parser")
    return soup.get_text(separator=' ', strip=True)


def parse_cms_fields_md(md_content):
    """Parse a CMS fields markdown table.

    Returns a list of dicts preserving the original display name, since the
    display name is what Webflow's schema exposes and what we need to match
    against. Each dict: {display_name, slug_guess, input_type, value}.
    """
    entries = []
    separator_seen = False

    for line in md_content.split('\n'):
        stripped = line.strip()
        if not stripped.startswith('|'):
            continue
        # Separator row: |---|---|...|
        if re.match(r'^\|[-|\s:=]+\|$', stripped):
            separator_seen = True
            continue
        if not separator_seen:
            continue  # skip header row

        parts = stripped.split('|')
        if len(parts) < 5:
            continue

        field_name = parts[2].strip()
        input_type = parts[3].strip()
        # Rejoin remaining columns so pipe chars inside content are preserved.
        # If the line ended with a trailing `|`, parts[-1] is "" (empty post-split
        # token) and we drop it; if it didn't, keep every column.
        content_parts = parts[4:-1] if parts[-1].strip() == "" else parts[4:]
        content = '|'.join(content_parts).strip()

        if not field_name:
            continue

        # Drop surrounding backticks if present (md code-fence) for any field.
        if content.startswith('`') and content.endswith('`'):
            content = content[1:-1]

        # Convert semicolon-bullet markdown to <ul> ONLY for true list fields
        # — never for fields that should stay plain-text. The actual decision
        # is finalized at push time using the schema's field type; here we
        # apply the conversion only for non-plain-text candidates.
        if input_type == "Rich Text" and not is_trainer_paragraph_field(field_name):
            content = rich_text_bullets_to_html(content)

        # Note: FAQ wrapping + trainer plain-text stripping happen later in the
        # push pipeline, once we know the Webflow schema field type for sure.

        entries.append({
            "display_name": field_name,
            "slug_guess": field_name_to_slug(field_name),
            "input_type": input_type,
            "value": content,
        })

    return entries


# ─── BLOCK PARSING (shared by all upload paths) ───────────────────────────────

def parse_blocks(html_content):
    """Parse already-wrapped HTML into the block dicts the UI consumes."""
    soup = BeautifulSoup(html_content, "html.parser")
    blocks_list = []
    for element in soup.children:
        if not isinstance(element, Tag):
            continue
        is_embed = element.get("data-rt-embed-type") == "true"
        blocks_list.append({
            "type": "embed" if is_embed else "plain",
            "html": str(element),
            "tag": element.name,
            "preview": element.get_text()[:100].replace("\n", " ").strip(),
            "chars": len(str(element)),
        })
    return blocks_list


def safe_json(resp):
    """Decode a Response body as JSON, falling back to a raw-text wrapper."""
    try:
        return resp.json()
    except ValueError:
        return {"_raw_body": resp.text}


# ─── CREDENTIAL PERSISTENCE ───────────────────────────────────────────────────
# Optional: stash token + collection ID in a local JSON file so the user
# doesn't have to retype them on every reload / between course pushes.
# Plaintext on disk — opt-in only.

import os
import pathlib

CREDS_PATH = pathlib.Path.home() / ".webflow_publisher.json"


def load_saved_creds():
    """Return {'api_token': str, 'collection_id': str, 'remember': bool} or empty dict."""
    try:
        if CREDS_PATH.exists():
            with open(CREDS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {
                    "api_token": str(data.get("api_token", "")),
                    "collection_id": str(data.get("collection_id", "")),
                    "remember": bool(data.get("remember", False)),
                }
    except (OSError, ValueError):
        pass
    return {}


def save_creds(api_token, collection_id, remember):
    """Persist (or clear) creds. remember=False writes an empty file shell."""
    try:
        if not remember:
            if CREDS_PATH.exists():
                CREDS_PATH.unlink()
            return True, None
        payload = {
            "api_token": api_token or "",
            "collection_id": collection_id or "",
            "remember": True,
        }
        with open(CREDS_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        # Best-effort chmod 600 on POSIX; Windows ignores.
        try:
            os.chmod(CREDS_PATH, 0o600)
        except OSError:
            pass
        return True, None
    except OSError as e:
        return False, str(e)


def reset_push_state():
    """Clear per-item session state so the user can push the next course
    without reloading the page or re-entering credentials."""
    keep_prefixes = ("collection_id", "saved_", "remember_creds")
    for k in list(st.session_state.keys()):
        if not any(k == kp or k.startswith(kp) for kp in keep_prefixes):
            del st.session_state[k]


# ─── CMS-FIELDS PIPELINE (shared by single + bulk push) ───────────────────────

# Fields that DEFAULT to "skip" (unticked) in the single-file push selector,
# and that bulk-Update mode also skips by default to avoid clobbering identity.
DEFAULT_SKIP_FIELDS = {
    "name", "slug",
    "canonical", "canonicallinks", "canonicalurl",
    "coursename",
    "deliverytype",
    "duration",
    "whichcourselevel",
    "whichcoursetype",
    "whichcoursecategory",
    "whichcoursesubcategory",
}


def _norm_name(s):
    """Lowercase + drop non-alphanumerics — used for forgiving display-name matches."""
    return re.sub(r'[^a-z0-9]+', '', str(s).strip().lower())


def build_display_to_slug(field_map):
    """Return {normalized-display-name: slug}, indexed by display name and slug."""
    dts = {}
    for fslug, finfo in field_map.items():
        dn = finfo.get("displayName") or finfo.get("name") or ""
        if dn:
            dts[_norm_name(dn)] = fslug
        dts.setdefault(_norm_name(fslug), fslug)
    return dts


def match_entry_to_slug(entry, field_map, display_to_slug):
    """Resolve a parsed .md entry to a real schema slug. Returns slug or None."""
    norm = _norm_name(entry["display_name"])
    real = display_to_slug.get(norm)
    if not real and entry["slug_guess"] in field_map:
        real = entry["slug_guess"]
    if not real:
        best = None
        for known_norm, known_slug in display_to_slug.items():
            if known_norm and (known_norm in norm or norm in known_norm):
                if best is None or len(known_norm) > len(best[0]):
                    best = (known_norm, known_slug)
        if best:
            real = best[1]
    return real


def apply_field_type_transforms(value, field_info, display_name):
    """Schema-type-aware post-processing applied right before push.
    PlainText/Email/Link/Phone → strip HTML; RichText + FAQ name → wrap;
    RichText + Trainer Paragraph name → strip HTML."""
    if not isinstance(value, str):
        return value
    f_type = field_info.get("type", "")
    schema_dn = field_info.get("displayName") or field_info.get("name") or ""

    if f_type in {"PlainText", "Email", "Link", "Phone"}:
        return plain_text_only(value)
    if is_trainer_paragraph_field(display_name) or is_trainer_paragraph_field(schema_dn):
        return plain_text_only(value)
    if f_type == "RichText" and (is_faq_field(display_name) or is_faq_field(schema_dn)):
        return wrap_faq_section(value)
    return value


def resolve_md_to_field_data(api_token, parsed_entries, field_map, ref_cache):
    """Run a parsed .md (list of entries) through schema matching, value
    resolution, and field-type-aware post-processing. Returns a dict:
    {real_slug: resolved_value} plus a list of (display_name, real_slug,
    default_skip) match records and a list of resolution warnings."""
    display_to_slug = build_display_to_slug(field_map)
    resolved_data, matches, warnings_list = {}, [], []

    for entry in parsed_entries:
        display = entry["display_name"]
        real_slug = match_entry_to_slug(entry, field_map, display_to_slug)

        schema_norm = ""
        if real_slug:
            schema_dn = field_map[real_slug].get("displayName") or field_map[real_slug].get("name") or ""
            schema_norm = _norm_name(schema_dn)
        default_skip = (
            _norm_name(display) in DEFAULT_SKIP_FIELDS
            or schema_norm in DEFAULT_SKIP_FIELDS
            or _norm_name(real_slug or "") in DEFAULT_SKIP_FIELDS
        )

        matches.append((entry, real_slug, default_skip))
        if not real_slug:
            continue

        resolved, warn = resolve_field_value(
            api_token, field_map[real_slug], entry["value"], ref_cache
        )
        if warn:
            warnings_list.append(f"**{display}** (`{real_slug}`): {warn}")
        if resolved is None:
            continue

        resolved = apply_field_type_transforms(resolved, field_map[real_slug], display)
        resolved_data[real_slug] = resolved

    return resolved_data, matches, warnings_list


def push_items_bulk(api_token, collection_id, create_items, update_items, live=False):
    """Send create + update payloads to Webflow. Returns a list of per-item
    result dicts: {label, action, status, slug, item_id, error}. Webflow
    accepts up to 100 items per call — we chunk just in case the caller
    passes more."""
    suffix = "/live" if live else ""
    headers = get_headers(api_token)
    results = []

    def _chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i+n]

    # ── Updates: PATCH /items[ /live ] ──────────────────────────────────
    for chunk in _chunks(update_items, 100):
        url = f"{WEBFLOW_API_BASE}/collections/{collection_id}/items{suffix}"
        payload = {"items": [{"id": it["item_id"], "fieldData": it["field_data"]}
                              for it in chunk]}
        try:
            resp = requests.patch(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
            ok = resp.status_code == 200
            body = safe_json(resp)
        except requests.RequestException as e:
            ok, body = False, {"error": str(e)}
        for it in chunk:
            results.append({
                "label": it["label"], "action": "update",
                "slug": it["slug"], "item_id": it["item_id"],
                "status": "✅" if ok else "❌",
                "error": "" if ok else json.dumps(body)[:400],
            })

    # ── Creates: POST /items (always Draft) ─────────────────────────────
    for chunk in _chunks(create_items, 100):
        url = f"{WEBFLOW_API_BASE}/collections/{collection_id}/items"
        payload = {"items": [{"fieldData": it["field_data"], "isDraft": True}
                              for it in chunk]}
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
            ok = resp.status_code in (200, 201, 202)
            body = safe_json(resp)
        except requests.RequestException as e:
            ok, body = False, {"error": str(e)}
        created_items = body.get("items", []) if isinstance(body, dict) and ok else []
        for idx, it in enumerate(chunk):
            new_id = created_items[idx].get("id") if idx < len(created_items) else ""
            results.append({
                "label": it["label"], "action": "create",
                "slug": it["slug"], "item_id": new_id,
                "status": "✅" if ok else "❌",
                "error": "" if ok else json.dumps(body)[:400],
            })

    return results


# ─── STREAMLIT UI ─────────────────────────────────────────────────────────────

st.set_page_config(page_title="Edstellar → Webflow CMS Publisher", page_icon="🚀", layout="wide")

st.title("🚀 Edstellar → Webflow CMS Publisher")
st.caption("Upload HTML or CMS fields → Preview → Push to any Webflow collection")

# Load saved creds once per session so reload / next-course doesn't lose them
if "saved_creds_loaded" not in st.session_state:
    saved = load_saved_creds()
    st.session_state["saved_api_token"] = saved.get("api_token", "")
    st.session_state["saved_collection_id"] = saved.get("collection_id", "")
    st.session_state["remember_creds"] = saved.get("remember", False)
    if saved.get("collection_id"):
        st.session_state["collection_id"] = saved["collection_id"]
    st.session_state["saved_creds_loaded"] = True

# Sidebar
with st.sidebar:
    st.header("⚙️ Settings")
    api_token = st.text_input(
        "Webflow API Token",
        type="password",
        value=st.session_state.get("saved_api_token", ""),
        help="Site API token with CMS edit+read scope",
        key="api_token_input",
    )

    collection_id = st.text_input(
        "Collection ID",
        value=st.session_state.get("collection_id", ""),
        placeholder="e.g. 64ac3a242208dda62b6e6a90",
        help="Webflow CMS Collection ID — find it in Webflow Dashboard → CMS → [Collection] → Settings",
        key="collection_id_input",
    )
    st.session_state["collection_id"] = collection_id

    remember_creds = st.checkbox(
        "💾 Remember on this machine",
        value=st.session_state.get("remember_creds", False),
        help=("Stores token + collection ID in plaintext at "
              f"`{CREDS_PATH}` so reloads and next-course pushes don't "
              "ask you to re-enter them. Untick to clear."),
        key="remember_creds",
    )
    # Persist (or clear) whenever the checkbox or values change
    prev = (st.session_state.get("saved_api_token", ""),
            st.session_state.get("saved_collection_id", ""),
            st.session_state.get("_prev_remember", None))
    curr = (api_token, collection_id, remember_creds)
    if curr != prev:
        ok, err = save_creds(api_token, collection_id, remember_creds)
        if ok:
            st.session_state["saved_api_token"] = api_token if remember_creds else ""
            st.session_state["saved_collection_id"] = collection_id if remember_creds else ""
            st.session_state["_prev_remember"] = remember_creds
        elif err:
            st.warning(f"Couldn't save creds: {err}")

    push_live = st.checkbox("Push to Live (not just Draft)", value=False,
                             help="If checked, updates go live immediately")

    if st.button("🔄 Push another item (reset)", use_container_width=True,
                  help="Clears the current item / uploads but keeps your token and collection ID."):
        reset_push_state()
        st.rerun()

    # API Test button
    if api_token and collection_id:
        if st.button("🧪 Test API Connection", use_container_width=True):
            with st.spinner("Testing..."):
                results = test_api_connection(api_token, collection_id)

            # Collection
            col = results.get("collection", {})
            if col:
                if "name" in col:
                    st.success(f"**Collection:** {col['status']} — {col['name']} ({col['fields']} fields)")
                else:
                    st.error(f"**Collection:** {col['status']}")
                    st.code(col.get("error", ""), language="json")

            # Items
            items = results.get("items", {})
            if items:
                if "total_items" in items:
                    st.success(f"**Items:** {items['status']} — {items['total_items']} items")
                    st.caption(f"Sample: {items.get('sample', '—')}")
                else:
                    st.error(f"**Items:** {items['status']}")

            # Token info
            tok = results.get("token", {})
            if tok:
                st.info(f"**Token:** {tok['status']}")
    else:
        st.caption("Enter API token and Collection ID above, then test connection")

    st.divider()
    st.markdown("**Active Collection:**")
    st.code(collection_id if collection_id else "not set", language=None)

    st.divider()
    st.markdown("""
    **Workflow:**
    1. Enter item slug
    2. Upload HTML file
    3. Auto-processes into blocks
    4. Preview & push

    **Content types:**
    - 🟢 Plain rich text → as-is
    - 🟡 Embed → wrapped with `data-rt-embed-type`

    **Bulk mode:** pick *Bulk Push CMS Fields (.md, up to 5)*
    to upload 1–5 .md files and push them in one batch.
    """)

# Slug input
# Mode selector
mode = st.radio(
    "📋 Mode",
    ["Update Existing Item", "Create New Item", "Push CMS Fields (.md)",
     "Bulk Push CMS Fields (.md, up to 5)"],
    horizontal=True,
)

# Initialize variables for both modes
slug = ""
new_name = ""
new_slug = ""
new_meta_title = ""
new_meta_desc = ""
new_description = ""
new_canonical = ""
new_primary_keyword = ""
new_keyword_volume = 0
new_format_blog = True
new_faqs_section = True
include_flag_fields = False

if mode == "Update Existing Item":
    slug = st.text_input("🔗 Item Slug",
                          placeholder="corporate-training-companies-malaysia",
                          help="Slug of the existing item to update")

    if slug and api_token:
        if st.button("🔍 Find Item"):
            with st.spinner("Searching..."):
                item, error = search_item_by_slug(api_token, slug, collection_id)
            if error:
                st.error(error)
            else:
                st.session_state["found_item"] = item
                fd = item.get("fieldData", {})
                st.success(f"✅ **{fd.get('name')}** — ID: `{item['id']}`")
    elif slug and not api_token:
        st.info("Enter your API token in the sidebar to search.")

    # Show editable meta fields if item found
    found_item = st.session_state.get("found_item")
    if found_item:
        fd = found_item.get("fieldData", {})
        with st.expander("✏️ Edit Fields (pre-filled from existing item)", expanded=True):
            edit_name = st.text_input("Name (H1)", value=fd.get("name", ""), key="edit_name")
            edit_slug = st.text_input("Slug", value=fd.get("slug", ""), key="edit_slug")
            edit_meta_title = st.text_input("Meta Title", value=fd.get("meta-title", ""), key="edit_meta_title")
            edit_meta_desc = st.text_area("Meta Description", value=fd.get("meta-description", ""), key="edit_meta_desc", max_chars=300)
            edit_canonical = st.text_input("Canonical Links", value=fd.get("canonical-links", ""), key="edit_canonical")

elif mode == "Create New Item":
    # Create new mode
    new_name = st.text_input("📝 Item Name*",
                              placeholder="11 Best Corporate Training Companies in Malaysia for 2026")
    # Auto-generate slug from name if the slug field is empty
    auto_slug_default = ""
    if new_name:
        auto_slug_default = re.sub(r'[^a-z0-9]+', '-', new_name.lower()).strip('-')
    new_slug = st.text_input("🔗 Slug*",
                              value=auto_slug_default,
                              placeholder="corporate-training-companies-malaysia",
                              help="URL slug — lowercase, hyphens, no spaces. Auto-filled from name; edit if you want a custom slug.")

    with st.expander("Optional Fields"):
        new_meta_title = st.text_input("Meta Title", placeholder="Same as title if blank")
        new_meta_desc = st.text_area("Meta Description", placeholder="Short description for SEO", max_chars=300)
        new_description = st.text_area("Description (excerpt)", placeholder="Short excerpt for listings", max_chars=500)
        new_canonical = st.text_input("Canonical URL", placeholder="https://www.edstellar.com/your-slug")
        new_primary_keyword = st.text_input("Primary Keyword", placeholder="corporate training companies malaysia")
        new_keyword_volume = st.number_input("Keyword Search Volume", min_value=0, value=0,
                                              help="Pushed only when > 0")
        include_flag_fields = st.checkbox(
            "Include 'New Format Blog' / 'FAQS Section' flags in payload",
            value=False,
            help="Leave off to skip these booleans entirely (collection defaults apply).",
        )
        new_format_blog = st.checkbox("New Format Blog", value=True)
        new_faqs_section = st.checkbox("FAQS Section", value=True)

    slug = new_slug  # for file naming

# ── Push CMS Fields (.md) mode ────────────────────────────────────────────────
if mode == "Push CMS Fields (.md)":
    st.divider()
    st.subheader("📋 Push CMS Fields from Markdown")

    if not api_token or not collection_id:
        st.warning("Enter your Webflow API token and Collection ID in the sidebar.")
        st.stop()

    md_file = st.file_uploader(
        "Upload CMS Fields .md file", type=["md"],
        help="Markdown table with columns: Section | Field Name | Input Type | Ref - Course Content",
    )

    if md_file:
        md_content = md_file.read().decode("utf-8")
        parsed = parse_cms_fields_md(md_content)

        if not parsed:
            st.error("No fields found. Check that the file contains the expected table format.")
            st.stop()

        st.success(f"Parsed **{len(parsed)} fields** from `{md_file.name}`")

        # ── Load the real collection schema (cached per collection) ─────────
        schema_key = f"schema_{collection_id}"
        if schema_key not in st.session_state:
            with st.spinner("Loading collection schema…"):
                schema, schema_err = fetch_collection_schema(api_token, collection_id)
            if schema_err:
                st.error(f"Failed to load schema: {schema_err}")
                st.stop()
            st.session_state[schema_key] = schema
        schema = st.session_state[schema_key]

        field_map = schema["field_map"]  # {slug: field_info}

        # Build display-name → slug lookup (case / punctuation insensitive)
        def _norm_name(s):
            s = str(s).strip().lower()
            return re.sub(r'[^a-z0-9]+', '', s)  # drop all non-alphanumeric

        display_to_slug = {}
        for fslug, finfo in field_map.items():
            dn = finfo.get("displayName") or finfo.get("name") or ""
            if dn:
                display_to_slug[_norm_name(dn)] = fslug
            # Also index by the slug itself as a fallback
            display_to_slug.setdefault(_norm_name(fslug), fslug)

        # Diagnostic: show schema field types
        with st.expander("🔎 Schema Inspector (field types)"):
            for fslug, finfo in field_map.items():
                ftype = finfo.get("type", "?")
                dn = finfo.get("displayName", "")
                validations = finfo.get("validations", {}) or {}
                ref_col = validations.get("collectionId")
                extra = f" → ref collection `{ref_col}`" if ref_col else ""
                st.caption(f"**{dn}** — slug: `{fslug}` — type: `{ftype}`{extra}")
            st.download_button(
                "📥 Download schema as JSON",
                data=json.dumps(schema["fields"], indent=2),
                file_name="webflow_collection_schema.json",
                mime="application/json",
            )

        # Diagnostic: referenced collections + available values (cached)
        ref_collection_ids = {
            (finfo.get("validations") or {}).get("collectionId")
            for finfo in field_map.values()
            if (finfo.get("validations") or {}).get("collectionId")
        }
        if ref_collection_ids:
            ref_diag_key = f"ref_diag_{collection_id}"
            with st.expander(f"🔗 Referenced Collections ({len(ref_collection_ids)}) — values you can use"):
                if ref_diag_key not in st.session_state:
                    ref_diag = {}
                    for rc in ref_collection_ids:
                        with st.spinner(f"Loading `{rc}`…"):
                            items, err = list_reference_options(api_token, rc)
                        ref_diag[rc] = (items, err)
                    st.session_state[ref_diag_key] = ref_diag
                else:
                    ref_diag = st.session_state[ref_diag_key]

                for rc, (items, err) in ref_diag.items():
                    if err:
                        st.error(f"`{rc}` — could not load: {err}")
                        continue
                    st.markdown(f"**`{rc}`** — {len(items)} items")
                    sample = []
                    for i in items[:25]:
                        fd = i.get("fieldData", {})
                        sample.append(f"`{fd.get('slug', '?')}` — {fd.get('name', '?')}")
                    st.caption(" · ".join(sample) + ("…" if len(items) > 25 else ""))

        # Fields that DEFAULT to "skip" (unticked) in the push selector below.
        # The user can always override per field. Matched against the normalized
        # .md label, the schema display name, and the field slug, so any of
        # those labels flips the default to skip.
        DEFAULT_SKIP_FIELDS = {
            "name",
            "slug",
            "canonical", "canonicallinks", "canonicalurl",
            "coursename",
            "deliverytype",
            "duration",
            "whichcourselevel",
            "whichcoursetype",
            "whichcoursecategory",
            "whichcoursesubcategory",
        }

        # ── Match each parsed entry to a real schema slug ────────────────────
        # Re-use cached ref lookups so API calls aren't repeated on every rerender
        ref_cache = st.session_state.get(f"ref_cache_{collection_id}", {})
        resolved_data = {}    # {slug: resolved_value} for every matched+resolved field
        matches = []          # list of (entry, resolved_slug or None, default_skip)
        warnings_list = []

        for entry in parsed:
            display = entry["display_name"]
            norm = _norm_name(display)
            real_slug = display_to_slug.get(norm)

            # Fallback 1: derived slug exact match
            if not real_slug and entry["slug_guess"] in field_map:
                real_slug = entry["slug_guess"]

            # Fallback 2: substring match against display names (longest-wins)
            if not real_slug:
                best = None
                for known_norm, known_slug in display_to_slug.items():
                    if known_norm and (known_norm in norm or norm in known_norm):
                        if best is None or len(known_norm) > len(best[0]):
                            best = (known_norm, known_slug)
                if best:
                    real_slug = best[1]

            # Decide the default push/skip for this field.
            schema_norm = ""
            if real_slug:
                schema_dn = field_map[real_slug].get("displayName") or field_map[real_slug].get("name") or ""
                schema_norm = _norm_name(schema_dn)
            default_skip = (
                norm in DEFAULT_SKIP_FIELDS
                or schema_norm in DEFAULT_SKIP_FIELDS
                or _norm_name(real_slug or "") in DEFAULT_SKIP_FIELDS
            )

            matches.append((entry, real_slug, default_skip))

            if not real_slug:
                continue

            resolved, warn = resolve_field_value(
                api_token, field_map[real_slug], entry["value"], ref_cache
            )
            if warn:
                warnings_list.append(f"**{display}** (`{real_slug}`): {warn}")
            if resolved is None:
                continue

            # ── Schema-type-aware post-processing ────────────────────────
            # This is the source of truth — name-matching alone misses
            # plural/apostrophe variants and gets fooled by similar fields.
            f_info = field_map[real_slug]
            f_type = f_info.get("type", "")
            schema_dn = f_info.get("displayName") or f_info.get("name") or ""

            # Plain-text fields (PlainText, Email, Link, Phone): Webflow
            # renders the value as literal characters, so strip ALL markup.
            # This catches Trainer Paragraph regardless of how it was named.
            if f_type in {"PlainText", "Email", "Link", "Phone"} and isinstance(resolved, str):
                resolved = plain_text_only(resolved)
            # Belt-and-suspenders: trainer paragraph by name, in case the
            # schema mis-types the field as RichText but it's actually plain.
            elif is_trainer_paragraph_field(display) or is_trainer_paragraph_field(schema_dn):
                if isinstance(resolved, str):
                    resolved = plain_text_only(resolved)

            # FAQ container — wrap in <div data-rt-embed-type="true"><section
            # class="faq" itemscope itemtype="schema.org/FAQPage">…</section>
            # </div>. Webflow strips bare <section> tags from Rich Text, so
            # the embed wrapper is required for the FAQ to render.
            if (f_type == "RichText"
                    and (is_faq_field(display) or is_faq_field(schema_dn))
                    and isinstance(resolved, str)):
                resolved = wrap_faq_section(resolved)

            resolved_data[real_slug] = resolved

        # Persist ref lookups so subsequent rerenders skip the API calls
        st.session_state[f"ref_cache_{collection_id}"] = ref_cache

        # ── Create-mode preset ───────────────────────────────────────────────
        # A NEW item needs Name + Slug + the core fields, so in "Create New Item"
        # mode default ALL matched fields ON. In "Update Existing Item" mode the
        # identity/core fields stay OFF (don't overwrite structural fields). The
        # md_action radio renders below, so read its persisted value here; reset the
        # per-field selections whenever the action changes so the right defaults apply.
        md_action_current = st.session_state.get("md_action", "Update Existing Item")
        is_create_mode = md_action_current == "Create New Item"
        if st.session_state.get(f"_md_last_action_{collection_id}") != md_action_current:
            for _k in [k for k in st.session_state if k.startswith(f"pushfld_{collection_id}_")]:
                del st.session_state[_k]
            st.session_state[f"_md_last_action_{collection_id}"] = md_action_current
        if is_create_mode:
            st.caption("🆕 **Create mode:** identity + core fields (Name, Slug, …) are included by "
                       "default — a new item requires them. New items are pushed as **Draft**.")

        # ── Bulk select / deselect (set state before the checkboxes render) ──
        sel_c1, sel_c2 = st.columns(2)
        with sel_c1:
            if st.button("✅ Select all", key="md_select_all", use_container_width=True):
                for _, rslug, _ in matches:
                    if rslug in resolved_data:
                        st.session_state[f"pushfld_{collection_id}_{rslug}"] = True
        with sel_c2:
            if st.button("⬜ Deselect all", key="md_deselect_all", use_container_width=True):
                for _, rslug, _ in matches:
                    if rslug in resolved_data:
                        st.session_state[f"pushfld_{collection_id}_{rslug}"] = False

        # ── Preview + per-field push/skip selector ───────────────────────────
        skipped_count = sum(1 for _, s, _ in matches if not s)
        push_slugs = set()
        with st.expander(f"📋 Field Mapping — tick the fields to push "
                         f"({len(matches) - skipped_count} matched / {skipped_count} unmatched)",
                         expanded=True):
            for entry, real_slug, default_skip in matches:
                col_chk, col_a, col_b = st.columns([0.5, 1, 3])
                with col_chk:
                    if real_slug and real_slug in resolved_data:
                        chk_key = f"pushfld_{collection_id}_{real_slug}"
                        if chk_key not in st.session_state:
                            # Create mode: everything ON (new item needs identity+core).
                            # Update mode: identity/core fields OFF by default.
                            st.session_state[chk_key] = is_create_mode or (not default_skip)
                        if st.checkbox("push", key=chk_key, label_visibility="collapsed"):
                            push_slugs.add(real_slug)
                    else:
                        st.caption("—")
                with col_a:
                    if real_slug:
                        schema_dn = field_map[real_slug].get("displayName", real_slug)
                        label = f"**{entry['display_name']}**"
                        if default_skip:
                            label += " · _skip by default_"
                        st.markdown(label)
                        st.caption(f"→ `{real_slug}` · {entry['input_type']}")
                        if _norm_name(schema_dn) != _norm_name(entry['display_name']):
                            st.caption(f"matched via: *{schema_dn}*")
                    else:
                        st.markdown(f"~~**{entry['display_name']}**~~")
                        st.caption(f"⚠️ no schema match · {entry['input_type']}")
                with col_b:
                    preview = entry["value"][:160].replace("\n", " ")
                    st.caption(preview + ("…" if len(entry["value"]) > 160 else ""))

        # Final payload — only the fields the user ticked
        field_data = {k: v for k, v in resolved_data.items() if k in push_slugs}

        unmatched = [e["display_name"] for e, s, _ in matches if not s]
        if unmatched:
            st.warning(f"**Skipped {len(unmatched)} fields** (no schema match): "
                       + ", ".join(f"_{u}_" for u in unmatched))
        deselected = [field_map[s].get("displayName", s)
                      for _, s, _ in matches if s in resolved_data and s not in push_slugs]
        if deselected:
            st.info(f"**{len(deselected)} fields unticked** (won't be pushed): "
                    + ", ".join(f"_{u}_" for u in deselected))
        st.caption(f"**{len(field_data)} field(s) will be pushed.**")
        if warnings_list:
            with st.expander(f"⚠️ {len(warnings_list)} resolution warnings"):
                for w in warnings_list:
                    st.caption(w)

        st.divider()
        # "Update Existing Item" is first = default (the common optimization case, keeps
        # identity fields OFF). Pick "Create New Item" for brand-new courses (identity ON).
        md_action = st.radio("Action", ["Update Existing Item", "Create New Item"],
                              horizontal=True, key="md_action")

        if md_action == "Update Existing Item":
            md_slug = st.text_input(
                "Slug of item to update",
                placeholder="burnout-prevention-and-recovery-training",
                key="md_update_slug",
            )

            if md_slug and api_token and collection_id:
                if st.button("🔍 Find Item", key="md_find"):
                    with st.spinner("Searching..."):
                        item, error = search_item_by_slug(api_token, md_slug, collection_id)
                    if error:
                        st.error(error)
                        st.session_state.pop("md_found_item", None)
                    else:
                        st.session_state["md_found_item"] = item
                        st.success(f"✅ **{item['fieldData'].get('name')}** — ID: `{item['id']}`")

            found_md = st.session_state.get("md_found_item")
            if found_md:
                item_id = found_md["id"]
                item_name = found_md["fieldData"].get("name", item_id)
                target = "**LIVE**" if push_live else "**Draft (staged)**"
                st.info(f"Update **{item_name}** → {target} | {len(field_data)} fields")

                confirm = st.checkbox(f"I confirm: update '{item_name}'", key="md_confirm_update")
                if confirm:
                    if st.button("🚀 Push All Fields", type="primary",
                                  use_container_width=True, key="md_push_update"):
                        with st.spinner("Pushing to Webflow..."):
                            if push_live:
                                url = f"{WEBFLOW_API_BASE}/collections/{collection_id}/items/live"
                            else:
                                url = f"{WEBFLOW_API_BASE}/collections/{collection_id}/items"
                            payload = {"items": [{"id": item_id, "fieldData": field_data}]}
                            resp = requests.patch(url, headers=get_headers(api_token), json=payload, timeout=REQUEST_TIMEOUT)

                        if resp.status_code == 200:
                            st.success("✅ Updated successfully!")
                            st.balloons()
                            with st.expander("API Response"):
                                st.json(safe_json(resp))
                        else:
                            st.error(f"❌ Failed — HTTP {resp.status_code}")
                            st.code(resp.text, language="json")

        else:  # Create New Item (upsert: update if slug exists, create otherwise)
            name_val = field_data.get("name", "")
            slug_val = field_data.get("slug", "")
            if name_val:
                st.info(f"**Create / Update:** {name_val}\n\nSlug: `{slug_val}` | {len(field_data)} fields | Status: Draft")
            else:
                st.warning("No 'name' field found in the markdown — required by Webflow.")

            confirm = st.checkbox(
                f"I confirm: push '{name_val or '(unnamed)'}'",
                key="md_confirm_create",
            )
            if confirm:
                if st.button("🚀 Push Item", type="primary",
                              use_container_width=True, key="md_push_create"):
                    # ── Step 1: check if slug already exists ──────────────────
                    with st.spinner("Checking if item already exists…"):
                        existing, _ = search_item_by_slug(api_token, slug_val, collection_id)

                    if existing:
                        # ── Update path ───────────────────────────────────────
                        item_id = existing["id"]
                        existing_name = existing["fieldData"].get("name", item_id)
                        st.info(f"Slug exists — updating **{existing_name}** (`{item_id}`)…")
                        with st.spinner("Updating…"):
                            patch_url = f"{WEBFLOW_API_BASE}/collections/{collection_id}/items"
                            patch_payload = {"items": [{"id": item_id, "fieldData": field_data}]}
                            resp = requests.patch(patch_url, headers=get_headers(api_token), json=patch_payload, timeout=REQUEST_TIMEOUT)
                        if resp.status_code == 200:
                            st.success(f"✅ Updated '{existing_name}' successfully!")
                            st.balloons()
                            with st.expander("API Response"):
                                st.json(safe_json(resp))
                        else:
                            st.error(f"❌ Update failed — HTTP {resp.status_code}")
                            st.code(resp.text, language="json")
                    else:
                        # ── Create path ───────────────────────────────────────
                        with st.spinner("Creating new item…"):
                            url = f"{WEBFLOW_API_BASE}/collections/{collection_id}/items"
                            payload = {"items": [{"fieldData": field_data, "isDraft": True}]}
                            resp = requests.post(url, headers=get_headers(api_token), json=payload, timeout=REQUEST_TIMEOUT)
                        if resp.status_code in (200, 201, 202):
                            st.success("✅ Item created as Draft!")
                            st.balloons()
                            with st.expander("API Response"):
                                st.json(safe_json(resp))
                        else:
                            st.error(f"❌ Failed — HTTP {resp.status_code}")
                            st.code(resp.text, language="json")

    st.stop()

# ── Bulk Push CMS Fields (.md) mode ───────────────────────────────────────────
if mode == "Bulk Push CMS Fields (.md, up to 5)":
    st.divider()
    st.subheader("📋 Bulk Push CMS Fields — up to 5 files at once")

    if not api_token or not collection_id:
        st.warning("Enter your Webflow API token and Collection ID in the sidebar.")
        st.stop()

    bulk_files = st.file_uploader(
        "Upload 1–5 CMS Fields .md files",
        type=["md"], accept_multiple_files=True,
        help="Each file is parsed against the same collection schema, then pushed as one item.",
    )

    if not bulk_files:
        st.info("Upload between 1 and 5 .md files. Larger batches are intentionally blocked — split into runs.")
        st.stop()

    if len(bulk_files) > 5:
        st.error(f"Got {len(bulk_files)} files. This mode is capped at **5 per run** to keep the diff "
                  "auditable and rate-limit risk low. Remove some and re-upload.")
        st.stop()

    # ── Schema + reference cache (shared across all files) ────────────────
    schema_key = f"schema_{collection_id}"
    if schema_key not in st.session_state:
        with st.spinner("Loading collection schema…"):
            schema, schema_err = fetch_collection_schema(api_token, collection_id)
        if schema_err:
            st.error(f"Failed to load schema: {schema_err}")
            st.stop()
        st.session_state[schema_key] = schema
    schema = st.session_state[schema_key]
    field_map = schema["field_map"]

    ref_cache = st.session_state.get(f"ref_cache_{collection_id}", {})

    bulk_action = st.radio(
        "Action for all files",
        ["Upsert (update if slug exists, else create as Draft)",
         "Update only (skip files whose slug isn't in the collection)",
         "Create only (always Draft; refuse files whose slug already exists)"],
        index=0, key="bulk_action",
    )
    include_identity = st.checkbox(
        "Include identity / structural fields (Name, Slug, Course Level/Type/…)",
        value=bulk_action.startswith("Create"),
        help="Create needs them; Update should usually skip them to avoid clobbering.",
        key="bulk_include_identity",
    )

    # ── Parse + resolve every file up front, build push-payload preview ──
    plans = []  # list of dicts {filename, slug, name, field_data, matches, warnings, lookup_err, existing}
    progress = st.progress(0.0, text="Parsing files…")
    for i, f in enumerate(bulk_files):
        md_content = f.read().decode("utf-8")
        parsed = parse_cms_fields_md(md_content)

        if not parsed:
            plans.append({"filename": f.name, "error": "No fields parsed — check table format."})
            progress.progress((i + 1) / len(bulk_files), text=f"Parsed {f.name}")
            continue

        full_resolved, matches, warnings_list = resolve_md_to_field_data(
            api_token, parsed, field_map, ref_cache
        )

        # Apply identity/structural skip rule unless the user opted in.
        # Always retain name + slug — Create needs them; Update tolerates them
        # (they'll match the existing record).
        if include_identity:
            resolved = dict(full_resolved)
        else:
            resolved = {}
            for k, v in full_resolved.items():
                schema_norm = _norm_name(field_map[k].get("displayName") or k)
                is_identity = (schema_norm in DEFAULT_SKIP_FIELDS
                               or _norm_name(k) in DEFAULT_SKIP_FIELDS)
                if (not is_identity) or k in ("name", "slug"):
                    resolved[k] = v

        slug_val = resolved.get("slug", "")
        name_val = resolved.get("name", "")

        # Check if slug exists already (one round-trip per file; acceptable for ≤5).
        existing, lookup_err = (None, None)
        if slug_val:
            existing, lookup_err = search_item_by_slug(api_token, slug_val, collection_id)

        plans.append({
            "filename": f.name,
            "slug": slug_val,
            "name": name_val,
            "field_data": resolved,
            "matches": matches,
            "warnings": warnings_list,
            "existing": existing,
            "lookup_err": lookup_err if not existing else None,
        })
        progress.progress((i + 1) / len(bulk_files), text=f"Parsed {f.name}")
    progress.empty()

    # Persist ref cache for subsequent reruns
    st.session_state[f"ref_cache_{collection_id}"] = ref_cache

    # ── Plan + selection table ───────────────────────────────────────────
    push_create, push_update, skipped = [], [], []
    st.markdown("### 🧾 Push plan")
    for idx, p in enumerate(plans):
        if "error" in p:
            with st.expander(f"❌ **{p['filename']}** — parse error", expanded=False):
                st.error(p["error"])
            skipped.append({"label": p["filename"], "reason": p["error"]})
            continue

        existing = p["existing"]
        if bulk_action.startswith("Update") and not existing:
            decision = "skip"
            reason = "slug not found — Update-only mode"
        elif bulk_action.startswith("Create") and existing:
            decision = "skip"
            reason = f"slug already exists (id `{existing['id']}`) — Create-only mode"
        elif existing:
            decision = "update"
            reason = f"slug exists (id `{existing['id']}`) — will PATCH"
        else:
            decision = "create"
            reason = "slug not found — will POST as Draft"

        tag = {"create": "🆕 CREATE", "update": "♻️ UPDATE", "skip": "⏭️ SKIP"}[decision]
        unmatched = [e["display_name"] for e, s, _ in p["matches"] if not s]

        header = (f"{tag}  ·  **{p['filename']}**  ·  "
                  f"`{p['slug'] or '—'}`  ·  "
                  f"{len(p['field_data'])} fields"
                  + (f"  ·  ⚠️ {len(unmatched)} unmatched" if unmatched else "")
                  + (f"  ·  ⚠️ {len(p['warnings'])} warnings" if p["warnings"] else ""))

        with st.expander(header, expanded=False):
            meta_a, meta_b = st.columns([2, 3])
            with meta_a:
                st.markdown(f"**Slug:** `{p['slug'] or '—'}`")
                st.markdown(f"**Name:** {p['name'] or '—'}")
                st.markdown(f"**Decision:** {tag}")
                st.caption(reason)
                if p.get("lookup_err"):
                    st.caption(f"slug lookup: {p['lookup_err']}")
            with meta_b:
                st.markdown(f"**Fields ready to push ({len(p['field_data'])})**")
                # Compact list of {schema display name → value preview}
                for fslug, fval in p["field_data"].items():
                    schema_dn = field_map[fslug].get("displayName") or fslug
                    preview = str(fval)
                    if len(preview) > 120:
                        preview = preview[:120] + "…"
                    preview = preview.replace("\n", " ")
                    st.caption(f"• **{schema_dn}** (`{fslug}`) — {preview}")

            if unmatched:
                st.markdown(f"**⚠️ Unmatched .md fields ({len(unmatched)}) — no schema match**")
                for u in unmatched:
                    st.caption(f"· _{u}_")

            if p["warnings"]:
                st.markdown(f"**⚠️ Resolution warnings ({len(p['warnings'])})**")
                for w in p["warnings"]:
                    st.caption(w)

        item = {
            "label": p["filename"],
            "slug": p["slug"],
            "name": p["name"],
            "field_data": p["field_data"],
            "item_id": existing["id"] if existing else "",
        }
        if decision == "create":
            push_create.append(item)
        elif decision == "update":
            push_update.append(item)
        else:
            skipped.append({"label": p["filename"], "reason": reason})

    st.divider()

    c1, c2, c3 = st.columns(3)
    c1.metric("🆕 To create", len(push_create))
    c2.metric("♻️ To update", len(push_update))
    c3.metric("⏭️ Skipped", len(skipped))

    target_label = "**LIVE**" if push_live else "**Draft (staged)**"
    st.info(f"Target: {target_label} · Collection: `{collection_id}`")

    if not (push_create or push_update):
        st.warning("Nothing to push. Adjust your mode or upload different files.")
        st.stop()

    confirm = st.checkbox(
        f"I confirm: push {len(push_create) + len(push_update)} item(s) "
        f"({len(push_create)} create / {len(push_update)} update).",
        key="bulk_confirm",
    )
    if confirm:
        if st.button("🚀 Push All", type="primary", use_container_width=True, key="bulk_push_btn"):
            with st.spinner("Pushing batch to Webflow…"):
                results = push_items_bulk(
                    api_token, collection_id,
                    create_items=push_create,
                    update_items=push_update,
                    live=push_live,
                )

            ok = [r for r in results if r["status"] == "✅"]
            bad = [r for r in results if r["status"] == "❌"]

            if ok and not bad:
                st.success(f"✅ Pushed {len(ok)} / {len(ok)} items.")
                st.balloons()
            elif ok and bad:
                st.warning(f"Partial success: {len(ok)} ok, {len(bad)} failed.")
            else:
                st.error(f"All {len(bad)} push(es) failed.")

            st.markdown("### 📋 Per-item result")
            for r in results:
                line = (f"{r['status']} **{r['label']}** — `{r['action']}` "
                        f"slug `{r['slug'] or '—'}` "
                        f"id `{r['item_id'] or '—'}`")
                st.markdown(line)
                if r["error"]:
                    with st.expander("error detail"):
                        st.code(r["error"], language="json")

            if skipped:
                with st.expander(f"⏭️ Skipped ({len(skipped)}) — not pushed by design"):
                    for s in skipped:
                        st.caption(f"**{s['label']}** — {s['reason']}")

    st.stop()

st.divider()

# Upload - two options
upload_type = st.radio("📤 Upload Type", [
    "Webflow-Ready HTML (direct push)",
    "Raw HTML (auto-converts)",
    "CSV (pre-formatted)",
    "Markdown (.md)",
], horizontal=True)

if upload_type == "Webflow-Ready HTML (direct push)":
    uploaded_file = st.file_uploader("📄 Upload Webflow-Ready HTML", type=["html", "htm"],
                                      help="HTML already formatted with data-rt-embed-type wrappers")

    if uploaded_file:
        raw_html = uploaded_file.read().decode("utf-8")
        st.caption(f"Loaded **{uploaded_file.name}** — {len(raw_html):,} characters")

        # Parse directly — no conversion needed
        blocks_list = parse_blocks(raw_html)

        st.session_state["blocks"] = blocks_list
        st.success(f"✅ {len(blocks_list)} blocks loaded directly (no conversion)")

elif upload_type == "Raw HTML (auto-converts)":
    uploaded_file = st.file_uploader("📄 Upload HTML", type=["html", "htm"])

    if uploaded_file:
        raw_html = uploaded_file.read().decode("utf-8")
        st.caption(f"Loaded **{uploaded_file.name}** — {len(raw_html):,} characters")

        # Step 1: Scan H2s from the HTML and let user select
        if "raw_html" not in st.session_state or st.session_state.get("raw_html_name") != uploaded_file.name:
            st.session_state["raw_html"] = raw_html
            st.session_state["raw_html_name"] = uploaded_file.name
            # Clear previous blocks
            if "blocks" in st.session_state:
                del st.session_state["blocks"]

        # Extract H2 headings from the raw HTML
        scan_soup = BeautifulSoup(raw_html, "html.parser")
        h2_tags = scan_soup.find_all("h2")
        h2_texts = [h2.get_text().strip() for h2 in h2_tags]

        if h2_texts:
            st.markdown("**Select sections to include:**")
            selected_h2s = []
            for i, h2_text in enumerate(h2_texts):
                checked = st.checkbox(h2_text, value=True, key=f"h2_select_{i}")
                if checked:
                    selected_h2s.append(h2_text)

            st.caption(f"{len(selected_h2s)} of {len(h2_texts)} sections selected")
            st.session_state["selected_h2s"] = selected_h2s

        if st.button("⚡ Process Selected Sections", type="primary", use_container_width=True):
            with st.spinner("Processing HTML..."):
                processed_html, stats = classify_and_wrap(raw_html)

                # Parse into individual blocks
                all_blocks = parse_blocks(processed_html)

                # Filter blocks based on selected H2s
                selected_h2s = st.session_state.get("selected_h2s", h2_texts)
                filtered_blocks = []
                include_current = True  # Include intro blocks before first H2

                for block in all_blocks:
                    # Check if this block is an H2
                    block_soup_check = BeautifulSoup(block["html"], "html.parser")
                    h2_el = block_soup_check.find("h2")

                    if h2_el and block["type"] == "plain":
                        h2_text = h2_el.get_text().strip()
                        include_current = h2_text in selected_h2s
                        if include_current:
                            filtered_blocks.append(block)
                    elif include_current:
                        filtered_blocks.append(block)

                st.session_state["blocks"] = filtered_blocks
                st.session_state["stats"] = {
                    "total_blocks": len(filtered_blocks),
                    "embed_blocks": sum(1 for b in filtered_blocks if b["type"] == "embed"),
                    "plain_blocks": sum(1 for b in filtered_blocks if b["type"] == "plain"),
                    "warnings": stats.get("warnings", []),
                    "total_chars": sum(b["chars"] for b in filtered_blocks),
                }
                st.rerun()

elif upload_type == "CSV (pre-formatted)":
    uploaded_csv = st.file_uploader("📄 Upload Content CSV", type=["csv"])

    if uploaded_csv:
        import csv
        import io
        csv_text = uploaded_csv.read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)

        # Concatenate all content rows
        content_parts = [row.get("content", "").strip() for row in rows if row.get("content", "").strip()]
        csv_content = "\n".join(content_parts)

        st.caption(f"Loaded **{uploaded_csv.name}** — {len(rows)} blocks, {len(csv_content):,} characters")

        # Parse into blocks for display
        blocks_list = parse_blocks(csv_content)

        st.session_state["blocks"] = blocks_list
        st.session_state["processed_html"] = csv_content
        st.session_state["stats"] = {
            "total_blocks": len(blocks_list),
            "embed_blocks": sum(1 for b in blocks_list if b["type"] == "embed"),
            "plain_blocks": sum(1 for b in blocks_list if b["type"] == "plain"),
            "warnings": [],
            "total_chars": len(csv_content),
        }

else:  # Markdown (.md)
    uploaded_md = st.file_uploader("📄 Upload Markdown file", type=["md"],
                                    help="Plain Markdown article — converted to HTML blocks before push")

    if uploaded_md:
        import markdown as _md_lib
        md_text = uploaded_md.read().decode("utf-8")
        st.caption(f"Loaded **{uploaded_md.name}** — {len(md_text):,} characters")

        md_html = _md_lib.markdown(
            md_text,
            extensions=["tables", "fenced_code", "nl2br"],
        )

        # Tag bare table/pre with a class so classify_and_wrap's existing
        # rules (which require a class) treat them as embed blocks.
        _md_soup = BeautifulSoup(md_html, "html.parser")
        for _t in _md_soup.find_all("table"):
            _t["class"] = _t.get("class", []) + ["md-table"]
        for _p in _md_soup.find_all("pre"):
            _wrap = _md_soup.new_tag("div")
            _wrap["class"] = ["md-codeblock"]
            _p.wrap(_wrap)
        md_html = str(_md_soup)

        # Run through the same Raw-HTML pipeline so tables / pre / sections
        # get the data-rt-embed-type wrappers Webflow Rich Text requires.
        with st.spinner("Converting Markdown → Webflow-ready HTML..."):
            processed_html, stats = classify_and_wrap(md_html)

        blocks_list = parse_blocks(processed_html)
        st.session_state["blocks"] = blocks_list
        st.session_state["processed_html"] = processed_html
        st.session_state["stats"] = {
            "total_blocks": len(blocks_list),
            "embed_blocks": sum(1 for b in blocks_list if b["type"] == "embed"),
            "plain_blocks": sum(1 for b in blocks_list if b["type"] == "plain"),
            "warnings": stats.get("warnings", []),
            "total_chars": len(processed_html),
        }
        st.success(f"✅ {len(blocks_list)} blocks converted from Markdown "
                   f"({st.session_state['stats']['embed_blocks']} embeds, "
                   f"{st.session_state['stats']['plain_blocks']} plain)")

if "blocks" in st.session_state:
    blocks_list = st.session_state["blocks"]

    # Rebuild stats from current blocks
    embed_count = sum(1 for b in blocks_list if b["type"] == "embed")
    plain_count = sum(1 for b in blocks_list if b["type"] == "plain")
    total_chars = sum(b["chars"] for b in blocks_list)

    # Stats
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Blocks", len(blocks_list))
    c2.metric("🟢 Plain", plain_count)
    c3.metric("🟡 Embeds", embed_count)
    c4.metric("Total Size", f"{total_chars:,} ch")

    # ── Group blocks by H2 sections ──
    sections = []
    current_section = {"title": "📌 Introduction", "blocks": [], "indices": []}

    for idx, block in enumerate(blocks_list):
        # Detect H2 headings to start new sections
        soup_check = BeautifulSoup(block["html"], "html.parser")
        h2 = soup_check.find("h2")
        if h2 and block["type"] == "plain":
            # Save previous section
            if current_section["blocks"] or current_section["title"] == "📌 Introduction":
                sections.append(current_section)
            # Start new section
            h2_text = h2.get_text()[:50].strip()
            current_section = {"title": h2_text, "blocks": [], "indices": []}

        current_section["blocks"].append(block)
        current_section["indices"].append(idx)

    # Append last section
    if current_section["blocks"]:
        sections.append(current_section)

    # Create tabs for each section
    section_tabs = st.tabs([s["title"] for s in sections])

    for sec_idx, (section, sec_tab) in enumerate(zip(sections, section_tabs)):
        with sec_tab:
            st.caption(f"{len(section['blocks'])} blocks | {sum(b['chars'] for b in section['blocks']):,} chars")

            for i, (block, global_idx) in enumerate(zip(section["blocks"], section["indices"])):
                is_embed = block["type"] == "embed"
                icon = "🟡" if is_embed else "🟢"
                type_label = "EMBED" if is_embed else "PLAIN"
                tag_info = "" if is_embed else f" <{block['tag']}>"
                preview = block["preview"][:60]

                with st.expander(f"{icon} **Block {global_idx+1}** — {type_label}{tag_info} ({block['chars']:,} ch) | {preview}"):
                    if is_embed:
                        inner_soup = BeautifulSoup(block["html"], "html.parser")
                        wrapper = inner_soup.find("div", attrs={"data-rt-embed-type": "true"})
                        inner_html = wrapper.decode_contents().strip() if wrapper else block["html"]

                        edited = st.text_area(
                            "Edit HTML",
                            value=inner_html,
                            height=250,
                            key=f"edit_s{sec_idx}_b{i}",
                        )
                        # Update block if edited
                        if edited != inner_html:
                            new_html = f'<div data-rt-embed-type="true">\n{edited}\n</div>'
                            blocks_list[global_idx]["html"] = new_html
                            blocks_list[global_idx]["chars"] = len(new_html)
                            blocks_list[global_idx]["preview"] = BeautifulSoup(edited, "html.parser").get_text()[:100].replace("\n", " ").strip()

                        if block["chars"] > EMBED_CHAR_LIMIT:
                            st.error(f"⚠️ Exceeds {EMBED_CHAR_LIMIT:,} char limit!")
                    else:
                        edited = st.text_area(
                            "Edit HTML",
                            value=block["html"],
                            height=150,
                            key=f"edit_s{sec_idx}_b{i}",
                        )
                        if edited != block["html"]:
                            blocks_list[global_idx]["html"] = edited
                            blocks_list[global_idx]["chars"] = len(edited)
                            blocks_list[global_idx]["preview"] = BeautifulSoup(edited, "html.parser").get_text()[:100].replace("\n", " ").strip()

    # Save any edits back
    st.session_state["blocks"] = blocks_list

    # Rebuild processed HTML from blocks
    processed_html = "\n".join(b["html"] for b in blocks_list)
    st.session_state["processed_html"] = processed_html

    # Source HTML & Download
    st.divider()
    dl_col1, dl_col2 = st.columns(2)
    with dl_col1:
        with st.expander("💻 Source HTML"):
            st.code(processed_html[:15000] + ("\n\n... [TRUNCATED]" if len(processed_html) > 15000 else ""),
                    language="html")
    with dl_col2:
        st.download_button(
            "📥 Download Webflow-Ready HTML",
            data=processed_html,
            file_name=f"webflow_ready_{slug or 'content'}.html",
            mime="text/html",
            use_container_width=True
        )

    # Push section
    st.divider()
    st.subheader("🚀 Push to Webflow CMS")

    if not api_token or not collection_id:
        st.warning("Enter your Webflow API token and Collection ID in the sidebar.")
        target_field = "content"
    else:
        # Target-field selector — list all Rich Text fields in the collection
        # so the user can pick where the HTML goes (default "content" if present).
        schema_key = f"_schema_cache::{collection_id}"
        if schema_key not in st.session_state:
            with st.spinner("Loading collection schema..."):
                _sch, _sch_err = fetch_collection_schema(api_token, collection_id)
            st.session_state[schema_key] = _sch if not _sch_err else None
            if _sch_err:
                st.caption(f"⚠️ Could not load schema ({_sch_err}) — defaulting target to `content`.")

        _sch = st.session_state.get(schema_key) or {}
        _rt_fields = [f for f in _sch.get("fields", [])
                      if str(f.get("type", "")).lower() == "richtext"]

        if _rt_fields:
            _slugs = [f.get("slug") for f in _rt_fields]
            _labels = [f"{f.get('displayName', f.get('slug'))} ({f.get('slug')})"
                       for f in _rt_fields]
            _default = _slugs.index("content") if "content" in _slugs else 0
            _pick = st.selectbox(
                "🎯 Target Rich Text field",
                options=list(range(len(_slugs))),
                format_func=lambda i: _labels[i],
                index=_default,
                help="HTML will be pushed into this field. Defaults to `content` if it exists.",
            )
            target_field = _slugs[_pick]
        else:
            target_field = "content"
            st.caption("No Rich Text fields found in schema — defaulting target to `content`.")

    if not api_token or not collection_id:
        pass  # already warned above
    elif mode == "Update Existing Item":
        found_item = st.session_state.get("found_item")
        if not found_item:
            st.warning("Search for the item first using the slug above.")
        else:
            item_name = found_item["fieldData"].get("name", "?")
            item_id = found_item["id"]
            target = "**LIVE**" if push_live else "**Draft (staged)**"

            # Build update payload with meta fields
            update_fields = {target_field: processed_html}

            # Check if meta fields were edited
            if "edit_name" in st.session_state and st.session_state["edit_name"] != found_item["fieldData"].get("name", ""):
                update_fields["name"] = st.session_state["edit_name"]
            if "edit_slug" in st.session_state and st.session_state["edit_slug"] != found_item["fieldData"].get("slug", ""):
                update_fields["slug"] = st.session_state["edit_slug"]
            if "edit_meta_title" in st.session_state and st.session_state["edit_meta_title"] != found_item["fieldData"].get("meta-title", ""):
                update_fields["meta-title"] = st.session_state["edit_meta_title"]
            if "edit_meta_desc" in st.session_state and st.session_state["edit_meta_desc"] != found_item["fieldData"].get("meta-description", ""):
                update_fields["meta-description"] = st.session_state["edit_meta_desc"]
            if "edit_canonical" in st.session_state and st.session_state["edit_canonical"] != found_item["fieldData"].get("canonical-links", ""):
                update_fields["canonical-links"] = st.session_state["edit_canonical"]

            # Show what will be updated
            fields_updating = [k for k in update_fields.keys()]
            st.info(f"**Update:** {item_name} → {target}\n\nItem ID: `{item_id}` | Fields: {', '.join(fields_updating)}")

            confirm = st.checkbox(f"I confirm: update '{item_name}'")
            if confirm:
                if st.button("🚀 Push Content Now", type="primary", use_container_width=True):
                    with st.spinner("Pushing to Webflow..."):
                        # Build full payload
                        if push_live:
                            url = f"{WEBFLOW_API_BASE}/collections/{collection_id}/items/live"
                        else:
                            url = f"{WEBFLOW_API_BASE}/collections/{collection_id}/items"

                        payload = {
                            "items": [{
                                "id": item_id,
                                "fieldData": update_fields
                            }]
                        }
                        resp = requests.patch(url, headers=get_headers(api_token), json=payload, timeout=REQUEST_TIMEOUT)

                    if resp.status_code == 200:
                        st.success("✅ Updated successfully!")
                        st.balloons()
                        with st.expander("API Response"):
                            st.json(safe_json(resp))
                    else:
                        st.error(f"❌ Failed — HTTP {resp.status_code}")
                        st.code(resp.text, language="json")

    else:  # Create New Item
        if not new_name or not new_slug:
            st.warning("Name and Slug are required to create a new item.")
        else:
            # Build extra fields
            extra = {}
            if new_meta_title:
                extra["meta-title"] = new_meta_title
            if new_meta_desc:
                extra["meta-description"] = new_meta_desc
            if new_description:
                extra["description"] = new_description
            if new_canonical:
                extra["canonical-links"] = new_canonical
            elif new_slug:
                extra["canonical-links"] = f"https://www.edstellar.com/{new_slug}"
            if new_primary_keyword:
                extra["primary-keyword"] = new_primary_keyword
            if new_keyword_volume:
                extra["keyword-search-volume"] = new_keyword_volume
            if include_flag_fields:
                extra["new-format-blog"] = new_format_blog
                extra["faqs-section"] = new_faqs_section

            st.info(f"**Create:** {new_name}\n\nSlug: `{new_slug}` | Content: {total_chars:,} chars | Status: Draft")

            fields_summary = ", ".join(f"{k}" for k in extra.keys() if extra[k])
            st.caption(f"Extra fields: {fields_summary}")

            confirm = st.checkbox(f"I confirm: create new item '{new_name}'")
            if confirm:
                if st.button("🚀 Create Item", type="primary", use_container_width=True):
                    with st.spinner("Creating in Webflow..."):
                        resp = create_new_item(api_token, new_name, new_slug, processed_html,
                                                collection_id, extra, content_field=target_field)

                    if resp.status_code in (200, 201, 202):
                        st.success("✅ Item created as Draft!")
                        st.balloons()
                        with st.expander("API Response"):
                            st.json(safe_json(resp))
                    else:
                        st.error(f"❌ Failed — HTTP {resp.status_code}")
                        st.code(resp.text, language="json")
