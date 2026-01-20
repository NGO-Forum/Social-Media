from flask import Flask, render_template, request, render_template_string, redirect, url_for, session
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import os, requests, json, tweepy, re
from dotenv import load_dotenv
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload
from moviepy.editor import ImageClip, concatenate_videoclips, AudioFileClip, afx
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import timezone as pytz_timezone
from PIL import Image
from flask_sqlalchemy import SQLAlchemy
from flask import send_from_directory
from google.auth.transport.requests import Request
from pathlib import Path
from openai import OpenAI
import time
from urllib.parse import quote_plus
import base64, hashlib

# Fix for Pillow >= 10 / Python 3.13
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

dotenv_path = Path(__file__).parent / ".env"
load_dotenv()  # Load tokens from .env

app = Flask(__name__)
app.secret_key = "@Riti#NGOF2025"


# MySQL connection
# app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:@locahost/media'
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:media2025@mysql_db/media'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


now = datetime.now(pytz_timezone('Asia/Phnom_Penh'))

# --- Scheduler ---
scheduler = BackgroundScheduler(timezone=pytz_timezone('Asia/Phnom_Penh'))
scheduler.start()


# Post model
class Post(db.Model):
    __tablename__ = 'posts'  # explicitly use your actual table name
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text, nullable=False)
    images = db.Column(db.Text, nullable=True)  # comma-separated paths
    scheduled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), nullable=True)
    posted = db.Column(db.Boolean, default=False)


# --- User credentials from environment variables ---
USERS = {}
for i in range(1, 10):  # supports up to 9 users, change if needed
    email = os.getenv(f"USER_{i}_EMAIL")
    password = os.getenv(f"USER_{i}_PASSWORD")
    if email and password:
        USERS[email] = generate_password_hash(password)


import os

SOCIAL_API = {
    "twitter": {
        "api_key": os.getenv("TWITTER_API_KEY"),
        "api_secret": os.getenv("TWITTER_API_SECRET_KEY"),
        "access_token": os.getenv("TWITTER_ACCESS_TOKEN"),
        "access_secret": os.getenv("TWITTER_ACCESS_TOKEN_SECRET"),
    },

    "facebook": {
        "page_id": os.getenv("META_PAGE_ID"),
        "access_token": os.getenv("META_PAGE_TOKEN"),  # ✅ PAGE TOKEN
    },

    "instagram": {
        "instagram_id": os.getenv("META_INSTAGRAM_BUSINESS_ID"),
        "access_token": os.getenv("META_PAGE_TOKEN"),  # ✅ SAME PAGE TOKEN
    },

    "youtube": {
        "creds_file": "token.json"  # OAuth handled by Google
    },

    "linkedin": {
        "client_id": os.getenv("LINKEDIN_CLIENT_ID"),
        "client_secret": os.getenv("LINKEDIN_CLIENT_SECRET"),
        "redirect_uri": os.getenv("LINKEDIN_REDIRECT_URI"),
        "organization_id": os.getenv("LINKEDIN_ORGANIZATION_ID"),
        "tokens_file": "linkedin_tokens.json",
    },

    "tiktok": {
        "client_key": os.getenv("TIKTOK_CLIENT_KEY"),
        "client_secret": os.getenv("TIKTOK_CLIENT_SECRET"),
        "redirect_uri": os.getenv("TIKTOK_REDIRECT_URI"),
        "business_id": os.getenv("TIKTOK_BUSINESS_ID"),
        "tokens_file": "tiktok_tokens.json",
    },
}


SCOPES_YOUTUBE = ["https://www.googleapis.com/auth/youtube.upload"]

TIKTOK_TOKEN_FILE = SOCIAL_API["tiktok"]["tokens_file"]

# --- Helper functions for login ---
def login_required(func):
    """Decorator to protect routes"""
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper

# --- Routes for authentication ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        if email in USERS and check_password_hash(USERS[email], password):
            session["user"] = email
            return redirect(url_for("index"))
        else:
            return render_template("login.html", error="Invalid email or password")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


# --- Slideshow creator ---
def create_slideshow(image_paths, output_path, duration_per_image=2, music_path=None):
    # Create image clips
    clips = [
        ImageClip(path).set_duration(duration_per_image).resize(height=720)
        for path in image_paths
    ]

    # Merge clips
    video = concatenate_videoclips(clips, method="compose")

    # Add background music
    if music_path and os.path.exists(music_path):
        audio = AudioFileClip(music_path).volumex(0.5)
        audio = afx.audio_loop(audio, duration=video.duration)
        video = video.set_audio(audio)

    # Export
    video.write_videofile(
        output_path,
        fps=24,
        codec="libx264",
        audio_codec="aac"
    )

    return output_path

# --- Get a static song from /static/music ---
def get_static_song():
    music_folder = os.path.join(app.root_path, "static", "music")
    if not os.path.exists(music_folder):
        return None

    for f in os.listdir(music_folder):
        if f.lower().endswith(".mp3"):
            return os.path.join(music_folder, f)

    return None


# --- Website posting ---
def post_website(title, desc, media_paths, department, published_at):
    try:
        API_URL = os.getenv("API_WEBSITE_ENDPOINT")

        payload = {
            "title": title,
            "description": desc,
            "department": department,
            "link": None,
            "published_at": published_at
        }

        files = []
        for path in media_paths:
            ext = os.path.splitext(path)[1].lower()
            if ext in [".jpg", ".jpeg", ".png", ".webp"]:
                files.append(("images[]", (os.path.basename(path), open(path, "rb"), "image/jpeg")))

        response = requests.post(API_URL, data=payload, files=files)

        return response.status_code in (200, 201)

    except Exception as e:
        print("❌ Website post error:", e)
        return False

def split_media(media_paths):
    images, videos = [], []
    for p in media_paths or []:
        ext = os.path.splitext(p)[1].lower()
        if ext in [".jpg", ".jpeg", ".png", ".webp"]:
            images.append(p)
        elif ext in [".mp4", ".mov", ".mkv"]:
            videos.append(p)
    return images, videos



# --- Facebook posting with multiple images or single video ---
def post_facebook(title, desc, media_paths):
    token = os.getenv("META_PAGE_TOKEN")      # PAGE access token
    page_id = os.getenv("META_PAGE_ID")
    DOMAIN = "https://media.ngoforum.site/uploads/"

    if not token or not page_id:
        print("❌ FB missing token or page_id")
        return False

    message = ((title or "") + "\n\n" + (desc or "")).strip()
    images, videos = split_media(media_paths)

    try:
        # ================= VIDEO =================
        if videos:
            video_url = DOMAIN + os.path.basename(videos[0])
            r = requests.post(
                f"https://graph.facebook.com/v19.0/{page_id}/videos",
                data={
                    "file_url": video_url,
                    "description": message,
                    "access_token": token
                },
                timeout=120
            )
            print("📌 FB VIDEO:", r.status_code, r.text)
            return r.status_code in (200, 201)

        # ================= MULTI IMAGE =================
        attached_media = []

        for img in images[:30]:  # FB limit = 30
            img_url = DOMAIN + os.path.basename(img)

            r = requests.post(
                f"https://graph.facebook.com/v19.0/{page_id}/photos",
                data={
                    "url": img_url,
                    "published": "false",   # 🔴 MUST BE false
                    "access_token": token
                },
                timeout=30
            )

            print("📌 FB PHOTO:", r.status_code, r.text)

            data = r.json()
            if "id" not in data:
                return False

            attached_media.append({"media_fbid": data["id"]})

        # Publish feed post
        r = requests.post(
            f"https://graph.facebook.com/v19.0/{page_id}/feed",
            data={
                "message": message,
                "attached_media": json.dumps(attached_media),
                "access_token": token
            },
            timeout=30
        )

        print("📌 FB FEED:", r.status_code, r.text)
        return r.status_code in (200, 201)

    except Exception as e:
        print("❌ Facebook exception:", e)
        return False


# --- Instagram posting (image OR video) ---
def post_instagram(caption, media_paths):
    token = os.getenv("META_PAGE_TOKEN")
    ig_id = os.getenv("META_INSTAGRAM_BUSINESS_ID")
    DOMAIN = "https://media.ngoforum.site/uploads/"

    if not token or not ig_id:
        print("❌ IG missing token or ig_id")
        return False

    images, videos = split_media(media_paths)

    try:
        # =====================================================
        # 🎬 VIDEO / REELS
        # =====================================================
        if videos:
            video_url = DOMAIN + os.path.basename(videos[0])

            # 1️⃣ Create media container
            r = requests.post(
                f"https://graph.facebook.com/v21.0/{ig_id}/media",
                data={
                    "media_type": "REELS",
                    "video_url": video_url,
                    "caption": caption,
                    "share_to_feed": "true",
                    "access_token": token
                },
                timeout=30
            )

            data = r.json()
            if "id" not in data:
                print("❌ IG video create failed:", data)
                return False

            creation_id = data["id"]

            # 2️⃣ Poll processing status
            while True:
                time.sleep(3)

                s = requests.get(
                    f"https://graph.facebook.com/v21.0/{creation_id}",
                    params={
                        "fields": "status_code",
                        "access_token": token
                    },
                    timeout=30
                ).json()

                status = s.get("status_code")
                print("⏳ IG VIDEO STATUS:", status)

                if status == "FINISHED":
                    time.sleep(2)  # extra safety wait
                    break

                if status == "ERROR":
                    print("❌ IG video processing error:", s)
                    return False

            # 3️⃣ Publish
            r = requests.post(
                f"https://graph.facebook.com/v21.0/{ig_id}/media_publish",
                data={
                    "creation_id": creation_id,
                    "access_token": token
                },
                timeout=30
            )

            resp = r.json()
            print("📌 IG REEL:", r.status_code, resp)

            if "id" not in resp:
                print("❌ IG reel publish failed:", resp)
                return False

            return True

        # =====================================================
        # 🖼 IMAGE CAROUSEL
        # =====================================================
        children = []

        for img in images[:10]:
            img_url = DOMAIN + os.path.basename(img)

            r = requests.post(
                f"https://graph.facebook.com/v21.0/{ig_id}/media",
                data={
                    "image_url": img_url,
                    "is_carousel_item": "true",
                    "access_token": token
                },
                timeout=30
            )

            data = r.json()
            if "id" not in data:
                print("❌ IG image create failed:", data)
                return False

            children.append(data["id"])

        if not children:
            print("❌ No valid Instagram media found")
            return False

        # Create carousel parent
        r = requests.post(
            f"https://graph.facebook.com/v21.0/{ig_id}/media",
            data={
                "media_type": "CAROUSEL",
                "children": ",".join(children),
                "caption": caption,
                "access_token": token
            },
            timeout=30
        )

        parent = r.json()
        if "id" not in parent:
            print("❌ IG carousel parent failed:", parent)
            return False

        # Publish carousel
        r = requests.post(
            f"https://graph.facebook.com/v21.0/{ig_id}/media_publish",
            data={
                "creation_id": parent["id"],
                "access_token": token
            },
            timeout=30
        )

        resp = r.json()
        print("📌 IG CAROUSEL:", r.status_code, resp)

        if "id" not in resp:
            print("❌ IG carousel publish failed:", resp)
            return False

        return True

    except Exception as e:
        print("❌ Instagram exception:", str(e))
        return False

#--- YouTube token refresh ---
def refresh_youtube_token():
    """Refresh YouTube OAuth token using refresh_token inside token.json."""
    try:
        creds_file = SOCIAL_API['youtube']['creds_file']

        if not os.path.exists(creds_file):
            print("⚠️ YouTube: token.json not found, cannot refresh.")
            return

        creds = Credentials.from_authorized_user_file(creds_file, SCOPES_YOUTUBE)

        # Refresh access token if expired & refresh_token is available
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

            with open(creds_file, "w") as f:
                f.write(creds.to_json())

            print("✅ YouTube token refreshed successfully.")
        else:
            print("✅ YouTube token is still valid.")

    except Exception as e:
        print("❌ YouTube refresh error:", e)

scheduler.add_job(refresh_youtube_token, 'interval', minutes=30)

# --- YouTube posting (with auto refresh) ---
def post_youtube(title, desc, media_path):
    creds_file = SOCIAL_API['youtube']['creds_file']

    if not os.path.exists(creds_file):
        print("❌ token.json missing — run YouTube OAuth script first!")
        return False

    # Load token
    creds = Credentials.from_authorized_user_file(creds_file, SCOPES_YOUTUBE)

    # ✅ Refresh token if needed
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(creds_file, "w") as f:
                f.write(creds.to_json())
            print("✅ YouTube token refreshed before upload.")
        except Exception as e:
            print("❌ Failed to refresh YouTube token:", e)
            return False

    try:
        youtube = build("youtube", "v3", credentials=creds)
        media = MediaFileUpload(media_path, chunksize=-1, resumable=True)

        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title if title else (desc[:50] if desc else "Video"),
                    "description": desc or ""
                },
                "status": {"privacyStatus": "public"}
            },
            media_body=media
        )

        response = request.execute()

        if "id" in response:
            print("✅ YouTube upload successful:", response["id"])
            return True

        print("❌ YouTube upload failed:", response)
        return False

    except Exception as e:
        print("❌ YouTube post exception:", e)
        return False



#--- TikTok posting ---
def post_tiktok(title, desc, media_path):
    """
    Upload a video to TikTok using the Business API.

    Uses get_tiktok_access_token() so it automatically refreshes
    the token when needed.
    """
    # Build caption text
    if title and desc:
        text = f"{title}\n\n{desc}"
    else:
        text = (title or "") + ("\n\n" + desc if desc else "")
    text = text.strip() or " "

    # Get a valid access token (handles refresh)
    access_token = get_tiktok_access_token()
    if not access_token:
        print("❌ TikTok: no valid access token, please reauthorize via /tiktok/login")
        return False

    headers = {"Access-Token": access_token}

    # --- Upload video file ---
    upload_url = "https://business-api.tiktok.com/open_api/v1.3/media/upload/"
    try:
        with open(media_path, "rb") as f:
            files = {"video_file": f}
            resp = requests.post(upload_url, files=files, headers=headers)

        if resp.status_code != 200:
            print("❌ TikTok upload failed:", resp.status_code, resp.text)
            return False

        media_id = resp.json().get("data", {}).get("video_id")
        if not media_id:
            print("❌ TikTok upload response missing video_id:", resp.text)
            return False

        # --- Create the post ---
        post_url = "https://business-api.tiktok.com/open_api/v1.3/post/create/"

        body = {
            "business_id": SOCIAL_API['tiktok']['business_id'],
            "video_id": media_id,
            "caption": text
        }
        post_resp = requests.post(post_url, json=body, headers=headers)

        if post_resp.status_code == 200:
            print("✅ TikTok post created successfully:", post_resp.text)
            return True

        print("❌ TikTok post failed:", post_resp.status_code, post_resp.text)
        return False

    except Exception as e:
        print("❌ TikTok post exception:", e)
        return False


# --- Token helpers ---
def save_tiktok_tokens(tokens):
    with open(TIKTOK_TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=4)

def load_tiktok_tokens():
    if os.path.exists(TIKTOK_TOKEN_FILE):
        with open(TIKTOK_TOKEN_FILE, "r") as f:
            return json.load(f)
    return None

def get_tiktok_access_token():
    tokens = load_tiktok_tokens()
    if not tokens:
        print("❌ TikTok not authorized. Visit /tiktok/login")
        return None

    expires_at = datetime.fromisoformat(tokens["expires_at"]).astimezone(timezone.utc)

    if datetime.now(timezone.utc) >= expires_at:
        print("🔄 Refreshing TikTok token...")

        refresh_url = "https://business-api.tiktok.com/open_api/v1.3/oauth/refresh_token/"
        payload = {
            "app_id": SOCIAL_API["tiktok"]["client_key"],
            "secret": SOCIAL_API["tiktok"]["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"]
        }

        r = requests.post(refresh_url, json=payload)
        if r.status_code != 200:
            print("❌ TikTok refresh failed:", r.text)
            return None

        data = r.json()["data"]
        tokens = {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": (
                datetime.now(timezone.utc)
                + timedelta(seconds=int(data["expires_in"]))
            ).isoformat()
        }
        save_tiktok_tokens(tokens)
        print("✅ TikTok token refreshed")

    return tokens["access_token"]



def generate_pkce_pair():
    code_verifier = base64.urlsafe_b64encode(os.urandom(40)).decode('utf-8').rstrip("=")
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('utf-8')).digest()
    ).decode('utf-8').rstrip("=")

    return code_verifier, code_challenge


# --- OAuth login ---
@app.route("/tiktok/login")
def tiktok_login():
    auth_url = (
        "https://business-api.tiktok.com/open_api/v1.3/oauth/authorize/"
        f"?app_id={SOCIAL_API['tiktok']['client_key']}"
        f"&redirect_uri={quote_plus(SOCIAL_API['tiktok']['redirect_uri'])}"
        "&state=ngof123"
    )
    return redirect(auth_url)


@app.route("/tiktok/callback")
def tiktok_callback():
    auth_code = request.args.get("auth_code")
    if not auth_code:
        return f"Missing auth_code: {request.args}", 400

    token_url = "https://business-api.tiktok.com/open_api/v1.3/oauth/access_token/"
    payload = {
        "app_id": SOCIAL_API["tiktok"]["client_key"],
        "secret": SOCIAL_API["tiktok"]["client_secret"],
        "grant_type": "authorization_code",
        "auth_code": auth_code,
        "business_id": SOCIAL_API["tiktok"]["business_id"]
    }

    r = requests.post(token_url, json=payload)
    if r.status_code != 200:
        return f"Token exchange failed:<br>{r.text}", 400

    data = r.json()["data"]

    save_tiktok_tokens({
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": (
            datetime.now(timezone.utc)
            + timedelta(seconds=int(data["expires_in"]))
        ).isoformat()
    })

    return "✅ TikTok Business authorized successfully"



#  linkedin posting to organization page
def post_linkedin_org(title=None, description=None, image_paths=None):
    status = linkedin_token_status()

    # If token missing or expired → STOP and notify UI
    if status in ["missing", "expired"]:
        print("❌ LinkedIn token expired or missing. User must re-login.")
        return {
            "success": False,
            "error": "expired_token"
        }

    access_token = get_linkedin_access_token()
    if not access_token:
        print("❌ LinkedIn token unavailable.")
        return {
            "success": False,
            "error": "expired_token"
        }

    org_urn = f"urn:li:organization:{SOCIAL_API['linkedin']['organization_id']}"
    text = (title or "") + ("\n\n" + description if description else "")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json"
    }

    # Upload images
    assets = []
    if image_paths:
        for path in image_paths[:9]:
            reg = requests.post(
                "https://api.linkedin.com/v2/assets?action=registerUpload",
                headers=headers,
                json={
                    "registerUploadRequest": {
                        "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                        "owner": org_urn,
                        "serviceRelationships": [
                            {
                                "relationshipType": "OWNER",
                                "identifier": "urn:li:userGeneratedContent"
                            }
                        ]
                    }
                }
            )

            if reg.status_code not in [200, 201]:
                print("❌ LinkedIn register upload failed:", reg.text)
                return {"success": False}

            data = reg.json()
            upload_url = data["value"]["uploadMechanism"][
                "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
            ]["uploadUrl"]

            asset_urn = data["value"]["asset"]

            with open(path, "rb") as f:
                up = requests.put(upload_url, data=f, headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "image/jpeg"
                })

            if up.status_code not in [200, 201]:
                print("❌ LinkedIn image upload failed:", up.text)
                return {"success": False}

            assets.append({
                "status": "READY",
                "media": asset_urn,
                "description": {"text": "Image"},
            })

    payload = {
        "author": org_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "IMAGE" if assets else "NONE",
                "media": assets
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        }
    }

    r = requests.post("https://api.linkedin.com/v2/ugcPosts", headers=headers, json=payload)

    if r.status_code in [200, 201]:
        print("✅ LinkedIn post successful!")
        return {"success": True}

    print("❌ LinkedIn post failed:", r.text)
    return {"success": False}


# --- LinkedIn token helpers ---
def save_linkedin_tokens(tokens):
    with open(SOCIAL_API['linkedin']['tokens_file'], "w") as f:
        json.dump(tokens, f, indent=4)


def load_linkedin_tokens():
    path = SOCIAL_API['linkedin']['tokens_file']
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return None


def linkedin_token_status():
    """
    Returns:
        "missing" → no token file
        "expired" → token expired
        "warn"    → token <30 days left
        "valid"   → token healthy
    """
    tokens = load_linkedin_tokens()
    if not tokens:
        return "missing"

    expires_at = datetime.fromisoformat(tokens["expires_at"]).astimezone(timezone.utc)
    now = datetime.now(timezone.utc)

    # Already expired
    if now >= expires_at:
        return "expired"

    # Less than 30 days left
    if expires_at - now < timedelta(days=30):
        return "warn"

    return "valid"


def get_linkedin_access_token():
    """
    Returns a valid access token, or None if expired.
    Auto-redirect is handled in view functions.
    """
    tokens = load_linkedin_tokens()
    if not tokens:
        print("❌ No LinkedIn token found.")
        return None

    expires_at = datetime.fromisoformat(tokens["expires_at"]).astimezone(timezone.utc)

    if datetime.now(timezone.utc) >= expires_at:
        print("❌ LinkedIn access token EXPIRED — re-login required")
        return None

    return tokens["access_token"]


# ============================================================
#                    🔵 LINKEDIN LOGIN ROUTES
# ============================================================

@app.route("/linkedin/login")
def linkedin_login():
    client_id = SOCIAL_API['linkedin']['client_id']
    redirect_uri = SOCIAL_API['linkedin']['redirect_uri']

    scopes = "w_organization_social r_organization_social"

    url = (
        "https://www.linkedin.com/oauth/v2/authorization"
        f"?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={requests.utils.requote_uri(redirect_uri)}"
        f"&scope={requests.utils.requote_uri(scopes)}"
    )
    return redirect(url)


@app.route("/linkedin/callback")
def linkedin_callback():
    code = request.args.get("code")
    if not code:
        return "No code received", 400

    response = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": SOCIAL_API['linkedin']['redirect_uri'],
            "client_id": SOCIAL_API['linkedin']['client_id'],
            "client_secret": SOCIAL_API['linkedin']['client_secret']
        }
    )

    if response.status_code != 200:
        return f"Token exchange failed: {response.text}", 400

    data = response.json()

    # LinkedIn DOES NOT return refresh_token for organization posting
    tokens = {
        "access_token": data["access_token"],
        "expires_at": (
            datetime.now(timezone.utc) +
            timedelta(seconds=int(data["expires_in"]))
        ).isoformat(),
        "refresh_token": None
    }

    save_linkedin_tokens(tokens)
    return "LinkedIn authorized successfully!"


# ============================================================
#                     🔵 TOKEN WARNING BANNER
# ============================================================

@app.context_processor
def inject_linkedin_warning():
    """
    Injects a variable into every template:
        linkedin_warning = None | "expired" | "warn"
    """
    status = linkedin_token_status()

    if status == "warn":
        return {"linkedin_warning": "LinkedIn token expires soon — reconnect soon!"}

    if status == "expired":
        return {"linkedin_warning": "LinkedIn token EXPIRED — please reconnect!"}

    return {"linkedin_warning": None}


def clean_input_text(text):
    # Normalize quotes
    text = text.replace("«", "\"").replace("»", "\"")
    text = text.replace("“", "\"").replace("”", "\"")

    # Remove trailing "។" after quotes
    text = re.sub(r'\"\s*។', '"', text)

    # Remove duplicate Khmer header indicators
    text = re.sub(r"^🇰🇭.*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^🇬🇧.*", "", text, flags=re.MULTILINE)

    # Remove duplicated summary sections
    text = re.sub(r"Summary.*", "", text, flags=re.IGNORECASE)

    return text.strip()


# --- Routes ---
@app.route("/", methods=["GET"])
@login_required
def index():
    return render_template("index.html")

@app.route("/", methods=["POST"])
@login_required
def post_all():
    selected_platforms = request.form.getlist("platforms")
    title = request.form.get("title")
    desc = request.form.get("desc")  # English
    title_kh = request.form.get("title_kh")  # Khmer title
    desc_kh = request.form.get("desc_kh")    # Khmer description
    scheduled_time_str = request.form.get("scheduled_at")
    website_department = request.form.get("website_department")

    # --- Determine published_at ---
    ph_timezone = pytz_timezone("Asia/Phnom_Penh")

    if scheduled_time_str:
        # Use scheduled datetime
        naive_dt = datetime.strptime(scheduled_time_str, "%Y-%m-%dT%H:%M")
        published_at = ph_timezone.localize(naive_dt).strftime("%Y-%m-%d %H:%M:%S")
    else:
        # Use current Phnom Penh time
        published_at = datetime.now(ph_timezone).strftime("%Y-%m-%d %H:%M:%S")



    media_files = request.files.getlist("media[]")
    media_paths = []
    for file in media_files:
        if not file or not file.filename:
            continue  # skip empty file inputs

        filename = secure_filename(file.filename)

        if filename == "":
            continue

        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(path)
        media_paths.append(path)



    # Build full text (Khmer + English)
    full_text_parts = []

    # Khmer first
    if title_kh:
        full_text_parts.append(title_kh)
    if desc_kh:
        full_text_parts.append(desc_kh)

    # English
    if title:
        full_text_parts.append(title)
    if desc:
        full_text_parts.append(desc)


    # --- Save post to database ---
    images_str = ",".join(media_paths) if media_paths else None
    scheduled_at = datetime.strptime(scheduled_time_str, "%Y-%m-%dT%H:%M") if scheduled_time_str else None

    new_post = Post(
        title=title,
        description=desc,
        images=images_str,
        scheduled_at=scheduled_at
    )

    db.session.add(new_post)
    db.session.commit()


    
    # --- Function to post to all selected platforms ---
    def do_post(post_obj):
        Done, Failed = [], []

        # Create slideshow for YouTube/TikTok if multiple images
        slideshow_path = None
        if len(media_paths) > 1 and any(p in selected_platforms for p in ["youtube", "tiktok"]):
            slideshow_path = os.path.join(app.config['UPLOAD_FOLDER'], f"slideshow_{datetime.now().strftime('%Y%m%d%H%M%S')}.mp4")
            try:
                music_path = get_static_song()
                slideshow_path = create_slideshow(
                    media_paths,
                    slideshow_path,
                    duration_per_image=2,
                    music_path=music_path
                )

            except Exception as e:
                print("❌ Failed to create slideshow:", e)
                slideshow_path = None

        # --- Facebook: use English + Khmer together ---
        if "facebook" in selected_platforms:
            fb_title = ""  # Facebook mainly uses description
            fb_desc_parts = []

            # Add Khmer first
            if title_kh:
                fb_desc_parts.append(title_kh)
            if desc_kh:
                fb_desc_parts.append(desc_kh)

            # Add English after Khmer
            if title:
                fb_desc_parts.append(title)
            if desc:
                fb_desc_parts.append(desc)

            fb_desc = "\n\n".join(fb_desc_parts)  # Join all parts with line breaks

            success = post_facebook(fb_title, fb_desc, media_paths if media_paths else None)
            if success:
                Done.append("Facebook")
            else:
                Failed.append("Facebook")

        # Build YouTube description (Khmer + English together)
        yt_desc_parts = []

        # Khmer first
        if title_kh:
            yt_desc_parts.append(title_kh)
        if desc_kh:
            yt_desc_parts.append(desc_kh)

        # English
        if title:
            yt_desc_parts.append(title)
        if desc:
            yt_desc_parts.append(desc)

        youtube_description = "\n\n".join(yt_desc_parts)



        for platform in selected_platforms:
            if platform == "facebook":
                continue
            try:
                success = False
                if platform == "website":
                    success = post_website(
                        title,
                        desc,
                        media_paths[:10],
                        website_department,
                        published_at
                    )

                elif platform == "instagram":
                    ig_parts = []
                    if title:
                        ig_parts.append(title)
                    if desc:
                        ig_parts.append(desc)

                    ig_caption = "\n\n".join(ig_parts).strip()

                    if not ig_caption:
                        print("❌ Instagram skipped: English caption is empty")
                        Failed.append("Instagram (English text required)")
                        continue

                    if not media_paths:
                        print("❌ Instagram skipped: No media")
                        Failed.append("Instagram (No media)")
                        continue

                    success = post_instagram(ig_caption, media_paths[:10])

                elif platform == "youtube":
                    youtube_title = title or title_kh
                    success = post_youtube(
                        youtube_title,
                        youtube_description,
                        slideshow_path or media_paths[0]
                    )

                elif platform == "linkedin":
                    ln_title = title or ""
                    ln_desc = desc or ""

                    result = post_linkedin_org(
                        ln_title,
                        ln_desc,
                        media_paths[:9]
                    )

                    if result.get("error") == "expired_token":
                        Failed.append("LinkedIn (Token Expired — Please Login)")

                    elif result.get("success"):
                        Done.append("LinkedIn")

                    else:
                        Failed.append("LinkedIn")
                    
                elif platform == "tiktok":
                    success = (slideshow_path or media_paths) and post_tiktok(title_kh, desc_kh, slideshow_path or media_paths[0])

                if success:
                    Done.append(platform.capitalize())
                else:
                    Failed.append(platform.capitalize())

            except Exception as e:
                print(f"❌ {platform} post failed:", e)
                Failed.append(platform.capitalize())
        
        # --- Mark post as posted ---
        post_obj.posted = True
        db.session.commit()
        return Done, Failed

    # --- Check if scheduled ---
    if scheduled_time_str:
        tz = pytz_timezone('Asia/Phnom_Penh')
        naive = datetime.strptime(scheduled_time_str, "%Y-%m-%dT%H:%M")
        scheduled_time = tz.localize(naive)

        scheduler.add_job(do_post, 'date', run_date=scheduled_time, args=[new_post])
        return render_template_string("""
        <html>
        <head><script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script></head>
        <body>
            <script>
            {% raw %}
                Swal.fire({
                    icon: 'success',
                    title: 'Scheduled!',
                    html: '✅ Your post is scheduled for: {% endraw %}{{ scheduled_time }}{% raw %}',
                    confirmButtonColor: '#22c55e'
                }).then(()=>{window.location.href='/'});
            {% endraw %}
            </script>
        </body>
        </html>
        """, scheduled_time=scheduled_time.strftime("%Y-%m-%d %H:%M"))

    
    # Post immediately
    Done, Failed = do_post(new_post)

    # --- Return results (fixed Jinja + JS) ---
    if Done:
        platforms_html = "<br>".join(Done)
        return render_template_string("""
        <!DOCTYPE html>
        <html lang="en">
        <head><meta charset="UTF-8"><title>Posting Result</title>
        <script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script></head>
        <body>
        <script>
        {% raw %}
            Swal.fire({
                icon: 'success',
                title: 'Posted Successfully!',
                html: '{% endraw %}{{ platforms_html|safe }}{% raw %}',
                confirmButtonColor: '#28a745'
            }).then(() => { window.location.href = '/'; });
        {% endraw %}
        </script>
        </body>
        </html>
        """, platforms_html=platforms_html)

    elif Failed:
        platforms_html = "<br>".join(Failed)
        return render_template_string("""
        <html><head><script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script></head>
        <body>
        <script>
        {% raw %}
            Swal.fire({
                icon: 'error',
                title: 'Failed!',
                html: '❌ Failed to post: {% endraw %}{{ platforms_html|safe }}{% raw %}',
                confirmButtonColor: '#dc3545'
            }).then(() => { window.location.href = '/'; });
        {% endraw %}
        </script>
        </body>
        </html>
        """, platforms_html=platforms_html)

    else:
        return render_template_string("""
        <html><head><script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script></head>
        <body>
        <script>
        {% raw %}
            Swal.fire({
                icon: 'warning',
                title: 'No platforms selected!',
                text: 'Please choose at least one platform to post to.',
                confirmButtonColor: '#f39c12'
            }).then(() => { window.location.href = '/'; });
        {% endraw %}
        </script>
        </body>
        </html>
        """)

    
# ---------- Simple health route ----------
@app.route("/status")
def status():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

@app.template_filter('basename')
def basename_filter(path):
    import os
    return os.path.basename(path)


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route("/posts")
@login_required
def show_posts():
    posts = Post.query.order_by(Post.created_at.desc()).all()  # latest first
    return render_template("posts.html", posts=posts)


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    # Ensure LinkedIn env variables exist (warn but still run)
    if not SOCIAL_API['linkedin']['client_id'] or not SOCIAL_API['linkedin']['client_secret'] or not SOCIAL_API['linkedin']['organization_id']:
        print("WARNING: LinkedIn client_id, client_secret or organization_id not set in environment. Visit /linkedin/login will fail until set.")
    app.run(host="0.0.0.0", port=5000, debug=True)

