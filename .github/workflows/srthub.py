name: StreamHub Scraper

on:
  schedule:
    # Runs every hour
    - cron: '0 * * * *'
  workflow_dispatch:

jobs:
  scrape:
    runs-on: ubuntu-latest
    
    steps:
    - name: Checkout repository
      uses: actions/checkout@v3
      
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.10'
        
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install playwright httpx[http2] pytz
        pip install selectolax
        
    - name: Install Playwright browsers
      run: |
        playwright install chromium
        playwright install-deps
        
    - name: Run StreamHub scraper
      env:
        STREAMHUB_BASE_URL: ${{ secrets.STREAMHUB_BASE_URL }}
      run: |
        python -c "from srthub import run; run()"
        
    - name: Check for output files
      id: check_files
      run: |
        if [ -f srthub_vlc.m3u8 ] && [ -f srthub_tivimate.m3u8 ]; then
          echo "files_exist=true" >> $GITHUB_OUTPUT
        else
          echo "files_exist=false" >> $GITHUB_OUTPUT
        fi
        
    - name: Commit and push if changes
      if: steps.check_files.outputs.files_exist == 'true'
      run: |
        git config --local user.email "action@github.com"
        git config --local user.name "GitHub Action"
        git add srthub_vlc.m3u8 srthub_tivimate.m3u8
        git diff --quiet && git diff --staged --quiet || git commit -m "Update SrtHub playlists [skip ci]"
        git push
