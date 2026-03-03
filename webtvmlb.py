async def process_event(url: str, url_num: int) -> str | None:

    async with async_playwright() as p:

        browser = await p.firefox.launch(headless=True)

        context = await browser.new_context(
            user_agent=USER_AGENT,
            extra_http_headers={
                "Referer": BASE_URL,
                "Origin": BASE_URL.rstrip("/")
            }
        )

        # Block images / fonts / trackers (faster + less detection)
        await context.route(
            "**/*",
            lambda route, request: (
                route.abort()
                if request.resource_type in ["image", "font"]
                else route.continue_()
            ),
        )

        page = await context.new_page()

        captured = None

        # 🔥 CAPTURE ALL REQUESTS (BETTER THAN requestfinished)
        def handle_request(request):
            nonlocal captured
            if ".m3u8" in request.url and not captured:
                captured = request.url

        context.on("request", handle_request)

        try:

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except PlaywrightTimeoutError:
                pass

            await page.wait_for_timeout(4000)

            # Momentum click (ad bypass)
            for _ in range(2):
                try:
                    await page.mouse.click(500, 350)
                    await asyncio.sleep(1)
                except Exception:
                    pass

            # Click inside iframes
            for frame in page.frames:
                try:
                    await frame.click("body", timeout=2000)
                    await asyncio.sleep(1)
                except Exception:
                    pass

            # Wait for stream
            waited = 0
            while waited < 20 and not captured:
                await asyncio.sleep(1)
                waited += 1

            # Fallback HTML scan
            if not captured:
                html = await page.content()
                m = re.search(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html)
                if m:
                    captured = m.group(0)

            # Base64 fallback
            if not captured:
                html = await page.content()
                blobs = re.findall(r'["\']([A-Za-z0-9+/=]{40,200})["\']', html)
                for b in blobs:
                    try:
                        import base64
                        dec = base64.b64decode(b).decode("utf-8", "ignore")
                        if ".m3u8" in dec:
                            captured = dec.strip()
                            break
                    except Exception:
                        pass

        finally:
            context.remove_listener("request", handle_request)
            await page.close()
            await context.close()
            await browser.close()

        if captured:
            log.info(f"URL {url_num}) Captured M3U8 via browser")
        else:
            log.warning(f"URL {url_num}) Failed to capture stream")

        return captured
