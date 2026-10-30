# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Render posts.json into the post list in index.html.

Also takes each post's title from the first <h1> on its page and writes it
into posts.json and the page's <title>, points each post page at the
blog's favicon, and ends every page with the credits footer. Run after building or editing a post with bloops:
uv run build.py  (or python3 build.py, with Python 3.12+)

With --check (used by the pre-push hook), fail if anything had to be updated.
With --rebuild, first build every post's page again from its content.bbcode
with bloops, e.g. after updating bloops; set BLOOPS to run another bloops,
such as BLOOPS="uv run --project ../bloops bloops".
"""

import argparse
import json
import math
import os
import re
import shlex
import subprocess
from datetime import date
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import TypedDict, cast, final, override

ROOT = Path(__file__).parent
WORDS_PER_MINUTE = 200
START, END = "<!-- posts:start -->", "<!-- posts:end -->"
# keeps prettier from reformatting the list, which build.py owns
IGNORE = "<!-- prettier-ignore -->"

BLOOPS = shlex.split(os.environ.get("BLOOPS", "bloops"))

SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
TITLE = re.compile(r"<title>.*?</title>", re.DOTALL)
ICON_LINK = re.compile(r'[ \t]*<link\s+rel="icon"[^>]*>\n?')
# the favicon links of a post page, which is one folder below the root
ICON_LINKS = """    <link rel="icon" href="../favicon.ico" sizes="any" />
    <link rel="icon" href="../favicon.svg" type="image/svg+xml" />
"""
CREDITS_FOOTER = re.compile(
    r'[ \t]*<footer class="credits">.*?</footer>\n?', re.DOTALL
)
# the last thing in every page's <body>
CREDITS = """    <footer class="credits">
      Made with <a href="https://github.com/Bubu-Droid/bloops">bloops</a>
      and the
      <a href="https://github.com/Bubu-Droid/nekopurrin">nekopurrin</a>
      template.
    </footer>
"""


class Post(TypedDict):
    """One entry of posts.json."""

    title: str
    slug: str
    date: str
    excerpt: str


@final
class PostText(HTMLParser):
    """Collect a built post's body text and its title."""

    def __init__(self) -> None:
        """Start outside <body> with nothing collected."""
        super().__init__()
        self.in_body = False
        self.in_title = False
        self.in_footer = False
        self.seen_title = False
        self.text: list[str] = []
        self.title_parts: list[str] = []

    @property
    def title(self) -> str:
        """Text of the page's first <h1>, or "" if it has none."""
        return " ".join("".join(self.title_parts).split())

    @override
    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        """Track <body>, the credits footer and the title."""
        if tag == "body":
            self.in_body = True
        elif tag == "footer":
            self.in_footer = True
        elif tag == "h1" and not self.seen_title:
            self.in_title = self.seen_title = True

    @override
    def handle_endtag(self, tag: str) -> None:
        """Track leaving <body>, the credits footer and the title."""
        if tag == "body":
            self.in_body = False
        elif tag == "footer":
            self.in_footer = False
        elif tag == "h1":
            self.in_title = False

    @override
    def handle_data(self, data: str) -> None:
        """Keep body text but the footer's, and the title's separately."""
        if not self.in_body or self.in_footer:
            return
        self.text.append(data)
        if self.in_title:
            self.title_parts.append(data)


def read_post(slug: str) -> PostText:
    """Parse the post's built index.html."""
    page = PostText()
    page.feed((ROOT / slug / "index.html").read_text(encoding="utf-8"))
    return page


def reading_minutes(page: PostText) -> int:
    """Estimate the reading time of the whole post."""
    words = len(" ".join(page.text).split())
    return max(1, math.ceil(words / WORDS_PER_MINUTE))


def string_field(fields: dict[str, object], key: str, where: str) -> str:
    """Return fields[key], which must be a non-empty string."""
    value = fields.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"{where}: {key!r} must be a non-empty string")
    return value


def load_posts() -> tuple[list[tuple[Post, PostText]], bool]:
    """Read posts.json and each post's page, taking titles from their <h1>.

    Returns:
        Each post with its parsed page, and whether any title in posts.json
        was missing or differed from the page's <h1>.

    Raises:
        SystemExit: On any bad entry, with a message saying what is wrong.
    """
    text = (ROOT / "posts.json").read_text(encoding="utf-8")
    raw = cast("object", json.loads(text))
    if not isinstance(raw, list):
        raise SystemExit("posts.json: expected a list of posts")

    posts: list[tuple[Post, PostText]] = []
    retitled = False
    for i, entry in enumerate(cast("list[object]", raw), start=1):
        where = f"posts.json, post {i}"
        if not isinstance(entry, dict):
            raise SystemExit(f"{where}: expected an object")
        fields = cast("dict[str, object]", entry)
        unknown = (
            fields.keys() - Post.__required_keys__ - Post.__optional_keys__
        )
        if unknown:
            raise SystemExit(f"{where}: unknown field(s) {sorted(unknown)}")

        old_title = (
            string_field(fields, "title", where) if "title" in fields else ""
        )
        post: Post = {
            "title": old_title,
            "slug": string_field(fields, "slug", where),
            "date": string_field(fields, "date", where),
            "excerpt": string_field(fields, "excerpt", where),
        }

        slug = post["slug"]
        if not SLUG.fullmatch(slug):
            raise SystemExit(f"{where}: slug must look like 'my-post-title'")
        if any(p["slug"] == slug for p, _ in posts):
            raise SystemExit(f"{where}: slug {slug!r} is used twice")
        try:
            valid_date = date.fromisoformat(post["date"]).isoformat()
        except ValueError:
            valid_date = None
        if valid_date != post["date"]:
            raise SystemExit(f"{where}: date must be a real yyyy-mm-dd date")
        if not (ROOT / slug / "index.html").is_file():
            raise SystemExit(f"{where}: {slug}/index.html missing, run bloops")

        page = read_post(slug)
        post["title"] = page.title or old_title
        if not post["title"]:
            raise SystemExit(
                f"{where}: {slug} has no [h1] title and no 'title'"
            )
        retitled |= post["title"] != old_title
        posts.append((post, page))
    return posts, retitled


def write_if_changed(path: Path, old: str, new: str) -> bool:
    """Write new to path unless it equals old; return whether it changed."""
    if new == old:
        return False
    _ = path.write_text(new, encoding="utf-8")
    return True


def with_credits(html: str, where: str) -> str:
    """End the page's <body> with the credits footer, replacing any old one.

    Raises:
        SystemExit: If the page has no </body>.
    """
    html = CREDITS_FOOTER.sub("", html)
    body_end = html.rfind("</body>")
    if body_end < 0:
        raise SystemExit(f"{where} has no </body>")
    line_start = html.rfind("\n", 0, body_end) + 1
    return html[:line_start] + CREDITS + html[line_start:]


def update_page(post: Post) -> bool:
    """Set the post page's <title>, favicon links and credits footer.

    Bloops writes its own title and favicon, and no footer.
    """
    page = ROOT / post["slug"] / "index.html"
    html = page.read_text(encoding="utf-8")
    title = f"<title>{escape(post['title'])}</title>"
    new_html, found = TITLE.subn(lambda _: title, html, count=1)
    if not found:
        raise SystemExit(f"{post['slug']}/index.html has no <title>")
    # the blog's links go where the first icon link was; the rest are dropped
    before, *after = ICON_LINK.split(new_html)
    if not after:
        raise SystemExit(f"{post['slug']}/index.html has no favicon link")
    new_html = before + ICON_LINKS + "".join(after)
    new_html = with_credits(new_html, f"{post['slug']}/index.html")
    return write_if_changed(page, html, new_html)


def render_post(post: Post, page: PostText) -> str:
    """Render one post's list item."""
    return f"""        <li>
          <a class="title" href="{post["slug"]}/">{escape(post["title"])}</a>
          <div class="meta">
            <time datetime="{post["date"]}">{post["date"]}</time>
            <span class="minutes">{reading_minutes(page)} min</span>
          </div>
          <p class="excerpt">{escape(post["excerpt"])}</p>
        </li>"""


def render(posts: list[tuple[Post, PostText]]) -> str:
    """Render the post list, newest first."""
    if not posts:
        return '<p class="empty">Nothing here yet.</p>'
    newest_first = sorted(posts, key=lambda p: p[0]["date"], reverse=True)
    items = "\n".join(render_post(post, page) for post, page in newest_first)
    return f'<ul class="posts">\n{items}\n      </ul>'


def rebuild_pages() -> None:
    """Build every post's index.html again from its content.bbcode.

    Raises:
        SystemExit: If bloops cannot be run or fails on a post.
    """
    for source in sorted(ROOT.glob("*/content.bbcode")):
        folder = source.parent
        print(f"bloops: {folder.name}")
        try:
            result = subprocess.run(
                [*BLOOPS, "-i", str(folder)],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            raise SystemExit(
                f"{BLOOPS[0]!r} not found: install bloops, or set BLOOPS"
            ) from None
        if result.returncode:
            raise SystemExit(
                f"bloops failed on {folder.name}:\n"
                + (result.stderr or result.stdout).strip()
            )


def main() -> None:
    """Sync titles, update each post page, then rewrite the home page."""
    parser = argparse.ArgumentParser(
        description="Update the post list in index.html and each post's page."
    )
    mode = parser.add_mutually_exclusive_group()
    _ = mode.add_argument(
        "--check",
        action="store_true",
        help="fail if anything had to be updated (used by the pre-push hook)",
    )
    _ = mode.add_argument(
        "--rebuild",
        action="store_true",
        help="first rebuild every post's page from its content.bbcode",
    )
    args = parser.parse_args()
    check = cast("bool", args.check)
    if cast("bool", args.rebuild):
        rebuild_pages()
    posts, retitled = load_posts()
    changed: list[str] = []
    if retitled:
        posts_json = json.dumps(
            [p for p, _ in posts], indent=2, ensure_ascii=False
        )
        _ = (ROOT / "posts.json").write_text(
            posts_json + "\n", encoding="utf-8"
        )
        changed.append("posts.json")
    changed += [
        f"{post['slug']}/index.html" for post, _ in posts if update_page(post)
    ]
    index = ROOT / "index.html"
    html = index.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(START) + ".*?" + re.escape(END), re.DOTALL)
    if not pattern.search(html):
        raise SystemExit(f"index.html is missing the {START} / {END} markers")
    block = f"{START}\n      {IGNORE}\n      {render(posts)}\n      {END}"
    # a function, so backslashes in the block (e.g. TeX) aren't read as escapes
    new_html = with_credits(pattern.sub(lambda _: block, html), "index.html")
    if write_if_changed(index, html, new_html):
        changed.append("index.html")
    print(f"index.html: {len(posts)} post(s)")
    if changed and check:
        raise SystemExit(
            "build.py updated "
            + ", ".join(changed)
            + "; commit the changes and push again"
        )


if __name__ == "__main__":
    main()
