async def capture_stream(page, url, idx):
    stream_url = None

    def handle_response(res):
        nonlocal stream_url
        try:
            if ".m3u8" in res.url and not stream_url:
                stream_url = res.url
        except:
            pass

    page.on("response", handle_response)

    try:
        # ---------------------------------
        # STEP 1: OPEN HOMEPAGE FIRST
        # ---------------------------------
        await page.goto(BASE_URL, timeout=30000)
        await page.wait_for_timeout(2000)

        # ---------------------------------
        # STEP 2: NAVIGATE WITH REFERER
        # ---------------------------------
        await page.goto(
            url,
            timeout=30000,
            referer=BASE_URL
        )

        await page.wait_for_timeout(5000)

        # ---------------------------------
        # STEP 3: HUMAN-LIKE CLICK
        # ---------------------------------
        for _ in range(3):
            try:
                await page.mouse.click(500, 400)
                await asyncio.sleep(1)
            except:
                pass

        # ---------------------------------
        # STEP 4: HANDLE IFRAMES
        # ---------------------------------
        for frame in page.frames:
            try:
                await frame.click("body", timeout=2000)
                await asyncio.sleep(1)
            except:
                pass

        # ---------------------------------
        # STEP 5: WAIT FOR STREAM
        # ---------------------------------
        waited = 0
        while waited < 20 and not stream_url:
            await asyncio.sleep(1)
            waited += 1

        # ---------------------------------
        # STEP 6: FALLBACK HTML SCAN
        # ---------------------------------
        if not stream_url:
            html = await page.content()
            m = re.search(r'https?://[^\s"\']+\.m3u8[^\s"\']*', html)
            if m:
                stream_url = m.group(0)

    except Exception as e:
        log.warning(f"URL {idx}) Error: {e}")

    finally:
        try:
            page.remove_listener("response", handle_response)
        except:
            pass

    return stream_url
