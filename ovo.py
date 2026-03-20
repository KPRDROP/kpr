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

from utils import Cache, Time, get_logger, leagues, network

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s',
    datefmt='%Y-%m-%d | %H:%M:%S'
)
logger = logging.getLogger(__name__)

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
                    content = f.read().strip()
                    if content:
                        self.cached_events = json.loads(content)
                    else:
                        self.cached_events = {}
                logger.info(f"Loaded {len(self.cached_events)} cached events")
        except json.JSONDecodeError:
            logger.warning("Cache file corrupted, starting fresh")
            self.cached_events = {}
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
                
                # Try to detect encoding
                content_type = response.headers.get('Content-Type', '')
                if 'charset=' in content_type:
                    charset = content_type.split('charset=')[-1].split(';')[0].strip()
                else:
                    charset = 'utf-8'
                
                content = response.read().decode(charset, errors='ignore')
                return content
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed for {url}: {e}")
                if attempt == 2:
                    raise
                time.sleep(2)
        return None
    
    def extract_links_from_html(self, html_content, base_url):
        """Extract all links from HTML content using regex"""
        links = []
        
        # Pattern for href attributes
        href_pattern = r'href=["\']([^"\']+)["\']'
        matches = re.findall(href_pattern, html_content)
        
        for match in matches:
            # Skip empty, javascript, and anchor links
            if match and not match.startswith('#') and not match.startswith('javascript:'):
                # Make absolute URL
                absolute_url = urllib.parse.urljoin(base_url, match)
                links.append(absolute_url)
        
        return links
    
    def extract_event_links(self, html_content, base_url):
        """Extract event links specifically from the schedule page"""
        event_links = []
        
        # Pattern for volokit event URLs
        event_pattern = r'href=["\']([^"\']*?/lives/[^"\']+)["\']'
        matches = re.findall(event_pattern, html_content, re.IGNORECASE)
        
        for match in matches:
            absolute_url = urllib.parse.urljoin(base_url, match)
            if absolute_url not in event_links:
                event_links.append(absolute_url)
        
        # Also look for schedule cards and buttons
        card_patterns = [
            r'<div[^>]*class="[^"]*volo-schedule-card[^"]*"[^>]*>.*?<a[^>]*href="([^"]+)"',
            r'<a[^>]*class="[^"]*volo-schedulebtn-card[^"]*"[^>]*href="([^"]+)"',
            r'<a[^>]*href="([^"]*?/lives/[^"]+)"[^>]*>.*?<\/a>',
        ]
        
        for pattern in card_patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE | re.DOTALL)
            for match in matches:
                absolute_url = urllib.parse.urljoin(base_url, match)
                if absolute_url not in event_links:
                    event_links.append(absolute_url)
        
        return event_links
    
    def extract_iframe_url(self, page_content, base_url):
        """Extract iframe URL from page content"""
        # Pattern for iframe src
        iframe_patterns = [
            r'<iframe[^>]*src=["\']([^"\']+)["\']',
            r'iframe\.src\s*=\s*["\']([^"\']+)["\']',
            r'setAttribute\(["\']src["\'],\s*["\']([^"\']+)["\']\)',
            r'<embed[^>]*src=["\']([^"\']+)["\']',
        ]
        
        for pattern in iframe_patterns:
            matches = re.findall(pattern, page_content, re.IGNORECASE)
            for match in matches:
                if match and ('embed' in match.lower() or 'stream' in match.lower() or 'player' in match.lower()):
                    return urllib.parse.urljoin(base_url, match)
        
        # Look for embed URLs in scripts
        script_pattern = r'<script[^>]*>([\s\S]*?)</script>'
        scripts = re.findall(script_pattern, page_content, re.IGNORECASE)
        
        for script in scripts:
            embed_patterns = [
                r'https?://[^"\']*embed[^"\']*\.php[^"\']*',
                r'https?://[^"\']*source[^"\']*\.php[^"\']*',
                r'https?://[^"\']*stream[^"\']*\.php[^"\']*',
            ]
            for pattern in embed_patterns:
                matches = re.findall(pattern, script, re.IGNORECASE)
                for match in matches:
                    return match
        
        return None
    
    def extract_m3u8_from_embed(self, embed_url):
        """Extract M3U8 URL from embed page"""
        try:
            logger.debug(f"Fetching embed: {embed_url}")
            content = self.get_page(embed_url)
            if not content:
                return None
            
            # Method 1: Look for direct M3U8 URLs
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
            
            # Method 2: Look for JavaScript variables
            js_patterns = [
                r'(?:var|const|let)\s+(?:url|src|source|stream|link|video|hls|m3u8)\s*=\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'(?:var|const|let)\s+(?:url|src|source|stream|link|video|hls|m3u8)\s*=\s*["\']([^"\']+)["\']',
                r'(?:source|src|file|video)\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'(?:source|src|file|video)\s*:\s*["\']([^"\']+)["\']',
                r'playlist\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'url\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'src\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
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
                        # Try different padding
                        decoded = base64.b64decode(match + '=' * (4 - len(match) % 4)).decode('utf-8')
                        if '.m3u8' in decoded:
                            logger.debug(f"Found M3U8 from base64: {decoded}")
                            return decoded
                    except:
                        pass
            
            # Method 4: Look for fetch.php parameters
            fetch_pattern = r'fetch\.php\?([^"\']+)'
            matches = re.findall(fetch_pattern, content)
            for match in matches:
                if 'hd=' in match or 'id=' in match:
                    parsed = urllib.parse.urlparse(embed_url)
                    base_url = f"{parsed.scheme}://{parsed.netloc}"
                    new_url = f"{base_url}/source/fetch.php?{match}"
                    logger.debug(f"Trying fetch param URL: {new_url}")
                    
                    stream_content = self.get_page(new_url)
                    if stream_content:
                        for pattern in m3u8_patterns:
                            stream_matches = re.findall(pattern, stream_content, re.IGNORECASE)
                            if stream_matches:
                                for stream_match in stream_matches:
                                    if '.m3u8' in stream_match:
                                        logger.debug(f"Found M3U8 from fetch: {stream_match}")
                                        return stream_match
            
            # Method 5: Look in script blocks
            script_pattern = r'<script[^>]*>([\s\S]*?)</script>'
            scripts = re.findall(script_pattern, content, re.IGNORECASE)
            
            for script in scripts:
                if 'm3u8' in script.lower():
                    url_pattern = r'(https?://[^\s"\']+[^\s"\']*\.m3u8[^\s"\']*)'
                    matches = re.findall(url_pattern, script, re.IGNORECASE)
                    if matches:
                        for match in matches:
                            logger.debug(f"Found M3U8 in script: {match}")
                            return match
            
            # Method 6: Look for HLS.js initialization
            hls_patterns = [
                r'Hls\(\)[^;]*loadSource\(["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'new\s+Hls\([^)]*\)[^;]*loadSource\(["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'player\.load\(["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'videojs\([^)]*\)\.src\(["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'plyr\.setup\([^)]*source[^)]*["\']([^"\']+\.m3u8[^"\']*)["\']',
            ]
            
            for pattern in hls_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    for match in matches:
                        if '.m3u8' in match:
                            logger.debug(f"Found M3U8 from HLS.js: {match}")
                            return match
            
            return None
            
        except Exception as e:
            logger.error(f"Error extracting M3U8 from {embed_url}: {e}")
            return None
    
    def process_event_page(self, url):
        """Process individual event page to extract stream URL"""
        try:
            logger.debug(f"Processing event: {url}")
            content = self.get_page(url)
            if not content:
                return None
            
            # Extract iframe URL
            iframe_url = self.extract_iframe_url(content, url)
            if not iframe_url:
                # Try to find direct embed URL in the content
                embed_pattern = r'(https?://[^"\']+embed[^"\']+\.php[^"\']+)'
                matches = re.findall(embed_pattern, content, re.IGNORECASE)
                if matches:
                    iframe_url = matches[0]
                else:
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
            # Get the main page first
            main_url = 'http://volokit.xyz/'
            logger.info(f"Fetching main page from {main_url}")
            main_content = self.get_page(main_url)
            
            # Get the schedule page
            schedule_url = 'http://volokit.xyz/schedule/'
            logger.info(f"Fetching schedule from {schedule_url}")
            content = self.get_page(schedule_url)
            if not content:
                # Try to find schedule from main page
                if main_content:
                    schedule_pattern = r'href=["\']([^"\']*schedule[^"\']*)["\']'
                    matches = re.findall(schedule_pattern, main_content, re.IGNORECASE)
                    if matches:
                        schedule_url = urllib.parse.urljoin(main_url, matches[0])
                        logger.info(f"Found schedule at: {schedule_url}")
                        content = self.get_page(schedule_url)
                
                if not content:
                    logger.error("Failed to fetch schedule page")
                    return
            
            # Extract event links
            event_links = self.extract_event_links(content, schedule_url)
            
            # Also try to find events from main page
            if main_content and not event_links:
                event_links = self.extract_event_links(main_content, main_url)
            
            # Remove duplicates and filter
            event_links = list(set(event_links))
            
            # Filter only volokit.xyz events
            event_links = [link for link in event_links if 'volokit.xyz' in link and '/lives/' in link]
            
            logger.info(f"Found {len(event_links)} event links")
            
            if not event_links:
                logger.warning("No event links found. Attempting to parse raw HTML...")
                # Try to find any volokit.xyz/lives/ URLs in the content
                raw_pattern = r'volokit\.xyz/lives/[^"\'\s]+'
                raw_matches = re.findall(raw_pattern, content, re.IGNORECASE)
                for match in raw_matches:
                    full_url = 'http://' + match
                    if full_url not in event_links:
                        event_links.append(full_url)
                logger.info(f"Found {len(event_links)} event links from raw parsing")
            
            # Process events
            processed_events = []
            for i, event_url in enumerate(event_links[:30]):  # Limit to 30 events
                # Extract event info from URL
                event_name = event_url.split('/lives/')[-1].replace('/', ' ').replace('-', ' ').title()
                event_name = re.sub(r'\s+', ' ', event_name).strip()
                
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
                elif 'soccer' in event_lower or 'football' in event_lower:
                    group = 'SOCCER'
                    logo = 'https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/soccer.png'
                
                # Check cache
                cache_key = f"{event_url}_{datetime.now().strftime('%Y%m%d')}"
                if cache_key in self.cached_events:
                    cached = self.cached_events[cache_key]
                    processed_events.append(cached)
                    logger.info(f"Event {i+1}) Using cached: {event_name}")
                    continue
                
                # Process the event page
                logger.info(f"Processing event {i+1}/{len(event_links)}: {event_name}")
                result = self.process_event_page(event_url)
                
                if result and result.get('url'):
                    event_data = {
                        'url': result['url'],
                        'headers': result.get('headers', {}),
                        'title': event_name,
                        'group': group,
                        'logo': logo,
                        'original_url': event_url
                    }
                    processed_events.append(event_data)
                    logger.info(f"Event {i+1}) ✓ Captured M3U8")
                else:
                    logger.warning(f"Event {i+1}) ✗ No M3U8 found")
                
                # Add delay to avoid rate limiting
                time.sleep(1)
            
            self.events = processed_events
            self.save_cache()
            
        except Exception as e:
            logger.error(f"Error scraping events: {e}")
            import traceback
            traceback.print_exc()
    
    def generate_vlc_playlist(self, output_file='ovo_vlc.m3u8'):
        """Generate VLC-compatible playlist"""
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write('#EXTM3U\n')
                f.write('# Playlist generated by OVO Scraper\n')
                f.write(f'# Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
                f.write(f'# Events found: {len(self.events)}\n\n')
                
                for event in self.events:
                    title = event.get('title', 'Unknown Event')
                    group = event.get('group', 'Live Event')
                    logo = event.get('logo', 'https://i.gyazo.com/4a5e9fa2525808ee4b65002b56d3450e.png')
                    url = event.get('url', '')
                    
                    if url:
                        f.write(f'#EXTINF:-1 tvg-id="{group}.Event" tvg-logo="{logo}" group-title="{group}",[{group}] {title}\n')
                        f.write(f'{url}\n\n')
                
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
                f.write('# Playlist generated by OVO Scraper\n')
                f.write(f'# Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
                f.write(f'# Events found: {len(self.events)}\n\n')
                
                for event in self.events:
                    title = event.get('title', 'Unknown Event')
                    group = event.get('group', 'Live Event')
                    logo = event.get('logo', 'https://i.gyazo.com/4a5e9fa2525808ee4b65002b56d3450e.png')
                    url = event.get('url', '')
                    
                    if url:
                        f.write(f'#EXTINF:-1 tvg-id="{group}.Event" tvg-logo="{logo}" group-title="{group}",[{group}] {title}\n')
                        f.write(f'{url}\n\n')
                
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
                logger.info(f"Output files: ovo_vlc.m3u8, ovo_tivimate.m3u8")
            else:
                logger.warning("No events found - check if volokit.xyz is accessible")
                # Create empty playlists as placeholders
                with open('ovo_vlc.m3u8', 'w') as f:
                    f.write('#EXTM3U\n# No events found\n')
                with open('ovo_tivimate.m3u8', 'w') as f:
                    f.write('#EXTM3U\n# No events found\n')
            
            logger.info("OVO scraper completed")
            
        except Exception as e:
            logger.error(f"Scraper failed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

if __name__ == '__main__':
    scraper = OVOScraper()
    scraper.run()
