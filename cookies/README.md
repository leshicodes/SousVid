# Cookie Setup for Instagram (and other authenticated platforms)

Instagram increasingly requires a logged-in session to download Reels,
even public ones. You need to export your browser cookies and drop them here.

## Steps

### 1. Install a browser extension
- **Chrome/Edge**: [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
- **Firefox**: [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)

### 2. Export cookies for instagram.com
1. Log in to Instagram in your browser
2. Navigate to instagram.com
3. Click the extension icon → "Export" (Netscape format)
4. Save the file as `cookies.txt` in this `cookies/` directory

### 3. Rebuild the container
The cookies directory is mounted read-only into the container. 
No rebuild needed -- just restart:

```bash
docker compose restart recipe-transcription
```

## Notes
- The `cookies.txt` file is gitignored for security -- never commit it
- Cookies expire; if downloads start failing again, re-export
- TikTok usually works without cookies (public content is fine)
- YouTube Shorts never needs cookies
