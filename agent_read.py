import argparse
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
import trafilatura

##pip install playwright trafilatura
#playwright install chromium


URL_RE = re.compile(r"https?://\S+")

def extract_main_text(html: str) -> str:
    # trafilatura 会尽力抽取正文（对新闻/博客/论坛长文通常很好用）
    text = trafilatura.extract(html, include_comments=False, include_tables=False)
    return (text or "").strip()

def sanitize_filename(s: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:120] if len(s) > 120 else s

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def run_login(profile_dir: Path):
    print(f"[i] 使用持久化浏览器目录: {profile_dir}")
    print("[i] 将打开浏览器。请你手动完成登录（需要登录的站都可以顺便登一遍）。")
    print("[i] 登录完成后，回到终端按 Enter 结束。")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.new_page()
        page.goto("https://example.com", wait_until="domcontentloaded")
        input()
        ctx.close()
    print("[✓] 登录态已保存。以后访问同站点会自动带上。")

def fetch_and_extract(profile_dir: Path, url: str, out_dir: Path, headless: bool):
    ensure_dir(out_dir)
    ts = time.strftime("%Y%m%d-%H%M%S")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.new_page()

        print(f"[i] 打开: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            # 有些站加载慢/分段加载，稍等一下更容易拿到正文
            page.wait_for_timeout(1500)
            # 尝试滚动触发懒加载
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)

            title = (page.title() or "untitled").strip()
            html = page.content()
            text = extract_main_text(html)

            # 如果抽取失败，至少把可见文字拿出来兜底
            if not text:
                text = (page.inner_text("body") or "").strip()

            fname = sanitize_filename(f"{ts}_{title}") or f"{ts}_page"
            md_path = out_dir / f"{fname}.md"

            md = []
            md.append(f"# {title}\n")
            md.append(f"- URL: {url}\n")
            md.append("\n---\n")
            md.append(text if text else "[未能抽取正文，可能是强脚本渲染/付费墙/反爬拦截]\n")

            md_path.write_text("\n".join(md), encoding="utf-8")

            # 同时给你留个截图方便排查（尤其是碰到登录墙/付费墙）
            shot_path = out_dir / f"{fname}.png"
            try:
                page.screenshot(path=str(shot_path), full_page=True)
            except Exception:
                pass

            print(f"[✓] 已输出: {md_path}")
            if shot_path.exists():
                print(f"[i] 截图: {shot_path}")

        finally:
            ctx.close()

def read_urls_from_stdin() -> list[str]:
    data = sys.stdin.read()
    return URL_RE.findall(data)

def main():
    ap = argparse.ArgumentParser(description="PC 本地：一次登录，后续自动带登录态读链接并抽取正文")
    ap.add_argument("--login", action="store_true", help="首次运行：打开浏览器让你手动登录一次")
    ap.add_argument("--url", type=str, help="要读取的链接")
    ap.add_argument("--headful", action="store_true", help="显示浏览器窗口（调试/遇到登录墙建议开）")
    ap.add_argument("--profile", type=str, default=".agent_profile", help="持久化浏览器目录")
    ap.add_argument("--out", type=str, default="out", help="输出目录（markdown+截图）")
    args = ap.parse_args()

    profile_dir = Path(args.profile).resolve()
    out_dir = Path(args.out).resolve()

    if args.login:
        run_login(profile_dir)
        return

    urls = []
    if args.url:
        urls = [args.url.strip()]
    else:
        # 支持：echo "xxx https://..." | python agent_reader.py
        urls = read_urls_from_stdin()

    if not urls:
        print("用法：")
        print("  1) 首次登录：python agent_reader.py --login")
        print("  2) 读链接：  python agent_reader.py --url https://....")
        print("  3) 或从 stdin：echo '...https://...' | python agent_reader.py")
        sys.exit(1)

    for u in urls:
        fetch_and_extract(profile_dir, u, out_dir, headless=(not args.headful))

if __name__ == "__main__":
    main()
