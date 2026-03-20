#!/usr/bin/env python3
"""
OVO Scraper - Extracts M3U8 streams from volokit.xyz
Uses only standard library modules for compatibility
"""

import os
import re
import json
import time
import base64
import urllib.request
import urllib.parse
import urllib.error
import sys
import logging
import ssl
from datetime import datetime
from html.parser import HTMLParser

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s',
    datefmt='%Y-%m-%d | %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Custom HTML Parser for extracting data without BeautifulSoup
class HTMLParserExtended(HTMLParser):
    def __init__(self):
        super().__init__()
        self.iframes = []
        self.links = []
        self.scripts = []
        self.current_tag = None
        self.current_attrs = {}
    
    def handle_starttag(self, tag, attrs):
        self.current_tag = tag
        self.current_attrs = dict(attrs)
        
        if tag == 'iframe':
            src = dict(attrs).get('src', '')
            if src:
                self.iframes.append(src)
        elif tag == 'a':
            href = dict(attrs).get('href', '')
            if href:
                self.links.append(href)
    
    def handle_data(self, data):
        if self.current_tag == 'script':
            self.scripts.append(data)

class OVOScraper:
    def __init__(self):
        # Create SSL context that doesn't verify certificates (for problematic sites)
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        self.cache_file = 'ovo_cache.json'
        self.events = []
        self.cached_events = {}
        self.load_cache()
    
    def load_cache(self):
        """Load cached events from file"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    self.cached_events = json.load(f)
                logger.info(f"Loaded {len(self.cached_events)} cached events")
        except Exception as e:
            logger.error(f"Error loading cache: {e}")
            self.cached_events = {}
    
    def save_cache(self):
        """Save events to cache file"""
        try:
            cache_data = {}
            for event in self.events:
                if event.get('url'):
                    key = f"{event.get('title', '')}_{datetime.now().strftime('%Y%m%d')}"
                    cache_data[key] = {
                        'url': event['url'],
                        'title': event.get('title', ''),
                        'group': event.get('group', ''),
                        'logo': event.get('logo', ''),
                        'timestamp': time.time()
                    }
            with open(self.cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
            logger.info(f"Saved {len(cache_data)} events to cache")
        except Exception as e:
            logger.error(f"Error saving cache: {e}")
    
    def get_page(self, url):
        """Fetch a page with retries using urllib"""
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers=self.headers)
                response = urllib.request.urlopen(req, context=self.ssl_context, timeout=30)
                content = response.read().decode('utf-8', errors='ignore')
                return content
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed for {url}: {e}")
                if attempt == 2:
                    raise
                time.sleep(2)
        return None
    
    def extract_m3u8_from_embed(self, embed_url):
        """Extract M3U8 URL from embed page"""
        try:
            logger.debug(f"Fetching embed: {embed_url}")
            content = self.get_page(embed_url)
            if not content:
                return None
            
            # Method 1: Look for direct M3U8 URLs in the content
            m3u8_patterns = [
                r'(https?://[^\s"\']+\.m3u8[^\s"\']*)',
                r'(https?://[^\s"\']+\.m3u8(?:\?[^\s"\']*)?)',
                r'(https?://[^\s"\']+stream[^\s"\']*\.m3u8[^\s"\']*)',
                r'(https?://[^\s"\']+playlist[^\s"\']*\.m3u8[^\s"\']*)',
                r'(https?://[^\s"\']+\.m3u8(?:\?[^"\'\s]+)?)'
            ]
            
            for pattern in m3u8_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    for match in matches:
                        if '.m3u8' in match and 'clappr' not in match.lower():
                            logger.debug(f"Found M3U8: {match}")
                            return match
            
            # Method 2: Look for JavaScript variables that might contain the URL
            js_patterns = [
                r'(?:var|const|let)\s+(?:url|src|source|stream|link|video|hls|m3u8)\s*=\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'(?:var|const|let)\s+(?:url|src|source|stream|link|video|hls|m3u8)\s*=\s*["\']([^"\']+)["\']',
                r'(?:source|src|file|video)\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'(?:source|src|file|video)\s*:\s*["\']([^"\']+)["\']',
                r'playlist\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'url\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            ]
            
            for pattern in js_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    for match in matches:
                        if '.m3u8' in match:
                            logger.debug(f"Found M3U8 from JS: {match}")
                            return match
            
            # Method 3: Look for base64 encoded URLs
            b64_patterns = [
                r'atob\(["\']([^"\']+)["\']\)',
                r'decodeURIComponent\(["\']([^"\']+)["\']\)',
                r'Base64\.decode\(["\']([^"\']+)["\']\)',
            ]
            
            for pattern in b64_patterns:
                matches = re.findall(pattern, content)
                for match in matches:
                    try:
                        decoded = base64.b64decode(match).decode('utf-8')
                        if '.m3u8' in decoded:
                            logger.debug(f"Found M3U8 from base64: {decoded}")
                            return decoded
                    except:
                        pass
            
            # Method 4: Look for fetch.php parameters that might contain M3U8
            fetch_pattern = r'fetch\.php\?([^"\']+)'
            matches = re.findall(fetch_pattern, content)
            for match in matches:
                if 'hd=' in match or 'id=' in match:
                    # Try to construct URL from the embed page itself
                    parsed = urllib.parse.urlparse(embed_url)
                    base_url = f"{parsed.scheme}://{parsed.netloc}"
                    new_url = f"{base_url}/source/{match}"
                    logger.debug(f"Trying fetch param URL: {new_url}")
                    
                    # Try to fetch the actual stream URL
                    stream_content = self.get_page(new_url)
                    if stream_content:
                        # Look for M3U8 in the response
                        for pattern in m3u8_patterns:
                            stream_matches = re.findall(pattern, stream_content, re.IGNORECASE)
                            if stream_matches:
                                for stream_match in stream_matches:
                                    if '.m3u8' in stream_match:
                                        logger.debug(f"Found M3U8 from fetch: {stream_match}")
                                        return stream_match
            
            # Method 5: Look for JavaScript that might be generating the URL
            js_blocks = re.findall(r'<script[^>]*>([\s\S]*?)</script>', content)
            for js_block in js_blocks:
                # Look for patterns that might indicate M3U8 construction
                if 'm3u8' in js_block.lower() or 'playlist' in js_block.lower() or 'stream' in js_block.lower():
                    # Try to find any URL-like strings
                    url_pattern = r'(https?://[^\s"\']+[^\s"\']*\.m3u8[^\s"\']*)'
                    matches = re.findall(url_pattern, js_block, re.IGNORECASE)
                    if matches:
                        for match in matches:
                            logger.debug(f"Found M3U8 in script block: {match}")
                            return match
            
            # Method 6: Look for HLS.js initialization
            hls_patterns = [
                r'Hls\(\)[^;]*loadSource\(["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'new\s+Hls\([^)]*\)[^;]*loadSource\(["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'player\.load\(["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'videojs\([^)]*\)\.src\(["\']([^"\']+\.m3u8[^"\']*)["\']',
            ]
            
            for pattern in hls_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    for match in matches:
                        if '.m3u8' in match:
                            logger.debug(f"Found M3U8 from HLS.js: {match}")
                            return match
            
            logger.warning(f"No M3U8 found in {embed_url}")
            return None
            
        except Exception as e:
            logger.error(f"Error extracting M3U8 from {embed_url}: {e}")
            return None
    
    def extract_iframe_url(self, page_content, base_url):
        """Extract iframe URL from page content"""
        parser = HTMLParserExtended()
        parser.feed(page_content)
        
        # Look for iframes
        for src in parser.iframes:
            if src and ('embed' in src or 'stream' in src or 'player' in src):
                return urllib.parse.urljoin(base_url, src)
        
        # Look for script that might contain iframe URL
        for script in parser.scripts:
            if script:
                # Look for iframe creation
                iframe_patterns = [
                    r'iframe\.src\s*=\s*["\']([^"\']+)["\']',
                    r'createElement\(["\']iframe["\']\)[^;]*src\s*=\s*["\']([^"\']+)["\']',
                    r'<iframe[^>]*src=["\']([^"\']+)["\']',
                    r'setAttribute\(["\']src["\'],\s*["\']([^"\']+)["\']\)',
                ]
                for pattern in iframe_patterns:
                    matches = re.findall(pattern, script)
                    if matches:
                        for match in matches:
                            if 'embed' in match or 'stream' in match:
                                return urllib.parse.urljoin(base_url, match)
        
        return None
    
    def process_event_page(self, url, event_data):
        """Process individual event page to extract stream URL"""
        try:
            logger.debug(f"Processing event: {url}")
            content = self.get_page(url)
            if not content:
                return None
            
            # Extract iframe URL
            iframe_url = self.extract_iframe_url(content, url)
            if not iframe_url:
                logger.debug(f"No iframe found for {url}")
                return None
            
            logger.debug(f"Found iframe: {iframe_url}")
            
            # Extract M3U8 from iframe
            m3u8_url = self.extract_m3u8_from_embed(iframe_url)
            if m3u8_url:
                # Add required headers for the stream
                headers = {
                    'Referer': iframe_url,
                    'Origin': urllib.parse.urlparse(iframe_url).scheme + '://' + urllib.parse.urlparse(iframe_url).netloc,
                    'User-Agent': self.headers['User-Agent']
                }
                
                # Return the stream URL with headers
                return {
                    'url': m3u8_url,
                    'headers': headers
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error processing {url}: {e}")
            return None
    
    def scrape_events(self):
        """Scrape events from volokit.xyz"""
        try:
            # Get the schedule page
            schedule_url = 'http://volokit.xyz/schedule/'
            logger.info(f"Fetching schedule from {schedule_url}")
            content = self.get_page(schedule_url)
            if not content:
                logger.error("Failed to fetch schedule page")
                return
            
            parser = HTMLParserExtended()
            parser.feed(content)
            
            # Look for event links
            event_links = []
            
            # Find all links that might be events
            for href in parser.links:
                if '/lives/' in href and href not in event_links:
                    event_links.append(href)
            
            # Also look for schedule cards using regex
            card_pattern = r'<div[^>]*class="[^"]*volo-schedule-card[^"]*"[^>]*>.*?<a[^>]*href="([^"]+)"'
            card_matches = re.findall(card_pattern, content, re.IGNORECASE | re.DOTALL)
            for href in card_matches:
                if '/lives/' in href and href not in event_links:
                    event_links.append(href)
            
            # Remove duplicates
            event_links = list(set(event_links))
            logger.info(f"Found {len(event_links)} event links")
            
            # Process events
            processed_events = []
            for i, link in enumerate(event_links[:30]):  # Limit to 30 events
                full_url = urllib.parse.urljoin(schedule_url, link)
                
                # Extract event info from URL
                event_name = link.split('/lives/')[-1].replace('/', ' ').replace('-', ' ').title()
                
                # Determine group based on event name
                group = 'Live Event'
                logo = 'https://i.gyazo.com/4a5e9fa2525808ee4b65002b56d3450e.png'
                
                event_lower = event_name.lower()
                if 'nba' in event_lower:
                    group = 'NBA'
                    logo = 'https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/nba.png'
                elif 'nfl' in event_lower:
                    group = 'NFL'
                    logo = 'https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/nfl.png'
                elif 'mlb' in event_lower:
                    group = 'MLB'
                    logo = 'https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/mlb.png'
                elif 'nhl' in event_lower:
                    group = 'NHL'
                    logo = 'https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/nhl.png'
                elif 'boxing' in event_lower or 'fight' in event_lower:
                    group = 'BOXING'
                elif 'race' in event_lower or 'formula' in event_lower or 'motogp' in event_lower:
                    group = 'RACE'
                
                # Check cache
                cache_key = f"{full_url}_{datetime.now().strftime('%Y%m%d')}"
                if cache_key in self.cached_events:
                    cached = self.cached_events[cache_key]
                    processed_events.append(cached)
                    logger.info(f"URL {i+1}) Using cached M3U8")
                    continue
                
                # Process the event page
                logger.info(f"Processing URL {i+1}): {event_name}")
                result = self.process_event_page(full_url, {'title': event_name, 'group': group})
                
                if result and result.get('url'):
                    event_data = {
                        'url': result['url'],
                        'headers': result.get('headers', {}),
                        'title': event_name,
                        'group': group,
                        'logo': logo,
                        'original_url': full_url
                    }
                    processed_events.append(event_data)
                    logger.info(f"URL {i+1}) Captured M3U8")
                else:
                    logger.warning(f"URL {i+1}) No M3U8 found")
                
                # Add delay to avoid rate limiting
                time.sleep(1)
            
            self.events = processed_events
            self.save_cache()
            
        except Exception as e:
            logger.error(f"Error scraping events: {e}")
    
    def generate_vlc_playlist(self, output_file='ovo_vlc.m3u8'):
        """Generate VLC-compatible playlist"""
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write('#EXTM3U\n')
                
                for event in self.events:
                    title = event.get('title', 'Unknown Event')
                    group = event.get('group', 'Live Event')
                    logo = event.get('logo', 'https://i.gyazo.com/4a5e9fa2525808ee4b65002b56d3450e.png')
                    url = event.get('url', '')
                    
                    if url:
                        f.write(f'#EXTINF:-1 tvg-id="Live.Event" tvg-logo="{logo}" group-title="{group}",[{group}] {title} (VOLOKIT)\n')
                        f.write(f'{url}\n')
                
            logger.info(f"Generated {output_file} with {len(self.events)} events")
            return True
        except Exception as e:
            logger.error(f"Error generating VLC playlist: {e}")
            return False
    
    def generate_tivimate_playlist(self, output_file='ovo_tivimate.m3u8'):
        """Generate TiviMate-compatible playlist"""
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write('#EXTM3U\n')
                
                for event in self.events:
                    title = event.get('title', 'Unknown Event')
                    group = event.get('group', 'Live Event')
                    logo = event.get('logo', 'https://i.gyazo.com/4a5e9fa2525808ee4b65002b56d3450e.png')
                    url = event.get('url', '')
                    
                    if url:
                        f.write(f'#EXTINF:-1 tvg-id="Live.Event" tvg-logo="{logo}" group-title="{group}",[{group}] {title} (VOLOKIT)\n')
                        f.write(f'{url}\n')
                
            logger.info(f"Generated {output_file} with {len(self.events)} events")
            return True
        except Exception as e:
            logger.error(f"Error generating TiviMate playlist: {e}")
            return False
    
    def run(self):
        """Main execution method"""
        logger.info("Starting OVO scraper")
        
        try:
            self.scrape_events()
            
            if self.events:
                self.generate_vlc_playlist()
                self.generate_tivimate_playlist()
                logger.info(f"Final playlist size: {len(self.events)} events")
                logger.info(f"Total written: {len(self.events)}")
            else:
                logger.warning("No events found")
            
            logger.info("OVO scraper completed")
            
        except Exception as e:
            logger.error(f"Scraper failed: {e}")
            sys.exit(1)

if __name__ == '__main__':
    scraper = OVOScraper()
    scraper.run()
