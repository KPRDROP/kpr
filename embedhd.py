async def resolve_m3u8(page, url, idx):
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)

        # give JS time to inject player config
        await asyncio.sleep(5)

        # 1️⃣ scan all <script> tags
        scripts = await page.evaluate("""
            Array.from(document.scripts)
                .map(s => s.innerText || "")
                .join("\\n")
        """)

        match = re.search(r"https?://[^\"']+\\.m3u8[^\"']*", scripts)
        if match:
            log.info(f"URL {idx}) m3u8 found in script")
            return match.group(0)

        # 2️⃣ scan performance entries (HLS preload)
        perf = await page.evaluate("""
            performance.getEntries()
              .map(e => e.name)
              .join("\\n")
        """)

        match = re.search(r"https?://[^\"']+\\.m3u8[^\"']*", perf)
        if match:
            log.info(f"URL {idx}) m3u8 found in performance")
            return match.group(0)

        raise TimeoutError("m3u8 not detected")

    except Exception as e:
        log.warning(f"URL {idx}) Failed: {e}")
        return None
