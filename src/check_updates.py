"""This fork uses explicit, reviewable release downloads; no self-updating code."""
RELEASES_URL = "https://github.com/AckwellFoley/TikTok-Live-Recorder/releases"

def check_updates():
    print("Download updates from " + RELEASES_URL)
    return False
